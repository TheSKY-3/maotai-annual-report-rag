#!/usr/bin/env bash
# 容器入口：首次启动自动建库，然后同时拉起 API(8000) 与网页(8501)
set -e
cd /app

if [ ! -f data/index/baseline.npz ]; then
  echo "[启动] 首次运行：下载年报并建立检索索引（约 3-5 分钟）..."
  python scripts/download_data.py
  python scripts/extract_text.py
  python scripts/build_index.py
  echo "[启动] 索引就绪。"
fi

python -m uvicorn src.api:app --host 0.0.0.0 --port 8000 &
exec python -m streamlit run src/app.py --server.port 8501 \
  --server.address 0.0.0.0 --server.headless true
