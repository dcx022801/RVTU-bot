#!/bin/sh
# RVTU-bot 容器入口（源码位于 docker/entrypoint.sh，镜像内复制到 /app/entrypoint.sh）
# 1) root 启动时准备 /state 权限，把 /config 复制到 /state/config（配置模式），
#    然后 su-exec 降权到 rvtu；实际业务进程不以 root 运行。
# 2) .env 模式（CREDS_USER 去空白后非空）由 bootstrap.py 合成配置到 /state/config。
set -e
umask 077

STATE_DIR=${STATE_DIR:-/state}
SOURCE_CONFIG_DIR=${SOURCE_CONFIG_DIR:-/config}
CONFIG_DIR=${CONFIG_DIR:-$STATE_DIR/config}
ENV_CONFIG_DIR=${ENV_CONFIG_DIR:-$STATE_DIR/config}
if [ "$CONFIG_DIR" = "$SOURCE_CONFIG_DIR" ]; then
    CONFIG_DIR="$STATE_DIR/config"
fi

mkdir -p "$STATE_DIR/logs" "$STATE_DIR/config" 2>/dev/null || true

CREDS_TRIMMED=$(printf '%s' "${CREDS_USER:-}" | tr -d '[:space:]')
if [ -n "${CREDS_USER:-}" ] && [ -z "$CREDS_TRIMMED" ]; then
    echo "[entrypoint] CREDS_USER 仅包含空白，拒绝启动" >&2
    exit 1
fi

if [ "$(id -u)" = "0" ]; then
    if [ -n "$CREDS_TRIMMED" ]; then
        export CONFIG_DIR="$ENV_CONFIG_DIR"
        mkdir -p "$CONFIG_DIR"
    else
        export CONFIG_DIR
        mkdir -p "$CONFIG_DIR"
        if [ "$SOURCE_CONFIG_DIR" != "$CONFIG_DIR" ]; then
            rm -f "$CONFIG_DIR"/*.json 2>/dev/null || true
            cp -a "$SOURCE_CONFIG_DIR"/*.json "$CONFIG_DIR"/ 2>/dev/null || true
        fi
    fi
    chown -R rvtu:rvtu "$STATE_DIR" 2>/dev/null || true
    if [ "$CONFIG_DIR" != "$STATE_DIR" ] && [ "$CONFIG_DIR" != "$SOURCE_CONFIG_DIR" ]; then
        chown -R rvtu:rvtu "$CONFIG_DIR" 2>/dev/null || true
    fi
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec rvtu sh /app/entrypoint.sh "$@"
    fi
    echo "[entrypoint] su-exec 不可用，继续以 root 运行（不推荐）" >&2
fi

if [ -n "$CREDS_TRIMMED" ]; then
    export CONFIG_DIR="$ENV_CONFIG_DIR"
    python3 /app/bootstrap.py
    echo "[entrypoint] 已启用 .env 配置注入（CONFIG_DIR=$CONFIG_DIR）"
fi

chmod 700 "$CONFIG_DIR" 2>/dev/null || true
find "$CONFIG_DIR" -maxdepth 1 -type f -name '*.json' -exec chmod 600 {} \; 2>/dev/null || true

if [ ! -f "$CONFIG_DIR/creds.json" ]; then
    echo "[entrypoint] 缺少 $CONFIG_DIR/creds.json，无法启动" >&2
    exit 1
fi

exec python3 /app/scheduler.py
