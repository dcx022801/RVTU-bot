#!/bin/sh
# RVTU-bot 容器入口
# 检测到 .env 注入（CREDS_USER 非空）时：先用 bootstrap.py 把环境变量合成 JSON 配置，
# 并把 CONFIG_DIR 指向生成目录；否则按默认 /config 挂载读取。
set -e

if [ -n "${CREDS_USER:-}" ]; then
    export CONFIG_DIR="${ENV_CONFIG_DIR:-${STATE_DIR:-/state}/config}"
    python3 /app/bootstrap.py
    echo "[entrypoint] 已启用 .env 配置注入（CONFIG_DIR=$CONFIG_DIR）"
fi

exec python3 /app/scheduler.py
