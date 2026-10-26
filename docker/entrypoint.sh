#!/bin/sh
# RVTU-bot 容器入口（多用户，Web 管理页 11451）
# 业务进程以 rvtu 运行；用户、会话、管理员哈希、日志全部在 /state（挂载持久化）。
set -e
umask 077

STATE_DIR=${STATE_DIR:-/state}
mkdir -p "$STATE_DIR/logs" "$STATE_DIR/users" "$STATE_DIR/config" 2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
    chown -R rvtu:rvtu "$STATE_DIR" 2>/dev/null || true
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec rvtu sh /app/entrypoint.sh "$@"
    fi
    echo "[entrypoint] su-exec 不可用，继续以 root 运行（不推荐）" >&2
fi

# 不再使用 .env 注入：旧版 /state/config 或挂载的 /config 只用于首次迁移为 default 用户。
exec python3 /app/daemon.py
