#!/usr/bin/env bash
# 构建后将 ticket_data.json 内联到 index.html，使单个 HTML 文件可直接打开
set -euo pipefail

DIST_DIR="$(cd "$(dirname "$0")" && pwd)/dist"
HTML="$DIST_DIR/index.html"
DATA="$DIST_DIR/ticket_data.json"

if [ ! -f "$HTML" ]; then
  echo "dist/index.html 不存在，请先 pnpm build"
  exit 1
fi

if [ ! -f "$DATA" ]; then
  echo "dist/ticket_data.json 不存在，从 public 复制..."
  cp "$(dirname "$0")/public/ticket_data.json" "$DATA"
fi

# 在 <head> 后插入内联 script，把数据挂到 window.__TICKET_DATA__
python3 -c "
import json, sys

with open('$DATA', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('$HTML', 'r', encoding='utf-8') as f:
    html = f.read()

script = '<script>window.__TICKET_DATA__=' + json.dumps(data, ensure_ascii=False) + ';</script>'
html = html.replace('<head>', '<head>' + script, 1)

with open('$HTML', 'w', encoding='utf-8') as f:
    f.write(html)

print(f'已内联 ticket_data.json 到 index.html')
"

# 内联后不再需要单独的文件
rm -f "$DATA"
rm -f "$DIST_DIR"/favicon.svg "$DIST_DIR"/icons.svg
rm -rf "$DIST_DIR"/assets
echo "已清理 dist 多余文件"
