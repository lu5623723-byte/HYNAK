# 运行、备份、恢复与部署

## 本地

双击启动，默认只监听127.0.0.1。首次创建管理员，日常无需终端命令。若8765端口已占用，先关闭旧窗口；若使用其他端口，在程序旁`.env`内同时设置PORT与PUBLIC_BASE_URL。不要将数据库、附件、`.env`或测试凭据提交公共Git。

程序不会把启动脚本放入登录项，不创建系统服务。Mac/Windows需要Python 3.11+；首次建venv并联网安装固定依赖。Windows时区库随requirements提供。Mac脚本支持官方Python、Homebrew和当前Codex内置Python。Windows必须在实机双击验证，不以检查脚本文本替代。

## SQLite备份与隔离恢复

启动时及每5分钟检查一次，当天尚无备份则创建一个完整ZIP。管理员可在数据页面“立即备份”，程序将SQLite在线备份快照和私有附件、SHA256 manifest写入数据目录的`backups/`，临时文件完成后原子替换。备份不在网页公开提供。请定期把备份复制到受保护的独立设备；本地磁盘备份不能防止整盘丢失。

手动命令（使用本项目venv的Python）：

```sh
python manage.py backup
python manage.py restore /private/path/backup.zip /private/path/isolated-restored
```

恢复只允许空目录，验证每个文件校验和、防路径穿越、执行SQLite integrity_check。隔离恢复后，创建专用`.env`指向该目录，再启动并核对订单、实收、库存与附件；不要在正式服务运行时覆盖数据库。自动测试覆盖数据库、附件恢复及重新连接后的持久化。

忘记管理员密码可由本机受信任操作人执行`python manage.py reset-password username`，终端隐藏输入新密码，撤销该账号已有登录会话，并写审计。该入口只供拥有本机数据访问权的维护人员。

## HTTPS / PostgreSQL云部署（待目标环境实测）

交付包含`deploy/compose.yaml`、Dockerfile、Caddy HTTPS配置和数据库备份脚本。需要一台可运行Docker的Linux主机、域名A/AAAA解析、80/443开放，以及足够的持久盘空间。此交付没有创建云主机、购买域名或发布公网服务。

阿里云操作与当前部署状态见 [ALIYUN_DEPLOYMENT.md](ALIYUN_DEPLOYMENT.md)。`deploy/configure.py`可在服务器上生成私有随机配置；`deploy/start-production.sh`检查配置并等待内部服务启动。已有80/443服务时，先调整反向代理接入配置。

1. 将源码放入私有服务器目录；不上传本地测试库。复制`deploy/.env.example`为`deploy/.env`，填真实域名及独立随机DB_PASSWORD和SETUP_TOKEN。DB密码用URL安全字符避免连接串歧义；不要保留样例值。
2. 在deploy目录运行`docker compose up -d --build`。应用不映射公网端口，数据库只在内部网络；Caddy对外提供HTTPS并自动申请证书。
3. 浏览器访问真实HTTPS域名，在初始化页面输入SETUP_TOKEN创建管理员。完成后从环境中移除SETUP_TOKEN并重建应用容器。所有云端确认链接由PUBLIC_BASE_URL生成，强制HTTPS及Secure Cookie。
4. 用真实域名逐项执行U01–U17，尤其异地手机确认、重复点击、权限、附件、PostgreSQL锁和唯一约束；检查金额差额R$0.00。
5. 上线前配置每日异地备份及错误告警。`backup-postgres.sh`会暂时停止app，使用pg_dump并打包私有附件，完成后恢复app，从而得到一致备份。主机调度器每天调用该脚本，并把备份加密复制到异地。首次人工恢复验证成功前不能视为上线验收通过。

PostgreSQL恢复：在隔离环境新建空库，停止app，将`database.dump`通过`pg_restore -U hynak --dbname=hynak --clean --if-exists`恢复，再恢复attachments至私有数据卷；执行完整核对再启用。不要在正式运行时演练覆盖。

SQLite自动备份入口遇到PostgreSQL会明确返回`external_postgresql_backup_required`，不会假装已备份。compose中的业务写由事务级advisory lock串行化，适合本版小规模业务；扩大并发前改为更细粒度锁并验证。生产PostgreSQL尚未在本机实跑。

应用不记录完整URL访问日志，避免确认token进入日志；错误写stderr供容器日志收集。不要在代理上启用包含token路径的访问日志。附件不通过static目录公开；只有经鉴权且对象归属匹配的下载接口可读。

本地到云端数据迁移本版不自动执行。切换时冻结本地写入，保留备份，按核实期初快照导入云端，确认余额后再开放操作。没有真实银行依据，不将历史收款导入为本期银行实收。
