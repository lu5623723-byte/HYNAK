# 阿里云部署记录与操作

当前状态：用户已提供服务器 IP `47.106.123.56`，确认不使用域名、当前没有网站运行。SSH 连接方式待提供，尚未连接目标服务器，尚未发布公网服务。本机没有 Docker，容器构建、PostgreSQL 运行及公网 HTTPS 验证须在目标环境完成。

后续连接检查：用户已通过 Workbench 以 root 登录，系统为 Alibaba Cloud Linux 3 x86_64，无 Docker；已添加本次部署的两小时临时公钥。截图确认 sshd 监听全部地址22端口、INPUT默认允许、firewalld未启用。本地到公网SSH仍超时，尚未完成主机指纹的网络端核对，不能认定已获得远程命令权限。

## Workbench 上传部署包

公网 SSH 尚不可达时，可由用户在已登录的 Workbench 上传 `HYNAK_Aliyun_Deploy.tar.gz` 至 `/root`，运行：

```sh
tar -xzf /root/HYNAK_Aliyun_Deploy.tar.gz -C /root
bash /root/HYNAK_Aliyun_Deploy/deploy/install-aliyun.sh
```

脚本仅适用于当前 Alibaba Cloud Linux 3 x86_64 / IP 47.106.123.56，正式源码安装到 `/opt/hynak`，保留已有数据及凭据。首次部署遇到现有 `/opt/hynak`、同名Compose项目或网页端口占用会停止；不会卸载旧容器软件或清空Docker目录。Docker安装参考[阿里云文档](https://help.aliyun.com/zh/ecs/user-guide/install-and-use-docker)，RPM保持仓库签名校验，使用阿里云HTTPS镜像仓库。

脚本会检查配置、拉取镜像、构建应用、启动服务、校验PostgreSQL迁移版本，创建数据库/附件备份并将数据库恢复到独立临时验证库后删除该验证库。附件压缩完整性会检查，完整业务附件恢复验收仍需后续执行。每日备份安排在圣保罗时间03:30后随机15分钟内，保存在服务器 `/var/backups/hynak`；异地存储、轮换和错误告警未配置。备份过程中应用短暂停止。

HTTPS检查通过本机回环连接校验IP证书链；该检查通过后仍需外部网络测试。脚本成功后删除本次临时部署公钥；失败时公钥保持原先两小时到期限制。安装日志在 `/var/log/hynak/`，不打印初始化令牌/数据库密码。遇到下载或签发失败会停止并留下定位信息，不能按“已上线”报告。

首次管理员初始化令牌保存在 `/opt/hynak/deploy/.env`。用户在自己的终端私下查看并填写到HTTPS首页，不把令牌、管理员密码或 `.env`截图发到聊天。管理员建好后执行 `sh /opt/hynak/deploy/finish-setup.sh`，脚本先检查管理员存在，再移除初始化令牌并重建应用容器。

## Docker Hub 下载超时后的离线续装

服务器截图已确认 Docker 26.1.3 / Compose 2.27.0 安装成功，私有配置已生成。第4步连接 `registry-1.docker.io` 超时，尚未启动业务容器。

制作的 `HYNAK_Aliyun_Offline.tar.gz` 包含从官方 `docker.io/library/python:3.12-slim`、`postgres:17`、`caddy:2` 获取的 linux/amd64 镜像，以及从官方 PyPI 下载的31个 CPython 3.12 / manylinux2014 x86_64 兼容 wheel。镜像清单、配置、压缩层及解压后层均校验SHA256，来源和摘要见包内 `OFFICIAL_IMAGE_PROVENANCE.json`；各文件校验值在 `CHECKSUMS.json`。Linux目标环境的依赖闭包及版本约束已检查。

用户在 Workbench 上传离线包到 `/root` 后运行：

```sh
tar -xzf /root/HYNAK_Aliyun_Offline.tar.gz -C /root
bash /root/HYNAK_Aliyun_Offline/resume-offline.sh
```

恢复脚本先校验离线包及已安装应用的关键文件，再通过 `docker load`导入镜像、校验镜像ID和平台，使用Docker 26的legacy builder及 `--network=none`、pip `--no-index` 构建应用。构建上下文仅复制运行代码、模板、迁移、requirements和wheel，不包含服务器 `.env`。构建成功后备份并调整 Compose 为本地应用镜像及 `pull_policy: never`，替换已交付安装脚本以支持跳过下载，然后继续原有第5至7步。不变更原始 `.env`、数据库密码、初始化令牌和持久化数据卷。

离线包结构按照[Docker 26导入实现](https://github.com/moby/moby/blob/v26.1.3/image/tarexport/load.go)准备。本机无Docker引擎，镜像实际导入、离线构建和服务器业务启动仍须目标主机执行验证。本包不会修改Docker全局镜像加速器或引入第三方镜像源。阿里云文档说明传统个人镜像加速器停止同步最新镜像，相关限制见[官方说明](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)。

## 当前目标：公网 IP HTTPS

在确认目标主机、80/443 可用并上传源码后，执行 `python3 configure.py 47.106.123.56`。生成的配置自动选择 `Caddyfile.ip`，使用 Let's Encrypt ACME 的 `shortlived` profile 签发 IP 证书并由 Caddy 管理续期。`DOMAIN`变量为兼容现有部署保留名称，在此模式存储公网 IPv4。

IP 证书目前有效期约160小时，续期必须自动运行，证书数据卷不得清除。需要支持 ACME profile 的当前 Caddy 版本；部署时先运行 `docker compose run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`，再启动。首次验证公网返回的证书须包含目标 IP 且证书链受信任，不能仅凭容器已启动判定成功。参见 [Let's Encrypt 公网 IP 证书公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)、[Caddy ACME 配置](https://caddyserver.com/docs/caddyfile/directives/tls)。

不将云端 HTTPS 和 Secure Cookie 校验降级为明文 HTTP。此配置尚未在目标主机签发证书。

## 连接后检查

先核对主机身份、操作系统、磁盘空间、Docker Compose 版本、已有容器、80/443 监听服务和域名解析。若主机已有反向代理，使用现有代理接入方案，先调整配置，不直接启动占用相同端口的 Caddy。

阿里云安全组及系统防火墙允许公网 TCP 80/443；SSH 只允许维护来源。数据库 5432 和应用 8000 不对外发布。域名 A/AAAA 应指向实际提供服务的地址；没有可用 IPv6 服务时不添加 AAAA。

中国内地服务器绑定域名公开提供服务前需完成 ICP 备案，按实际区域和域名情况检查。参见[阿里云备案说明](https://help.aliyun.com/en/icp-filing/basic-icp-service/user-guide/icp-filing-application-overview)、[安全组规则](https://www.alibabacloud.com/help/en/ecs/user-guide/security-group-rules)。

## 新建环境启动

仅上传干净源码，不上传本地测试数据、`.env`、浏览器会话或演示账号。示例目录 `/opt/hynak`；执行账号需具备 Docker 权限。使用独立目录，先检查是否已经部署同名 `hynak` Compose 项目。

```sh
cd /opt/hynak/deploy
python3 configure.py orders.your-real-domain.com
sh start-production.sh
```

替换为真实域名。`configure.py`生成独立随机数据库密码和初始化令牌，文件权限 0600，不打印密钥，不覆盖已有 `.env`。应用以普通用户运行；数据库、附件、证书使用持久数据卷。构建上下文通过 `.dockerignore` 排除密钥及本地数据。

脚本先验证 Compose 配置，再等待数据库和应用健康。容器健康仅代表内部启动成功，还需从公网验证 `https://真实域名/health`、首页和证书。[Compose 等待行为](https://docs.docker.com/reference/cli/docker/compose/up/)、[Caddy 自动 HTTPS 前提](https://caddyserver.com/docs/automatic-https)。

通过服务器上的私有终端查看初始化令牌，访问 HTTPS 首页建立正式管理员；不在聊天、日志或源码中传递令牌和管理员密码。完成后从 `.env` 删除 `SETUP_TOKEN` 行，再执行：

```sh
docker compose up -d --no-deps --force-recreate app
```

不得设置 `HYNAK_TEST_MODE=1`。原有本地测试账户不会导入正式环境。

## 备份与上线验证

```sh
sh /opt/hynak/deploy/backup-postgres.sh /var/backups/hynak
```

备份脚本短暂停止 app，生成 PostgreSQL 自定义格式备份和附件压缩包，然后恢复 app。由主机调度器每天执行，首次先手动验证成功；确认保留周期和异地存储后再配置轮换/异地复制，避免无限增长或删除尚未验证的备份。`.env`需独立受保护保存。首次在隔离环境完成恢复并核对订单、库存和附件后，才能记录为备份验收通过。完整恢复步骤见 OPERATIONS.md。

上线验证包含：证书、HTTP 转 HTTPS、安全 Cookie、正式管理员登录、经销商只能现付且需付款时间/凭证、只有管理员可建寄售、经销商数据隔离、未登录不能读取附件、重启后数据持久化及备份恢复。测试订单必须明确标注并与真实业务区分。

已有正式数据时，更新前做一致备份并保留旧版本源码/镜像。迁移后回退可能需要数据库恢复，不能只切换代码。禁止运行 `docker compose down -v`，该命令会删除业务数据卷。
