"""DingTalk enterprise application bot: cached access token and group messages."""

import hashlib
import json
import re
import threading
import time
from urllib import request
from urllib.error import HTTPError

from .base import DeliveryError

BASE = "https://api.dingtalk.com"
DISPLAY_NAME = "钉钉企业内部应用机器人"
_tokens = {}
_lock = threading.Lock()


def validate(config, secret):
    """Validate only the group-message fields used by this integration."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,256}", config.get("app_id", "")):
        raise ValueError("请填写有效的 Client ID。")
    if not secret or len(secret) > 512:
        raise ValueError("请填写 Client Secret。")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", config.get("robot_code", "")):
        raise ValueError("请填写有效的 Robot Code。")
    if config.get("receive_id_type") != "open_conversation_id":
        raise ValueError("钉钉企业机器人仅支持群聊 openConversationId。")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{4,512}", config.get("receive_id", "")):
        raise ValueError("请填写有效的 openConversationId。")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post(path, body, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["x-acs-dingtalk-access-token"] = token
    req = request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers=headers,
        method="POST",
    )
    status = None
    try:
        try:
            response = request.build_opener(NoRedirect()).open(req, timeout=8)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(1024 * 1024)
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError()
    except Exception:
        detail = f"（HTTP {status}）" if isinstance(status, int) else ""
        raise DeliveryError(
            "钉钉连接或响应异常" + detail + "，请检查网络和应用配置。"
        ) from None
    if not 200 <= status < 300:
        code = result.get("code") or result.get("errcode")
        suffix = f"，错误码 {code}" if isinstance(code, (str, int)) else ""
        raise DeliveryError(
            f"钉钉请求失败（HTTP {status}{suffix}），请检查应用凭证、权限、发布状态及群聊配置。"
        )
    for key in ("code", "errcode"):
        code = result.get(key)
        if code not in (None, 0, "0"):
            raise DeliveryError(
                f"钉钉发送失败，错误码 {code}；请检查应用凭证、权限、发布状态及群聊配置。"
            )
    if result.get("success") is False:
        raise DeliveryError("钉钉发送失败，请检查应用凭证、权限、发布状态及群聊配置。")
    return result


def _access_token(app_id, secret):
    key = (app_id, hashlib.sha256(secret.encode()).hexdigest())
    with _lock:
        cached = _tokens.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        result = _post(
            "/v1.0/oauth2/accessToken", {"appKey": app_id, "appSecret": secret}
        )
        token, expire = result.get("accessToken"), result.get("expireIn")
        if (
            not isinstance(token, str)
            or not token
            or type(expire) is not int
            or expire <= 0
        ):
            raise DeliveryError("钉钉未返回有效的访问凭证。")
        _tokens.clear()
        _tokens[key] = (token, time.monotonic() + max(0, expire - 120))
        return token


def send(config, secret, text):
    validate(config, secret)
    token = _access_token(config["app_id"], secret)
    _post(
        "/v1.0/robot/groupMessages/send",
        {
            "robotCode": config["robot_code"],
            "openConversationId": config["receive_id"],
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": text}, ensure_ascii=False),
        },
        token,
    )
