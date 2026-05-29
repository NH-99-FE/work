# data-process

工单语义聚类数据处理模块。

## 功能

读取 Excel 工单数据，通过 LLM + 本地 Embedding 模型进行语义聚类，按「分类 → 主题 → 工单」三级结构输出 JSON，供前端展示。

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
| `OPENAI_BASE_URL` | API Base URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | LLM 模型名 | `gpt-4o` |

Embedding 模型默认使用 `BAAI/bge-small-zh-v1.5`（本地运行），如需更换可通过 `EMBED_MODEL` 环境变量指定。

## 数据文件

将 Excel 工单文件放在项目根目录的 `data/` 子目录下（即 `data-process/` 的上级 `data/`），脚本会自动读取：

- `../data/人工.xlsx`
- `../data/工单数据汇总_时间范围*.xlsx`

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

1. **阶段一**：LLM 批量抽取每条工单的核心问题摘要和关键词
2. **阶段二**：本地 Embedding 预聚合 → LLM 主题归并 → 跨分块同义主题合并
3. **阶段三**：合并过小组、拆分超大组

## 缓存

中间结果缓存在 `.cache/` 目录下，支持断点续跑：

- `summaries.json`：阶段一摘要缓存
- `embeddings.npz`：Embedding 向量缓存
- `clusters.json`：阶段二聚类缓存

如需强制重跑，删除对应缓存文件即可。

## 依赖

- Python >= 3.9
- [uv](https://docs.astral.sh/uv/) 包管理器
- 首次运行会自动下载 Embedding 模型到 `~/.cache/huggingface`
