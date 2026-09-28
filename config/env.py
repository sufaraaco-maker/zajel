"""قراءة الإعدادات من متغيرات البيئة ومن ملف ‎.env‎ اختياري.

الأسرار (مفاتيح البوتات، كلمات مرور البريد، مفاتيح التشفير) تُقرأ من البيئة
فقط ولا تُخزَّن في قاعدة البيانات، حتى لا تتسرب مع أي تفريغ للبيانات.
يدعم أيضاً صيغة ‎NAME_FILE=/run/secrets/...‎ لأسرار Docker.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get(name: str, default: str | None = None) -> str | None:
    file_path = os.environ.get(f"{name}_FILE")
    if file_path and Path(file_path).is_file():
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    value = get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "نعم"}


def get_int(name: str, default: int) -> int:
    value = get(name)
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def get_list(name: str, default: str = "") -> list[str]:
    value = get(name, default) or ""
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_database_url(url: str, base_dir: Path) -> dict:
    """postgres://user:pass@host:5432/db أو sqlite:///path/to/db.sqlite3"""
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")[0]
    if scheme == "sqlite":
        path = unquote(parsed.path)
        if path.startswith("//"):
            path = path[1:]
        elif path.startswith("/"):
            path = path[1:]
        db_path = Path(path) if Path(path).is_absolute() else base_dir / path
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(db_path),
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        }
    if scheme in {"postgres", "postgresql", "pgsql"}:
        options = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": options,
        }
    raise ValueError(f"نوع قاعدة بيانات غير مدعوم: {scheme}")
