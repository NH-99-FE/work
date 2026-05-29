#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DP_DIR="$ROOT_DIR/data-process"

echo "=== 工单分析平台启动 ==="

# ---------- 数据处理 ----------
echo ""
echo "[1/2] 运行数据聚类..."
cd "$DP_DIR"

if [ ! -f .env ]; then
  echo "  ⚠ 未找到 .env，请先配置："
  echo "    cp .env.example .env && vim .env"
  exit 1
fi

if ! command -v uv &>/dev/null; then
  echo "  ⚠ 未安装 uv，请先安装：https://docs.astral.sh/uv/"
  exit 1
fi

uv sync --quiet
uv run python cluster.py

# ---------- 前端 ----------
echo ""
echo "[2/2] 启动前端开发服务器..."
cd "$ROOT_DIR"

if [ ! -d node_modules ]; then
  echo "  安装前端依赖..."
  pnpm install
fi

pnpm dev
