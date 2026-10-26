# RVTU-bot

由deepseek-v4.1-Flash-1017、gpt6l共同编写

日照职业技术大学 晚自习、宿舍考勤打卡与校园网认证自动化工具。核心逻辑位于仓库根目录的 `app/`，支持多用户，同时提供：

- **Docker 版**：单容器、内置调度、host 网络，Web 管理页（11451）管理多用户，适合 Linux 服务器 7×24 运行（`docker/`）。
- **Windows GUI 版**：tkinter 控制台 + 系统托盘 + 开机自启，通过 GUI 管理多用户，适合个人 Windows（`win/`），Releases 提供免 Python 便携版。

| 任务 | 触发时间 | 说明 |
|---|---|---|
| 宿舍考勤打卡 | 每天 21:00 | 失败自动重试；打卡前 15 分钟预检（按状态选择性提醒） |
| 乐享晚自习打卡 | 每天 19:00 | MAC 通道优先，失败自动回退定位通道；打卡前 15 分钟预检 |
| 校园网 Portal 认证 | 每 1 分钟 | 掉线后自动重新认证（使用各自的 MAC） |

> 多用户共用一套时间表。打卡前 15 分钟预检时随机决定用户顺序（例如 A/B 谁先），到点后按该顺序依次拉起各用户任务，相邻用户间隔 1~10 秒。

## 目录结构

```
RVTU-bot/
├─ app/                  # 核心代码
│  ├─ multi_scheduler.py # 多用户调度：共享时间表、随机顺序、1~10s 间隔
│  ├─ daemon.py          # Docker 守护：多用户调度 + Web 管理页
│  ├─ webui.py/webui.html# Docker Web 管理页（11451）
│  └─ scripts/           # 打卡/晚自习/Portal/配置/日志/多用户工具
├─ defaults/             # 程序参数 + 新用户配置模板（user-template/）
├─ config/               # 旧版单用户配置（仅用于首次迁移）
├─ state/                # ★ 运行态持久化（管理员哈希、用户、凭据、日志）
├─ tests/                # 离线回归验证
├─ docker/               # Dockerfile、compose、入口、构建/启动脚本
├─ win/                  # Windows GUI、用户管理对话框、exe 构建脚本
├─ .dockerignore         # Docker 构建上下文过滤文件（必须位于仓库根）
└─ README.md
```

## 特性

- **多用户**：每个用户独立身份证号/密码、MAC 池、定位、邮件、Portal 配置；登录态、日志、任务状态互相隔离
- **共享时间表**：所有启用用户共用 `defaults/schedule.json` 的 19:00 / 21:00 / Portal 间隔
- **随机顺序 + 间隔拉起**：预检时随机决定用户顺序；到点后按顺序启动，相邻用户间隔 1~10 秒
- **Web 管理**：Docker 版通过 `http://<服务器IP>:11451` 管理用户、启停、查看状态和日志
- **单管理员账号**：首次启动生成随机管理员（用户名 6 位、密码 20 位），只打印到容器日志一次；磁盘仅保存 PBKDF2 哈希
- **持久化**：管理员、用户凭据、登录态、日志全部保存在 `/state`（挂载卷）；重建/升级容器不丢失
- **无 `.env` 注入**：账号配置不再从 `.env` 读取，改由 Web/GUI 写入持久化目录
- **打卡前预检**：仅在“待打卡/预检失败”时发邮件；已打卡/非上课日静默
- **凭据自愈**：token 过期后自动用身份证号重新登录，可长期无人值守
- **失败重试 / 结果邮件 / Portal 白名单 / Docker 运行加固**：与单用户版一致
- **Windows 托盘**：关闭窗口最小化到系统托盘，支持开机自启
- **旧配置迁移**：首次启动把旧 `state/config` 或挂载的 `config/` 迁移为默认用户

## Docker 版

要求：x86_64 Linux + Docker，Compose 插件 >= 2.24，host 网络。

```sh
# 1. 获取代码
git clone git@github.com:dcx022801/RVTU-bot.git && cd RVTU-bot

# 2. 启动（不再需要 .env 配置）
cd docker
docker compose pull
docker compose up -d

# 3. 首次启动查看一次性管理员账号（用户名 6 位、密码 20 位）
docker logs rvtu-bot | grep -A5 'Web 管理员'
```

然后访问 `http://<服务器IP>:11451/`，用一次性管理员账号登录，添加用户并填写配置、启用。

> 旧版 `.env` 注入已移除；旧部署的账号配置若在 `/state/config`（旧 .env 生成）或挂载的 `config/` 中，首次启动会自动迁移为 `default` 用户。旧 `.env` 文件可删除。

### 持久化与重建

```text
./state/                    ← 必须备份/保留
├─ web-admin.json           # 管理员哈希（明文密码不落盘）
├─ users/<slug>/profile.json# 显示名/启用状态
├─ users/<slug>/config/     # 该用户的 5 个 JSON（账号/邮件/MAC/定位/Portal）
├─ users/<slug>/state/      # 登录态、日志、任务状态
├─ multi_state.json         # 当日顺序、任务状态
├─ logs/                    # 全局多用户日志
└─ heartbeat                # Docker 健康检查
```

- `docker compose down` / `up -d --build` / 重建镜像：`./state` 绑定目录保留，管理员和用户凭据不丢失。
- 删除 `state/web-admin.json` 后重启容器：会生成新的管理员并在日志中打印一次。
- 备份 `state/` 即可备份全部用户配置和登录态。

### 构建与启动脚本

```sh
# 在仓库根执行
sh docker/build.sh            # 构建镜像
sh docker/start.sh            # 启动（内部 cd docker/）
sh docker/stop.sh             # 停止容器（保留 state/）
```

> 需要 host 网络模式，请在 Linux 宿主机上部署（Windows Docker Desktop 不支持完整 host 网络）。
> 镜像托管在 ghcr（私有包），拉取前登录一次：`echo <GitHub Token> | docker login ghcr.io -u dcx022801 --password-stdin`。
> 生产建议固定镜像 digest：`RVTU_IMAGE=ghcr.io/...@sha256:<digest> docker compose up -d`。

### Web 管理

- 用户列表：启用/停用、编辑、删除（删除移入 `state/users/.trash`，可恢复）。
- 编辑字段：显示名、身份证号/密码、SMTP、MAC 池、定位、Portal 白名单/MAC 格式。
- 状态与顺序：显示全局心跳、今日预检随机顺序、每个用户的考勤/晚自习状态。
- 日志：按用户查看 `multi.log` / `checkin.log` / `happy_checkin.log` / `portal-auth.log` 等。
- 保存后约 5 秒内生效，无需重启容器。
- Web 页为 HTTP，请通过 HTTPS 反向代理或仅内网访问；管理员密码即账号密码。

## Windows GUI 版

要求：Windows 10/11、Python 3.10+（安装时勾选 Add Python to PATH）。

```bat
:: 1. 安装依赖（自动创建 win\.venv，不污染全局 Python）
win\install.bat

:: 2. 启动（无控制台窗口）
win\start.bat
```

- 首次启动在 `state\users\default\` 创建默认用户（或从旧 `state\config` 迁移）。
- 主界面“用户管理”可新增/编辑/删除用户、启用/停用、打开配置目录；表单覆盖账号、邮件、MAC/定位、Portal。
- 主界面下拉框选择用户，查看该用户的考勤/晚自习/Portal 状态；日志窗口显示全局多用户日志。
- 关闭窗口 = 最小化到系统托盘；勾选“开机自启”写入当前用户注册表。
- 所有用户/凭据/日志保存在 `state\users\`（`.gitignore` 忽略），移动 exe/项目请保留 `state\`。

### Windows 便携版（exe）

Releases 提供 `RVTU-bot-<版本>-win64.zip`：无需安装 Python，解压到可写目录后双击 `RVTU-bot.exe`。便携版在 exe 同级写 `state\`，请勿放在 `Program Files`。

自行构建：

```bat
win\install.bat
win\build-exe.bat v2026.10.26.1
```

产物为 `dist\RVTU-bot-v2026.10.26.1-win64.zip`；构建脚本会自动安装 PyInstaller。未做代码签名，SmartScreen 可能提示。

## 配置

| 位置 | 内容 |
|---|---|
| `state/users/<slug>/config/creds.json` | user（身份证号）/ pass |
| `state/users/<slug>/config/mail.json` | host / port / user / password / sender / to[] |
| `state/users/<slug>/config/macs.json` | macs[] |
| `state/users/<slug>/config/location.json` | latitude / longitude / locationInfo |
| `state/users/<slug>/config/portal.json` | umac / uplinkMac / allowedHosts / allowedSchemes / allowAny / macFormat |
| `state/web-admin.json` | Docker Web 管理员（仅哈希） |

新用户模板在 `defaults/user-template/`；Docker 镜像内为 `/defaults/user-template`。

程序参数在 `defaults/`：

- `schedule.json`：打卡时间、重试、预检分钟数、Portal 间隔；`multi.launch_gap_min_sec/max` 控制多用户启动间隔（默认 1~10 秒）。
- `api.json`：接口地址、UA、探测地址。
- 多用户模式忽略 `random_delay_sec_max`（改由预检随机顺序 + 1~10 秒间隔控制）。
- `notify_before_min` 只能填 `0`（关闭预检）或 `>=6`；1~5 会落在空窗口，启动时自动关闭并告警。

## 手动操作

Docker（按用户执行；`a` 为示例标识）：

```sh
docker exec --user 100:101 rvtu-bot sh -c \
  'CONFIG_DIR=/state/users/a/config STATE_DIR=/state/users/a/state \
   python3 /app/scripts/checkin.py --dry-run'
```

Windows（`win\run.bat <用户标识> <脚本> [参数]`）：

```bat
win\run.bat a app\scripts\checkin.py --dry-run
win\run.bat a app\scripts\happy_checkin.py --precheck --dry-run
win\run.bat a app\scripts\refresh_creds.py --check
win\run.bat a app\scripts\portal_auth.py
```

## 验证

```sh
python3 tests/verify.py
# Windows: py -3 tests\verify.py
```

离线运行：临时目录 + 假数据 + 本地 mock，不访问真实学校接口/SMTP。当前 56/56 通过。

## 从旧版升级

- Docker 文件仍在 `docker/`；命令在 `docker/` 下执行。
- `.env` 注入已移除：账号配置由 Web 管理；旧 `/state/config` 或挂载 `config/` 会自动迁移为 `default` 用户。
- 旧 `.env` 文件可删除；`RVTU_IMAGE` 可用 shell 环境变量指定。
- Web 管理页端口 `11451`；首次启动查看容器日志的一次性管理员账号。
- 旧单用户 `state/config`（Windows GUI）会迁移为 `state/users/default`；新用户由 GUI 管理。
- `state/` 必须保留，否则管理员和用户凭据会丢失（可从备份恢复）。

## 安全提示

- Web 管理页使用 HTTP 明文，请通过 HTTPS 反向代理或仅内网访问；不要直接暴露到公网。
- 管理员密码只在首次启动时打印一次；磁盘只保存 PBKDF2 哈希。忘记可删除 `state/web-admin.json` 重启重置。
- 用户学校密码、邮箱授权码保存在 `state/users/*/config/`（权限 600），请保护 `state/` 备份。
- 未做代码签名，Windows SmartScreen 可能提示。
- 本项目仅供学习交流与个人自动化使用，请遵守学校相关管理规定。

## License

[GPL-3.0](LICENSE)
