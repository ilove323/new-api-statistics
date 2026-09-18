"""Read-only database aggregates, current prices and numeric Excel output."""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SQL = Path(__file__).with_name("usage.sql").read_text()
TZ = ZoneInfo("Asia/Shanghai")
TOKEN_FIELDS = [
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
]
HEADERS = [
    "用户名",
    "显示名",
    "消费请求数",
    "模型",
    "总Token",
    "输入Token",
    "输出Token",
    "缓存读取Token",
    "缓存写入Token",
    "倍率",
    "输入价格（元/M）",
    "输出价格（元/M）",
    "缓存读价格（元/M）",
    "缓存写价格（元/M）",
    "消费金额（元）",
]
FIELDS = [
    "username",
    "display_name",
    "request_count",
    "model_name",
    "total_tokens",
    *TOKEN_FIELDS,
    "group_ratio",
    "input_price",
    "output_price",
    "cache_price",
    "write_price",
    "amount",
]


def convert_tokens(row):
    """Solve only for cache reads; never overwrite measured usage or money."""
    row.update(
        converted_cache_read_tokens=None,
        converted_total_tokens=None,
        conversion_status="不折算：非两种倍率",
    )
    if row.get("ratio_count") != 2:
        return
    ratio = row["group_ratio"]
    cache_price = row["cache_price"]
    if ratio is None or ratio <= 0 or cache_price is None or cache_price <= 0:
        row["conversion_status"] = "无法折算：倍率或缓存读价格无效"
        return
    fixed = Decimal(0)
    for token, price in [
        ("pricing_input_tokens", "input_price"),
        ("output_tokens", "output_price"),
        ("cache_write_tokens", "write_price"),
    ]:
        count = row.get(token)
        if count is None or count < 0 or (count and row[price] is None):
            row["conversion_status"] = "无法折算：单价缺失或计价用量无效"
            return
        fixed += Decimal(count) * (row[price] or Decimal(0))
    cache = (row["amount"] * 1000000 / ratio - fixed) / cache_price
    total = Decimal(row["total_tokens"]) - row["cache_read_tokens"] + cache
    if cache < 0 or total < 0:
        row["conversion_status"] = "无法折算：唯一解为负数"
        return
    row.update(
        converted_cache_read_tokens=cache,
        converted_total_tokens=total,
        conversion_status="已折算（非原始用量）",
    )


def parse_boundary(value, end=False):
    """Accept second/minute precision; preserve legacy whole-date queries."""
    try:
        if len(value) == 10:
            return datetime.combine(
                date.fromisoformat(value), time(23, 59, 59) if end else time(), TZ
            )
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    except (ValueError, TypeError):
        pass
    raise ValueError("请选择有效的开始、结束时间（北京时间）。")


def period(start, end):
    """Include the selected end second; SQL uses an exclusive upper bound."""
    first, last = parse_boundary(start), parse_boundary(end, end=True)
    if last < first:
        raise ValueError("结束时间不能早于开始时间。")
    if last - first >= timedelta(days=367):
        raise ValueError("单次查询最多 367 天，请缩小时间范围。")
    return int(first.timestamp()), int(last.timestamp()) + 1


def current_prices(model, options):
    """Use configured prices only: missing current prices remain unknown."""

    def value(key):
        raw = options.get(key, {}).get(model)
        return Decimal(str(raw)) if raw is not None else None

    ratio = value("ModelRatio")
    base = ratio * 2 if ratio is not None else None
    result = {"input_price": base}
    for field, key in [
        ("output_price", "CompletionRatio"),
        ("cache_price", "CacheRatio"),
        ("write_price", "CreateCacheRatio"),
    ]:
        multiplier = value(key)
        result[field] = (
            base * multiplier if base is not None and multiplier is not None else None
        )
    # Per-request pricing has no meaningful per-million-token price.
    if value("ModelPrice") is not None:
        result = dict.fromkeys(result)
    return result


def cost_formula(row):
    """Explain displayed-token pricing without treating it as historical billing."""
    terms = []
    ratio = row["group_ratio"]
    for label, token, price in [
        ("输入", "input_tokens", "input_price"),
        ("输出", "output_tokens", "output_price"),
        ("缓存读", "cache_read_tokens", "cache_price"),
        ("缓存写", "cache_write_tokens", "write_price"),
    ]:
        count, unit = row[token], row[price]
        weighted = (
            Decimal(count) * unit
            if unit is not None
            else (Decimal(0) if count == 0 else None)
        )
        terms.append(dict(label=label, tokens=count, price=unit, weighted=weighted))
    calculated = None
    if ratio is not None and all(t["weighted"] is not None for t in terms):
        calculated = sum((t["weighted"] for t in terms), Decimal(0)) * ratio / 1000000
    return dict(
        terms=terms,
        ratio=ratio,
        calculated=calculated,
        difference=row["amount"] - calculated if calculated is not None else None,
        converted=row.get("converted_cache_read_tokens") is not None,
        ratio_count=row.get("ratio_count"),
    )


def decorate(rows, options):
    for row in rows:
        row["display_name"] = (row.get("display_name") or "").strip()
        for field in ["total_tokens", "request_count", *TOKEN_FIELDS]:
            row[field] = int(row[field])
        row.update(current_prices(row["model_name"], options))
        convert_tokens(row)
        if row["converted_cache_read_tokens"] is not None:
            cache = int(
                row["converted_cache_read_tokens"].quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
            row["converted_cache_read_tokens"] = cache
            row["converted_total_tokens"] = (
                row["total_tokens"] - row["cache_read_tokens"] + cache
            )
        if row.get("pricing_input_tokens") is not None:
            row["pricing_input_tokens"] = int(row["pricing_input_tokens"])
        # Preserve source values for auditing; use one consistent report basis.
        for field in ("cache_read_tokens", "total_tokens"):
            row["original_" + field] = row[field]
            converted = row["converted_" + field]
            if converted is not None:
                row[field] = converted
        row["cost_formula"] = cost_formula(row)
    return rows


def load_site_name():
    """Read New API branding on page load so configuration changes take effect."""
    with psycopg.connect(
        connect_timeout=8,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    ) as conn:
        row = conn.execute(
            "SELECT value FROM options WHERE key = %s", ("SystemName",)
        ).fetchone()
    return (row[0] or "").strip() if row and (row[0] or "").strip() else "New API"


def load_report(start, end, *, by_token=False, token_ids=None, groups=None):
    """Load either user-model totals or user-token-model totals read-only."""
    first, last = period(start, end)
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        rows = conn.execute(
            SQL,
            {
                "start": first,
                "end": last,
                "by_token": by_token,
                "token_ids": token_ids,
                "groups": groups,
            },
        ).fetchall()
        user_ids = list({row["user_id"] for row in rows})
        names = {}
        if user_ids:
            display_rows = conn.execute(
                "SELECT id, COALESCE(display_name, '') AS display_name FROM users WHERE id = ANY(%s)",
                (user_ids,),
            ).fetchall()
            names = {record["id"]: record["display_name"] for record in display_rows}
        for row in rows:
            row["display_name"] = names.get(row["user_id"], "")
        records = conn.execute(
            "SELECT key, value FROM options WHERE key = ANY(%s)",
            (
                [
                    "ModelRatio",
                    "CompletionRatio",
                    "CacheRatio",
                    "CreateCacheRatio",
                    "ModelPrice",
                ],
            ),
        ).fetchall()
    options = {r["key"]: json.loads(r["value"] or "{}") for r in records}
    return decorate(rows, options)


def load_token_options(start, end):
    """Return the latest name of each token used in the selected interval."""
    first, last = period(start, end)
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        return conn.execute(
            """SELECT DISTINCT ON (token_id) token_id,
                      COALESCE(NULLIF(btrim(token_name), ''), '未知令牌') AS token_name
               FROM logs
               WHERE type=2 AND created_at >= %s AND created_at < %s
               ORDER BY token_id, created_at DESC, id DESC""",
            (first, last),
        ).fetchall()


def load_group_options(start, end):
    """Return groups used in the selected interval."""
    first, last = period(start, end)
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        return conn.execute(
            """SELECT DISTINCT COALESCE("group", '') AS group_name
               FROM logs
               WHERE type=2 AND created_at >= %s AND created_at < %s
               ORDER BY group_name""",
            (first, last),
        ).fetchall()


def totals(rows, start=None, end=None):
    tokens = sum(r["total_tokens"] for r in rows)
    amount = sum((r["amount"] for r in rows), Decimal(0))
    result = {
        "total_tokens": tokens,
        "amount": amount,
        "request_count": sum(r["request_count"] for r in rows),
        "users": len({r["user_id"] for r in rows}),
        "models": len({r["model_name"] for r in rows}),
        **{field: sum(r[field] for r in rows) for field in TOKEN_FIELDS},
    }
    if start is not None and end is not None:
        first, last = period(start, end)
        # Use the entire selected interval, including idle time and the end second.
        seconds = last - first
        result.update(
            duration_seconds=seconds,
            tpm=Decimal(tokens) * 60 / seconds,
            rpm=Decimal(result["request_count"]) * 60 / seconds,
        )
    return result


def rankings(rows):
    """Aggregate before ranking; normalized totals avoid counting caches twice."""
    users, models = {}, {}
    for row in rows:
        key = (row["user_id"], row["username"])
        user = users.setdefault(
            key,
            dict(
                user_id=key[0],
                username=key[1],
                display_name=row.get("display_name", ""),
                amount=Decimal(0),
                total_tokens=0,
                **dict.fromkeys(TOKEN_FIELDS, 0),
            ),
        )
        for field in ["amount", "total_tokens", *TOKEN_FIELDS]:
            user[field] += row[field]
        models[row["model_name"]] = (
            models.get(row["model_name"], Decimal(0)) + row["amount"]
        )
    return {
        "model_amount": sorted(
            (dict(model_name=k, amount=v) for k, v in models.items()),
            key=lambda r: (-r["amount"], r["model_name"]),
        ),
        "user_tokens": sorted(
            users.values(),
            key=lambda r: (-r["total_tokens"], r["username"], r["user_id"]),
        ),
        "user_amount": sorted(
            users.values(), key=lambda r: (-r["amount"], r["username"], r["user_id"])
        ),
    }


def export_excel(rows, start, end):
    """Merge user cells, retain numeric prices and calculate totals with SUM."""
    wb = Workbook()
    ws = wb.active
    ws.title = "用户模型用量"
    first, last = parse_boundary(start), parse_boundary(end, end=True)
    ws.append(
        [
            "统计时间",
            f"{first:%Y-%m-%d %H:%M:%S} 至 {last:%Y-%m-%d %H:%M:%S}（北京时间）",
        ]
    )
    ws.merge_cells("C1:O1")
    ws.append(HEADERS)
    for r in rows:
        values = [r.get(f) for f in FIELDS]
        ws.append(values)
        # Treat database names literally, including strings starting with '='.
        for col in (1, 2, 4):
            ws.cell(ws.max_row, col).data_type = "s"
    last = ws.max_row
    ws.append(["总计"])
    for col in (3, 5, 6, 7, 8, 9, 15):
        letter = get_column_letter(col)
        ws.cell(last + 1, col, f"=SUM({letter}3:{letter}{last})" if rows else 0)
    begin = 3
    for i in range(1, len(rows) + 1):
        if i == len(rows) or (rows[i]["user_id"], rows[i]["username"]) != (
            rows[i - 1]["user_id"],
            rows[i - 1]["username"],
        ):
            if i + 2 > begin:
                ws.merge_cells(
                    start_row=begin, end_row=i + 2, start_column=1, end_column=1
                )
                ws.merge_cells(
                    start_row=begin, end_row=i + 2, start_column=2, end_column=2
                )
            begin = i + 3
    for cells in ws.iter_rows(min_row=3):
        for c in cells:
            c.alignment = Alignment(vertical="center")
            if c.column not in (1, 2, 4):
                c.number_format = (
                    "#,##0" if c.column in (3, 5, 6, 7, 8, 9) else "#,##0.######"
                )
    for c in ws[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="156B59")
    for i, width in enumerate(
        [24, 24, 18, 36, 30, 22, 22, 30, 22, 12, 24, 24, 24, 24, 22], 1
    ):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "E3"
    ws.auto_filter.ref = f"A2:O{last}"
    ranked = rankings(rows)
    for title, key, columns in [
        (
            "模型消费",
            "model_amount",
            [("模型", "model_name"), ("消费金额（元）", "amount")],
        ),
        (
            "用户Token用量",
            "user_tokens",
            [
                ("用户名", "username"),
                ("显示名", "display_name"),
                ("总Token", "total_tokens"),
                ("输入Token", "input_tokens"),
                ("输出Token", "output_tokens"),
                ("缓存读取Token", "cache_read_tokens"),
                ("缓存写入Token", "cache_write_tokens"),
            ],
        ),
        (
            "用户消费",
            "user_amount",
            [
                ("用户名", "username"),
                ("显示名", "display_name"),
                ("消费金额（元）", "amount"),
            ],
        ),
    ]:
        sheet = wb.create_sheet(title)
        sheet.append(["统计时间", ws["B1"].value])
        if key == "user_tokens":
            sheet.cell(1, 3, ws["C1"].value)
            sheet.merge_cells("C1:H1")
        sheet.append(["序号", *[label for label, _ in columns]])
        for rank, row in enumerate(ranked[key], 1):
            sheet.append([rank, *[row[field] for _, field in columns]])
            for col, (_, field) in enumerate(columns, 2):
                if field in ("username", "display_name"):
                    sheet.cell(sheet.max_row, col).data_type = "s"
        end_row = sheet.max_row
        sheet.append(["总计"])
        for col, (_, field) in enumerate(columns, 2):
            if field not in ("username", "display_name"):
                letter = get_column_letter(col)
                sheet.cell(
                    end_row + 1,
                    col,
                    f"=SUM({letter}3:{letter}{end_row})" if ranked[key] else 0,
                )
        for col, (_, field) in enumerate(columns, 2):
            sheet.column_dimensions[get_column_letter(col)].width = (
                36 if field == "username" else 24
            )
            for line in range(3, sheet.max_row + 1):
                sheet.cell(line, col).number_format = (
                    "#,##0.######" if field == "amount" else "#,##0"
                )
        for c in sheet[2]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="156B59")
        sheet.freeze_panes = "D3" if key.startswith("user_") else "C3"
        sheet.auto_filter.ref = f"A2:{get_column_letter(len(columns) + 1)}{end_row}"
    summary = wb.create_sheet("区间汇总")
    summary.append(["统计时间", ws["B1"].value])
    summary.cell(1, 3, ws["C1"].value)
    summary.column_dimensions["C"].width = 60
    summary.append(["指标", "数值"])
    for title, col in [
        ("消费金额（元）", "O"),
        ("总Token", "E"),
        ("输入Token", "F"),
        ("输出Token", "G"),
        ("缓存读取Token", "H"),
        ("缓存写入Token", "I"),
        ("消费请求数", "C"),
    ]:
        summary.append([title, f"='用户模型用量'!{col}{last + 1}"])
    first_second, last_second = period(start, end)
    summary.append(["区间分钟数", f"=({last_second}-{first_second})/60"])
    summary.append(["区间平均 TPM", "=B4/B10"])
    summary.append(["区间平均 RPM", "=B9/B10"])
    summary.column_dimensions["A"].width = 26
    summary.column_dimensions["B"].width = 62
    for row in range(3, 13):
        summary.cell(row, 2).number_format = (
            "#,##0" if row in (4, 5, 6, 7, 8, 9) else "#,##0.######"
        )
    for c in summary[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="156B59")
    summary.freeze_panes = "B3"
    result = BytesIO()
    wb.save(result)
    result.seek(0)
    return result
