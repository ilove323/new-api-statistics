#!/usr/bin/env python3
# Usage: PGHOST=... PGPASSWORD=... python -m new_api_statistics.app
# Production: docker compose up -d --build (Gunicorn serves /statistics/).
"""Single-container statistics page and authenticated read-only API."""

import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, jsonify, render_template, request, send_file
from flask.json.provider import DefaultJSONProvider
import psycopg
from new_api_statistics import balance
from new_api_statistics import notifications

from new_api_statistics.report import (
    TZ,
    export_excel,
    load_report,
    totals,
    parse_boundary,
    rankings,
    load_site_name,
)
from new_api_statistics.auth import verify_admin

app = Flask(__name__, static_url_path="/statistics/static")


class JSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


app.json = JSONProvider(app)


@app.before_request
def authenticate():
    if request.path == "/healthz":
        return None
    auth = request.authorization
    if (
        not auth
        or auth.type != "basic"
        or not verify_admin(auth.username, auth.password)
    ):
        return (
            "请使用 New API 管理员用户名和密码登录。",
            401,
            {"WWW-Authenticate": 'Basic realm="New API Statistics", charset="UTF-8"'},
        )


@app.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    )
    return response


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/statistics")
@app.get("/statistics/")
def index():
    now = datetime.now(TZ).replace(microsecond=0)
    return render_template(
        "index.html",
        site_name=load_site_name(),
        start=now.replace(day=1, hour=0, minute=0, second=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        end=now.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def selected():
    start, end = request.args.get("start", ""), request.args.get("end", "")
    rows = load_report(start, end)
    user = request.args.get("user", "").strip()
    if user:
        rows = [r for r in rows if r["username"] == user]
    model = request.args.get("model", "").strip()
    if model:
        rows = [r for r in rows if r["model_name"] == model]
    return start, end, rows


@app.get("/statistics/api/usage")
def usage():
    start, end, rows = selected()
    return jsonify(
        start=start,
        end=end,
        rows=rows,
        totals=totals(rows, start, end),
        rankings=rankings(rows),
        updated_at=datetime.now(TZ).isoformat(timespec="seconds"),
    )


@app.get("/statistics/api/export")
def export():
    start, end, rows = selected()
    first = parse_boundary(start).strftime("%Y-%m-%d_%H-%M-%S")
    last = parse_boundary(end, end=True).strftime("%Y-%m-%d_%H-%M-%S")
    return send_file(
        export_excel(rows, start, end),
        as_attachment=True,
        download_name=f"usage_{first}_{last}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def monitor_write_allowed():
    # Basic credentials are ambient. A custom header plus JSON blocks cross-site forms.
    return (
        request.headers.get("X-Statistics-Request") == "1"
        and request.is_json
        and request.headers.get("Sec-Fetch-Site") != "cross-site"
    )


@app.get("/statistics/api/balance")
def balance_status():
    return jsonify(balance.snapshot(live=request.args.get("live") == "1"))


@app.get("/statistics/api/balance/alert")
def balance_alert_api():
    try:
        balance.check_once(daily=False)
    except balance.CheckBusy:
        return jsonify(error="其他检查或设置保存正在进行，请稍后重试。"), 409
    alert = notifications.current_alert_record()
    return jsonify(has_alert=alert is not None, alert=alert)


@app.put("/statistics/api/balance/settings")
def balance_settings():
    if not monitor_write_allowed():
        return jsonify(error="不允许的设置请求。"), 403
    try:
        balance.save_settings(request.get_json(), request.authorization.username)
    except balance.SettingsConflict:
        return jsonify(error="设置已被其他管理员修改，请重新打开设置。"), 409
    return jsonify(saved=True)


@app.post("/statistics/api/balance/check")
def balance_check():
    if not monitor_write_allowed():
        return jsonify(error="不允许的检查请求。"), 403
    try:
        checked = balance.check_once(daily=False)
    except balance.CheckBusy:
        return jsonify(error="其他检查或设置保存正在进行，请稍后再次点击铃铛。"), 409
    return jsonify(balance.snapshot(live=not checked))


@app.errorhandler(ValueError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@app.route("/statistics/api/balance/channel", methods=["GET", "PUT"])
def notification_settings():
    if request.method == "GET":
        return jsonify(notifications.snapshot(request.args.get("channel") or None))
    if not monitor_write_allowed():
        return jsonify(error="不允许的设置请求。"), 403
    try:
        notifications.save(request.get_json(), request.authorization.username)
    except balance.SettingsConflict:
        return jsonify(error="渠道配置已改变，请重新打开设置。"), 409
    return jsonify(notifications.snapshot())


@app.post("/statistics/api/balance/channel/test")
def notification_test():
    if not monitor_write_allowed():
        return jsonify(error="不允许的测试请求。"), 403
    body = request.get_json()
    if not isinstance(body, dict) or type(body.get("version")) is not int:
        raise ValueError("请先保存报警渠道。")
    try:
        notifications.deliver(test=True, expected_version=body["version"])
    except balance.SettingsConflict:
        return jsonify(error="渠道配置已改变，请重新打开设置。"), 409
    return jsonify(sent=True)


@app.errorhandler(psycopg.Error)
def database_error(exc):
    app.logger.error("Database query failed: %s", type(exc).__name__)
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return jsonify(error="查询超过 60 秒，请缩小时间范围后重试。"), 504
    return jsonify(error="数据库查询失败，请检查连接配置与数据库日志。"), 503


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8091")))
