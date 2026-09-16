# New API Statistics

[![CI](https://github.com/ilove323/new-api-statistics/actions/workflows/ci.yml/badge.svg)](https://github.com/ilove323/new-api-statistics/actions/workflows/ci.yml)

New API 的自托管用量统计与余额监控工具。Flask + PostgreSQL，支持 Docker Compose，
与 New API 共用 Docker 网络，由 Nginx 转发到 `/statistics/`。

维护者与当前贡献者：[@ilove323](https://github.com/ilove323)。

![用量统计界面，全部为虚构演示数据](docs/assets/dashboard.png)

截图中的站点、用户、模型及金额均为虚构演示数据。

## 功能

- 用户、模型消费排名，输入、输出、缓存 Token 统计，Excel 多工作表导出。
- New API 管理员账号登录，时间筛选精确到秒。
- 独立监控库保存预算、月度归档和余额报警，每天北京时间 10:00 检查。
- 飞书、钉钉企业应用通知，报警查询 API。

## 快速开始

需要现有 New API PostgreSQL 和 Docker 网络；原库使用专用只读账号。
初次部署请先阅读[部署说明](docs/deployment.md)。
启用余额报警还需按[监控库初始化](docs/monitoring.md)创建独立数据库。

```bash
cp .env.example .env
# 填写实际数据库连接、Docker 网络和可选监控参数
chmod 600 .env
docker compose up -d --build
```

Nginx 示例见 [nginx.conf.example](nginx.conf.example)，访问
`https://<你的域名>/statistics/`。无 Nginx 的端口配置也在部署文档中。

发布镜像的安装与升级见[发版说明](docs/releasing.md)。
首个正式标签尚未发布，目前可从源码构建。

## 文档

- [部署、认证与直接端口访问](docs/deployment.md)
- [统计口径与数学折算](docs/calculation.md)
- [New API 数据库兼容范围](docs/compatibility.md)
- [余额监控、建库 SQL](docs/monitoring.md)
- [飞书与钉钉通知](docs/notifications.md)
- [报警 API](docs/api.md)
- [升级、迁移与备份](docs/upgrading.md)
- [贡献和开发](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) · [安全报告](SECURITY.md)

Token 缓存语义取决于上游日志。部分报表值涉及数学折算，部署前请核对统计口径。
原始 New API 日志和实际消费金额不会被修改。

## 许可证

Copyright 2026 ilove323。采用 [Apache-2.0](LICENSE)，允许商业使用。
第三方图标许可见 [NOTICE](NOTICE) 和 [Lucide 许可](src/new_api_statistics/static/LUCIDE-LICENSE)。
