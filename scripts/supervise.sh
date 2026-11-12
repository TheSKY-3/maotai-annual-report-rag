#!/usr/bin/env bash
# 零 sudo 守护（WSL 用户级 systemd 环境性不可用时的等价实现）：
# 子进程退出即重启（等价 Restart=always）；登录自启由 Windows 启动项拉起本脚本。
# 真服务器请改用 systemd 单元（scripts/systemd/*.service.in）。
cd "$(dirname "$0")/.." || exit 1
VENV="${VENV:-$HOME/venvs/maotai}"
PY="$VENV/bin/python"
mkdir -p logs
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1}"
"$PY" scripts/prepare_data.py >> logs/prepare_linux.log 2>&1
(
  while true; do
    "$PY" -m uvicorn src.api:app --host 0.0.0.0 --port 8000 >> logs/api_linux.log 2>&1
    echo "[supervise] api exited, restart in 2s" >> logs/supervise.log
    sleep 2
  done
) &
(
  while true; do
    "$PY" -m streamlit run src/app.py --server.port 8501 \
      --server.address 0.0.0.0 --server.headless true >> logs/web_linux.log 2>&1
    echo "[supervise] web exited, restart in 2s" >> logs/supervise.log
    sleep 2
  done
) &
wait