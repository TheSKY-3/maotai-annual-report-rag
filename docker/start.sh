#!/usr/bin/env bash
# 容器入口：首次启动自动建库，然后同时拉起 API(8000) 与网页(8501)
set -e
cd /app

if [ ! -f data/index/contextual.npz ]; then
  echo "[启动] 索引随仓库分发；缺失说明镜像构建上下文不完整。" >&2
  exit 1
fi
echo "[启动] 检查数据自包含（模型/PDF/原文页/表格页，缺什么补什么）..."
python scripts/prepare_data.py

python -m uvicorn src.api:app --host 0.0.0.0 --port 8000 &
exec python -m streamlit run src/app.py --server.port 8501 \
  --server.address 0.0.0.0 --server.headless true
