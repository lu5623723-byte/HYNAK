# 交付索引

- `README.md`：首次使用和业务流程。
- `start-mac.command` / `start-windows.bat`：本地双击启动。
- `app/`：真实后端、数据库模型、权限、业务交易、网页界面。
- `migrations/`：初始schema迁移入口；`manage.py`提供迁移、备份、隔离恢复和密码重置。
- `templates/opening-balances.xlsx`：六类核实期初模板，不含虚构业务记录。
- `tests/`：113项自动测试，包括U01–U17的本地对应部分。
- `docs/ARCHITECTURE.md`：架构、ER关系、状态机、安全token、四里程碑。
- `docs/ACCEPTANCE.md` / `docs/TEST_RESULTS.txt`：实际验收范围及测试输出。
- `docs/DATA_GUIDE.md`：模板列映射、待核实数据、导出单位。
- `docs/OPERATIONS.md`：备份恢复、HTTPS/PostgreSQL部署。
- `docs/BACKLOG.md`：二期与未验证事项。
- `deploy/`：Docker Compose + Caddy + PostgreSQL配置及云备份脚本。

交付包不含数据库、客户资料、付款附件、实际密码或`.env`。首次启动由使用人自行创建管理员。没有购买或发布云服务。尚待Windows实机、PostgreSQL目标环境及外网HTTPS验收。
