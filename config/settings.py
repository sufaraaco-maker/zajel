"""إعدادات arcms. كل ما يخص المؤسسة يأتي من البيئة (انظر ‎.env.example‎)."""

from __future__ import annotations

import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from . import env

BASE_DIR = Path(__file__).resolve().parent.parent
env.load_dotenv(BASE_DIR / ".env")

TESTING = "test" in sys.argv[1:2] or env.get_bool("ARCMS_TESTING")

DEBUG = env.get_bool("ARCMS_DEBUG", False)
SECRET_KEY = env.get("ARCMS_SECRET_KEY") or ""
# توليد المفاتيح لا يحتاج مفتاحاً سرياً بعد (هو الذي يولّده).
_KEYGEN = any(cmd in sys.argv for cmd in ("arcms_keys", "arcms_backup_keygen"))
if not SECRET_KEY:
    if DEBUG or TESTING or _KEYGEN:
        SECRET_KEY = "dev-insecure-key-change-me-" + "x" * 30
    else:
        raise ImproperlyConfigured("ARCMS_SECRET_KEY مطلوب في بيئة الإنتاج.")

SITE_URL = (env.get("ARCMS_SITE_URL", "http://localhost:8000") or "").rstrip("/")
ALLOWED_HOSTS = env.get_list("ARCMS_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env.get_list("ARCMS_CSRF_TRUSTED_ORIGINS", SITE_URL)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sitemaps",
    "arcms.accounts",
    "arcms.audit",
    "arcms.core",
    "arcms.content",
    "arcms.distribution",
    "arcms.analytics",
    "arcms.backups",
    "arcms.importer",
    "arcms.tips",
    "arcms.studio",
    "arcms.public",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "arcms.core.middleware.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "arcms.accounts.middleware.TwoFactorMiddleware",
    "arcms.audit.middleware.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "arcms.importer.middleware.LegacyRedirectMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "arcms.core.context_processors.site",
            ],
            "builtins": ["arcms.core.templatetags.arabic"],
        },
    }
]

DATABASES = {
    "default": env.parse_database_url(
        env.get("ARCMS_DATABASE_URL", "sqlite:///var/arcms.sqlite3"), BASE_DIR
    )
}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    Path(DATABASES["default"]["NAME"]).parent.mkdir(parents=True, exist_ok=True)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

_cache_url = env.get("ARCMS_CACHE_URL", "")
if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
elif _cache_url.startswith("redis"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": _cache_url}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "arcms_cache",
        }
    }

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "studio:home"
LOGOUT_REDIRECT_URL = "accounts:login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية")]
TIME_ZONE = env.get("ARCMS_TIME_ZONE", "Asia/Hebron")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(env.get("ARCMS_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env.get("ARCMS_MEDIA_ROOT", str(BASE_DIR / "media")))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
if TESTING:
    MEDIA_ROOT = BASE_DIR / "var" / "test-media"
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o640

# --- الأمان ---
_behind_proxy = env.get_bool("ARCMS_BEHIND_PROXY", True)
if _behind_proxy:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
_https = SITE_URL.startswith("https://")
SESSION_COOKIE_SECURE = _https
CSRF_COOKIE_SECURE = _https
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "arcms_session"
CSRF_COOKIE_NAME = "arcms_csrf"
SESSION_COOKIE_AGE = env.get_int("ARCMS_SESSION_HOURS", 12) * 3600
SECURE_SSL_REDIRECT = _https and env.get_bool("ARCMS_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = 31536000 if _https else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# مفتاح تشفير الحقول الحساسة (ملاحظات المصادر). يُولَّد بـ: manage.py arcms_keys
FIELD_ENCRYPTION_KEY = env.get("ARCMS_FIELD_KEY", "")
if not FIELD_ENCRYPTION_KEY and (DEBUG or TESTING):
    FIELD_ENCRYPTION_KEY = "ZGV2LW9ubHktZmllbGQta2V5LWRvLW5vdC11c2UtISE="

# --- المصادقة الثنائية ---
ARCMS_2FA_ISSUER = env.get("ARCMS_2FA_ISSUER", "arcms")
ARCMS_LOGIN_MAX_FAILURES = env.get_int("ARCMS_LOGIN_MAX_FAILURES", 5)
ARCMS_LOGIN_LOCK_MINUTES = env.get_int("ARCMS_LOGIN_LOCK_MINUTES", 15)
ARCMS_TIPS_RETENTION_DAYS = env.get_int("ARCMS_TIPS_RETENTION_DAYS", 90)

# --- البريد (النشرة اليومية والإشعارات الداخلية) ---
EMAIL_BACKEND = env.get(
    "ARCMS_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
if TESTING:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_HOST = env.get("ARCMS_SMTP_HOST", "localhost")
EMAIL_PORT = env.get_int("ARCMS_SMTP_PORT", 587)
EMAIL_HOST_USER = env.get("ARCMS_SMTP_USER", "")
EMAIL_HOST_PASSWORD = env.get("ARCMS_SMTP_PASSWORD", "")
EMAIL_USE_TLS = env.get_bool("ARCMS_SMTP_TLS", True)
EMAIL_USE_SSL = env.get_bool("ARCMS_SMTP_SSL", False)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env.get("ARCMS_FROM_EMAIL", "newsroom@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- قنوات التوزيع (الأسرار فقط؛ بقية الإعدادات من لوحة التحكم) ---
ARCMS_TELEGRAM_BOT_TOKEN = env.get("ARCMS_TELEGRAM_BOT_TOKEN", "")
ARCMS_WHATSAPP_TOKEN = env.get("ARCMS_WHATSAPP_TOKEN", "")
ARCMS_WHATSAPP_PHONE_NUMBER_ID = env.get("ARCMS_WHATSAPP_PHONE_NUMBER_ID", "")
ARCMS_WHATSAPP_API_VERSION = env.get("ARCMS_WHATSAPP_API_VERSION", "v21.0")
ARCMS_VAPID_PUBLIC_KEY = env.get("ARCMS_VAPID_PUBLIC_KEY", "")
ARCMS_VAPID_PRIVATE_KEY = env.get("ARCMS_VAPID_PRIVATE_KEY", "")
ARCMS_VAPID_SUBJECT = env.get("ARCMS_VAPID_SUBJECT", f"mailto:{DEFAULT_FROM_EMAIL}")
ARCMS_HTTP_TIMEOUT = env.get_int("ARCMS_HTTP_TIMEOUT", 15)

# --- النسخ الاحتياطي المشفّر ---
ARCMS_BACKUP_DIR = Path(env.get("ARCMS_BACKUP_DIR", str(BASE_DIR / "backups")))
ARCMS_BACKUP_PUBLIC_KEY = env.get("ARCMS_BACKUP_PUBLIC_KEY", "")
ARCMS_BACKUP_KEEP = env.get_int("ARCMS_BACKUP_KEEP", 14)
if TESTING:
    ARCMS_BACKUP_DIR = BASE_DIR / "var" / "test-backups"

# --- التحليلات ---
ARCMS_ANALYTICS_RAW_DAYS = env.get_int("ARCMS_ANALYTICS_RAW_DAYS", 35)
ARCMS_WORKER_INTERVAL = env.get_int("ARCMS_WORKER_INTERVAL", 20)

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env.get("ARCMS_LOG_LEVEL", "INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
if TESTING:
    LOGGING["root"]["level"] = "CRITICAL"
    LOGGING["handlers"]["console"]["level"] = "CRITICAL"
if DEBUG or TESTING:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
