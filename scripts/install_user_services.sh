#!/usr/bin/env bash
# 安装 systemd 用户级服务（零 sudo）：崩溃自动重启；开机自启靠 linger。
# 用法: bash scripts/install_user_services.sh   （VENV 可覆盖）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$HOME/venvs/maotai}"
DIR="$HOME/.config/systemd/user"
mkdir -p "$DIR"
# 非登录会话没有用户总线：先 linger 拉起用户管理器，再显式指 runtime dir
loginctl enable-linger "$USER" 2>/dev/null || true
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
for s in maotai-api maotai-web; do
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__VENV__|$VENV|g" \
    "$ROOT/scripts/systemd/$s.service.in" > "$DIR/$s.service"
done
systemctl --user daemon-reload
systemctl --user enable --now maotai-api maotai-web
echo "--- status ---"
systemctl --user --no-pager --full status maotai-api maotai-web | head -14
echo "--- linger（开机自启前提）---"
loginctl show-user "$USER" -p Linger