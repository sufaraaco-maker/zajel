"""نسخة خارج الخادم: رفع النسخ الاحتياطية المشفّرة إلى تخزين متوافق مع S3.

يعمل مع AWS S3 وCloudflare R2 وBackblaze B2 وWasabi وMinIO. النسخة مشفّرة قبل
أن تغادر الخادم بالمفتاح العام، فالمزوّد لا يقرأ منها شيئاً. التوقيع AWS SigV4
مكتوب بالمكتبة القياسية ومختبر بأمثلة AWS المنشورة.

افتراضياً لا يحذف الخادم أي نسخة بعيدة (ARCMS_OFFSITE_KEEP=0): من يخترق الخادم
لا يستطيع مسح النسخ البعيدة إن كان مفتاح الوصول بلا صلاحية حذف. الاحتفاظ يُضبط
بقاعدة دورة حياة في الحاوية نفسها.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests
from django.conf import settings

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
PART_SIZE = 64 * 1024 * 1024  # أجزاء الرفع المتعدد (الحد الأدنى في S3 خمسة ميغابايت)
SINGLE_PUT_LIMIT = PART_SIZE


class OffsiteError(Exception):
    pass


def configured() -> bool:
    return bool(settings.ARCMS_OFFSITE_ENDPOINT and settings.ARCMS_OFFSITE_BUCKET
                and settings.ARCMS_OFFSITE_ACCESS_KEY and settings.ARCMS_OFFSITE_SECRET_KEY)


# --- التوقيع SigV4 ---


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def sign_v4(method: str, url: str, *, region: str, service: str, access_key: str, secret_key: str,
            headers: dict[str, str] | None = None, payload_hash: str = EMPTY_SHA256,
            now: dt.datetime | None = None, s3: bool = True) -> dict[str, str]:
    """يعيد الترويسات الموقّعة (Authorization وx-amz-date وx-amz-content-sha256 وHost)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    parts = urlsplit(url)
    host = parts.netloc
    hdrs = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    hdrs["host"] = host
    hdrs["x-amz-date"] = amz_date
    if s3:
        hdrs["x-amz-content-sha256"] = payload_hash
    # S3 لا يعيد ترميز المسار؛ الخدمات الأخرى تُرمّزه مرتين (كما في مجموعة اختبارات AWS)
    path = parts.path or "/"
    canonical_uri = path if s3 else _uri_encode(path, keep_slash=True)
    query = []
    for item in filter(None, parts.query.split("&")):
        k, _, v = item.partition("=")
        query.append((_uri_encode(requests.utils.unquote(k), keep_slash=False),
                      _uri_encode(requests.utils.unquote(v), keep_slash=False)))
    canonical_query = "&".join(f"{k}={v}" for k, v in sorted(query))
    names = sorted(hdrs)
    canonical_headers = "".join(f"{n}:{' '.join(hdrs[n].split())}\n" for n in names)
    signed_headers = ";".join(names)
    canonical = "\n".join([method.upper(), canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _sign(_sign(_sign(_sign(("AWS4" + secret_key).encode(), date), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    return hdrs


# --- العميل ---


def _object_url(key: str = "", query: str = "") -> str:
    endpoint = settings.ARCMS_OFFSITE_ENDPOINT.rstrip("/")
    bucket = settings.ARCMS_OFFSITE_BUCKET
    path = "/" + _uri_encode(key, keep_slash=True) if key else "/"
    if settings.ARCMS_OFFSITE_VIRTUAL_HOST:
        scheme, _, host = endpoint.partition("://")
        url = f"{scheme}://{bucket}.{host}{path}"
    else:
        url = f"{endpoint}/{bucket}{path if key else ''}"
    return url + (f"?{query}" if query else "")


def _request(method: str, url: str, *, data: bytes = b"", headers: dict | None = None, ok=(200,)) -> requests.Response:
    payload_hash = hashlib.sha256(data).hexdigest()
    signed = sign_v4(
        method, url, region=settings.ARCMS_OFFSITE_REGION, service="s3",
        access_key=settings.ARCMS_OFFSITE_ACCESS_KEY, secret_key=settings.ARCMS_OFFSITE_SECRET_KEY,
        headers=headers, payload_hash=payload_hash,
    )
    signed.pop("host", None)  # تضيفه مكتبة requests
    try:
        resp = requests.request(method, url, data=data or None, headers=signed, timeout=max(60, settings.ARCMS_HTTP_TIMEOUT))
    except requests.RequestException as exc:
        raise OffsiteError(f"تعذّر الاتصال بالتخزين البعيد: {type(exc).__name__}") from exc
    if resp.status_code not in ok:
        raise OffsiteError(f"رفض التخزين البعيد الطلب ({resp.status_code}): {_s3_message(resp)}")
    return resp


def _s3_message(resp) -> str:
    try:
        root = ElementTree.fromstring(resp.content)
        code = root.findtext("Code") or ""
        message = root.findtext("Message") or ""
        return f"{code} {message}".strip()[:200]
    except ElementTree.ParseError:
        return (resp.text or "")[:200]


def object_key(filename: str) -> str:
    prefix = settings.ARCMS_OFFSITE_PREFIX.strip("/")
    return f"{prefix}/{filename}" if prefix else filename


def upload(path: Path, key: str) -> int:
    """يرفع الملف (برفع متعدد الأجزاء إن كبر) ويتحقق من حجمه بعد الرفع. يعيد الحجم."""
    size = path.stat().st_size
    if size <= SINGLE_PUT_LIMIT:
        _request("PUT", _object_url(key), data=path.read_bytes(),
                 headers={"content-type": "application/octet-stream"})
    else:
        _multipart(path, key)
    head = _request("HEAD", _object_url(key))
    remote = int(head.headers.get("Content-Length", "-1"))
    if remote != size:
        raise OffsiteError(f"حجم النسخة البعيدة ({remote}) لا يطابق المحلية ({size}).")
    return size


def _multipart(path: Path, key: str) -> None:
    resp = _request("POST", _object_url(key, "uploads="), headers={"content-type": "application/octet-stream"})
    upload_id = _xml_text(resp.content, "UploadId")
    if not upload_id:
        raise OffsiteError("لم يُعد التخزين البعيد معرّف الرفع المتعدد.")
    etags = []
    try:
        with open(path, "rb") as fh:
            number = 1
            while chunk := fh.read(PART_SIZE):
                part = _request("PUT", _object_url(key, f"partNumber={number}&uploadId={quote(upload_id, safe='')}"), data=chunk)
                etags.append((number, part.headers.get("ETag", "")))
                number += 1
        body = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>" for n, etag in etags
        ) + "</CompleteMultipartUpload>"
        _request("POST", _object_url(key, f"uploadId={quote(upload_id, safe='')}"), data=body.encode(),
                 headers={"content-type": "application/xml"})
    except OffsiteError:
        try:  # لا تبقى أجزاء يتيمة تُحتسب في الفاتورة
            _request("DELETE", _object_url(key, f"uploadId={quote(upload_id, safe='')}"), ok=(200, 204))
        except OffsiteError:
            pass
        raise


def _xml_text(content: bytes, tag: str) -> str:
    root = ElementTree.fromstring(content)
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el.text or ""
    return ""


def list_keys() -> list[str]:
    prefix = settings.ARCMS_OFFSITE_PREFIX.strip("/")
    query = "list-type=2" + (f"&prefix={quote(prefix + '/', safe='')}" if prefix else "")
    resp = _request("GET", _object_url("", query))
    root = ElementTree.fromstring(resp.content)
    return [el.text for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "Key" and el.text]


def rotate_remote() -> int:
    """يحذف ما زاد على ARCMS_OFFSITE_KEEP من النسخ البعيدة؛ صفر = لا حذف أبداً (الافتراضي)."""
    keep = settings.ARCMS_OFFSITE_KEEP
    if keep <= 0:
        return 0
    keys = sorted((k for k in list_keys() if k.endswith(".arcbak")), reverse=True)
    removed = 0
    for key in keys[keep:]:
        _request("DELETE", _object_url(key), ok=(200, 204))
        removed += 1
    return removed


def check() -> str:
    """يتحقق من الوصول إلى الحاوية دون رفع شيء."""
    _request("HEAD", _object_url(""))
    return settings.ARCMS_OFFSITE_BUCKET
