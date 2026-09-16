# 报警 API

以下命令均在项目根目录执行。

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
