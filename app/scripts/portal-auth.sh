#!/bin/sh
# portal-auth.sh — 兼容入口；实际逻辑在 portal_auth.py
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/portal_auth.py" "$@"
