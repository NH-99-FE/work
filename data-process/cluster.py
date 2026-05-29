"""
工单语义聚类脚本（Embedding + Agglomerative 聚类）
1. 读取 Excel 工单数据
2. 阶段一：LLM 为每条工单抽取「核心问题摘要」
3. 阶段二：百度千帆 Embedding + Agglomerative 层次聚类
4. 阶段三：LLM 批量命名
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
from sklearn.cluster import AgglomerativeClustering

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
NAME_MODEL = os.getenv("NAME_MODEL", "gpt-4o")  # LLM 命名用的模型

EMBED_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBED_URL = os.getenv("EMBEDDING_URL", "https://qianfan.baidubce.com/v2/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "embedding-v1")

EXCEL_DIR = Path(__file__).parent.parent / "data"
OUTPUT_PATH = Path(__file__).parent.parent / "public" / "ticket_data.json"
CACHE_DIR = Path(__file__).parent / ".cache"
SUMMARY_CACHE = CACHE_DIR / "summaries.json"
EMBED_CACHE = CACHE_DIR / f"embeddings_{EMBED_MODEL.replace('/', '_')}_v2.npz"

EXCEL_FILES = sorted(EXCEL_DIR.glob("*.xlsx"))

SHEET_NAME = "工单数据"

COL = {
    "id": 0, "type": 1, "title": 4, "phenomenon": 5,
    "service_staff": 6, "status": 7, "source": 9,
    "ticket_type": 10, "category1": 11,
    "satisfaction": 14, "feedback_time": 15,
    "response_time": 22, "process_duration": 23, "messages": 24,
}

GROUP_MIN = 2
AGGLO_DISTANCE_THRESHOLD = 0.25  # 余弦距离阈值，< 此值归同一簇（0.25 ≈ 余弦相似度 0.75）
BATCH_NAME_SIZE = 6   # LLM 命名每批簇数

# 并发与批量
SUMMARY_BATCH = 20        # 阶段一每批工单数
LLM_CONCURRENCY = 50      # 全局 LLM 并发上限
EMBED_BATCH_API = 16      # 百度 embedding API 每批条数
EMBED_CONCURRENCY = 30    # embedding API 并发上限
HTTP_MAX_CONNECTIONS = 50 # httpx 连接池上限


# ─── 数据读取 ───────────────────────────────────────────

def read_excel_files() -> pd.DataFrame:
    if not EXCEL_FILES:
        print(f"\n❌ data/ 目录中没有找到 xlsx 文件，请将需要处理的 Excel 工单文件放入 {EXCEL_DIR} 目录后重试。\n")
        raise SystemExit(1)
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


def _clean_for_embed(text: str) -> str:
    """比 _clean_raw 更强的清洗，用于 embedding 输入"""
    text = _clean_raw(text)
    text = re.sub(r"[\w.-]+@[\w.-]+", "", text)       # 去邮箱
    text = re.sub(r"\b\d{6,}\b", "", text)             # 去纯数字 ID
    text = re.sub(r"\bERR[_A-Z0-9]+\b", "", text)     # 去 ERR_XXX 报错码
    text = re.sub(r"\b0x[0-9a-fA-F]+\b", "", text)    # 去 0xXXX 十六进制码
    text = re.sub(r"\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2}", "", text)  # 去时间戳
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_raw_text_full(ticket: dict) -> str:
    """完整原文（不截断），仅用于 hash。取标题+现象+提问者消息（不含客服和机器人）"""
    parts = []
    if ticket["title"]:
        parts.append(ticket["title"])
    if ticket["phenomenon"]:
        parts.append(ticket["phenomenon"])
    # 找提问者：第一条非 ROBOT 消息的发送者
    questioner = None
    for m in ticket["messages"]:
        if m["role"] != "robot":
            questioner = m["sender"]
            break
    if questioner:
        for m in ticket["messages"]:
            if m["sender"] == questioner:
                parts.append(m["content"])
    return _clean_raw(" ".join(parts))


def extract_raw_text(ticket: dict) -> str:
    """截断后的文本，喂给 LLM"""
    return extract_raw_text_full(ticket)[:600]


def extract_embed_text(ticket: dict) -> str:
    """用于 embedding 的文本：标题 + 现象，强清洗后截断"""
    parts = []
    if ticket["title"]:
        parts.append(ticket["title"])
    if ticket["phenomenon"]:
        parts.append(ticket["phenomenon"])
    text = " ".join(parts)
    return _clean_for_embed(text)[:300]


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
    tmp = EMBED_CACHE.with_name(EMBED_CACHE.name + ".tmp.npz")
    np.savez(tmp, keys=keys, vecs=vecs)
    tmp.replace(EMBED_CACHE)


# ─── LLM / Embedding HTTP 封装 ──────────────────────────

class LLMClient:
    def __init__(self):
        limits = httpx.Limits(
            max_connections=HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=HTTP_MAX_CONNECTIONS,
        )
        self.client = httpx.AsyncClient(timeout=120, limits=limits, http2=False)
        self.llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)
        self.embed_sem = asyncio.Semaphore(EMBED_CONCURRENCY)

    # ── LLM chat ──

    async def chat(self, prompt: str, temperature: float = 0.2, response_json: bool = False, model: str | None = None) -> str:
        use_model = model or MODEL
        # Kimi-K2.x 系列只接受 temperature=1
        actual_temp = 1.0 if use_model.startswith("Kimi") else temperature
        payload = {
            "model": use_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": actual_temp,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        async with self.llm_sem:
            resp = await self._post_with_retry(
                f"{BASE_URL}/chat/completions",
                payload,
                headers_override={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            return resp.json()["choices"][0]["message"]["content"]

    # ── 百度千帆 Embedding API ──

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用百度千帆 embedding API，返回与输入等长的 embedding 列表"""
        async with self.embed_sem:
            resp = await self._post_with_retry(
                EMBED_URL,
                {"model": EMBED_MODEL, "input": texts},
                headers_override={
                    "Authorization": f"Bearer {EMBED_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            data = resp.json()
            results = sorted(data["data"], key=lambda x: x["index"])
            return [r["embedding"] for r in results]

    # ── 通用带重试 POST ──

    async def _post_with_retry(
        self,
        url: str,
        payload: dict,
        max_attempts: int = 5,
        headers_override: dict | None = None,
    ) -> httpx.Response:
        headers = headers_override or {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = await self.client.post(url, headers=headers, json=payload)
                if resp.status_code < 500 and resp.status_code != 429:
                    resp.raise_for_status()
                    return resp
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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

SUMMARY_PROMPT_TEMPLATE = """你是工单分析师。请为下列工单抽取核心问题摘要，用于后续语义聚类。

要求：
- core_issue：一句话概括工单的核心主题，15-35 字。必须包含产品/功能名 + 具体场景 + 问题或诉求。去掉账号/时间/版本号等细节，保留问题本质。
  好的示例：「Comate网页版切换GPT模型时审批弹窗无法跳过」「ducc更新后重启时插件冲突致会话不显示」「iCoding开发机非root用户无法登录」「OneAPI请求deepseek-v4-pro模型时提示无可用渠道」
  不好的示例：「显示异常」（缺少产品和场景）、「咨询问题」（什么都没说）、「无法启动」（缺少产品和场景）、「用户反馈问题」（太笼统）
- 禁止使用「咨询」「询问」「了解」「求问」等空洞标签。这些词会让不同主题的工单错误地互相靠近，必须替换为具体主题：
  用户问"是否支持X"→写"X的支持情况"（如"Comate访问百度内网地址的支持情况"）；
  问"怎么操作Y"→写"Y的操作方式"（如"Comate IDE进入Mission Mode的操作方式"）；
  问"如何配置Z"→写"Z的配置方式"（如"ducc切换计费账号的配置方式"）；
  如果用户明确在抱怨X不可用，才写"X不可用/无法X"。不要把咨询扭曲成问题。
- 产品名必须具体：不能只写"API""账户""客户端"，必须写明是哪个产品的，如"Comate API""ducc账户""iCoding客户端"。
- 不要复述原文，要做归纳。

输出严格 JSON：{{"results": [{{"id": "<原 id>", "core_issue": "..."}}, ...]}}

工单列表：
{items}
"""


async def summarize_batch(llm: LLMClient, batch: list[dict]) -> dict[str, dict]:
    """对一批工单调用 LLM 抽取摘要，返回 {id: {core_issue}}"""
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
            t["_summary"] = {"core_issue": t["_raw"][:30]}
            miss += 1
    if miss:
        print(f"  ⚠️ {miss} 条未成功抽取摘要，已用原文兜底（未写入缓存）")

    for t in tickets:
        t.pop("_raw", None)


# ─── 阶段二：百度千帆 Embedding ─────────────────────────

async def compute_embeddings_api(llm: LLMClient, texts: list[str]) -> np.ndarray:
    """通过百度千帆 API 计算 embedding，带缓存。返回 L2 归一化后的向量矩阵。"""
    cache = load_embed_cache()
    hashes = [text_hash(t) for t in texts]
    miss_idx = [i for i, h in enumerate(hashes) if h not in cache]
    print(f"  embedding 缓存命中 {len(texts) - len(miss_idx)}/{len(texts)}，待计算 {len(miss_idx)} 条")

    if miss_idx:
        batches = [miss_idx[i:i + EMBED_BATCH_API] for i in range(0, len(miss_idx), EMBED_BATCH_API)]
        print(f"  → {len(batches)} 批 API 调用，并发 {EMBED_CONCURRENCY}")

        # 按批次并发调用 API
        batch_map: dict[int, list[list[float]]] = {}

        async def fetch_batch(batch_idx: int, b: list[int]):
            batch_texts = [texts[j] for j in b]
            result = await llm.embed(batch_texts)
            batch_map[batch_idx] = result

        await asyncio.gather(*(fetch_batch(i, b) for i, b in enumerate(batches)))

        # 写入缓存
        for batch_idx, b in enumerate(batches):
            result = batch_map[batch_idx]
            for k, idx in enumerate(b):
                cache[hashes[idx]] = np.array(result[k], dtype=np.float32)

        save_embed_cache(cache)

    if not cache:
        raise RuntimeError("embedding 全部失败，无法继续")

    arr = np.stack([cache[h] for h in hashes]).astype(np.float32)
    # L2 归一化：之后用欧式距离等价于余弦距离
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-9, None)
    arr = arr / norms
    return arr


# ─── Agglomerative 层次聚类 ────────────────────────────────

def agglomerative_cluster(embeddings: np.ndarray, distance_threshold: float = AGGLO_DISTANCE_THRESHOLD) -> np.ndarray:
    """Agglomerative 层次聚类，按余弦距离阈值切割。返回标签数组。"""
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    return clusterer.fit_predict(embeddings)


def merge_similar_clusters(labels: np.ndarray, embeddings: np.ndarray,
                           threshold: float = 0.85) -> np.ndarray:
    """合并质心余弦相似度超过阈值的簇（Agglomerative 可能拆出语义相同的小簇）"""
    labels = labels.copy()
    unique_labels = sorted(set(labels))
    if len(unique_labels) <= 1:
        return labels

    # 计算每个簇的归一化质心
    centroids = {}
    for l in unique_labels:
        c = embeddings[labels == l].mean(axis=0)
        n = np.linalg.norm(c)
        centroids[l] = c / n if n > 0 else c

    changed = True
    while changed:
        changed = False
        current_labels = sorted(set(labels))
        if len(current_labels) <= 1:
            break

        # 重新计算当前簇的质心
        centroids = {}
        for l in current_labels:
            c = embeddings[labels == l].mean(axis=0)
            n = np.linalg.norm(c)
            centroids[l] = c / n if n > 0 else c

        # 找最相似的一对
        best_sim = -1
        best_pair = None
        for i, la in enumerate(current_labels):
            for lb in current_labels[i + 1:]:
                sim = float(centroids[la] @ centroids[lb])
                if sim > best_sim:
                    best_sim = sim
                    best_pair = (la, lb)

        if best_sim >= threshold and best_pair:
            # 把较小的簇合并到较大的簇
            la, lb = best_pair
            size_a = np.sum(labels == la)
            size_b = np.sum(labels == lb)
            if size_a >= size_b:
                labels[labels == lb] = la
            else:
                labels[labels == la] = lb
            changed = True

    return labels

BATCH_NAME_PROMPT_TEMPLATE = """根据每个簇内的工单摘要，为该簇起一个标题。

规则：
- 标题 8-16 字，必须包含产品名+具体问题，不要斜杠
- 必须从摘要内容归纳，禁止凭空发挥
- 不同簇的标题必须不同

好的标题示例：「Comate切换模型时审批弹窗无法跳过」「ducc重启后会话记录不显示」「iCoding开发机非root无法登录」「OneAPI请求DeepSeek模型时无可用渠道」
不好的标题：「功能异常」「使用问题」「模型相关」「无法使用」—— 这些太笼统，没有信息量。

{clusters_text}

输出严格 JSON：
{{"themes": [{{"index": 1, "name": "标题", "id_suffix": "english-kebab-case"}}, ...]}}"""

SINGLE_NAME_PROMPT_TEMPLATE = """根据以下工单摘要，起一个标题。

规则：
- 标题 8-16 字，必须包含产品名+具体问题，不要斜杠
- 必须从摘要内容归纳，禁止凭空发挥

好的标题示例：「Comate切换模型时审批弹窗无法跳过」「ducc重启后会话记录不显示」「iCoding开发机非root无法登录」
不好的标题：「功能异常」「使用问题」「模型相关」「无法使用」—— 太笼统。

{items}

输出严格 JSON：{{"name": "标题", "id_suffix": "english-kebab-case"}}"""


def _ticket_summary_line(t: dict) -> str:
    """取工单的摘要行，用于命名 prompt"""
    if "_summary" in t and t["_summary"].get("core_issue"):
        return t["_summary"]["core_issue"]
    return t.get("title", "") or "(无内容)"


async def llm_name_batch(
    llm: LLMClient,
    groups_info: list[tuple[int, int, list[dict]]],
) -> dict[int, dict]:
    """批量命名多个簇。返回 {原index: {name, id_suffix}}"""
    if not groups_info:
        return {}

    # 单簇时走单簇模板
    if len(groups_info) == 1:
        idx, _, tickets = groups_info[0]
        result = await llm_name_group(llm, tickets)
        return {idx: result}

    clusters_text_parts = []
    for i, (_, count, tickets) in enumerate(groups_info):
        items = "\n".join(f"  - {_ticket_summary_line(t)}" for t in tickets[:15])
        clusters_text_parts.append(f"簇{i + 1}（{count}条工单）：\n{items}")

    clusters_text = "\n\n".join(clusters_text_parts)
    prompt = BATCH_NAME_PROMPT_TEMPLATE.format(clusters_text=clusters_text)

    try:
        content = await llm.chat(prompt, temperature=0.2, response_json=True, model=NAME_MODEL)
        data = parse_json_loose(content)
        themes = data.get("themes", []) if isinstance(data, dict) else data

        results: dict[int, dict] = {}
        for theme in themes:
            try:
                batch_idx = int(theme.get("index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= batch_idx < len(groups_info):
                orig_idx = groups_info[batch_idx][0]
                results[orig_idx] = {
                    "name": str(theme.get("name", "")).strip(),
                    "id_suffix": str(theme.get("id_suffix", "")).strip(),
                }

        # 兜底：未命名的簇逐个补
        missing = [(idx, count, tickets) for idx, count, tickets in groups_info if idx not in results]
        if missing:
            print(f"      批量命名有 {len(missing)} 个簇缺失，逐个补命名...")
            for idx, _, tickets in missing:
                results[idx] = await llm_name_group(llm, tickets)

        return results
    except Exception as e:
        print(f"      批量命名失败：{e}，回退逐个命名...")
        results: dict[int, dict] = {}
        for idx, _, tickets in groups_info:
            results[idx] = await llm_name_group(llm, tickets)
        return results


async def llm_name_group(llm: LLMClient, tickets: list[dict]) -> dict:
    """让 LLM 为一组工单生成主题名（单簇命名，也作批量兜底）"""
    items = "\n".join(
        f"- {_ticket_summary_line(t)}" for t in tickets[:30]
    )
    prompt = SINGLE_NAME_PROMPT_TEMPLATE.format(items=items)
    try:
        content = await llm.chat(prompt, temperature=0.2, response_json=True, model=NAME_MODEL)
        data = parse_json_loose(content)
        return {
            "name": str(data.get("name", "")).strip(),
            "id_suffix": str(data.get("id_suffix", "")).strip(),
        }
    except Exception as e:
        print(f"      命名失败：{e}")
        return {"name": "", "id_suffix": ""}


# ─── 聚类主逻辑 ─────────────────────────────────────────

async def cluster_category(
    llm: LLMClient,
    category: str,
    tickets: list[dict],
    embeddings: np.ndarray,
) -> list[dict]:
    """对一个一级分类下的工单做 Agglomerative 聚类 + LLM 命名"""
    n = len(tickets)
    if n < GROUP_MIN:
        name_result = await llm_name_group(llm, tickets) if n > 0 else {}
        suffix = name_result.get("id_suffix") or "general"
        return [{
            "id": f"{category}-{suffix}",
            "name": name_result.get("name") or "通用问题",
            "count": n,
            "tickets": tickets,
        }]

    # 1. Agglomerative 层次聚类
    labels = agglomerative_cluster(embeddings)

    # 2. 合并相似簇（Agglomerative 可能拆出语义相同的小簇）
    before_merge = len(set(labels))
    labels = merge_similar_clusters(labels, embeddings)
    after_merge = len(set(labels))
    if before_merge != after_merge:
        print(f"    相似簇合并: {before_merge} → {after_merge}")

    # 3. LLM 批量命名
    unique_labels = sorted(set(labels))
    print(f"    Agglomerative → {len(unique_labels)} 组，开始命名...")

    # 构建每簇信息：(簇在 unique_labels 中的序号, 工单数, 工单列表)
    groups_info: list[tuple[int, int, list[dict]]] = []
    label_to_idx: dict[int, int] = {}
    for i, l in enumerate(unique_labels):
        group_idx_arr = np.where(labels == l)[0]
        group_tickets = [tickets[j] for j in group_idx_arr]
        label_to_idx[l] = i
        groups_info.append((i, len(group_tickets), group_tickets))

    # 分批（每批 BATCH_NAME_SIZE 个簇），各批并发
    batches = [
        groups_info[i:i + BATCH_NAME_SIZE]
        for i in range(0, len(groups_info), BATCH_NAME_SIZE)
    ]
    print(f"    命名分 {len(batches)} 批（每批 ≤{BATCH_NAME_SIZE} 簇），并发执行...")

    batch_results = await asyncio.gather(
        *(llm_name_batch(llm, b) for b in batches)
    )

    # 合并结果
    name_map: dict[int, dict] = {}
    for br in batch_results:
        name_map.update(br)

    # 6. 组装结果
    groups: list[dict] = []
    for l in unique_labels:
        group_idx = np.where(labels == l)[0]
        group_tickets = [tickets[i] for i in group_idx]
        idx = label_to_idx[l]
        name_result = name_map.get(idx, {})
        suffix = name_result.get("id_suffix") or f"group-{l}"
        # 去重 suffix
        used_suffixes = {g["id"].split("-")[-1] for g in groups}
        if suffix in used_suffixes:
            suffix = f"{suffix}-{l}"
        groups.append({
            "id": f"{category}-{suffix}",
            "name": name_result.get("name") or f"主题{l + 1}",
            "count": len(group_tickets),
            "tickets": group_tickets,
        })

    return groups


# ─── 主流程 ─────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("工单语义聚类（Embedding + Agglomerative 聚类）")
    print("=" * 60)

    if not API_KEY or API_KEY == "your-api-key-here":
        raise SystemExit("未配置 OPENAI_API_KEY，无法运行")
    if not EMBED_API_KEY:
        raise SystemExit("未配置 EMBEDDING_API_KEY，无法运行")

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

        print("\n[4/5] 阶段二：百度千帆 Embedding + Agglomerative 聚类...")
        # embedding 输入：LLM 摘要（core_issue），信息精炼且保留区分度
        embed_texts = [
            t["_summary"]["core_issue"] or extract_embed_text(t) or t["title"] or "无内容"
            for t in all_tickets
        ]
        embeddings = await compute_embeddings_api(llm, embed_texts)

        # 按一级分类分组：小分类（< GROUP_MIN）合并到「其他」，避免丢数据
        category_map: dict[str, list[int]] = {}
        for i, cat in enumerate(cat1_list):
            category_map.setdefault(cat, []).append(i)

        small_cats = {k: v for k, v in category_map.items() if len(v) < GROUP_MIN}
        big_cats = {k: v for k, v in category_map.items() if len(v) >= GROUP_MIN}
        if small_cats:
            merged = [i for v in small_cats.values() for i in v]
            other_key = "其他"
            big_cats.setdefault(other_key, []).extend(merged)
            print(f"  小分类（<{GROUP_MIN}条）共 {len(small_cats)} 个、{len(merged)} 条 → 并入「{other_key}」: "
                  + ", ".join(f"{k}({len(v)})" for k, v in small_cats.items()))
        category_map = big_cats
        print(f"  有效分类: {len(category_map)} 个")

        sorted_cats = sorted(category_map.items(), key=lambda x: -len(x[1]))

        async def run_category(cat_name: str, idxs: list[int]):
            sub_tickets = [all_tickets[i] for i in idxs]
            sub_embs = embeddings[idxs]
            print(f"    启动分类: {cat_name} ({len(sub_tickets)} 条)")
            groups = await cluster_category(llm, cat_name, sub_tickets, sub_embs)
            for g in groups:
                g["tickets"].sort(key=lambda t: t.get("feedbackTime", ""), reverse=True)
            print(f"    完成分类: {cat_name} → {len(groups)} 组")
            return {
                "id": cat_name,
                "name": cat_name,
                "count": len(sub_tickets),
                "groups": groups,
            }

        # 所有一级分类并发跑（共享 LLM 信号量做实际限流）
        results = await asyncio.gather(
            *(run_category(c, ix) for c, ix in sorted_cats),
            return_exceptions=True,
        )
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

    # 构建 messages 索引：{ticket_id: messages}
    messages_map = {t["id"]: t.pop("messages") for t in all_tickets}

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

    # 输出 messages 索引文件
    MESSAGES_PATH = OUTPUT_PATH.parent / "ticket_messages.json"
    with open(MESSAGES_PATH, "w", encoding="utf-8") as f:
        json.dump(messages_map, f, ensure_ascii=False, indent=2)

    group_sizes = [g["count"] for c in categories for g in c["groups"]]
    print(f"\n{'=' * 60}")
    print(f"输出文件: {OUTPUT_PATH}")
    print(f"总工单: {len(all_tickets)}, 分类: {len(categories)}, 聚类组: {total_groups}")
    if group_sizes:
        print(f"组大小: min={min(group_sizes)}, max={max(group_sizes)}, avg={np.mean(group_sizes):.1f}")

    for c in categories:
        sizes = [g["count"] for g in c["groups"]]
        size_str = ", ".join(str(s) for s in sorted(sizes, reverse=True))
        print(f"  {c['id']}: {len(sizes)} 组 [{size_str}]")


if __name__ == "__main__":
    asyncio.run(main())
