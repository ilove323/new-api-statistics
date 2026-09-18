#!/usr/bin/env python3
# Usage: MONITOR_DATABASE_URL=<disposable database> python tests/check_migrations.py
"""Verify fresh installs, legacy upgrades and migration version recording."""

import os
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_statistics import balance


def main():
    dsn = os.environ["MONITOR_DATABASE_URL"]
    for legacy in (False, True):
        schema = "migration_test_" + uuid.uuid4().hex
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def connect():
            return psycopg.connect(
                dsn, row_factory=dict_row, options="-c search_path=" + schema
            )

        try:
            if legacy:
                with connect() as conn:
                    conn.execute("""CREATE TABLE notification_settings (
                        id integer PRIMARY KEY, version bigint DEFAULT 1,
                        enabled boolean DEFAULT false, channel text DEFAULT 'feishu_app',
                        updated_at timestamptz DEFAULT now(), updated_by text,
                        last_attempt_at timestamptz, last_success_at timestamptz, last_error text,
                        app_id text, secret_encrypted text, robot_code text,
                        receive_id_type text, receive_id text
                    )""")
                    conn.execute("""INSERT INTO notification_settings
                        (id, app_id, secret_encrypted, robot_code, receive_id_type, receive_id)
                        VALUES (1, 'fixture-app', 'fixture-ciphertext', '', 'chat_id', 'fixture-chat')""")
            with patch.object(balance, "connect", connect):
                balance.initialize()
                with connect() as conn:
                    conn.execute("UPDATE balance_settings SET budget=1234 WHERE id=1")
                balance.initialize()
            with connect() as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) AS n FROM schema_migrations"
                    ).fetchone()["n"]
                    == 3
                )
                assert (
                    conn.execute(
                        "SELECT budget FROM balance_settings WHERE id=1"
                    ).fetchone()["budget"]
                    == 1234
                )
                if legacy:
                    row = conn.execute(
                        "SELECT * FROM notification_feishu_settings WHERE id=1"
                    ).fetchone()
                    assert row["secret_encrypted"] == "fixture-ciphertext"
                    assert row["receive_id"] == "fixture-chat"
                    assert not conn.execute("""SELECT 1 FROM information_schema.columns
                        WHERE table_schema=current_schema() AND table_name='notification_settings'
                        AND column_name='secret_encrypted'""").fetchone()
                assert conn.execute(
                    "SELECT to_regclass('notification_dingtalk_webhook_settings') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_excluded_channels') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_channel_inventory') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_month_channels') AS name"
                ).fetchone()["name"]
                assert (
                    conn.execute(
                        "SELECT to_regclass('notification_dingtalk_settings') AS name"
                    ).fetchone()["name"]
                    is None
                )
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )
    print("PASS: fresh install, legacy migration, idempotency and data preservation")


if __name__ == "__main__":
    main()
