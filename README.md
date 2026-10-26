# RVTU-bot

日照职业技术大学晚自习、宿舍考勤打卡与校园网 Portal 认证自动化工具，支持多用户，提供 Docker 与 Windows GUI 两种运行方式。

## 任务

| 任务 | 时间 |
|---|---|
| 宿舍考勤打卡 | 每天 21:00 |
| 乐享晚自习打卡 | 每天 19:00 |
| 校园网 Portal 认证 | 每 1 分钟 |

多用户共用一套时间表。打卡前 15 分钟预检并随机决定用户顺序，到点按顺序启动，用户间隔 1~10 秒；Portal 掉线后自动重新认证。

## Docker 部署

要求：x86_64 Linux、Docker、Compose ≥ 2.24（host 网络）。

```sh
git clone git@github.com:dcx022801/RVTU-bot.git
cd RVTU-bot/docker
docker compose pull
docker compose up -d
docker logs rvtu-bot | grep -A5 'Web 管理员'
```

首次启动会在日志中打印一次性管理员账号，然后访问 `http://<服务器IP>:11451` 登录，添加用户并启用。

> 镜像托管在私有 ghcr 仓库，拉取前需登录一次：
> `echo <GitHub Token> | docker login ghcr.io -u dcx022801 --password-stdin`

## Windows GUI

要求：Windows 10/11、Python 3.10+。

```bat
win\install.bat
win\start.bat
```

在“用户管理”中添加/编辑用户并启用。关闭窗口最小化到托盘，可勾选开机自启。免 Python 的便携版见 Releases。

## 数据与备份

管理员账号、用户凭据、登录态和日志全部保存在 `state/`：

- 升级、重建容器或移动程序时务必保留 `state/`，备份它即可备份所有数据。
- 忘记管理员密码：删除 `state/web-admin.json` 后重启，从日志获取新账号。
- 每个用户的配置在 `state/users/<用户标识>/config/`，推荐通过 Web/GUI 修改。

旧版的 `state/config` 或挂载的 `config/` 会在首次启动时自动迁移为 `default` 用户。

## 手动操作

```sh
# Docker（a 为用户标识）
docker exec --user 100:101 rvtu-bot sh -c \
  'CONFIG_DIR=/state/users/a/config STATE_DIR=/state/users/a/state \
   python3 /app/scripts/checkin.py --dry-run'
```

```bat
:: Windows
win\run.bat a app\scripts\checkin.py --dry-run
```

## 验证

```sh
python3 tests/verify.py
:: Windows: py -3 tests\verify.py
```

## 安全提示

- Web 管理页使用 HTTP，请仅在内网访问或通过 HTTPS 反向代理，不要直接暴露公网。
- 用户学校密码、邮箱授权码保存在 `state/`，请妥善保管备份。
- 本项目仅供学习交流与个人自动化使用，请遵守学校相关管理规定。

## License

[GPL-3.0](LICENSE)
