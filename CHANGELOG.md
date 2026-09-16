# Changelog

## Unreleased

- Add multi-select user and model filters scoped to the user-model detail table.
- Add persistent column visibility controls to the user-model detail table.

## 0.1.0 - 2026-09-17

First release. Requires an existing New API installation backed by PostgreSQL.

- Add user/model usage reports, token breakdowns, rankings and Excel exports.
- Reuse New API administrator authentication.
- Add monthly budget archives, daily balance checks and Feishu/DingTalk notifications.
- Package application under src/new_api_statistics.
- Add Apache-2.0 license, maintainer information and deployment documentation.
- Exclude Git metadata and private files from container builds.
- Add versioned monitoring database migrations.
- Add CI, dependency updates and container release automation.

See [release notes](docs/releases/v0.1.0.md) for deployment requirements and upgrade notes.
