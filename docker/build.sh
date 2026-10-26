#!/bin/sh
# 构建 RVTU-bot Docker 镜像（构建上下文为仓库根目录）
# 用法：sh docker/build.sh
# 自定义镜像：RVTU_IMAGE=rvtu-bot:test sh docker/build.sh
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname "$DIR")
cd "$ROOT"

IMAGE=${RVTU_IMAGE:-ghcr.io/dcx022801/rvtu-bot:latest}
echo "[build] 镜像：$IMAGE"
docker build --provenance=false --sbom=false -f docker/Dockerfile -t "$IMAGE" .
echo "[build] 完成：$IMAGE"
