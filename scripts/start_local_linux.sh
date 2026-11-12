#!/usr/bin/env bash
# 本地 Linux（WSL2 Ubuntu）一键起服务：与 docker/start.sh 同构的原生 venv 路线。
# 用法: bash scripts/start_local_linux.sh
#   VENV 可覆盖（默认 ~/venvs/maotai）；Key 经 .env 注入，不落命令行。
set -e
cd "$(dirname "$0")/.."
VENV="${VENV:-$HOME/venvs/maotai}"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  echo "缺 venv。先执行："
  echo "  python3 -m venv $VENV"
  echo "  $VENV/bin/pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu"
  echo "  $VENV/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -f data/index/contextual.npz ]; then
  echo "缺索引 data/index/contextual.npz（随仓库分发）；请先恢复或重建。"
  exit 1
fi
"$PY" scripts/prepare_data.py
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1}"
mkdir -p logs
# setsid 脱离 WSL 会话：否则 wsl.exe 退出时会话清理会杀掉后台服务
setsid nohup "$PY" -m uvicorn src.api:app --host 0.0.0.0 --port 8000 >>logs/api_linux.log 2>&1 &
setsid nohup "$PY" -m streamlit run src/app.py --server.port 8501 \
  --server.address 0.0.0.0 --server.headless true >>logs/web_linux.log 2>&1 &
echo "API http://localhost:8000/docs · 网页 http://localhost:8501 · 日志 logs/*_linux.log"