# 升级与备份

升级前记录应用版本或镜像 digest，并分别备份 New API 数据库、监控数据库、
部署 .env 以及 NOTIFICATION_ENCRYPTION_KEY。备份应存放在仓库外。

监控库可以使用 PostgreSQL 管理员导出：

```bash
docker exec <PostgreSQL容器名> sh -lc \
  'pg_dump -U "$POSTGRES_USER" -Fc new_api_statistics' > /安全备份目录/monitor.dump
```

维护者发布镜像后，修改 compose.release.yml 使用的 IMAGE_TAG，再执行：

```bash
docker compose -f compose.release.yml pull
docker compose -f compose.release.yml up -d
docker compose -f compose.release.yml logs --tail=100 balance-worker
```

监控库初始化在事务和 advisory lock 内运行。schema_migrations 保存已执行脚本名称；
脚本只执行一次。001_initial.sql 同时兼容空库和此前无版本表的监控库。
它保留原有预算、归档和渠道配置，并迁移旧通知表、清理已废弃的已读状态。
已有未使用版本表的安装升级时仍需先备份。

后续只新增编号递增的迁移文件。迁移失败回滚事务，不写入版本记录。
不要编辑已发布迁移。此版本不提供自动降级 SQL；涉及删列等变更时，
仅回退镜像可能不足以恢复服务，应同时恢复升级前监控库和原加密密钥。

应用对 New API 使用只读查询，监控迁移仅作用于 MONITOR_DATABASE_URL 指定库。
