# RVTU-bot —— 日职打卡 + 校园网 Portal 认证（多用户，单容器，host 网络）
# 构建上下文为仓库根目录：
#   docker build -f docker/Dockerfile -t rvtu-bot:latest .
FROM rom3eur8hklqtwq0fa.xuanyuan.run/library/alpine:3.21@sha256:ee16d92f96db08d9d2d07da48555a27f3a69d8f0759021dbc601195ed0d2b5bd

# 关联 GitHub 仓库（ghcr 包页面显示与权限继承）
LABEL org.opencontainers.image.source="https://github.com/dcx022801/RVTU-bot" \
      org.opencontainers.image.description="RVTU-bot：日职打卡与校园网认证自动化（多用户，alpine 单容器）"

# apk 源换国内镜像（构建提速）
RUN sed -i 's#://dl-cdn.alpinelinux.org#://mirrors.aliyun.com#g' /etc/apk/repositories \
 && apk add --no-cache python3 py3-paramiko curl iproute2 tzdata su-exec \
 && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone \
 && addgroup -S rvtu \
 && adduser -S -D -H -G rvtu -h /app -s /sbin/nologin rvtu \
 && mkdir -p /state/logs /state/users /state/config \
 && chown -R rvtu:rvtu /state

ENV TZ=Asia/Shanghai \
    STATE_DIR=/state \
    RVTU_STATE_DIR=/state \
    RVTU_USERS_DIR=/state/users \
    RVTU_TEMPLATE_DIR=/defaults/user-template \
    RVTU_WEB_PORT=11451 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 核心代码 + 共享默认值（含新用户模板）
COPY app/ /app/
COPY defaults/ /defaults/

# Docker 专属入口与兼容 sh 入口
COPY docker/entrypoint.sh /app/entrypoint.sh
COPY docker/portal-auth.sh /app/scripts/portal-auth.sh

RUN chmod +x /app/entrypoint.sh /app/scripts/*.sh

# Web 管理页（宿主机用 host 网络直连 11451）
EXPOSE 11451

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python3 -c "import os,sys,time; p='/state/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 180 else 1)"

CMD ["sh", "/app/entrypoint.sh"]
