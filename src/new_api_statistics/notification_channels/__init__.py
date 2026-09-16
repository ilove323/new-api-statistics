"""One implementation per notification channel; the registry is server-controlled."""

from . import dingtalk_app, feishu_app

CHANNELS = {"feishu_app": feishu_app, "dingtalk_app": dingtalk_app}
