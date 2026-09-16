# Security

维护者：[@ilove323](https://github.com/ilove323)。
目前仅维护最新发布版本。首个版本发布前，修复在 main 上进行。

请勿在公开 Issue 中提交密码、令牌、数据库备份、客户数据或可直接利用的漏洞详情。
若仓库已启用 GitHub 私密漏洞报告，请使用仓库 Security 页的 Report a vulnerability。
若该入口不可用，请先提交不含漏洞细节的 Issue，请维护者提供私密报告方式。

统计连接对 New API 使用只读事务；监控连接只指向独立监控库。
使用 HTTPS 保护管理员 Basic Auth；妥善备份通知加密密钥及监控库。
`/healthz` 仅表示进程可响应，不代表数据库可访问。
