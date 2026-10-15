# RVTU-bot —— 日职打卡 + 校园网 Portal 认证（单容器，host 网络）
FROM rom3eur8hklqtwq0fa.xuanyuan.run/library/alpine:3.21

# 关联 GitHub 仓库（ghcr 包页面显示与权限继承）
LABEL org.opencontainers.image.source="https://github.com/dcx022801/RVTU-bot" \
      org.opencontainers.image.description="RVTU-bot：日职打卡与校园网认证自动化（alpine 单容器）"

# apk 源换国内镜像（构建提速）
RUN sed -i 's#://dl-cdn.alpinelinux.org#://mirrors.aliyun.com#g' /etc/apk/repositories \
 && apk add --no-cache python3 py3-paramiko curl iproute2 tzdata \
 && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone

ENV TZ=Asia/Shanghai \
    CONFIG_DIR=/config \
    DEFAULTS_DIR=/defaults \
    STATE_DIR=/state

WORKDIR /app

# 配置三层：/config 用户参数（挂载）、/defaults 程序参数、/state 运行态（挂载）
COPY app/ /app/
COPY defaults/ /defaults/

# 入口：entrypoint.sh 支持 .env 注入（可选），随后启动内置调度器
CMD ["sh", "/app/entrypoint.sh"]
