# RVTU-bot —— 日职打卡 + 校园网 Portal 认证（单容器，host 网络）
FROM rom3eur8hklqtwq0fa.xuanyuan.run/library/alpine:3.21@sha256:ee16d92f96db08d9d2d07da48555a27f3a69d8f0759021dbc601195ed0d2b5bd

# 关联 GitHub 仓库（ghcr 包页面显示与权限继承）
LABEL org.opencontainers.image.source="https://github.com/dcx022801/RVTU-bot" \
      org.opencontainers.image.description="RVTU-bot：日职打卡与校园网认证自动化（alpine 单容器）"

# apk 源换国内镜像（构建提速）
RUN sed -i 's#://dl-cdn.alpinelinux.org#://mirrors.aliyun.com#g' /etc/apk/repositories \
 && apk add --no-cache python3 py3-paramiko curl iproute2 tzdata su-exec \
 && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone \
 && addgroup -S rvtu \
 && adduser -S -D -H -G rvtu -h /app -s /sbin/nologin rvtu \
 && mkdir -p /state/logs /state/config \
 && chown -R rvtu:rvtu /state

ENV TZ=Asia/Shanghai \
    CONFIG_DIR=/state/config \
    SOURCE_CONFIG_DIR=/config \
    DEFAULTS_DIR=/defaults \
    STATE_DIR=/state \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 配置三层：/config 用户参数（挂载后复制到 /state/config）、/defaults 程序参数、/state 运行态
COPY app/ /app/
COPY defaults/ /defaults/

# 入口以 root 启动，修正挂载目录权限后通过 su-exec 降权到 rvtu；
# 实际业务进程（scheduler/打卡/Portal）以 UID 100（rvtu）运行。
RUN chmod +x /app/entrypoint.sh /app/scripts/*.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python3 -c "import os,sys,time; p='/state/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 180 else 1)"

CMD ["sh", "/app/entrypoint.sh"]
