# 对外余额与报警 API

两个独立接口均使用已有 New API 令牌认证，不创建独立 API Key：

```http
Authorization: Bearer sk-<New API令牌>
```

令牌和所属用户必须启用且未删除，令牌未过期。无需管理员角色。
此授权允许持有有效令牌的用户读取站点整体预算，并通过报警接口触发检查和通知；
返回的不是该令牌或所属用户的个人余额。本次不提供按 Key 的独立预算。
令牌剩余额度不参与校验，但令牌自身状态必须为启用。

## 实时余额

`GET /statistics/api/balance`

```bash
curl --fail-with-body   -H 'Authorization: Bearer sk-<New API令牌>'   -H 'Accept: application/json'   https://example.com/statistics/api/balance
```

```json
{
  "code": 0,
  "data": {
    "site": "示例网关",
    "currency": "CNY",
    "total_quota": 10000.0,
    "used_quota": 7500.0,
    "remaining_quota": 2500.0,
    "alert_threshold": 2000.0,
    "usage_percent": 75.0,
    "checked_at": "2026-09-19T10:00:00+08:00"
  }
}
```

- 每次请求重新汇总本月消费，历史月份读取监控库的月度渠道归档；两者均应用当前渠道排除规则。
- 不缓存计算结果，不写报警记录、不发送通知，不改变每日 10 点的定时检查。
- 不受页面的时间、用户、模型和令牌筛选影响。
- 总额度来自余额设置；累计消费为起始月份以来归档费用加本月费用。
- 金额是 JSON number，保持账务原有精度；JSON 不保证显示末尾的零。
- 已用百分比保留两位小数；总额度为零时返回 null，不截断超支百分比或负余额。
- checked_at 是本次计算的截止时间，ISO 8601、北京时间 +08:00。
- 无报警时同样返回完整余额。未配置监控或历史归档不齐全时返回 503，不以零冒充余额。
- 可每 5～10 分钟轮询；每次均访问数据库，多个调用方应错峰并避免并发重复请求。

## 报警检查

`GET /statistics/api/alert`

```bash
curl --fail-with-body   -H 'Authorization: Bearer sk-<New API令牌>'   https://example.com/statistics/api/alert
```

每次调用执行即时检查、更新报警记录，低于阈值时调用已启用的通知渠道。
这不是单纯的余额查询，轮询余额请使用上面的 /balance。

有报警返回：
`{"has_alert":true,"alert":{...}}`。
alert 包含 title、site_name、budget、spent、remaining、threshold、currency、
checked_at、timezone，金额为数字，时间为带时区 ISO 8601。
无报警返回 `{"has_alert":false,"alert":null}`。
并发检查冲突返回 409，数据库失败返回 503，查询超时返回 504。
认证失败返回 401。对外接口不接受 Basic Auth。

## 升级与网页兼容

旧的 `/statistics/api/balance/alert` 已移至 `/statistics/api/alert`；
调用方需同时把管理员 Basic Auth 改为 New API Bearer Key。
原余额页面数据移至内部 `/statistics/api/balance/status`。
网页及其内部管理接口仍使用管理员登录，不向普通令牌开放管理权限。
现有 /statistics/ Nginx 转发即可覆盖新接口。只通过 HTTPS 对外使用，不在 URL 中传递凭据。

数据库只读账号需具有 tokens 表 SELECT 权限，例如由数据库管理员执行：

```sql
GRANT SELECT ON TABLE public.tokens TO statistics_reader;
```

账号名、schema 应按实际配置替换；无需修改 New API 数据或新建密钥表。
