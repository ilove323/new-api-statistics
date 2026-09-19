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

后续数据库结构变更应新增编号递增的迁移文件。迁移失败回滚事务，不写入版本记录。
此版本不提供自动降级 SQL；涉及删列等变更时，
仅回退镜像可能不足以恢复服务，应同时恢复升级前监控库和原加密密钥。

应用对 New API 使用只读查询，监控迁移仅作用于 MONITOR_DATABASE_URL 指定库。

## 升级到 0.1.1

`0.1.1` 会自动执行 `002` 至 `004` 迁移，增加渠道排除规则、逐渠道月度归档和
钉钉 Webhook 配置表。首次余额检查或保存设置时，系统会尝试把已有月度总额拆分为
“月份 + 渠道 ID”明细；因此升级前应确认 New API 消费日志仍覆盖累计起始月份。
若重新读取的历史金额低于旧月度归档，自动拆分会失败并保留旧数据，避免静默覆盖。
管理员仍可在页面通过“追溯历史计费”查看逐月差额，并在明确确认后使用当前完整数据覆盖。

旧版钉钉企业应用凭据无法转换成群机器人 Webhook。迁移会删除旧凭据表，并在旧钉钉渠道
处于选中状态时切换到 `dingtalk_webhook`、关闭通知并提示重新配置。飞书配置、预算、警报和
已有月度总额不会因此删除。重新启用前请在“报警渠道”中填写 Webhook URL，并按机器人安全
设置决定是否填写加签密钥。

## 0.1.1 余额 API 变更

对外接口改用 New API 管理员 PAT：报警路径改为 /statistics/api/alert；
/statistics/api/balance 返回实时余额，不再返回网页内部状态。网页状态已同步迁移。
只读账号需具备 users.access_token 查询权限；无需数据库结构迁移。
具体请求和权限范围见 [API 文档](api.md)。
