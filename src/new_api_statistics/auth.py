"""Verify New API administrator passwords and API keys against PostgreSQL."""

from datetime import datetime, timezone

import bcrypt
import psycopg
from psycopg.rows import dict_row

# Use the same password-check cost for missing accounts without accepting them.
DUMMY_HASH = bcrypt.hashpw(b"not-a-user-password", bcrypt.gensalt(rounds=10))


def find_user(username):
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as conn:
        return conn.execute(
            "SELECT id, username, password, role, status, deleted_at "
            "FROM users WHERE username = %s LIMIT 1",
            (username,),
        ).fetchone()


def verify_admin(username, password):
    if not username or not password or len(username) > 128:
        return False
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        return False
    user = find_user(username)
    hashed = (user.get("password") or "").encode("utf-8") if user else DUMMY_HASH
    try:
        matches = bcrypt.checkpw(encoded, hashed)
    except ValueError:
        return False
    return bool(
        matches
        and user
        and user["role"] >= 10
        and user["status"] == 1
        and user["deleted_at"] is None
    )


def verify_api_key(value):
    """Accept an active New API key without creating another credential store."""
    if not value or len(value) > 256:
        return False
    key = value[3:] if value.startswith("sk-") else value
    if not key or len(key) > 128:
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as conn:
        row = conn.execute(
            """SELECT t.id FROM tokens t JOIN users u ON u.id=t.user_id
               WHERE t.key=%s AND t.status=1 AND t.deleted_at IS NULL
                 AND (t.expired_time IS NULL OR t.expired_time=-1 OR t.expired_time>%s)
                 AND u.status=1 AND u.deleted_at IS NULL LIMIT 1""",
            (key, now),
        ).fetchone()
    return row is not None
