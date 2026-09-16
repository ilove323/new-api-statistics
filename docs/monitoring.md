# 余额监控与数据库初始化

以下命令均在项目根目录执行。

## 余额监控

- 右上角齿轮设置总额度、剩余额度阈值、累计起始月份与启用状态；默认关闭、起始月份默认本月。这里的总额度是独立预算，不会修改 New API 用户配额。
- 余额 = 总额度 - 起始月份以来的已归档消费 - 本月消费。金额与报告一致，取 New API `logs.type=2` 的 `SUM(quota)/500000`，不依赖页面时间/用户筛选，也不使用模型现价重新计费。
- 保存设置时立即一次性归档起始月份至上月的缺失月份，保存成功即表示归档完成；失败则整个设置保存回滚。已归档月份只读监控库，不重新拉取、不覆盖；起始月份前移时只补齐缺少的月份。
- `balance-worker` 每天北京时间早上 10:00 检查一次报警，每月 1 日同时归档上月。进程直接休眠到下一次 10:00，不做每分钟轮询，也不在启动时立即统计。错过当日 10:00 后重启会等待次日，可点击铃铛手动检查。数据库每日标记保证多个 worker 不重复执行同一天的定时检查。
- 每次点击铃铛会现场检查一次余额并生成、更新或删除警报；当月消费现场查询，历史月份只读归档库（缺失归档会先补齐）。手动检查不占用、不取消早上 10:00 的定时检查。页面不再每分钟轮询。保存设置后的查看不会额外触发报警。
- 月度归档是当时的快照，后续日志删除、修改或迟到入账不会自动改写归档。请在日志保留期覆盖的范围内选择起始月份，否则已被删除的历史费用无法恢复。
- 剩余额度严格小于阈值时报警。数据库最多保留一条警报，每次定时或手动检查覆盖金额和检查时间。下一次检查发现余额大于或等于阈值时，删除警报，不保留“已解除”记录；再次不足时重新生成一条。仅提高额度但仍低于阈值时不会清除警报。警报不区分已读/未读，没有确认按钮或未读计数，旧版已读记录表在升级时删除。页面只显示最新检查时间，不显示首次报警时间；月度消费归档和设置审计不受影响。
- 查询失败保留上次结果并显示过期/失败状态，不把失败当零消费。SMTP 未接入。余额设置、警报、归档都不加入 Excel 导出。

### 独立数据库

额度、月度归档、报警和通知渠道配置不能写入 New API 原库。首次部署时，用 PostgreSQL
管理员一次性创建独立数据库和专用账号。先进入 PostgreSQL 容器中的 `postgres` 管理库：

```bash
docker exec -it <PostgreSQL容器名> sh -lc 'psql -U "$POSTGRES_USER" -d postgres'
```

进入 `psql` 后执行以下 SQL。将 `<random-password>` 替换为强随机密码；不要使用示例占位符：

```sql
CREATE ROLE statistics_monitor LOGIN PASSWORD '<random-password>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE new_api_statistics OWNER statistics_monitor;
REVOKE CONNECT ON DATABASE new_api_statistics FROM PUBLIC;
GRANT CONNECT ON DATABASE new_api_statistics TO statistics_monitor;
```

`CREATE DATABASE` 必须在 `postgres` 等其他数据库中执行，不能先连接尚未创建的
`new_api_statistics`。上述 SQL 用于首次创建；如果角色或数据库已经存在，不要重复执行
`CREATE ROLE` 或 `CREATE DATABASE`，应核对现有 owner 和密码。

在 `.env` 添加监控库连接串。连接串中的密码含 `@`、`:`、`/`、`#` 等特殊字符时必须 URL 编码：

```ini
MONITOR_DATABASE_URL=postgresql://statistics_monitor:<random-password>@postgres:5432/new_api_statistics
```

生成一次通知凭据加密密钥，并把命令输出写入同一个 `.env`。不要把密钥提交到 Git：

```bash
docker run --rm python:3.12-slim \
  python -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

```ini
NOTIFICATION_ENCRYPTION_KEY=<上一步生成的值>
```

`PG*` 仍指向 New API 原库，查询使用只读事务。`MONITOR_DATABASE_URL` 必须指向新建的独立库；服务账号不需要建库权限。新库由 worker 自动建表，需可创建表及读写自己拥有的表。管理员登录仍读取 New API 的用户和密码，不建立另一套登录账号。

```bash
chmod 600 .env
docker compose up -d --build statistics balance-worker
docker compose ps
docker compose logs --tail=50 balance-worker
```

首次启动后，应用会自动创建监控表。可使用 PostgreSQL 管理员验证，正常应能看到
`balance_*`、`notification_settings`、`notification_feishu_settings` 和
`notification_dingtalk_settings` 等表：

```bash
docker exec <PostgreSQL容器名> sh -lc \
  'psql -U "$POSTGRES_USER" -d new_api_statistics -c "\\dt"'
```

Nginx 原有 `/statistics/` 转发不需变更，也不暴露新的服务端口。未配置连接串时原统计功能照常使用，监控不可用。请将独立库与 New API 库分别纳入数据库备份。
