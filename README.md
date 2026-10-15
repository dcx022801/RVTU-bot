# RVTU-bot

由deepseek-v4.1编写
日照职业技术大学 晚自习、宿舍考勤打卡与校园网认证自动化工具。单容器部署、内置调度，一条命令即可全自动运行：

| 任务 | 触发时间 | 说明 |
|---|---|---|
| 宿舍考勤打卡 | 每天 21:00（随机延迟 0~59s） | 失败自动重试；打卡前 15 分钟邮件提醒 |
| 乐享晚自习打卡 | 每天 19:00（随机延迟 0~59s） | MAC 通道优先，失败自动回退定位通道 |
| 校园网 Portal 认证 | 每 1 分钟 | 掉线后自动重新认证（使用指定的两个 MAC） |

## 特性

- **打卡前提醒**：每次打卡前 15 分钟发送邮件通知，可及时决定是否阻止本次打卡（关闭容器即可）
- **凭据自愈**：token 过期后自动用身份证号重新登录并更新凭据，可长期无人值守
- **失败重试**：打卡失败 20 分钟后自动重试一次
- **结果邮件**：每次执行结果发送到配置的邮箱
- **随机延迟**：到点后随机延迟再执行，避免固定整点请求
- **配置分层**：用户配置 / 程序参数 / 运行态三层分离，互不干扰

> 是否参与打卡完全由容器开关决定：在学校时保持容器运行，假期/长期不在学校时关闭容器即可。

## 快速开始

要求：x86_64 Linux + Docker（含 compose 插件）。

```sh
# 1. 获取代码
git clone git@github.com:dcx022801/RVTU-bot.git && cd RVTU-bot

# 2. 配置（二选一）
#    · 推荐：cp .env.example .env 后按注释填写
#    · 或：编辑 config/ 下的 5 个 JSON 文件

# 3. 拉取已发布镜像并启动（无需本地构建；或 docker compose up -d --build 本地构建）
docker compose pull
docker compose up -d

# 4. 查看运行日志
docker logs -f rvtu-bot
```

> 需要 host 网络模式，请在 Linux 宿主机上部署（Windows Docker Desktop 不支持）。

> 镜像托管在 ghcr（私有包），拉取前登录一次：`echo <GitHub Token> | docker login ghcr.io -u dcx022801 --password-stdin`。

## 配置

| 方式 | 说明 |
|---|---|
| `.env` 注入（推荐） | `cp .env.example .env` 后填写；填了 `CREDS_USER` 即启用，容器启动时自动合成配置 |
| `config/` JSON | 直接编辑 `config/` 下的 5 个 JSON 文件（creds / mail / macs / location / portal） |

两种方式同时存在时，`.env` 优先。修改配置后 `docker compose restart` 生效。

> 程序参数（调度时间、打卡前提醒分钟数等）在 `defaults/` 目录，一般无需修改。

## 常用操作

```sh
# 预演（不会真实打卡、不发邮件）
docker exec rvtu-bot python3 /app/scripts/checkin.py --dry-run
docker exec rvtu-bot python3 /app/scripts/happy_checkin.py --dry-run

# 检查登录态
docker exec rvtu-bot python3 /app/scripts/refresh_creds.py --check

# 手动跑一次校园网认证
docker exec rvtu-bot sh /app/scripts/portal-auth.sh

# 测试打卡前提醒（只打印，不发信）
docker exec rvtu-bot python3 /app/scripts/notify.py --dry-run "测试提醒" "测试内容"
```

## 免责声明

本项目仅供学习交流与个人自动化使用，请遵守学校相关管理规定。使用产生的任何后果由使用者自行承担。

## License

[GPL-3.0](LICENSE)
