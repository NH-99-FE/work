# 工单分析平台

基于 LLM 语义聚类的工单分析系统，包含数据处理和前端可视化两部分。

- **data-process**：Python 后端，读取 Excel 工单数据，通过 LLM + Embedding 进行语义聚类，输出 JSON
- **前端**：React + Vite + Tailwind，以分类/主题/工单三级结构展示聚类结果，支持搜索、筛选、会话详情查看

## 前置准备

1. **放置数据文件**：将 Excel 工单文件放入 `data/` 目录，替换前请先删除旧文件
2. **配置 LLM API**：
   ```bash
   cd data-process
   cp .env.example .env
   # 编辑 .env，填入 OPENAI_API_KEY 和 OPENAI_BASE_URL
   ```
3. **Embedding 模型**：默认使用 `BAAI/bge-small-zh-v1.5`（本地运行，首次自动下载），国内下载慢可设置镜像：
   ```bash
   export HF_ENDPOINT=https://hf-mirror.com
   ```

## 快速开始

```bash
# 一键启动（数据处理 + 前端开发服务器）
./start.sh
```

或分别启动：

### 1. 数据处理

```bash
cd data-process

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY 等配置

# 安装依赖
uv sync

# 运行聚类
uv run python cluster.py
```

输出文件：`public/ticket_data.json`

### 2. 前端

```bash
# 安装依赖
pnpm install

# 开发模式
pnpm dev

# 构建
pnpm build
```

前端会读取 根目录`ticket_data.json` 进行展示。

## 环境要求

- Node.js 18+ & pnpm
- Python 3.9+ & [uv](https://docs.astral.sh/uv/)
- LLM API（兼容 OpenAI 接口格式）

## 项目结构

```
├── data/                    # 输入数据（Excel 工单文件，替换前请先删除旧文件）
├── data-process/           # 数据处理
│   ├── cluster.py          # 聚类主脚本
│   ├── .env.example        # 环境变量模板
│   └── .cache/             # 中间缓存
├── public/                 # 静态资源
│   └── ticket_data.json    # 聚类结果（由 cluster.py 生成）
├── src/                    # 前端源码
│   ├── App.tsx             # 主页面：搜索/筛选/工单列表
│   ├── types.ts            # TypeScript 类型定义
│   ├── components/         # UI 组件
│   └── hooks/              # 数据获取
├── start.sh                # 一键启动脚本
└── package.json
```

## 数据处理流程

`cluster.py` 分三阶段处理：

1. **LLM 抽取摘要**：为每条工单生成核心问题摘要和关键词
2. **Embedding + LLM 归并**：本地 Embedding 预聚合 → LLM 主题归并 → 跨块去重
3. **兜底处理**：合并小组、拆分超大组

所有中间结果均有本地缓存（`.cache/`），支持断点续跑。
