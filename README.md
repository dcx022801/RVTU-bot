# RVTU-bot

由deepseek-v4.1-Flash-1017、gpt6l共同编写

日照职业技术大学 晚自习、宿舍考勤打卡与校园网认证自动化工具。核心逻辑位于仓库根目录的 `app/`，同时提供：

- **Docker 版**：单容器、内置调度、host 网络，适合 Linux 服务器 7×24 运行（`docker/`）。
- **Windows GUI 版**：tkinter 控制台 + 系统托盘 + 开机自启，适合在个人 Windows 电脑上运行（`win/`）。

| 任务 | 触发时间 | 说明 |
|---|---|---|
| 宿舍考勤打卡 | 每天 21:00（随机延迟 0~59s） | 失败自动重试；打卡前 15 分钟预检（按状态选择性提醒） |
| 乐享晚自习打卡 | 每天 19:00（随机延迟 0~59s） | MAC 通道优先，失败自动回退定位通道；打卡前 15 分钟预检 |
| 校园网 Portal 认证 | 每 1 分钟 | 掉线后自动重新认证（使用指定的 MAC） |

## 目录结构

```
RVTU-bot/
├─ app/                  # 核心代码：调度器 + 打卡/晚自习/Portal 脚本
├─ defaults/             # 程序参数（调度时间、接口地址、探测地址）
├─ config/               # 用户配置模板（5 个 JSON）
├─ state/                # 运行态（登录态、日志、调度状态、Windows GUI 配置副本，自动生成）
├─ tests/verify.py       # 离线回归验证
├─ docker/               # Dockerfile、compose、入口、.env 模板、构建/启动脚本
├─ win/                  # Windows GUI、依赖清单、安装/启动脚本
├─ .dockerignore         # Docker 构建上下文过滤文件（必须位于仓库根）
└─ README.md
```

> Docker、Windows 两个入口复用同一套 `app/` 核心逻辑，区别只在运行方式与配置注入方式。

## 特性

- **打卡前预检**：每次打卡前 15 分钟查询今日状态，仅在“待打卡”（发提醒，可停止调度阻止）与“预检失败”时发送邮件；已打卡/非上课日静默
- **凭据自愈**：token 过期后自动用身份证号重新登录并更新凭据，可长期无人值守
- **失败重试**：打卡失败 20 分钟后自动重试一次
- **结果邮件**：每次执行结果发送到配置的邮箱
- **随机延迟**：到点后随机延迟再执行，避免固定整点请求
- **配置分层**：用户配置 / 程序参数 / 运行态三层分离，互不干扰
- **配置校验**：缺失、占位符、非法 MAC/坐标/时间在发请求前拒绝，避免误提交
- **调度持久化**：当日任务与待重试状态落盘，重启不会重复打卡，也不会丢失等待中的重试
- **单实例保护**：调度器文件锁 + Windows GUI 互斥量，避免重复执行
- **Portal 白名单**：只向 `portal.allowedHosts` 内的主机提交账号密码，默认 fail-closed
- **运行加固**：Docker 业务进程降权到非 root；只读根文件系统、资源限制、健康检查、日志轮转
- **Windows 托盘**：关闭窗口最小化到系统托盘，支持开机自启

> 是否参与打卡完全由运行开关决定：在学校时保持容器/调度运行；假期或长期不在学校时停止容器/调度即可。

## Docker 版

要求：x86_64 Linux + Docker，Compose 插件 >= 2.24（`env_file.required` 语法）。

```sh
# 1. 获取代码
git clone git@github.com:dcx022801/RVTU-bot.git && cd RVTU-bot

# 2. 配置（二选一，都在仓库根目录操作）
#    · 推荐：cp docker/.env.example docker/.env 后按注释填写
#    · 或：编辑 config/ 下的 5 个 JSON 文件
#    注意：PORTAL_ALLOWED_HOSTS（或 portal.allowedHosts）必须填写合法门户主机，
#          否则 Portal 认证默认拒绝提交凭据（fail-closed）

# 3. 拉取已发布镜像并启动（无需本地构建）
cd docker
docker compose pull
docker compose up -d

# 4. 查看运行日志
docker logs -f rvtu-bot
```

或使用脚本（在仓库根执行）：

```sh
sh docker/start.sh            # 启动（等价于 cd docker && docker compose up -d）
sh docker/build.sh            # 本地构建镜像
sh docker/stop.sh             # 停止容器（保留 state/）
```

> 需要 host 网络模式，请在 Linux 宿主机上部署（Windows Docker Desktop 不支持完整 host 网络）。

> 镜像托管在 ghcr（私有包），拉取前登录一次：`echo <GitHub Token> | docker login ghcr.io -u dcx022801 --password-stdin`。
>
> 生产建议在 `docker/.env` 设置 `RVTU_IMAGE=ghcr.io/...@sha256:<digest>` 固定镜像，避免 `latest` 漂移。

### Docker 配置

| 方式 | 说明 |
|---|---|
| `docker/.env` 注入（推荐） | `cp docker/.env.example docker/.env` 后填写；填了 `CREDS_USER` 即启用，容器启动时自动合成配置 |
| 根目录 `config/` JSON | 直接编辑 `config/` 下的 5 个 JSON 文件（creds / mail / macs / location / portal） |

两种方式同时存在时，`.env` 优先。修改配置后 `docker compose restart`（在 `docker/` 目录）生效。

Portal 关键配置：

- `PORTAL_ALLOWED_HOSTS` / `portal.allowedHosts`：允许提交账号密码的门户主机白名单，必填。
- `PORTAL_ALLOW_ANY=1` / `portal.allowAny=true`：仅临时排障时跳过白名单，不安全。
- `portal.macFormat`：`auto` / `colon` / `hyphen`，控制提交 MAC 的分隔符。

## Windows GUI 版

要求：Windows 10/11、Python 3.10+（安装时勾选 Add Python to PATH）。

```bat
:: 1. 安装依赖（自动创建 win\.venv，不污染全局 Python）
win\install.bat

:: 2. 启动（无控制台窗口；首次启动会把 config\ 下的 5 个模板复制到 state\config\）
win\start.bat

:: 3. 在 GUI 中点“打开配置目录”，编辑 state\config\ 下的 JSON
::    配置完整后点“重启调度”；该目录已被 .gitignore 忽略，不要直接把真实凭据写进 config\ 模板
::    也可设置 RVTU_CONFIG_DIR 指向其他配置目录（GUI 与调度器都会使用）
```

- 配置完整时，GUI 启动后会自动开始调度；配置有误会弹窗提示，取消则保持停止。
- Windows GUI 配置目录默认是 `state\config\`（模板从 `config/` 复制，凭据不进入 git）；Docker 版仍用 `config/` 或 `docker/.env`。
- 关闭窗口 = 最小化到系统托盘；双击托盘图标恢复。
- 托盘菜单和主界面均可启动/停止/重启调度、打开配置/日志目录。
- 勾选“开机自启”写入当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，无需管理员权限。
- 退出请在托盘菜单或主界面点“退出程序”；退出会停止调度。
- 移动项目目录后需删除 `win\.venv` 并重新运行 `win\install.bat`，并重新勾选开机自启。
- Windows 原生运行 Portal 认证时，建议在 `portal.json` 填写 `umac`；认证仅适用于 Portal 网页认证，不适用于 802.1X 客户端认证。
- 运行日志：`state\logs\`（`scheduler.log`、`checkin.log`、`happy_checkin.log`、`portal-auth.log`、`gui.log`）。

### Windows 便携版（exe）

Releases 提供 `RVTU-bot-<版本>-win64.zip`：无需安装 Python，解压到可写目录后双击 `RVTU-bot.exe` 即可。程序会在同级目录生成 `state\`，请勿放到 `Program Files` 等无写权限目录。

如自行构建：

```bat
win\install.bat
win\build-exe.bat v2026.10.26
```

产物为 `dist\RVTU-bot-v2026.10.26-win64.zip`；构建脚本会自动安装 PyInstaller。未做代码签名，SmartScreen 可能提示。

手动预演（不打卡、不发邮件；`run.bat` 自动使用 `win\.venv` 与 `state\config\`）：

```bat
win\run.bat app\scripts\checkin.py --dry-run
win\run.bat app\scripts\happy_checkin.py --dry-run
win\run.bat app\scripts\refresh_creds.py --check
```

## 配置

Docker 使用 `config/`（或 `docker/.env`）；Windows GUI 默认使用 `state\config\`。

| 文件 | 字段 |
|---|---|
| `creds.json` | user（身份证号）/ pass |
| `mail.json` | host / port / user / password / sender / to[] |
| `macs.json` | macs[] |
| `location.json` | latitude / longitude / locationInfo |
| `portal.json` | umac / uplinkMac / allowedHosts / allowedSchemes / allowAny / macFormat |

程序参数（调度时间、打卡前提醒分钟数等）在 `defaults/`，一般无需修改。

`notify_before_min` 只能填 `0`（关闭预检）或 `>=6`；1~5 会落在空窗口，启动时自动关闭并告警。

## 常用操作（Docker）

> 业务进程以 UID 100 运行且容器 `cap_drop: ALL`，手动业务命令请带 `--user 100:101`；否则 root 无法读取 600 配置。

```sh
# 预演（不会真实打卡、不发邮件）
docker exec --user 100:101 rvtu-bot python3 /app/scripts/checkin.py --dry-run
docker exec --user 100:101 rvtu-bot python3 /app/scripts/happy_checkin.py --dry-run

# 检查登录态
docker exec --user 100:101 rvtu-bot python3 /app/scripts/refresh_creds.py --check

# 手动跑一次校园网认证
docker exec --user 100:101 rvtu-bot python3 /app/scripts/portal_auth.py

# 测试打卡前预检（只查询状态并打印，不发信）
docker exec --user 100:101 rvtu-bot python3 /app/scripts/happy_checkin.py --precheck --dry-run
docker exec --user 100:101 rvtu-bot python3 /app/scripts/checkin.py --precheck --dry-run

# 测试邮件通道（只打印，不发信）
docker exec --user 100:101 rvtu-bot python3 /app/scripts/notify.py --dry-run "测试提醒" "测试内容"
```

## 验证

```sh
python3 tests/verify.py
# Windows: py -3 tests\verify.py
```

离线运行：使用临时目录、假数据和本地 mock，不访问真实学校接口或 SMTP。

## 从旧版目录结构升级

旧版把 Dockerfile、compose、入口脚本和 `.env.example` 放在仓库根。新版调整如下（核心功能不变）：

- Dockerfile / compose / entrypoint / bootstrap / portal-auth.sh / `.env.example` 移入 `docker/`。
- 若旧部署在仓库根有 `.env`，移动到 `docker/.env`；Docker 命令改为在 `docker/` 目录执行。
- 根目录 `config/`、`defaults/`、`state/` 位置不变；compose 的挂载路径已改为 `../config`、`../defaults`、`../state`。
- `.dockerignore` 因 Docker 构建上下文要求保留在仓库根，其余 Docker 文件均在 `docker/`。
- compose 固定项目名为 `rvtu-bot`；若切换时提示容器名冲突，先停止旧容器再启动。

## 免责声明

本项目仅供学习交流与个人自动化使用，请遵守学校相关管理规定。使用产生的任何后果由使用者自行承担。

## License

[GPL-3.0](LICENSE)
