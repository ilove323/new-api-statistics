# New API 兼容范围

当前实现直读 PostgreSQL，不支持 SQLite 或 MySQL。Python 要求 3.12+；
CI 配置覆盖 PostgreSQL 15/16，其他版本需自行验证。尚无按 New API 发布版本验证的兼容矩阵，
不能仅凭协议兼容就断言数据库兼容。

依赖字段：

| 表 | 字段 |
| --- | --- |
| logs | id, created_at, user_id, username, model_name, quota, prompt_tokens, completion_tokens, other, type, channel_id |
| users | id, username, display_name, password, role, status, deleted_at |
| options | key, value |
| channels | id, name, status |

消费日志为 type=2，quota / 500000 为消费金额。认证要求 bcrypt 密码、
role >= 10、status=1 且 deleted_at 为空。价格来自 options 中模型倍率配置。
金额单位和 quota 换算必须与实际站点一致。

缓存字段和模型语义见[统计口径](calculation.md)。自定义 fork 改动数据库、
配额单位或缓存语义时，须以虚构样本验证后接入。
