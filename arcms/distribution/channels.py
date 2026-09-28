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
    if not push_endpoint_allowed(subscription.endpoint) or not push_keys_valid(subscription.p256dh, subscription.auth):
        raise SubscriptionGone()  # اشتراك مشوّه (أو سابق للتحقق): يُعطَّل ولا يُرسل إليه
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
        if status in (404, 410, 400, 403, 413):
            raise SubscriptionGone() from exc
        raise RetryableError(str(exc)[:200]) from exc
    except requests.RequestException as exc:
        raise RetryableError(str(exc)[:200]) from exc
    except Exception as exc:  # أي خطأ غير متوقع يُحتسب فشلاً لهذا الاشتراك وحده ولا يُسقط الإرسال للبقية
        log.warning("push failed for subscription %s: %s", subscription.pk, type(exc).__name__)
        raise RetryableError(type(exc).__name__) from exc


# خدمات الإشعارات الفعلية في المتصفحات. لا نقبل عنوان اشتراك خارجها، فلا يصبح
# الخادم أداة لطلب عناوين داخلية أو بطيئة عمداً.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


def push_endpoint_allowed(endpoint: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443):
        return False
    allowed = PUSH_SERVICE_HOSTS + tuple(getattr(settings, "ARCMS_PUSH_EXTRA_HOSTS", ()))
    return any(host == h or host.endswith("." + h) for h in allowed)


def push_keys_valid(p256dh: str, auth: str) -> bool:
    import base64
    import binascii

    def decode(value: str) -> bytes | None:
        if not isinstance(value, str) or not value or len(value) > 200:
            return None
        try:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (binascii.Error, ValueError):
            return None

    key, secret = decode(p256dh), decode(auth)
    return key is not None and len(key) == 65 and key[0] == 4 and secret is not None and len(secret) == 16


# --- صفحة فيسبوك (Graph API) ---

_FB_RETRY_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80001}


def _facebook_error(resp) -> Exception:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    message = str(err.get("message") or f"HTTP {resp.status_code}")[:200]
    if code in _FB_RETRY_CODES or resp.status_code >= 500:
        return RetryableError(f"فيسبوك: {message}")
    return PermanentError(f"فيسبوك رفض النشر ({code}): {message}")


def _facebook_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.ARCMS_FACEBOOK_API_VERSION}/{path}"


def facebook_post(message: str, link: str) -> str:
    if not (settings.ARCMS_FACEBOOK_PAGE_ID and settings.ARCMS_FACEBOOK_PAGE_TOKEN):
        raise PermanentError("بيانات صفحة فيسبوك غير مضبوطة (ARCMS_FACEBOOK_PAGE_ID وARCMS_FACEBOOK_PAGE_TOKEN).")
    try:
        resp = requests.post(
            _facebook_url(f"{settings.ARCMS_FACEBOOK_PAGE_ID}/feed"),
            data={"message": message, "link": link, "access_token": settings.ARCMS_FACEBOOK_PAGE_TOKEN},
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise RetryableError(f"فيسبوك: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise _facebook_error(resp)
    return str(resp.json().get("id", ""))


def facebook_check() -> str:
    """يتحقق من الرمز ويعيد اسم الصفحة، دون نشر شيء."""
    if not (settings.ARCMS_FACEBOOK_PAGE_ID and settings.ARCMS_FACEBOOK_PAGE_TOKEN):
        raise PermanentError("بيانات صفحة فيسبوك غير مضبوطة.")
    resp = requests.get(
        _facebook_url(settings.ARCMS_FACEBOOK_PAGE_ID),
        params={"fields": "name", "access_token": settings.ARCMS_FACEBOOK_PAGE_TOKEN},
        timeout=_timeout(),
    )
    if resp.status_code != 200:
        raise _facebook_error(resp)
    return str(resp.json().get("name", ""))


# --- إكس (X API v2 بتوقيع OAuth 1.0a لحساب المؤسسة) ---

X_API = "https://api.x.com/2"


def _pct(value) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="~-._")


def oauth1_header(method: str, url: str, *, consumer_key: str, consumer_secret: str, token: str, token_secret: str,
                  extra_params: dict | None = None, nonce: str | None = None, timestamp: str | None = None) -> str:
    """ترويسة Authorization بتوقيع HMAC-SHA1 (RFC 5849). extra_params لمعاملات النموذج المشمولة بالتوقيع."""
    import base64
    import hashlib
    import hmac
    import secrets
    import time

    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    params = {**oauth, **(extra_params or {})}
    param_str = "&".join(f"{k}={v}" for k, v in sorted((_pct(k), _pct(v)) for k, v in params.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def _x_credentials() -> dict:
    creds = {
        "consumer_key": settings.ARCMS_X_API_KEY,
        "consumer_secret": settings.ARCMS_X_API_SECRET,
        "token": settings.ARCMS_X_ACCESS_TOKEN,
        "token_secret": settings.ARCMS_X_ACCESS_SECRET,
    }
    if not all(creds.values()):
        raise PermanentError("مفاتيح حساب إكس غير مضبوطة (ARCMS_X_API_KEY/SECRET وARCMS_X_ACCESS_TOKEN/SECRET).")
    return creds


def _x_error(resp) -> Exception:
    try:
        data = resp.json()
        detail = str(data.get("detail") or data.get("title") or data)[:200]
    except ValueError:
        detail = f"HTTP {resp.status_code}"
    if resp.status_code == 429 or resp.status_code >= 500:
        return RetryableError(f"إكس: {detail}")
    return PermanentError(f"إكس رفض النشر ({resp.status_code}): {detail}")


def x_post(text: str) -> str:
    url = f"{X_API}/tweets"
    headers = {"Authorization": oauth1_header("POST", url, **_x_credentials()), "Content-Type": "application/json"}
    try:
        resp = requests.post(url, data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                             headers=headers, timeout=_timeout())
    except requests.RequestException as exc:
        raise RetryableError(f"إكس: {type(exc).__name__}") from exc
    if resp.status_code not in (200, 201):
        raise _x_error(resp)
    return str(resp.json().get("data", {}).get("id", ""))


def x_check() -> str:
    url = f"{X_API}/users/me"
    resp = requests.get(url, headers={"Authorization": oauth1_header("GET", url, **_x_credentials())}, timeout=_timeout())
    if resp.status_code != 200:
        raise _x_error(resp)
    return "@" + str(resp.json().get("data", {}).get("username", ""))


X_LIMIT = 280
X_URL_LENGTH = 23  # كل رابط يُحسب 23 حرفاً مهما طال


def social_text(*, title: str, summary: str, url: str, kicker: str = "", limit: int | None = None) -> str:
    """نص عادي (بلا HTML) للمنصات الاجتماعية، يُقصّ ليتسع للرابط ضمن حد الأحرف إن وُجد."""
    head = f"{kicker} | " if kicker else ""
    body = f"{head}{title}"
    if summary and limit is None:
        body += f"\n\n{summary}"
    if limit:
        room = limit - X_URL_LENGTH - 2
        if len(body) > room:
            body = body[: room - 1].rstrip() + "…"
    return f"{body}\n\n{url}"


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
