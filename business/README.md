# 账号与项目服务

FastAPI + SQLAlchemy + PostgreSQL + Alembic。独立服务只监听本机回环，由主 API 转发。

包括用户名/密码注册、Argon2 密码哈希、JWT 与持久可撤销会话、退出、修改密码，以及项目、素材、草稿、作品的归属检查。不是音乐生成 Worker。

安装 `.env`、迁移和运行方法见 [SETUP](../docs/SETUP.md)。使用自己的数据库，不附原团队数据库或账号。

测试仅能指向以 `_test` 结尾的专用数据库，详见 [TESTING](../docs/TESTING.md)。
