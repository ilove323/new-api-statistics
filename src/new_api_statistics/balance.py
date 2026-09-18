"""Budget ledger in a separate database; New API usage is always read-only."""

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from new_api_statistics.report import TZ


def configured():
    return bool(os.environ.get("MONITOR_DATABASE_URL"))


def connect():
    if not configured():
        raise ValueError("余额监控数据库尚未配置。")
    return psycopg.connect(
        os.environ["MONITOR_DATABASE_URL"],
        connect_timeout=8,
        row_factory=dict_row,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def initialize():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(90216321)")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
        )""")
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        # The lock and transaction make upgrades atomic across web/worker processes.
        for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            if migration.name not in applied:
                conn.execute(migration.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (%s)",
                    (migration.name,),
                )
        conn.execute(
            "INSERT INTO balance_settings(id,start_month) VALUES (1,%s) ON CONFLICT DO NOTHING",
            (datetime.now(TZ).date().replace(day=1),),
        )


def next_month(month):
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def month_list(first, end):
    result = []
    while first < end:
        result.append(first)
        first = next_month(first)
    return result


def source_channels():
    """Read the current New API channel catalog without modifying it."""
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as conn:
        return conn.execute(
            """SELECT id AS channel_id,
                      COALESCE(NULLIF(btrim(name), ''), '渠道 #' || id::text) AS channel_name,
                      status AS channel_status
               FROM channels ORDER BY id"""
        ).fetchall()


def usage_channels_snapshot():
    """Combine the live New API channel list with the persisted exclusion set."""
    channels = source_channels()
    with connect() as conn:
        excluded = set(excluded_usage_channel_ids(conn))
    return [dict(row, included=row["channel_id"] not in excluded) for row in channels]


def validate_excluded_channel_ids(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError("请选择有效的消费渠道。")
    result = set()
    for channel_id in value:
        if type(channel_id) is not int or channel_id < 0 or channel_id in result:
            raise ValueError("请选择有效的消费渠道。")
        result.add(channel_id)
    return sorted(result)


def excluded_usage_channel_ids(conn):
    return [
        row["channel_id"]
        for row in conn.execute(
            "SELECT channel_id FROM balance_excluded_channels"
        ).fetchall()
    ]


def validate_settings(body, today=None):
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        raise ValueError("请填写有效的监控设置。")
    values = {}
    for key in ("budget", "threshold"):
        try:
            value = Decimal(str(body.get(key)))
        except InvalidOperation:
            raise ValueError("额度和阈值必须是非负金额。") from None
        if (
            not value.is_finite()
            or not 0 <= value <= Decimal("999999999999")
            or value != value.quantize(Decimal(".01"))
        ):
            raise ValueError("额度和阈值最多两位小数，且不能为负数或过大。")
        values[key] = value
    try:
        month = datetime.strptime(body["start_month"], "%Y-%m").date()
        if month.strftime("%Y-%m") != body["start_month"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("累计起始月份格式应为 YYYY-MM。") from None
    if (
        not date(1970, 1, 1)
        <= month
        <= (today or datetime.now(TZ).date()).replace(day=1)
    ):
        raise ValueError("累计起始月份不能晚于本月。")
    if type(body.get("version")) is not int or body["version"] < 1:
        raise ValueError("设置版本无效，请重新打开设置。")
    return dict(
        values,
        start_month=month,
        enabled=body["enabled"],
        version=body["version"],
        excluded_channel_ids=validate_excluded_channel_ids(
            body.get("excluded_channel_ids")
        ),
    )


class SettingsConflict(Exception):
    """Another administrator changed the same settings."""


class CheckBusy(Exception):
    """An in-progress check must not be reported as a completed manual check."""


class HistoryDataMissing(Exception):
    """Archived billing cannot be safely rebuilt from incomplete source logs."""


def replace_excluded_channels(conn, channel_ids, username):
    if channel_ids is None:
        return
    conn.execute("DELETE FROM balance_excluded_channels")
    if channel_ids:
        conn.execute(
            """INSERT INTO balance_excluded_channels(channel_id,updated_by)
               SELECT channel_id,%s FROM unnest(%s::bigint[]) AS channel_id""",
            (username, channel_ids),
        )


def update_settings(conn, values, username):
    row = conn.execute(
        """UPDATE balance_settings SET budget=%(budget)s,
        threshold=%(threshold)s,start_month=%(start_month)s,enabled=%(enabled)s,
        version=version+1,updated_at=now(),updated_by=%(username)s
        WHERE id=1 AND version=%(version)s RETURNING *""",
        dict(values, username=username),
    ).fetchone()
    if not row:
        raise SettingsConflict()
    return row


def audit_settings(conn, row, username):
    conn.execute(
        """INSERT INTO balance_settings_audit(username,version,budget,threshold,start_month,enabled)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (
            username,
            row["version"],
            row["budget"],
            row["threshold"],
            row["start_month"],
            row["enabled"],
        ),
    )


def save_settings(body, username):
    values = validate_settings(body)
    excluded_channel_ids = values.pop("excluded_channel_ids")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(90216321)")
        row = update_settings(conn, values, username)
        replace_excluded_channels(conn, excluded_channel_ids, username)
        audit_settings(conn, row, username)
        if not row["enabled"]:
            conn.execute(
                "UPDATE balance_alerts SET resolved_at=now(),updated_at=now() WHERE resolved_at IS NULL"
            )
        archive_missing(conn, row["start_month"], datetime.now(TZ))
    return row


def recalculate_history(body, username, now=None):
    """Atomically rebuild closed-month archives only when source logs are complete."""
    values = validate_settings(body)
    excluded_channel_ids = values.pop("excluded_channel_ids")
    if excluded_channel_ids is None:
        raise ValueError("请重新打开设置并刷新消费渠道后再追溯。")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    current = now.date().replace(day=1)
    months = month_list(values["start_month"], current)
    if not months:
        raise ValueError("当前起始月份没有可追溯的历史归档。")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(90216321)")
        archived = {
            row["month"]: row["amount"]
            for row in conn.execute(
                "SELECT month,amount FROM balance_months WHERE month >= %s AND month < %s",
                (values["start_month"], current),
            ).fetchall()
        }
        if any(month not in archived for month in months):
            raise HistoryDataMissing()
        amounts = source_amounts_with_raw(months, now, excluded_channel_ids)
        if any(amounts[month]["raw"] < archived[month] for month in months):
            raise HistoryDataMissing()
        row = update_settings(conn, values, username)
        replace_excluded_channels(conn, excluded_channel_ids, username)
        audit_settings(conn, row, username)
        for month in months:
            conn.execute(
                "UPDATE balance_months SET amount=%s,archived_at=now() WHERE month=%s",
                (amounts[month]["filtered"], month),
            )
        if not row["enabled"]:
            conn.execute(
                "UPDATE balance_alerts SET resolved_at=now(),updated_at=now() WHERE resolved_at IS NULL"
            )
    return {"version": row["version"], "months": len(months)}


def archive_missing(conn, first, now):
    """Backfill only absent closed months; existing ledger entries are immutable."""
    current = now.date().replace(day=1)
    archived = {
        r["month"] for r in conn.execute("SELECT month FROM balance_months").fetchall()
    }
    missing = [m for m in month_list(first, current) if m not in archived]
    if not missing:
        return
    excluded = excluded_usage_channel_ids(conn)
    for month, amount in source_amounts(missing, now, excluded).items():
        conn.execute(
            "INSERT INTO balance_months(month,amount) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (month, amount),
        )


def source_amounts_with_raw(months, now, excluded_channel_ids=None):
    """Return raw and filtered billing in one read-only source query."""
    if not months:
        return {}
    ranges = []
    params = []
    filtered_sum = "SUM(quota::numeric)/500000"
    if excluded_channel_ids:
        filtered_sum = (
            "SUM(quota::numeric) FILTER (WHERE channel_id IS NULL "
            "OR NOT (channel_id = ANY(%s::bigint[])))/500000"
        )
        params.append(excluded_channel_ids)
    for month in sorted(set(months)):
        first = datetime.combine(month, datetime.min.time(), tzinfo=TZ)
        last = datetime.combine(next_month(month), datetime.min.time(), tzinfo=TZ)
        ranges.append("(created_at >= %s AND created_at < %s)")
        params.extend(
            [
                int(first.timestamp()),
                min(int(last.timestamp()), int(now.timestamp()) + 1),
            ]
        )
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        rows = conn.execute(
            """SELECT date_trunc('month',to_timestamp(created_at)
            AT TIME ZONE 'Asia/Shanghai')::date AS month,
            SUM(quota::numeric)/500000 AS raw_amount,"""
            + filtered_sum
            + """ AS filtered_amount FROM logs
            WHERE type=2 AND ("""
            + " OR ".join(ranges)
            + ")"
            + " GROUP BY 1",
            params,
        ).fetchall()
    found = {
        row["month"]: {"raw": row["raw_amount"], "filtered": row["filtered_amount"]}
        for row in rows
    }
    zero = {"raw": Decimal(0), "filtered": Decimal(0)}
    return {month: found.get(month, zero.copy()) for month in months}


def source_amounts(months, now, excluded_channel_ids=None):
    """One SQL aggregate, no request-log pagination; half-open Beijing months."""
    return {
        month: amounts["filtered"]
        for month, amounts in source_amounts_with_raw(
            months, now, excluded_channel_ids
        ).items()
    }


def check_once(now=None, source=None, daily=True):
    """Idempotent rollover and catch-up, serialized across worker replicas."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    current = now.date().replace(day=1)
    with connect() as conn:
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(90216321) AS locked"
        ).fetchone()["locked"]:
            if not daily:
                raise CheckBusy()
            return
        settings = conn.execute("SELECT * FROM balance_settings WHERE id=1").fetchone()
        if not settings or not settings["enabled"]:
            return
        if (
            daily
            and conn.execute(
                "SELECT 1 FROM balance_daily_runs WHERE day=%s", (now.date(),)
            ).fetchone()
        ):
            return
        archived = {
            r["month"]
            for r in conn.execute("SELECT month FROM balance_months").fetchall()
        }
        missing = [
            m for m in month_list(settings["start_month"], current) if m not in archived
        ]
        excluded = excluded_usage_channel_ids(conn)
        amounts = (
            source(missing + [current], now)
            if source is not None
            else source_amounts(missing + [current], now, excluded)
        )
        # Do not publish a result calculated against settings changed mid-query.
        latest = conn.execute(
            "SELECT version FROM balance_settings WHERE id=1 FOR UPDATE"
        ).fetchone()
        if latest["version"] != settings["version"]:
            if not daily:
                raise CheckBusy()
            return
        for month in missing:
            conn.execute(
                "INSERT INTO balance_months(month,amount) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (month, amounts[month]),
            )
        historical = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS amount FROM balance_months "
            "WHERE month >= %s AND month < %s",
            (settings["start_month"], current),
        ).fetchone()["amount"]
        spent = historical + amounts[current]
        remaining = settings["budget"] - spent
        conn.execute(
            """INSERT INTO balance_state(id,version,current_month,current_amount,archived_amount,
            remaining,checked_at,last_error) VALUES (1,%s,%s,%s,%s,%s,%s,NULL)
            ON CONFLICT(id) DO UPDATE SET version=EXCLUDED.version,current_month=EXCLUDED.current_month,
            current_amount=EXCLUDED.current_amount,archived_amount=EXCLUDED.archived_amount,
            remaining=EXCLUDED.remaining,checked_at=EXCLUDED.checked_at,last_error=NULL""",
            (
                settings["version"],
                current,
                amounts[current],
                historical,
                remaining,
                now,
            ),
        )
        if remaining < settings["threshold"]:
            conn.execute(
                """INSERT INTO balance_alerts(remaining,threshold,spent,budget,updated_at)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT ((true))
                DO UPDATE SET remaining=EXCLUDED.remaining,threshold=EXCLUDED.threshold,
                spent=EXCLUDED.spent,budget=EXCLUDED.budget,updated_at=EXCLUDED.updated_at,resolved_at=NULL""",
                (remaining, settings["threshold"], spent, settings["budget"], now),
            )
        else:
            conn.execute("DELETE FROM balance_alerts")
        if daily:
            conn.execute(
                "INSERT INTO balance_daily_runs(day) VALUES (%s) ON CONFLICT DO NOTHING",
                (now.date(),),
            )
    # Commit the latest alert before contacting an external notification channel.
    from new_api_statistics.notifications import notify_safely

    notify_safely()
    return True


def record_failure():
    # Never expose database credentials or SQL diagnostics to the browser.
    with connect() as conn:
        conn.execute(
            "INSERT INTO balance_state(id,last_error) VALUES (1,'监控查询失败，请检查服务日志。') "
            "ON CONFLICT(id) DO UPDATE SET last_error=EXCLUDED.last_error"
        )


def snapshot(live=False):
    if not configured():
        return dict(configured=False)
    with connect() as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        settings = conn.execute("SELECT * FROM balance_settings WHERE id=1").fetchone()
        if not settings:
            raise ValueError("余额监控正在初始化，请稍后重试。")
        state = conn.execute("SELECT * FROM balance_state WHERE id=1").fetchone()
        months = conn.execute(
            "SELECT month,amount,archived_at FROM balance_months "
            "WHERE month >= %s ORDER BY month DESC",
            (settings["start_month"],),
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM balance_alerts ORDER BY updated_at DESC LIMIT 1"
        ).fetchall()
        excluded = excluded_usage_channel_ids(conn)
    now = datetime.now(TZ)
    if live:
        current = now.date().replace(day=1)
        months = [m for m in months if m["month"] < current]
        archived = {m["month"] for m in months}
        if any(m not in archived for m in month_list(settings["start_month"], current)):
            return dict(
                configured=True,
                settings=settings,
                state=state,
                months=months,
                alerts=alerts,
                valid=False,
                stale=True,
            )
        amount = source_amounts([current], now, excluded)[current]
        historical = sum((m["amount"] for m in months), Decimal(0))
        state = dict(
            version=settings["version"],
            current_month=current,
            current_amount=amount,
            archived_amount=historical,
            remaining=settings["budget"] - historical - amount,
            checked_at=now,
            last_error=None,
        )
    valid = bool(
        state
        and state["checked_at"]
        and state["version"] == settings["version"]
        and state["current_month"] == now.date().replace(day=1)
    )
    stale = (
        not valid
        or (now - state["checked_at"]).total_seconds() > 90000
        or bool(state["last_error"])
    )
    return dict(
        configured=True,
        settings=settings,
        state=state,
        months=months,
        alerts=alerts,
        valid=valid,
        stale=stale,
    )
