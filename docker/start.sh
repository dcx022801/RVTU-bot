#!/bin/sh
# 启动 RVTU-bot（docker 目录下执行 compose，构建上下文/挂载路径均已改为仓库根）
# 用法：sh docker/start.sh            拉取/启动并后台运行
#       sh docker/start.sh --build    本地构建后启动
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"

if [ ! -f .env ] && [ -f ../.env ]; then
    echo "[start] 提示：检测到仓库根目录 .env；新布局请把它移动到 docker/.env。" >&2
    echo "        例如（在仓库根执行）：mv .env docker/.env" >&2
fi

docker compose up -d "$@"
docker compose ps
echo "[start] 查看日志：docker logs -f rvtu-bot"
