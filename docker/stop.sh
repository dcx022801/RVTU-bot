#!/bin/sh
# 停止 RVTU-bot 容器（不删除运行态目录）
# 用法：sh docker/stop.sh
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"
docker compose stop "$@"
