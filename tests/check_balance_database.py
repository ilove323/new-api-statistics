#!/usr/bin/env python3
# Usage: MONITOR_DATABASE_URL=... python tests/check_balance_database.py
# Creates and removes an isolated test schema in the monitoring database only.
"""Real PostgreSQL checks for rollover, alerts, settings races and SQL boundaries."""

import os
import sys
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from new_api_statistics import balance
from new_api_statistics import notifications
from cryptography.fernet import Fernet
from new_api_statistics.notification_channels import dingtalk_webhook, feishu_app
from new_api_statistics.notification_channels.base import DeliveryError
from new_api_statistics.report import TZ


def main():
    dsn = os.environ["MONITOR_DATABASE_URL"]
    schema = "balance_test_" + uuid.uuid4().hex
    original_connect = psycopg.connect

    def connect(*args, **kwargs):
        return original_connect(
            dsn, row_factory=dict_row, options="-c search_path=" + schema
        )

    with original_connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with (
            patch.object(balance, "connect", connect),
            patch(
                "new_api_statistics.notifications.load_site_name",
                return_value="示例网关",
            ),
        ):
            balance.initialize()
            when = datetime.now(TZ).replace(day=10)
            current = when.date().replace(day=1)
            previous = (current - timedelta(days=1)).replace(day=1)
            first = (previous - timedelta(days=1)).replace(day=1)
            body = dict(
                budget="100",
                threshold="10",
                enabled=True,
                start_month=first.strftime("%Y-%m"),
                version=1,
            )
            calls = []

            def source(months, now):
                if months:
                    calls.append(months)
                return {m: Decimal(45) for m in months}

            with patch("new_api_statistics.balance.source_amounts", source):
                balance.save_settings(body, "test_admin")
            assert calls == [[first, previous]], calls
            balance.check_once(when, source, daily=False)
            with connect() as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) AS n FROM balance_daily_runs"
                    ).fetchone()["n"]
                    == 0
                )
            calls.clear()
            balance.check_once(when, source)
            balance.check_once(when, source)
            assert calls == [[current]], calls
            balance.check_once(when, source, daily=False)
            assert calls == [[current], [current]], calls
            with connect() as conn:
                assert (
                    conn.execute("SELECT count(*) AS n FROM balance_months").fetchone()[
                        "n"
                    ]
                    == 2
                )
                assert (
                    conn.execute("SELECT count(*) AS n FROM balance_alerts").fetchone()[
                        "n"
                    ]
                    == 1
                )
                state = conn.execute("SELECT * FROM balance_state").fetchone()
                assert (
                    state["remaining"] == Decimal(-35)
                    and state["archived_amount"] == 90
                )
            assert "unread" not in balance.snapshot()
            assert all("read_at" not in a for a in balance.snapshot()["alerts"])
            with patch("new_api_statistics.balance.source_amounts", source):
                live = balance.snapshot(live=True)
                assert live["state"]["remaining"] == -35
                assert calls[-1] == [current]
                balance.save_settings(dict(body, budget="145", version=2), "test_admin")
            balance.check_once(when, source)
            with connect() as conn:
                assert (
                    conn.execute("SELECT resolved_at FROM balance_alerts").fetchone()[
                        "resolved_at"
                    ]
                    is None
                )
            balance.check_once(when + timedelta(days=1), source)
            with connect() as conn:
                assert (
                    conn.execute("SELECT count(*) AS n FROM balance_alerts").fetchone()[
                        "n"
                    ]
                    == 0
                )
            recovered = balance.snapshot()
            assert recovered["alerts"] == []
            with patch("new_api_statistics.balance.source_amounts", source):
                balance.save_settings(dict(body, version=3), "test_admin")
            balance.check_once(when + timedelta(days=2), source)
            with connect() as conn:
                assert (
                    conn.execute("SELECT count(*) AS n FROM balance_alerts").fetchone()[
                        "n"
                    ]
                    == 1
                )
                alert = conn.execute("SELECT * FROM balance_alerts").fetchone()
                assert alert["resolved_at"] is None
                assert alert["updated_at"] == when + timedelta(days=2)
            try:
                balance.save_settings(body, "test_admin")
                raise AssertionError("stale version accepted")
            except balance.SettingsConflict:
                pass

            # Configuration changed while source SQL was running: reject the stale result.
            def race(months, now):
                with connect() as conn:
                    conn.execute(
                        "UPDATE balance_settings SET version=5,budget=500 WHERE id=1"
                    )
                return source(months, now)

            try:
                balance.check_once(when, race, daily=False)
                raise AssertionError("stale manual result accepted")
            except balance.CheckBusy:
                pass
            with connect() as conn:
                assert (
                    conn.execute("SELECT version FROM balance_state").fetchone()[
                        "version"
                    ]
                    == 4
                )
            balance.record_failure()
            assert balance.snapshot()["stale"]
            rollover = datetime.combine(
                balance.next_month(current), datetime.min.time(), tzinfo=TZ
            )
            balance.check_once(rollover, source)
            assert calls[-1] == [current, balance.next_month(current)]
            # Query a small synthetic logs table, not New API's business tables.
            with connect() as conn:
                conn.execute(
                    "CREATE TABLE logs(created_at bigint,type integer,quota bigint)"
                )
                for moment, kind, quota in [
                    (datetime(2025, 12, 31, 23, 59, 59, tzinfo=TZ), 2, 500000),
                    (datetime(2026, 1, 1, tzinfo=TZ), 2, 1000000),
                    (datetime(2026, 1, 1, tzinfo=TZ), 1, 9000000),
                    (datetime(2026, 2, 1, tzinfo=TZ), 2, 1500000),
                ]:
                    conn.execute(
                        "INSERT INTO logs VALUES (%s,%s,%s)",
                        (int(moment.timestamp()), kind, quota),
                    )
            with patch("new_api_statistics.balance.psycopg.connect", connect):
                amounts = balance.source_amounts(
                    [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)],
                    datetime(2026, 2, 1, tzinfo=TZ),
                )
            assert amounts == {
                date(2025, 12, 1): Decimal(1),
                date(2026, 1, 1): Decimal(2),
                date(2026, 2, 1): Decimal(3),
            }
            # Simulate an older installation with multiple resolved alerts and reads.
            with connect() as conn:
                conn.execute("DROP TABLE schema_migrations")
                latest = conn.execute(
                    "INSERT INTO balance_alerts(remaining,threshold,spent,budget) "
                    "VALUES (0,1,10,10) ON CONFLICT ((true)) "
                    "DO UPDATE SET updated_at=now() RETURNING id"
                ).fetchone()["id"]
                conn.execute("DROP INDEX one_balance_alert")
                conn.execute(
                    "CREATE TABLE balance_alert_reads (alert_id bigint REFERENCES balance_alerts(id), "
                    "username text,read_at timestamptz DEFAULT now())"
                )
                for _ in range(2):
                    old = conn.execute(
                        "INSERT INTO balance_alerts(remaining,threshold,spent,budget,updated_at,resolved_at) "
                        "VALUES (0,1,10,10,'2000-01-01','2000-01-02') RETURNING id"
                    ).fetchone()["id"]
                    conn.execute(
                        "INSERT INTO balance_alert_reads(alert_id,username) VALUES (%s,'old_reader')",
                        (old,),
                    )
            balance.initialize()
            balance.initialize()
            with connect() as conn:
                remaining = conn.execute("SELECT id FROM balance_alerts").fetchall()
                assert remaining == [{"id": latest}]
                assert (
                    conn.execute(
                        "SELECT to_regclass('balance_alert_reads') AS name"
                    ).fetchone()["name"]
                    is None
                )
            # Notification secrets and deliveries stay entirely inside this test schema.
            with (
                patch.dict(
                    os.environ,
                    NOTIFICATION_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                ),
                patch.object(feishu_app, "send") as send,
            ):
                config = dict(
                    version=1,
                    enabled=True,
                    channel="feishu_app",
                    app_id="cli_fixture",
                    app_secret="fixture-secret",
                    receive_id_type="user_id",
                    receive_id="employee123",
                )
                notifications.save(config, "fixture_admin")
                result = notifications.snapshot()
                assert result["secret_configured"] and "secret_encrypted" not in result
                with connect() as conn:
                    cipher = conn.execute(
                        "SELECT secret_encrypted FROM notification_feishu_settings"
                    ).fetchone()["secret_encrypted"]
                    assert (
                        cipher != "fixture-secret"
                        and notifications.decrypt(cipher) == "fixture-secret"
                    )
                    columns = {
                        row["column_name"]
                        for row in conn.execute("""SELECT column_name
                        FROM information_schema.columns WHERE table_schema=current_schema()
                        AND table_name='notification_settings'""").fetchall()
                    }
                    assert (
                        not {
                            "app_id",
                            "secret_encrypted",
                            "robot_code",
                            "receive_id_type",
                            "receive_id",
                        }
                        & columns
                    )
                    assert conn.execute(
                        "SELECT webhook_encrypted,secret_encrypted,signing_enabled "
                        "FROM notification_dingtalk_webhook_settings WHERE id=1"
                    ).fetchone() == {
                        "webhook_encrypted": "",
                        "secret_encrypted": "",
                        "signing_enabled": False,
                    }
                    assert (
                        conn.execute(
                            "SELECT to_regclass('notification_dingtalk_settings') AS name"
                        ).fetchone()["name"]
                        is None
                    )
                notifications.save(
                    dict(config, version=2, app_secret=""), "fixture_admin"
                )
                notifications.deliver(test=True, expected_version=3)
                assert (
                    send.call_count == 1 and send.call_args.args[1] == "fixture-secret"
                )
                assert "站点：示例网关" in send.call_args.args[2]
                assert notifications.snapshot()["last_success_at"]
                notifications.deliver()
                assert send.call_count == 2
                assert "【余额不足报警】\n站点：示例网关\n" in send.call_args.args[2]
                send.side_effect = DeliveryError("测试发送失败")
                notifications.deliver()
                assert notifications.snapshot()["last_error"] == "测试发送失败"
                # Check commits actual state despite a subsequent channel failure.
                with connect() as conn:
                    conn.execute(
                        "UPDATE balance_settings SET budget=1,threshold=10 WHERE id=1"
                    )
                balance.check_once(when, source, daily=False)
                assert balance.snapshot()["state"]["remaining"] < 0
                assert notifications.snapshot()["last_error"] == "测试发送失败"
                with connect() as conn:
                    conn.execute("UPDATE balance_settings SET budget=10000 WHERE id=1")
                before = send.call_count
                balance.check_once(when, source, daily=False)
                assert send.call_count == before and not balance.snapshot()["alerts"]
                notifications.save(
                    dict(config, version=3, enabled=False, app_secret=""),
                    "fixture_admin",
                )
                notifications.deliver()
                assert send.call_count == before
                try:
                    notifications.save(
                        dict(config, version=4, app_id="cli_changed", app_secret=""),
                        "fixture_admin",
                    )
                    raise AssertionError("changed application retained old secret")
                except ValueError:
                    pass
                try:
                    notifications.save(config, "fixture_admin")
                    raise AssertionError("stale channel version accepted")
                except balance.SettingsConflict:
                    pass
                assert notifications.snapshot()["version"] == 4
                assert notifications.snapshot()["app_id"] == "cli_fixture"
                with connect() as conn:
                    # Recreate an unversioned installation for the open_id upgrade.
                    conn.execute("DROP TABLE schema_migrations")
                    conn.execute(
                        "UPDATE notification_feishu_settings SET receive_id_type='open_id', "
                        "receive_id='ou_old' WHERE id=1"
                    )
                    conn.execute(
                        "UPDATE notification_settings SET enabled=true WHERE id=1"
                    )
                balance.initialize()
                migrated = notifications.snapshot()
                assert (
                    migrated["receive_id_type"] == "user_id"
                    and migrated["receive_id"] == ""
                )
                assert not migrated["enabled"] and migrated["secret_configured"]
                assert migrated["version"] == 5
                balance.initialize()
                assert notifications.snapshot()["version"] == 5
                webhook = (
                    "https://oapi.dingtalk.com/robot/send?access_token=fixture-token"
                )
                notifications.save(
                    dict(
                        version=5,
                        enabled=True,
                        channel="dingtalk_webhook",
                        webhook_url=webhook,
                        signing_enabled=True,
                        signing_secret="fixture-signing-secret",
                    ),
                    "fixture_admin",
                )
                result = notifications.snapshot()
                assert result["webhook_configured"]
                assert result["signing_secret_configured"]
                assert "webhook_encrypted" not in result
                with connect() as conn:
                    stored = conn.execute(
                        "SELECT webhook_encrypted,secret_encrypted "
                        "FROM notification_dingtalk_webhook_settings WHERE id=1"
                    ).fetchone()
                assert notifications.decrypt(stored["webhook_encrypted"]) == webhook
                assert (
                    notifications.decrypt(stored["secret_encrypted"])
                    == "fixture-signing-secret"
                )
                with patch.object(dingtalk_webhook, "send") as ding_send:
                    notifications.deliver(test=True, expected_version=6)
                assert ding_send.call_args.args[0]["webhook_url"] == webhook
                assert ding_send.call_args.args[1] == "fixture-signing-secret"
            print(
                "PASS: encrypted credentials, secret retention, stale versions, test delivery, failures, recovery"
            )
        print(
            "PASS: immediate backfill, one check per day, live current month only, archive reuse, alerts, races, SQL boundaries"
        )
    finally:
        with original_connect(dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


if __name__ == "__main__":
    main()
