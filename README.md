# New API Statistics

与 New API 部署在同一 Docker 网络中的用量统计应用。Flask 页面与查询接口运行在
同一容器内，直接只读查询 New API 的 PostgreSQL 数据库。可由现有 Nginx 通过
`/statistics/` 路径转发，也可映射宿主机端口直接访问。无需修改 New API 程序，
也不需要挂载 Docker socket。

## 部署条件

- 已运行的 New API、PostgreSQL 和 Docker Compose；Nginx 为可选。
- 统计容器加入 New API 的现有 Docker 网络，不新建或替换其数据库。
- 数据库账号具有 SELECT `logs`、`options`、`users` 的权限，建议使用专用只读账号。
- 使用 Nginx 时默认其运行在同一宿主机上；容器化 Nginx 和无 Nginx 的访问方式见下文。

## 配置与启动

根据 `.env.example` 创建 `.env`，将权限设为 `600`，填写实际数据库连接参数：

| 参数 | 说明 |
| --- | --- |
| `PGHOST` | PostgreSQL 在共享 Docker 网络中的服务名或网络别名 |
| `PGPORT` | PostgreSQL 容器内部端口，通常为 `5432` |
| `PGDATABASE` | New API 使用的数据库名 |
| `PGUSER` / `PGPASSWORD` | 只读数据库账号及密码 |
| `BIND_HOST` | 宿主机监听地址；配合 Nginx 使用 `127.0.0.1`，无 Nginx 直接访问使用 `0.0.0.0` |
| `PORT` | 暴露到宿主机的端口，默认 `8091` |
| `PYTHON_IMAGE` | 构建使用的 Python 镜像，默认 `python:3.12-slim` |
| `PIP_INDEX_URL` | 可选 Python 包索引地址 |
| `NEW_API_NETWORK` | 已有 New API Docker 网络名，默认 `new-api-network` |
| `MONITOR_DATABASE_URL` | 独立监控库连接串；用于保存额度、月度归档、报警及通知渠道配置 |
| `NOTIFICATION_ENCRYPTION_KEY` | 通知渠道 Secret 的 Fernet 加密密钥；生成一次后必须持久保存 |

设置 `NEW_API_NETWORK` 为现有 New API 网络的实际名称，也可在本机专用的
`docker-compose.override.yml` 中覆盖 `networks.new-api.name`，无需改动通用 Compose 文件。
可通过 `docker network ls` 检查；Compose 中的名称只是默认配置，不保证与其他部署相同。
环境专用覆盖文件不应分发，已加入 Git 和 Docker 忽略规则。
`PGHOST` 不能填写 `127.0.0.1`，因为它在统计容器内指向统计容器自身。
不要为统计应用公开数据库端口。`.env` 不应提交、分发或打入镜像。

```bash
chmod 600 .env
docker compose up -d --build statistics balance-worker
docker compose ps
docker compose logs --tail 100 statistics
docker compose logs --tail 100 balance-worker
```

应用默认仅绑定宿主机 `127.0.0.1:8091`。使用 Nginx 时继续阅读下一节；不使用 Nginx 时按“无 Nginx 直接访问”配置外部监听。

## 配合 Nginx

把 `nginx.conf.example` 中两个 location 加入现有站点的 HTTPS `server` 块，
保留 New API 原来的 `/` 转发配置。`/statistics/` 转发到统计应用，其他路径继续访问 New API。
`proxy_pass` 不要额外添加末尾斜杠，以保留应用需要的 `/statistics/` 路径前缀。

```bash
nginx -t
nginx -s reload
```

随后访问 `https://<你的域名>/statistics/`。如果修改了 `PORT`，应同步修改
`nginx.conf.example` 中的 upstream 端口。HTTP 请求应跳转 HTTPS，避免明文传输登录凭据。

如果 Nginx 也在容器内，需要将其加入共享 Docker 网络，并将 upstream 改为
`http://statistics:8000`（或为统计容器设置唯一网络别名后使用该别名）。
此时不能使用 `127.0.0.1:8091`，因为那会指向 Nginx 容器自身。

## 无 Nginx 直接访问

如果单独部署且没有 Nginx，在 `.env` 中设置：

```ini
BIND_HOST=0.0.0.0
PORT=8091
```

重新创建容器，使端口映射生效：

```bash
docker compose up -d --build --force-recreate
docker compose ps
```

`docker compose ps` 应显示类似 `0.0.0.0:8091->8000/tcp`。在服务器防火墙或云安全组中，
只向需要访问的来源开放 TCP `8091`，然后直接访问：

```text
http://<服务器IP>:8091/statistics/
```

报警 API 地址相应为：

```text
http://<服务器IP>:8091/statistics/api/balance/alert
```

示例：

```bash
curl -u '<管理员用户名>:<管理员密码>' \
  http://<服务器IP>:8091/statistics/api/balance/alert
```

该方式确实对外提供宿主机端口，不需要 Nginx。由于登录使用 HTTP Basic Auth，普通 HTTP 会以可还原形式传输凭据，
只适合受信任内网；如果跨公网访问，必须在入口增加 HTTPS，不应把 `8091` 裸露给整个互联网。

## 登录与权限

网页使用 New API 原有管理员用户名和密码，
读取 `users.password` 的 bcrypt 哈希进行校验。仅允许 `role >= 10`、`status = 1`
且未软删除的账号；每次请求重新检查，禁用、降权和修改密码立即生效。
不配置独立网页登录账号，也不修改 New API 用户数据。

## 统计口径

- 页头站点名称和浏览器标题读取 New API 数据库 `options.SystemName`，页面刷新时重新读取；未配置时显示 New API。

- 北京时间精确到秒，结束时间包含该秒；SQL 使用半开区间，单次最多 367 天。
  旧的纯日期 API 参数仍兼容，结束日期包含全天。
- 快捷按钮点选后立即查询：上个月为完整自然月（00:00:00 至 23:59:59），
  近30天、近7天、近1天按当前时刻往前推。浏览器处于其他时区时仍按北京时间填写。
- 仅统计 `type=2` 消费日志，按用户 ID、用户名、模型聚合。
- 当前模型口径约定如下，部署前必须确认与上游日志一致，不依赖历史日志的 `usage_semantic`：
  名称以 claude 开头的模型输入不含缓存，直接作为纯输入；其余所有模型纯输入 = max(原输入 - 缓存读 - 缓存写, 0)，逐请求计算后汇总。
  原始去重总量：Claude 为原输入+输出+缓存读+缓存写；其他模型为原输入+输出，不重复增加缓存。
  此为应用当前的模型口径约定，不代表所有 OpenAI 兼容协议必然使用相同上游语义；若渠道或模型命名改变须重新确认。
  OpenAI 缓存读写前缀可能重叠，不能以四项之和替代原始去重总量。
- 缓存写优先 `cache_write_tokens`，其次 `cache_creation_tokens`；不重复叠加。
- 页面和 Excel 的输入列均为纯输入（不含缓存读写），输出列使用 completion_tokens，缓存读写单列。
  API 的 raw_input_tokens 保留未扣除缓存的日志输入。计价非缓存输入与展示输入使用相同口径。
- 区间平均 TPM = 去重总 Token × 60 / 区间秒数；区间平均 RPM = 消费日志条数 × 60 / 区间秒数。
  分母为完整所选区间（包含结束秒和空闲时间），不是实时值或峰值；RPM 仅统计 type=2 消费日志，不包含错误等其他日志。
  切换用户后使用同一区间重新计算该用户的均值。
- 排序标签页包括模型消费、用户 Token 用量、用户消费，展示全部排名；用户 Token 排序按上述去重总量。
- 金额来自实际 `SUM(quota)/500000`，不按当前价格重新计费。
- 倍率取区间内该用户模型的最后请求，按 `created_at DESC, id DESC` 排序。
- 消费金额支持悬停、键盘聚焦或点击查看计算明细：展示当前单价、四类 Token、最后倍率、计算金额、实际金额及差额。
  缺少非零用量对应的单价时不计算等式；零用量不产生费用。合计提示只对当前筛选明细金额求和。
  该计算不是历史计费重放；历史价格/倍率、其他计费项和取整可能造成差异。
- 仅区间内同用户同模型恰好两种倍率时计算折算缓存读；原始数据库日志和实际金额不覆盖。
  折算缓存读 = (实际金额 × 1000000 / 最后倍率 − 非缓存输入 × 输入单价 − 输出 × 输出单价 − 缓存写 × 缓存写单价) / 缓存读单价。
  折算总 Token = 原去重总 Token − 原缓存读 + 折算缓存读。有有效解时覆盖报表的缓存读与总 Token，无有效解则保留原值。
  页面、排名、TPM 和 Excel 统一使用合并口径，保留统一折算说明，不再单列折算值，也不展示金额差额。
  API 的 original_cache_read_tokens / original_total_tokens 保留原始值供核对。
  价格沿用当前 options 配置；计价非缓存输入根据日志语义去重。缺价、无效倍率或负数解时留空，原因保留在 API 内部字段。
  页面和 Excel 不展示折算状态列。缓存读四舍五入为整数后同步调整总量；取整可能带来微小计费尾差。
  仅由倍率变化引起的差异才保证高倍率减少、低倍率增加。
- 当前输入价格为 `ModelRatio * 2` 元/M，其余三类乘对应 options 倍率。
  缺失配置显示空白，不把未经确认的默认值当真实价格；按次计价模型的 Token 单价留空。
- 零消费且没有请求的用户没有模型明细；实际存在的零金额请求保留。
- 页面用户筛选作用于当前快照；Excel 导出重新查询所选区间和用户，活跃区间可能变化。
- Excel 包含用户模型用量、模型消费、用户Token用量、用户消费、区间汇总五个工作表，均遵循当前时间和用户筛选。
- 页面 URL 加 `?dev=1` 时，明细表额外显示缓存命中率和每百万 Token 金额；其他值不启用。命中率为缓存读 /（纯输入 + 缓存读），每百万 Token 金额为实际金额 / 总 Token × 1,000,000。合计按汇总量计算，不平均各行比率；分母为零显示 `—`。这些列仅在浏览器展示，Excel 始终不包含。

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

## 报警渠道

报警和测试消息均包含站点名称，实时读取 New API 数据库的 `options.SystemName`，与页面名称一致。

齿轮中的“报警渠道”选择“飞书企业自建应用”，填写 App ID、App Secret、接收目标类型及 ID。
群聊使用 `chat_id`（`oc_` 开头），个人使用企业内的 `user_id`，不使用 `open_id`。
旧的个人 open_id 配置升级后会停用并清空接收目标，应用凭证保留；重新填写 user_id 后再启用。
额度设置和渠道设置分别保存；更改渠道后先保存，再点击“发送测试消息”。渠道默认关闭。
测试发送不要求开启自动通知，也不会创建余额报警或修改额度。

飞书应用需启用机器人能力，申请 `im:message:send_as_bot` 权限并发布，完成租户审批。
发送群消息前将机器人加入目标群，向个人发送时需满足应用可用范围，并开通“获取用户 user ID”权限。
仅发送通知，不配置事件订阅、回调或长连接；服务需能访问 `https://open.feishu.cn`。
详见[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)和
[自建应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)。

选择“钉钉企业内部应用”时，填写 Client ID、Client Secret、Robot Code 和群聊
`openConversationId`。Client ID 与 Client Secret 用于调用
`/v1.0/oauth2/accessToken` 获取访问凭证；Robot Code 标识发送机器人，
`openConversationId` 标识接收群聊。应用需启用机器人能力、开通企业机器人发送消息权限、发布应用，
并将机器人加入目标群。本实现仅发送群消息，不订阅事件或配置回调；服务需能访问
`https://api.dingtalk.com`。`openConversationId` 是钉钉群会话 ID，不是飞书的 `oc_` ID。

在安装依赖的环境中生成一次加密密钥，写入部署环境 `.env` 的 `NOTIFICATION_ENCRYPTION_KEY`：

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

密钥必须持久保留并安全备份，web 与 worker 使用同一个值。不要每次部署重新生成。
App Secret 或 Client Secret 加密保存到独立监控数据库，API 不回传密钥或密文；编辑时留空沿用原密钥。
更改某渠道的应用 ID 后需重新填写该渠道的 Secret。数据库备份需要配合加密密钥才能恢复通知能力。
公共表 `notification_settings` 仅保存当前启用渠道及最近发送状态；飞书配置保存在
`notification_feishu_settings`，钉钉配置保存在 `notification_dingtalk_settings`。
切换渠道不会覆盖另一渠道的凭据；旧版公共表中的飞书或钉钉凭据会在初始化时迁移到对应表，
随后从公共表删除凭据字段。

每天北京时间 10:00 和点击铃铛的成功检查中，余额低于阈值且渠道已启用就发送一次通知。
反复点击铃铛会再次发送；普通页面加载和保存渠道不发送。余额恢复清除本地报警，不发送恢复消息，
已发出的渠道消息不会撤回。最新发送时间与失败原因保存在独立库并显示在渠道设置里。
发送失败不回滚余额检查，不做每分钟重试；下次正常检查再尝试。网络超时可能已经送达，故不盲目重发。
访问凭证按有效期缓存，过期刷新；请求不跟随重定向，不记录响应中的敏感信息。

通用配置与分发在 `notifications.py`；各渠道独立放在 `notification_channels/`，
当前实现为 `feishu_app.py` 和 `dingtalk_app.py`。报警配置不影响原统计和 Excel 导出。

### 报警记录 API

`GET /statistics/api/balance/alert` 每次调用都执行一次即时余额检查，实时查询当月消费并更新报警记录。
若检查后仍低于阈值，也会调用已启用的报警渠道，行为与页面点击铃铛一致。接口随后返回最新 JSON，
并使用现有 New API 管理员账号密码进行 HTTP Basic Auth：

```bash
curl -u '<管理员用户名>:<管理员密码>' \
  https://example.com/statistics/api/balance/alert
```

有报警时，`has_alert` 为 `true`，`alert` 分别提供标题、站点、额度、消费、余额、阈值、币种、
检查时间及时区；金额为 JSON 数字。无报警时返回 HTTP `200` 和
`{"has_alert":false,"alert":null}`，便于调用方稳定解析。
若同时有其他检查或设置保存正在执行，返回 HTTP `409`；查询失败沿用监控接口的错误状态。

调用方必须通过 HTTPS 访问，不另设 API Key，也不在 URL 查询参数中传递凭证。
现有 `/statistics/` Nginx 转发可以直接覆盖该接口，无需增加 location。

## 开发与验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 设置 PG* 环境变量后运行
.venv/bin/python app.py
.venv/bin/python -m unittest discover -s tests
```

本地开发入口 `http://127.0.0.1:8091/statistics/`，容器部署使用 Gunicorn。
宿主机开发时，`PGHOST` 需使用宿主机实际可访问的数据库地址，而不是仅容器内可解析的服务名。
数据库集成测试需要配置 `PGHOST`；未配置时跳过，测试使用只读 CTE 构造数据，不修改日志。
所有查询在只读事务中运行，数据库 statement_timeout 为 60 秒。
页面、查询和导出均受 Basic Auth 保护，凭据通过 HTTPS 传递。
数据库不可达时拒绝认证；`/healthz` 仅返回进程状态，不检查数据库。
