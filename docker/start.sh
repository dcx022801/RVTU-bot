#!/bin/sh
# 启动 RVTU-bot 多用户容器（docker 目录下执行 compose）
# 用法：sh docker/start.sh            拉取/启动并后台运行
#       sh docker/start.sh --build    本地构建后启动
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"

docker compose up -d "$@"
docker compose ps
echo "[start] Web 管理页：http://<服务器IP>:11451/"
echo "[start] 首次启动请查看容器日志获取一次性管理员账号："
echo "        docker logs rvtu-bot | grep -A5 'Web 管理员'"
echo "[start] 查看日志：docker logs -f rvtu-bot"
