"""
工单语义聚类脚本（LLM 主导）
1. 读取 Excel 工单数据
2. 阶段一：LLM 为每条工单抽取「核心问题摘要」
3. 阶段二：embedding 预聚合 + LLM 全局归并主题
4. 阶段三：兜底分配 + 主题命名
5. 输出 JSON
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")  # 本地 sentence-transformers 模型

EXCEL_DIR = Path(__file__).parent.parent / "data"
OUTPUT_PATH = Path(__file__).parent.parent / "public" / "ticket_data.json"
CACHE_DIR = Path(__file__).parent / ".cache"
SUMMARY_CACHE = CACHE_DIR / "summaries.json"
EMBED_CACHE = CACHE_DIR / "embeddings.npz"
CLUSTER_CACHE = CACHE_DIR / "clusters.json"

EXCEL_FILES = [
    EXCEL_DIR / "人工.xlsx",
    EXCEL_DIR / "工单数据汇总_时间范围2026-05-11 00_00_00至2026-05-27 23_59_59 (1).xlsx",
]

SHEET_NAME = "工单数据"

COL = {
    "id": 0, "type": 1, "title": 4, "phenomenon": 5,
    "service_staff": 6, "status": 7, "channel": 8, "source": 9,
    "ticket_type": 10, "category1": 11, "category2": 12,
    "satisfaction": 14, "feedback_time": 15,
    "response_time": 22, "process_duration": 23, "messages": 24,
}

GROUP_MIN = 5
GROUP_MAX = 30

# 并发与批量
SUMMARY_BATCH = 10        # 阶段一每批工单数
LLM_CONCURRENCY = 20      # 全局 LLM 并发上限（所有阶段共享）
MERGE_CHUNK = 80          # 阶段二每个 LLM 归并块的最大工单数
EMBED_BATCH = 64          # 本地 embedding 批量
SIM_THRESHOLD = 0.82      # embedding 预聚合阈值
HTTP_MAX_CONNECTIONS = 50 # httpx 连接池上限


# ─── 数据读取 ───────────────────────────────────────────

def read_excel_files() -> pd.DataFrame:
    dfs = []
    for f in EXCEL_FILES:
        if f.exists():
            df = pd.read_excel(f, sheet_name=SHEET_NAME, header=0)
            dfs.append(df)
            print(f"  读取 {f.name}: {len(df)} 条")
    if not dfs:
        raise FileNotFoundError("未找到 Excel 文件")
    result = pd.concat(dfs, ignore_index=True)
    result = result.drop_duplicates(subset=[result.columns[COL["id"]]], keep="first")
    print(f"  合并去重后: {len(result)} 条")
    return result


def parse_messages(raw: str) -> list[dict]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    messages = []
    pattern = r"(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.*)"
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(pattern, line)
        if not m:
            continue
        sender, time_str, content = m.group(1), m.group(2), m.group(3)
        role = "robot" if sender == "ROBOT" else "user"
        if "ruliu-img" in content or re.search(r"https?://\S+\.(png|jpg|jpeg|gif|bmp)", content, re.I):
            msg_type = "image"
        elif "```" in content:
            msg_type = "code"
        else:
            msg_type = "text"
        messages.append({"sender": sender, "time": time_str, "content": content, "type": msg_type, "role": role})
    return messages


def build_ticket(row: pd.Series) -> dict:
    def val(idx, default=""):
        if idx >= len(row):
            return default
        v = row.iloc[idx]
        return default if pd.isna(v) else str(v)

    sat = val(COL["satisfaction"], "0")
    try:
        satisfaction = int(float(sat))
    except ValueError:
        satisfaction = 0

    return {
        "id": val(COL["id"]),
        "title": val(COL["title"]),
        "phenomenon": val(COL["phenomenon"]),
        "status": val(COL["status"]),
        "customerService": val(COL["service_staff"]),
        "source": val(COL["source"]),
        "satisfaction": satisfaction,
        "feedbackTime": val(COL["feedback_time"]),
        "responseTime": val(COL["response_time"]),
        "processDuration": val(COL["process_duration"]),
        "ticketType": val(COL["ticket_type"]),
        "messages": parse_messages(val(COL["messages"])),
    }


def _clean_raw(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"ruliu-img\S*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_raw_text_full(ticket: dict) -> str:
    """完整原文（不截断），仅用于 hash"""
    parts = []
    if ticket["title"]:
        parts.append(ticket["title"])
    if ticket["phenomenon"]:
        parts.append(ticket["phenomenon"])
    user_msgs = [m["content"] for m in ticket["messages"] if m["role"] == "user"][:2]
    parts.extend(user_msgs)
    return _clean_raw(" ".join(parts))


def extract_raw_text(ticket: dict) -> str:
    """截断后的文本，喂给 LLM"""
    return extract_raw_text_full(ticket)[:600]


# ─── 缓存 ───────────────────────────────────────────────

def text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def load_summary_cache() -> dict[str, dict]:
    if SUMMARY_CACHE.exists():
        try:
            with open(SUMMARY_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  📁 命中摘要缓存：{len(data)} 条")
            return data
        except Exception as e:
            print(f"  ⚠️ 摘要缓存损坏，忽略：{e}")
    return {}


def save_summary_cache(cache: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY_CACHE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    tmp.replace(SUMMARY_CACHE)


def load_embed_cache() -> dict[str, np.ndarray]:
    if EMBED_CACHE.exists():
        try:
            data = np.load(EMBED_CACHE, allow_pickle=False)
            keys = data["keys"]
            vecs = data["vecs"]
            cache = {str(k): vecs[i] for i, k in enumerate(keys)}
            print(f"  📁 命中 embedding 缓存：{len(cache)} 条")
            return cache
        except Exception as e:
            print(f"  ⚠️ embedding 缓存损坏，忽略：{e}")
    return {}


def save_embed_cache(cache: dict[str, np.ndarray]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    keys = np.array(list(cache.keys()))
    vecs = np.stack(list(cache.values())) if cache else np.zeros((0, 0), dtype=np.float32)
    # 用 .npz 结尾的临时路径，避免 np.savez 自动追加扩展名导致 replace 找不到文件
    tmp = EMBED_CACHE.with_name(EMBED_CACHE.name + ".tmp.npz")
    np.savez(tmp, keys=keys, vecs=vecs)
    tmp.replace(EMBED_CACHE)


def load_cluster_cache() -> dict[str, dict]:
    """阶段二聚类结果缓存：key -> {themes, hashes_present}"""
    if CLUSTER_CACHE.exists():
        try:
            with open(CLUSTER_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  📁 命中阶段二缓存：{len(data)} 个分类")
            return data
        except Exception as e:
            print(f"  ⚠️ 阶段二缓存损坏，忽略：{e}")
    return {}


def save_cluster_cache(cache: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CLUSTER_CACHE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    tmp.replace(CLUSTER_CACHE)


def cluster_cache_key(category: str, tickets: list[dict]) -> str:
    """缓存 key：分类名 + 该分类下所有工单 hash + core_issue 的稳定签名"""
    parts = sorted(
        f"{t['_hash']}|{t['_summary'].get('core_issue', '')}" for t in tickets
    )
    body = category + "::" + "\n".join(parts)
    return text_hash(body)


_LOCAL_EMBED_MODEL = None


def _get_local_embed_model():
    """惰性加载本地 embedding 模型（首次使用时下载/加载，之后复用）"""
    global _LOCAL_EMBED_MODEL
    if _LOCAL_EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer
        print(f"  🔄 加载本地 embedding 模型: {EMBED_MODEL}（首次会下载到 ~/.cache/huggingface）")
        _LOCAL_EMBED_MODEL = SentenceTransformer(EMBED_MODEL)
    return _LOCAL_EMBED_MODEL


def compute_embeddings_cached(texts: list[str]) -> np.ndarray:
    """对 texts 求 embedding：本地模型 + 缓存。命中则跳过，未命中批量计算后落盘。"""
    cache = load_embed_cache()
    hashes = [text_hash(t) for t in texts]
    miss_idx = [i for i, h in enumerate(hashes) if h not in cache]
    print(f"  embedding 缓存命中 {len(texts) - len(miss_idx)}/{len(texts)}，待计算 {len(miss_idx)} 条")

    if miss_idx:
        model = _get_local_embed_model()
        miss_texts = [texts[i] for i in miss_idx]
        # sentence-transformers 自带批处理 + 进度条
        vecs = model.encode(
            miss_texts,
            batch_size=EMBED_BATCH,
            normalize_embeddings=True,
            show_progress_bar=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        for k, idx in enumerate(miss_idx):
            cache[hashes[idx]] = vecs[k]
        save_embed_cache(cache)

    if not cache:
        raise RuntimeError("embedding 全部失败，无法继续")

    arr = np.stack([cache[h] for h in hashes]).astype(np.float32)
    return arr


# ─── LLM / Embedding HTTP 封装 ──────────────────────────

class LLMClient:
    def __init__(self):
        limits = httpx.Limits(
            max_connections=HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=HTTP_MAX_CONNECTIONS,
        )
        self.client = httpx.AsyncClient(timeout=120, limits=limits, http2=False)
        self.headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        self.llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def chat(self, prompt: str, temperature: float = 0.2, response_json: bool = False) -> str:
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        async with self.llm_sem:
            resp = await self._post_with_retry(f"{BASE_URL}/chat/completions", payload)
            return resp.json()["choices"][0]["message"]["content"]

    async def _post_with_retry(self, url: str, payload: dict, max_attempts: int = 5) -> httpx.Response:
        """对 5xx / 429 / 网络异常做指数退避"""
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = await self.client.post(url, headers=self.headers, json=payload)
                if resp.status_code < 500 and resp.status_code != 429:
                    resp.raise_for_status()
                    return resp
                # 5xx / 429 → 重试
                last_err = httpx.HTTPStatusError(
                    f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp,
                )
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_err = e
            if attempt < max_attempts - 1:
                wait = min(2 ** attempt, 30) + (attempt * 0.3)
                await asyncio.sleep(wait)
        assert last_err is not None
        raise last_err

    async def close(self):
        await self.client.aclose()


def parse_json_loose(text: str):
    """从 LLM 返回中提取 JSON（容忍 ```json 包裹和前后噪声）"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1)
    # 尝试整体解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退化：找第一个 { 或 [ 到对应的最后一个 } 或 ]
    for open_c, close_c in [("[", "]"), ("{", "}")]:
        i = text.find(open_c)
        j = text.rfind(close_c)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法解析 JSON：{text[:200]}")


# ─── 阶段一：抽取问题摘要 ───────────────────────────────

SUMMARY_PROMPT_TEMPLATE = """你是工单分析师。请为下列工单抽取「核心问题」摘要，用于后续聚类。

要求：
- core_issue：一句话描述用户遇到的核心问题，10-25 字，去掉环境/账号/时间等具体细节，保留问题本质。例如「插件升级后无法启动」「反馈接口返回 401」「文档同步失败」。
- keywords：3-6 个关键词（中文/英文均可），覆盖产品/动作/异常表现。
- 不要复述原文，要做归纳。

输出严格 JSON：{{"results": [{{"id": "<原 id>", "core_issue": "...", "keywords": ["...", "..."]}}, ...]}}

工单列表：
{items}
"""


async def summarize_batch(llm: LLMClient, batch: list[dict]) -> dict[str, dict]:
    """对一批工单调用 LLM 抽取摘要，返回 {id: {core_issue, keywords}}"""
    items = "\n".join(
        f"id={t['_sid']}: {extract_raw_text(t) or '(无内容)'}"
        for t in batch
    )
    prompt = SUMMARY_PROMPT_TEMPLATE.format(items=items)
    for attempt in range(2):
        try:
            content = await llm.chat(prompt, response_json=True)
            data = parse_json_loose(content)
            results = data.get("results", []) if isinstance(data, dict) else data
            out = {}
            for r in results:
                sid = str(r.get("id", "")).strip()
                if sid:
                    out[sid] = {
                        "core_issue": str(r.get("core_issue", "")).strip(),
                        "keywords": r.get("keywords", []) or [],
                    }
            return out
        except Exception as e:
            if attempt == 1:
                print(f"      摘要抽取失败（已重试）：{e}")
                return {}
            await asyncio.sleep(1)
    return {}


async def summarize_all(llm: LLMClient, tickets: list[dict]) -> None:
    """为所有工单原地写入 _summary 字段（带缓存）"""
    for i, t in enumerate(tickets):
        t["_sid"] = f"t{i}"
        t["_raw"] = extract_raw_text(t) or t["title"] or "(无内容)"
        t["_hash"] = text_hash(extract_raw_text_full(t) or t["title"] or "")

    cache = load_summary_cache()

    hit = 0
    pending: list[dict] = []
    for t in tickets:
        if t["_hash"] in cache:
            t["_summary"] = cache[t["_hash"]]
            hit += 1
        else:
            pending.append(t)
    print(f"  缓存命中 {hit}/{len(tickets)}，待抽取 {len(pending)} 条")

    if not pending:
        for t in tickets:
            t.pop("_raw", None)
        return

    batches = [pending[i:i + SUMMARY_BATCH] for i in range(0, len(pending), SUMMARY_BATCH)]
    print(f"  → {len(batches)} 批，全局并发 {LLM_CONCURRENCY}")

    done = 0
    flush_every = 20
    tasks = [summarize_batch(llm, b) for b in batches]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        for t in pending:
            if t["_sid"] in result:
                t["_summary"] = result[t["_sid"]]
                cache[t["_hash"]] = result[t["_sid"]]
        done += 1
        if done % flush_every == 0:
            save_summary_cache(cache)
        if done % 10 == 0 or done == len(batches):
            print(f"    进度 {done}/{len(batches)}")
    save_summary_cache(cache)

    miss = 0
    for t in tickets:
        if "_summary" not in t:
            t["_summary"] = {"core_issue": t["_raw"][:40], "keywords": []}
            miss += 1
    if miss:
        print(f"  ⚠️ {miss} 条未成功抽取摘要，已用原文兜底（未写入缓存）")

    # _hash 后续阶段二缓存还要用，留到 main 末尾再清理
    for t in tickets:
        t.pop("_raw", None)


# ─── 阶段二：embedding 预聚合 + LLM 归并 ────────────────

def union_find_clusters(sim: np.ndarray, threshold: float) -> list[list[int]]:
    """按相似度阈值做 union-find 粗聚合"""
    n = sim.shape[0]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 矢量化提取上三角中相似度过阈的对
    mask = np.triu(sim >= threshold, k=1)
    ii, jj = np.where(mask)
    for i, j in zip(ii.tolist(), jj.tolist()):
        union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)
    return list(groups.values())


MERGE_PROMPT_TEMPLATE = """你是数据分析师，需要把同一类目下的工单按「具体问题」归并成主题。

类目：{category}

工单核心问题列表（id: 摘要）：
{items}

任务：
1. 把语义相同/相近的工单合并到同一主题。同义不同形（如「无法启动」「打不开」「启动失败」）必须合并。
2. 每个主题至少 {gmin} 条、至多 {gmax} 条；若某主题超过 {gmax}，请按更细粒度拆分。
3. 不要使用「其他问题」「综合问题」等宽泛名称。
4. 主题名 8-16 字，可用斜杠连接相关概念。
5. 描述一句话概括典型表现。

输出严格 JSON：
{{
  "themes": [
    {{
      "name": "主题名",
      "description": "一句话描述",
      "id_suffix": "english-kebab-case",
      "ticket_ids": ["t3","t8",...]
    }},
    ...
  ]
}}
所有给出的 id 必须出现且仅出现一次。"""


async def llm_merge_chunk(llm: LLMClient, category: str, tickets: list[dict]) -> list[dict]:
    """对一个 chunk 的工单做 LLM 主题归并"""
    items = "\n".join(f"{t['_sid']}: {t['_summary']['core_issue']}" for t in tickets)
    prompt = MERGE_PROMPT_TEMPLATE.format(
        category=category, items=items, gmin=GROUP_MIN, gmax=GROUP_MAX,
    )
    for attempt in range(2):
        try:
            content = await llm.chat(prompt, temperature=0.2, response_json=True)
            data = parse_json_loose(content)
            themes = data.get("themes", []) if isinstance(data, dict) else data
            return themes
        except Exception as e:
            if attempt == 1:
                print(f"      主题归并失败（已重试）：{e}")
                return []
            await asyncio.sleep(1)
    return []


THEME_DEDUP_PROMPT = """以下是同一类目「{category}」下的多个候选主题（来自不同分块的归并结果）。
请识别其中**实际指代同一问题**的主题并合并。

候选主题列表（编号: 名称 - 描述）：
{items}

要求：
1. 同义不同形（如「无法启动」「打不开」「启动失败」）必须合并为一个主题。
2. 不同问题保持独立，不要过度合并。
3. 输出 JSON：每个合并后的主题给出 group_name、group_description，以及包含的候选主题编号列表。

输出严格 JSON：
{{
  "merged": [
    {{"group_name": "主题名", "group_description": "一句话描述", "members": [1, 5, 12]}},
    ...
  ]
}}
所有候选编号必须出现且仅出现一次。"""


async def llm_dedup_themes(llm: LLMClient, category: str, themes: list[dict]) -> list[list[int]]:
    """让 LLM 识别同义主题，返回成员索引分组列表（每个内层列表是合并到同一主题的原 theme idx）"""
    if len(themes) <= 1:
        return [[i] for i in range(len(themes))]

    items = "\n".join(
        f"{i + 1}: {t.get('name', '').strip()} - {t.get('description', '').strip()}"
        for i, t in enumerate(themes)
    )
    prompt = THEME_DEDUP_PROMPT.format(category=category, items=items)
    try:
        content = await llm.chat(prompt, temperature=0.1, response_json=True)
        data = parse_json_loose(content)
        merged = data.get("merged", []) if isinstance(data, dict) else data
        groups: list[list[int]] = []
        seen: set[int] = set()
        for m in merged:
            members = m.get("members", []) or []
            idxs = []
            for x in members:
                try:
                    j = int(x) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= j < len(themes) and j not in seen:
                    idxs.append(j)
                    seen.add(j)
            if idxs:
                # 把合并后的名称/描述写回第一个 theme，后续直接用
                themes[idxs[0]]["name"] = str(m.get("group_name", "")).strip() or themes[idxs[0]].get("name", "")
                themes[idxs[0]]["description"] = str(m.get("group_description", "")).strip() or themes[idxs[0]].get("description", "")
                groups.append(idxs)
        # 兜底：未出现的 theme 各自独立
        for i in range(len(themes)):
            if i not in seen:
                groups.append([i])
        return groups
    except Exception as e:
        print(f"      主题去重失败，跳过：{e}")
        return [[i] for i in range(len(themes))]


def split_by_embedding_chunks(tickets: list[dict], embeddings: np.ndarray, target_chunk: int) -> list[list[int]]:
    """
    将 tickets 按 embedding 相似度预聚合成块，每块 ≤ target_chunk。
    单个超大簇用 KMeans-lite（按种子最远点）切分。
    """
    n = len(tickets)
    if n <= target_chunk:
        return [list(range(n))]

    sim = embeddings @ embeddings.T
    raw_groups = union_find_clusters(sim, SIM_THRESHOLD)

    chunks: list[list[int]] = []
    pending: list[int] = []
    for g in sorted(raw_groups, key=len, reverse=True):
        if len(g) > target_chunk:
            # 用相似度做贪心切分
            remaining = set(g)
            while remaining:
                seed = next(iter(remaining))
                # 取与 seed 最相似的 target_chunk-1 个
                cand = sorted(remaining, key=lambda x: -sim[seed, x])[:target_chunk]
                chunks.append(cand)
                remaining -= set(cand)
        else:
            if len(pending) + len(g) <= target_chunk:
                pending.extend(g)
            else:
                if pending:
                    chunks.append(pending)
                pending = list(g)
    if pending:
        chunks.append(pending)
    return chunks


async def cluster_category_llm(
    llm: LLMClient,
    category: str,
    tickets: list[dict],
    embeddings: np.ndarray,
    cluster_cache: dict | None = None,
) -> list[dict]:
    """对一个一级分类下的工单做 LLM 主导的聚类"""
    n = len(tickets)
    if n < GROUP_MIN:
        return [{
            "id": f"{category}-general",
            "name": "通用问题",
            "description": "该分类下工单数量较少，统一归组",
            "count": n,
            "tickets": tickets,
        }]

    sid_to_ticket = {t["_sid"]: t for t in tickets}
    sid_to_idx = {t["_sid"]: i for i, t in enumerate(tickets)}
    hash_to_sid = {t["_hash"]: t["_sid"] for t in tickets}

    # 0. 先看阶段二缓存
    cache_key = cluster_cache_key(category, tickets)
    cached = (cluster_cache or {}).get(cache_key)
    if cached:
        print(f"    📁 命中阶段二缓存，跳过 LLM")
        theme_meta = cached["theme_meta"]
        assignments_by_hash = cached["assignments"]
        # 把 hash→theme_key 翻译成 sid→theme_key
        assigned = {
            hash_to_sid[h]: tk
            for h, tk in assignments_by_hash.items()
            if h in hash_to_sid
        }
        # 兜底：缓存里没有的 hash（理论上不应发生，因为 cache_key 已锁定集合）
        for t in tickets:
            if t["_sid"] not in assigned:
                assigned[t["_sid"]] = "fallback"
                theme_meta.setdefault("fallback", {
                    "name": "未分类问题",
                    "description": "缓存重放时未匹配，统一归组",
                    "suffix": "uncategorized",
                })
    else:
        # 1. 切块（让单次 LLM 归并不过长）
        chunks = split_by_embedding_chunks(tickets, embeddings, MERGE_CHUNK)
        print(f"    embedding 预聚合 → {len(chunks)} 块")

        # 2. 每块跑 LLM 归并（共享全局 LLM 信号量）
        all_themes: list[dict] = []

        async def run_chunk(idxs: list[int]) -> list[dict]:
            sub = [tickets[i] for i in idxs]
            return await llm_merge_chunk(llm, category, sub)

        chunk_results = await asyncio.gather(*(run_chunk(ix) for ix in chunks))
        for themes in chunk_results:
            all_themes.extend(themes)

        # 2.5. 跨 chunk 同义主题二次合并
        if len(chunks) > 1 and len(all_themes) > 1:
            dedup_groups = await llm_dedup_themes(llm, category, all_themes)
            if dedup_groups:
                new_themes: list[dict] = []
                for group in dedup_groups:
                    if not group:
                        continue
                    primary = all_themes[group[0]]
                    merged_ticket_ids: list[str] = []
                    for j in group:
                        merged_ticket_ids.extend(all_themes[j].get("ticket_ids", []) or [])
                    primary = dict(primary)  # 浅拷贝
                    primary["ticket_ids"] = merged_ticket_ids
                    new_themes.append(primary)
                print(f"    跨 chunk 主题合并: {len(all_themes)} → {len(new_themes)}")
                all_themes = new_themes

        # 3. 收集分配 + 兜底未分配
        assigned: dict[str, str] = {}  # sid -> theme_key
        theme_meta: dict[str, dict] = {}  # theme_key -> {name, description, suffix}
        for t_idx, theme in enumerate(all_themes):
            key = f"th{t_idx}"
            raw_suffix = str(theme.get("id_suffix", "")).strip() or "theme"
            unique_suffix = f"{raw_suffix}-{t_idx + 1}"
            theme_meta[key] = {
                "name": str(theme.get("name", "")).strip() or f"主题{t_idx + 1}",
                "description": str(theme.get("description", "")).strip(),
                "suffix": unique_suffix,
            }
            for sid in theme.get("ticket_ids", []) or []:
                sid = str(sid).strip()
                if sid in sid_to_ticket and sid not in assigned:
                    assigned[sid] = key

        missing = [t for t in tickets if t["_sid"] not in assigned]
        if missing:
            def is_zero(emb: np.ndarray) -> bool:
                return float(np.linalg.norm(emb)) < 1e-6

            valid_missing = [t for t in missing if not is_zero(embeddings[sid_to_idx[t["_sid"]]])]
            zero_missing = [t for t in missing if is_zero(embeddings[sid_to_idx[t["_sid"]]])]

            if valid_missing:
                emb_bucket: dict[str, list[np.ndarray]] = {}
                for sid, key in assigned.items():
                    emb = embeddings[sid_to_idx[sid]]
                    if not is_zero(emb):
                        emb_bucket.setdefault(key, []).append(emb)
                centroids = {k: np.mean(np.stack(v), axis=0) for k, v in emb_bucket.items() if v}
                if centroids:
                    keys = list(centroids.keys())
                    cmat = np.stack([centroids[k] for k in keys])
                    cmat = cmat / np.clip(np.linalg.norm(cmat, axis=1, keepdims=True), 1e-9, None)
                    for t in valid_missing:
                        v = embeddings[sid_to_idx[t["_sid"]]]
                        sims = cmat @ v
                        best = keys[int(np.argmax(sims))]
                        assigned[t["_sid"]] = best
                else:
                    zero_missing.extend(valid_missing)
                    valid_missing = []

            if zero_missing or (not assigned):
                theme_meta["fallback"] = {
                    "name": "未分类问题",
                    "description": "embedding 或 LLM 主题归并失败，统一归组",
                    "suffix": "uncategorized",
                }
                for t in zero_missing:
                    assigned[t["_sid"]] = "fallback"
                for t in missing:
                    if t["_sid"] not in assigned:
                        assigned[t["_sid"]] = "fallback"

        # 写入阶段二缓存（key 已绑定到此 category 的工单集合 + core_issue）
        if cluster_cache is not None:
            sid_to_hash = {t["_sid"]: t["_hash"] for t in tickets}
            cluster_cache[cache_key] = {
                "theme_meta": theme_meta,
                "assignments": {sid_to_hash[sid]: tk for sid, tk in assigned.items()},
            }

    # 4. 组装组
    ticket_bucket: dict[str, list[dict]] = {}
    for sid, key in assigned.items():
        ticket_bucket.setdefault(key, []).append(sid_to_ticket[sid])

    groups: list[dict] = []
    for key, ts in ticket_bucket.items():
        meta = theme_meta.get(key, {"name": "未分类", "description": "", "suffix": key})
        groups.append({
            "id": f"{category}-{meta['suffix']}",
            "name": meta["name"],
            "description": meta["description"],
            "count": len(ts),
            "tickets": ts,
        })

    # 5. 收尾：合并过小组、再切过大组（LLM 拆分超大组，失败回退 embedding）
    groups = await post_process_groups(llm, groups, embeddings, sid_to_idx, sid_to_ticket, category)
    return groups


SUBDIVIDE_PROMPT_TEMPLATE = """以下是同一类目下、被 LLM 归为同一主题但数量过多（{count} 条）的工单。
父主题：{parent_name}
父主题描述：{parent_desc}

需要按更细粒度（具体错误类型 / 触发场景 / 报错形式 / 子功能）拆分成 {n_parts} 个子主题。

工单核心问题列表（id: 摘要）：
{items}

要求：
1. 子主题必须比父主题更具体（不要重复父主题名）。
2. 每个子主题至少 {gmin} 条、至多 {gmax} 条。
3. 不要使用「其他」「其它」「综合」类宽泛名称。
4. 子主题名 8-16 字，可用斜杠连接相关概念。
5. 所有给出的 id 必须出现且仅出现一次。

输出严格 JSON：
{{
  "sub_themes": [
    {{
      "name": "子主题名",
      "description": "一句话描述",
      "id_suffix": "english-kebab-case",
      "ticket_ids": ["t3","t8",...]
    }},
    ...
  ]
}}"""


async def llm_subdivide_group(
    llm: LLMClient,
    parent_name: str,
    parent_desc: str,
    tickets: list[dict],
    n_parts: int,
) -> list[dict]:
    """让 LLM 把超大组拆成 n_parts 个子主题；失败返回 []"""
    items = "\n".join(f"{t['_sid']}: {t['_summary']['core_issue']}" for t in tickets)
    prompt = SUBDIVIDE_PROMPT_TEMPLATE.format(
        count=len(tickets),
        parent_name=parent_name,
        parent_desc=parent_desc,
        n_parts=n_parts,
        items=items,
        gmin=GROUP_MIN,
        gmax=GROUP_MAX,
    )
    for attempt in range(2):
        try:
            content = await llm.chat(prompt, temperature=0.2, response_json=True)
            data = parse_json_loose(content)
            sub = data.get("sub_themes", []) if isinstance(data, dict) else data
            return sub or []
        except Exception as e:
            if attempt == 1:
                print(f"      子主题拆分失败（已重试）：{e}")
                return []
            await asyncio.sleep(1)
    return []


# ─── 阶段三：兜底（合并小组 / 切大组） ──────────────────

def group_centroid(group: dict, embeddings: np.ndarray, sid_to_idx: dict) -> np.ndarray:
    vecs = [embeddings[sid_to_idx[t["_sid"]]] for t in group["tickets"]]
    c = np.mean(np.stack(vecs), axis=0)
    n = np.linalg.norm(c)
    return c / n if n > 0 else c


async def post_process_groups(
    llm: LLMClient,
    groups: list[dict],
    embeddings: np.ndarray,
    sid_to_idx: dict,
    sid_to_ticket: dict,
    category: str,
) -> list[dict]:
    # 合并 < GROUP_MIN 的组到最相似的大组
    changed = True
    while changed:
        changed = False
        small = [g for g in groups if g["count"] < GROUP_MIN]
        large = [g for g in groups if g["count"] >= GROUP_MIN]
        if not small or not large:
            break
        large_centroids = np.stack([group_centroid(g, embeddings, sid_to_idx) for g in large])
        for sg in small:
            sc = group_centroid(sg, embeddings, sid_to_idx)
            sims = large_centroids @ sc
            target = large[int(np.argmax(sims))]
            target["tickets"].extend(sg["tickets"])
            target["count"] = len(target["tickets"])
            groups.remove(sg)
            changed = True
            break

    # 如果仍存在 < GROUP_MIN 的组（说明全是小组），合并所有
    if groups and all(g["count"] < GROUP_MIN for g in groups):
        merged_tickets = [t for g in groups for t in g["tickets"]]
        groups = [{
            "id": f"{category}-general",
            "name": "通用问题",
            "description": "工单较分散，未形成稳定主题",
            "count": len(merged_tickets),
            "tickets": merged_tickets,
        }]

    # 切超大组（> GROUP_MAX）：优先让 LLM 按更细粒度拆，失败回退到 embedding 硬切
    final: list[dict] = []
    for g in groups:
        if g["count"] <= GROUP_MAX:
            final.append(g)
            continue

        n_parts = (g["count"] + GROUP_MAX - 1) // GROUP_MAX
        sub_groups = await _llm_split_oversized(llm, g, n_parts, category)
        if not sub_groups:
            sub_groups = _embedding_split_oversized(g, n_parts, embeddings, sid_to_idx)
        # 子组若仍超大，递归再切（但深度限 1，避免死循环）
        for sg in sub_groups:
            if sg["count"] > GROUP_MAX:
                sub_n = (sg["count"] + GROUP_MAX - 1) // GROUP_MAX
                final.extend(_embedding_split_oversized(sg, sub_n, embeddings, sid_to_idx))
            else:
                final.append(sg)
    return final


async def _llm_split_oversized(llm: LLMClient, g: dict, n_parts: int, category: str) -> list[dict]:
    """用 LLM 把超大组拆成子主题"""
    print(f"    🪓 拆分超大组「{g['name']}」({g['count']} 条) → 目标 {n_parts} 个子主题")
    sub_themes = await llm_subdivide_group(
        llm, g["name"], g["description"], g["tickets"], n_parts,
    )
    if not sub_themes:
        return []

    # 按 sid 还原工单
    sid_to_ticket = {t["_sid"]: t for t in g["tickets"]}
    assigned: dict[str, int] = {}
    for i, sub in enumerate(sub_themes):
        for sid in sub.get("ticket_ids", []) or []:
            sid = str(sid).strip()
            if sid in sid_to_ticket and sid not in assigned:
                assigned[sid] = i

    # 漏分配的工单：分到子主题中数量最少的，保持均衡
    missing = [t for t in g["tickets"] if t["_sid"] not in assigned]
    if missing and sub_themes:
        for t in missing:
            counts = [sum(1 for v in assigned.values() if v == i) for i in range(len(sub_themes))]
            assigned[t["_sid"]] = counts.index(min(counts))

    sub_groups: list[dict] = []
    for i, sub in enumerate(sub_themes):
        bucket = [sid_to_ticket[s] for s, idx in assigned.items() if idx == i]
        if not bucket:
            continue
        raw_suffix = str(sub.get("id_suffix", "")).strip() or f"sub-{i + 1}"
        sub_groups.append({
            "id": f"{category}-{raw_suffix}-{i + 1}",
            "name": str(sub.get("name", "")).strip() or f"{g['name']} 子组{i + 1}",
            "description": str(sub.get("description", "")).strip() or g["description"],
            "count": len(bucket),
            "tickets": bucket,
        })
    return sub_groups


def _embedding_split_oversized(g: dict, n_parts: int, embeddings: np.ndarray, sid_to_idx: dict) -> list[dict]:
    """LLM 拆分失败时的兜底：按 embedding 最远种子硬切，命名带 #N"""
    idxs = [sid_to_idx[t["_sid"]] for t in g["tickets"]]
    sub_embs = embeddings[idxs]
    seeds = [0]
    for _ in range(n_parts - 1):
        d = np.min(1 - sub_embs @ sub_embs[seeds].T, axis=1)
        seeds.append(int(np.argmax(d)))
    seed_vecs = sub_embs[seeds]
    labels = np.argmax(sub_embs @ seed_vecs.T, axis=1)
    out: list[dict] = []
    for k in range(n_parts):
        sub_tickets = [g["tickets"][i] for i in range(len(g["tickets"])) if labels[i] == k]
        if not sub_tickets:
            continue
        out.append({
            "id": f"{g['id']}-p{k + 1}",
            "name": f"{g['name']} #{k + 1}",
            "description": g["description"],
            "count": len(sub_tickets),
            "tickets": sub_tickets,
        })
    return out


# ─── 主流程 ─────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("工单语义聚类（LLM 主导）")
    print("=" * 60)

    if not API_KEY or API_KEY == "your-api-key-here":
        raise SystemExit("未配置 OPENAI_API_KEY，无法运行")

    print("\n[1/5] 读取 Excel...")
    df = read_excel_files()

    print("\n[2/5] 构建工单数据...")
    all_tickets: list[dict] = []
    cat1_list: list[str] = []
    for _, row in df.iterrows():
        t = build_ticket(row)
        all_tickets.append(t)
        cat_val = row.iloc[COL["category1"]] if COL["category1"] < len(row) else ""
        cat1_list.append("未分类" if pd.isna(cat_val) else str(cat_val).strip())
    print(f"  共 {len(all_tickets)} 条工单")

    llm = LLMClient()

    try:
        print("\n[3/5] 阶段一：LLM 抽取问题摘要...")
        await summarize_all(llm, all_tickets)

        print("\n[4/5] 阶段二：embedding + LLM 主题归并...")
        # 全量 embedding（基于 core_issue + keywords）
        embed_texts = [
            (t["_summary"]["core_issue"] + " " + " ".join(t["_summary"].get("keywords", []) or [])).strip()
            or extract_raw_text(t) or t["title"] or "无内容"
            for t in all_tickets
        ]
        embeddings = compute_embeddings_cached(embed_texts)

        # 按一级分类分组：小分类（< GROUP_MIN）合并到「其他」，避免丢数据
        category_map: dict[str, list[int]] = {}
        for i, cat in enumerate(cat1_list):
            category_map.setdefault(cat, []).append(i)

        small_cats = {k: v for k, v in category_map.items() if len(v) < GROUP_MIN}
        big_cats = {k: v for k, v in category_map.items() if len(v) >= GROUP_MIN}
        if small_cats:
            merged = [i for v in small_cats.values() for i in v]
            other_key = "其他"
            # 若已存在「其他」分类，则合并到一起
            big_cats.setdefault(other_key, []).extend(merged)
            print(f"  小分类（<{GROUP_MIN}条）共 {len(small_cats)} 个、{len(merged)} 条 → 并入「{other_key}」: "
                  + ", ".join(f"{k}({len(v)})" for k, v in small_cats.items()))
        category_map = big_cats
        print(f"  有效分类: {len(category_map)} 个")

        sorted_cats = sorted(category_map.items(), key=lambda x: -len(x[1]))

        cluster_cache = load_cluster_cache()

        async def run_category(cat_name: str, idxs: list[int]):
            sub_tickets = [all_tickets[i] for i in idxs]
            sub_embs = embeddings[idxs]
            print(f"    启动分类: {cat_name} ({len(sub_tickets)} 条)")
            groups = await cluster_category_llm(llm, cat_name, sub_tickets, sub_embs, cluster_cache)
            for g in groups:
                g["tickets"].sort(key=lambda t: t.get("feedbackTime", ""), reverse=True)
            print(f"    完成分类: {cat_name} → {len(groups)} 组")
            return {
                "id": cat_name,
                "name": cat_name,
                "count": len(sub_tickets),
                "groups": groups,
            }

        # 所有一级分类并发跑（共享 LLM 信号量做实际限流）；单个分类失败不影响其他
        results = await asyncio.gather(
            *(run_category(c, ix) for c, ix in sorted_cats),
            return_exceptions=True,
        )
        save_cluster_cache(cluster_cache)
        categories = []
        for (cat_name, _), r in zip(sorted_cats, results):
            if isinstance(r, Exception):
                print(f"  ❌ 分类「{cat_name}」处理失败：{r}")
            else:
                categories.append(r)
    finally:
        await llm.close()

    print("\n[5/5] 输出 JSON...")
    # 清理临时字段
    for t in all_tickets:
        t.pop("_sid", None)
        t.pop("_summary", None)
        t.pop("_hash", None)

    all_ticket_types = sorted(set(t["ticketType"] for t in all_tickets if t["ticketType"]))
    total_groups = sum(len(c["groups"]) for c in categories)

    result = {
        "summary": {
            "totalTickets": len(all_tickets),
            "totalCategories": len(categories),
            "totalGroups": total_groups,
            "ticketTypes": all_ticket_types,
        },
        "categories": categories,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    group_sizes = [g["count"] for c in categories for g in c["groups"]]
    print(f"\n{'=' * 60}")
    print(f"输出文件: {OUTPUT_PATH}")
    print(f"总工单: {len(all_tickets)}, 分类: {len(categories)}, 聚类组: {total_groups}")
    if group_sizes:
        print(f"组大小: min={min(group_sizes)}, max={max(group_sizes)}, avg={np.mean(group_sizes):.1f}")

    for c in categories:
        sizes = [g["count"] for g in c["groups"]]
        over = sum(1 for s in sizes if s > GROUP_MAX)
        under = sum(1 for s in sizes if s < GROUP_MIN)
        flag = f" ⚠️ 超大={over}, 超小={under}" if (over or under) else ""
        print(f"  {c['id']}: {len(sizes)} 组{flag}")


if __name__ == "__main__":
    asyncio.run(main())
