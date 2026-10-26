#!/bin/sh
# portal-auth.sh — 兼容入口；实际逻辑在 portal_auth.py
# 镜像内位于 /app/scripts/（与 portal_auth.py 同目录）；
# 源码目录 docker/ 下直接运行时回退到 ../app/scripts/portal_auth.py。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$SCRIPT_DIR/portal_auth.py" ]; then
    exec python3 "$SCRIPT_DIR/portal_auth.py" "$@"
fi
exec python3 "$SCRIPT_DIR/../app/scripts/portal_auth.py" "$@"
