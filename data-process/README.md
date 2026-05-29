# data-process

工单语义聚类数据处理模块。

## 功能

读取 Excel 工单数据，通过 LLM 摘要 + 百度千帆 Embedding + Agglomerative 聚类，按「分类 → 主题 → 工单」三级结构输出 JSON，供前端展示。

## 使用

```bash
# 安装依赖
uv sync

# 运行
uv run python cluster.py
```

## 环境变量

在 `.env` 中配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | LLM API Key（必填） | - |
| `OPENAI_BASE_URL` | LLM API Base URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 摘要模型 | `GLM-5.1` |
| `NAME_MODEL` | 命名模型（可与摘要模型不同） | `GLM-5.1` |
| `EMBEDDING_API_KEY` | 百度千帆 Embedding API Key（必填） | - |
| `EMBEDDING_URL` | 百度千帆 Embedding API URL | `https://qianfan.baidubce.com/v2/embeddings` |
| `EMBED_MODEL` | Embedding 模型名 | `embedding-v1` |

## 数据文件

将 Excel 工单文件放在项目根目录的 `data/` 子目录下（即 `data-process/` 的上级 `data/`），脚本会自动读取该目录下所有 `.xlsx` 文件。

目录布局：

```
work/
├── data/                       # 输入 xlsx
├── data-process/               # 本模块
│   ├── cluster.py
│   └── .cache/                 # 中间缓存
└── public/
    └── ticket_data.json        # 输出
```

输出：`../public/ticket_data.json`

## 处理流程

1. **阶段一：LLM 抽取摘要** — 从标题+现象+提问者消息中抽取 core_issue（产品/功能 + 场景 + 问题），禁止空洞的"咨询"标签，20 条/批，50 并发
2. **阶段二：百度千帆 Embedding + Agglomerative 聚类** — 对摘要做 embedding（1024 维），按一级分类分组后用 Agglomerative 层次聚类（余弦距离阈值 0.25），再合并质心相似度 > 0.85 的簇
3. **阶段三：LLM 批量命名** — 6 簇/批为每个簇生成精准标题，命名模型可独立配置

## 缓存

中间结果缓存在 `.cache/` 目录下，支持断点续跑：

- `summaries.json`：阶段一摘要缓存
- `embeddings_embedding-v1_v2.npz`：百度千帆 Embedding 向量缓存

如需强制重跑，删除对应缓存文件即可。

## 依赖

- Python >= 3.9
- [uv](https://docs.astral.sh/uv/) 包管理器
- 百度千帆 Embedding API（网络请求，无需本地模型）
