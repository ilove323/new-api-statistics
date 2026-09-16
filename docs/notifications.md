# 通知渠道

以下命令均在项目根目录执行。

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

通用配置与分发在 `src/new_api_statistics/notifications.py`；各渠道独立放在同包的 `notification_channels/`，
当前实现为 `feishu_app.py` 和 `dingtalk_app.py`。报警配置不影响原统计和 Excel 导出。
