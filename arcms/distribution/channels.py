"""عملاء القنوات الخارجية. كل دالة ترفع PermanentError حين لا تفيد الإعادة."""

from __future__ import annotations

import json
import logging
import re
from html import escape

import requests
from django.conf import settings

from arcms.core.jobs import PermanentError

log = logging.getLogger("arcms.distribution")


class RetryableError(Exception):
    pass


def _timeout() -> int:
    return settings.ARCMS_HTTP_TIMEOUT


# --- تيليجرام (Bot API) ---


def telegram_api(method: str, data: dict) -> dict:
    token = settings.ARCMS_TELEGRAM_BOT_TOKEN
    if not token:
        raise PermanentError("رمز بوت تيليجرام غير مضبوط (ARCMS_TELEGRAM_BOT_TOKEN).")
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=data, timeout=_timeout())
    except requests.RequestException as exc:
        raise RetryableError(f"تعذّر الاتصال بتيليجرام: {exc.__class__.__name__}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {"ok": False, "description": resp.text[:200]}
    if resp.status_code == 429:
        retry = body.get("parameters", {}).get("retry_after", 30)
        raise RetryableError(f"تيليجرام يطلب التمهل {retry} ثانية")
    if resp.status_code >= 500:
        raise RetryableError(f"خطأ من خادم تيليجرام {resp.status_code}")
    if not body.get("ok"):
        # 400/401/403: رمز خاطئ، أو البوت ليس مشرفاً في القناة، أو معرّف قناة خاطئ.
        raise PermanentError(f"تيليجرام رفض الطلب: {body.get('description', resp.status_code)}")
    return body.get("result", {})


def telegram_send(chat_id: str, html_text: str, photo_url: str | None = None, silent: bool = False) -> str:
    if photo_url and len(html_text) <= 1024:
        result = telegram_api(
            "sendPhoto",
            {
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": html_text,
                "parse_mode": "HTML",
                "disable_notification": silent,
            },
        )
    else:
        result = telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html_text[:4096],
                "parse_mode": "HTML",
                "disable_notification": silent,
                "link_preview_options": {"is_disabled": False, "prefer_large_media": True},
            },
        )
    return str(result.get("message_id", ""))


def telegram_check() -> str:
    me = telegram_api("getMe", {})
    return f"@{me.get('username', '?')}"


# --- واتساب (WhatsApp Business Cloud API) ---


def whatsapp_send_template(phone: str, template: str, language: str, params: list[str]) -> str:
    token = settings.ARCMS_WHATSAPP_TOKEN
    phone_id = settings.ARCMS_WHATSAPP_PHONE_NUMBER_ID
    if not token or not phone_id:
        raise PermanentError("بيانات واتساب غير مضبوطة (ARCMS_WHATSAPP_TOKEN و ARCMS_WHATSAPP_PHONE_NUMBER_ID).")
    if not template:
        raise PermanentError("لم يُحدَّد قالب واتساب معتمد في إعدادات القناة.")
    url = f"https://graph.facebook.com/{settings.ARCMS_WHATSAPP_API_VERSION}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": re.sub(r"\D", "", phone),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language or "ar"},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in params]}
            ],
        },
    }
    try:
        resp = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=_timeout())
    except requests.RequestException as exc:
        raise RetryableError(f"تعذّر الاتصال بواتساب: {exc.__class__.__name__}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"واتساب مشغول ({resp.status_code})")
    if resp.status_code >= 400:
        err = body.get("error", {})
        raise PermanentError(f"واتساب رفض الرسالة: {err.get('message', resp.status_code)}")
    messages = body.get("messages") or [{}]
    return str(messages[0].get("id", ""))


# --- إشعارات المتصفح (Web Push / VAPID) ---


class SubscriptionGone(Exception):
    pass


def push_send(subscription, payload: dict) -> None:
    from pywebpush import WebPushException, webpush

    if not settings.ARCMS_VAPID_PRIVATE_KEY:
        raise PermanentError("مفاتيح VAPID غير مضبوطة (ARCMS_VAPID_PRIVATE_KEY).")
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.ARCMS_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.ARCMS_VAPID_SUBJECT},
            ttl=6 * 3600,
            timeout=_timeout(),
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            raise SubscriptionGone() from exc
        raise RetryableError(str(exc)[:200]) from exc


# --- صياغة الرسائل ---


def render_message(template: str, *, title: str, summary: str, url: str, kicker: str = "", category: str = "") -> str:
    template = template or "{kicker}<b>{title}</b>\n\n{summary}\n\n{url}"
    values = {
        "title": escape(title),
        "summary": escape(summary),
        "url": url,
        "kicker": f"{escape(kicker)} | " if kicker else "",
        "category": escape(category),
    }
    try:
        text = template.format(**values)
    except (KeyError, IndexError, ValueError):
        text = "{kicker}<b>{title}</b>\n\n{summary}\n\n{url}".format(**values)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
