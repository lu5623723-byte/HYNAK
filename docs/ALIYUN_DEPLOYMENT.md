# 阿里云部署

本仓库包含应用源码、测试、数据库迁移及Docker部署配置。支持本地SQLite与云端PostgreSQL。公开源码不包含正式环境凭据、数据库、客户付款附件或本地离线镜像包。

## 已有 Alibaba Cloud Linux 3 x86_64 服务器

将代码放到服务器的独立目录，以root运行以下命令，将PUBLIC_IPV4替换为服务器真实公网IPv4：

```sh
bash deploy/install-aliyun.sh PUBLIC_IPV4
```

脚本安装Docker和Compose，生成权限0600的私有配置，使用PostgreSQL及Caddy启动服务。遇到已有安装目录、同名项目或80/443占用会停止。脚本为公网IPv4 HTTPS配置，使用支持ACME profile的Caddy及Let's Encrypt短期IP证书。网络与证书签发须在目标主机验证。

域名部署可在deploy目录运行 `python3 configure.py your-real-domain.com`，然后 `sh start-production.sh`。域名解析和80/443访问规则须先配置；中国内地服务器按实际情况完成备案。

首次访问HTTPS首页，使用服务器deploy/.env中的SETUP_TOKEN建立正式管理员，随后执行 `sh deploy/finish-setup.sh` 移除初始化令牌。正式环境保持HYNAK_TEST_MODE关闭。

## 备份与验证

安装脚本会创建一次一致备份，并把数据库恢复到独立临时验证库，随后删除该验证库；附件压缩完整性另行检查。每日备份安排在圣保罗时间03:30之后15分钟内，保存在服务器本地。请另行配置受保护的异地备份、轮换与告警，并执行完整业务恢复验收。

代码测试基线为113项通过，环境为Python 3.12与隔离SQLite。Windows实机、生产PostgreSQL、公网HTTPS及权限业务验收需要在目标环境继续验证。

## 镜像下载

直接构建需要能够访问Docker Hub和PyPI。遇到Docker Hub下载超时，应通过受信任的镜像仓库或经过校验的官方离线镜像续装。服务器下载失败不代表应用已启动。

操作细节见OPERATIONS.md。不要将正式.env、私钥、数据库或付款附件提交到Git。
