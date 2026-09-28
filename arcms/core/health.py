"""فحص الجاهزية قبل التجربة على خادم حقيقي وبعدها (‎manage.py arcms_check‎).

كل فحص يعيد (المستوى، الرسالة): ok أو warn أو fail. الفحوص «الحيّة»
(الاتصال بتيليجرام وواتساب والبريد) لا تُجرى إلا عند الطلب صراحة.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone


@dataclass
class Check:
    area: str
    level: str  # ok | warn | fail
    message: str
    hint: str = ""


def run_checks(live: bool = False) -> list[Check]:
    out: list[Check] = []
    add = out.append

    # --- الإعدادات الأساسية ---
    if settings.DEBUG:
        add(Check("الإعدادات", "fail", "وضع التطوير DEBUG مفعّل.", "اضبط ARCMS_DEBUG=0 في الإنتاج."))
    else:
        add(Check("الإعدادات", "ok", "وضع الإنتاج مفعّل."))
    if len(settings.SECRET_KEY) < 50 or settings.SECRET_KEY.startswith("dev-insecure"):
        add(Check("الإعدادات", "fail", "المفتاح السري ضعيف أو افتراضي.", "ولّد مفتاحاً بـ manage.py arcms_keys"))
    else:
        add(Check("الإعدادات", "ok", "المفتاح السري مضبوط."))
    if not settings.SITE_URL.startswith("https://"):
        add(Check("الإعدادات", "warn", f"رابط الموقع لا يستخدم HTTPS: {settings.SITE_URL}", "الكوكيز الآمنة وHSTS تعمل مع HTTPS فقط."))
    else:
        add(Check("الإعدادات", "ok", f"الموقع على HTTPS: {settings.SITE_URL}"))
    if settings.FIELD_ENCRYPTION_KEY.startswith("ZGV2LW9ubHkt"):
        add(Check("التشفير", "fail", "مفتاح تشفير الحقول افتراضي.", "ولّد ARCMS_FIELD_KEY بـ manage.py arcms_keys"))
    elif not settings.FIELD_ENCRYPTION_KEY:
        add(Check("التشفير", "fail", "ARCMS_FIELD_KEY غير مضبوط."))
    else:
        add(Check("التشفير", "ok", "مفتاح تشفير الحقول الحساسة مضبوط."))
    try:
        from arcms.accounts.models import User

        sample = User.objects.exclude(totp_secret="").only("totp_secret").first()
        if sample is not None and sample.totp_secret.startswith("⚠"):
            add(Check("التشفير", "fail", "تعذّر فك البيانات المشفّرة بالمفتاح الحالي.",
                      "ARCMS_FIELD_KEY لا يطابق المفتاح الذي شُفّرت به البيانات. أعد المفتاح الصحيح (أو أضفه بعد فاصلة)."))
    except Exception:  # noqa: BLE001 - قبل إنشاء الجداول
        pass

    # --- قاعدة البيانات والبحث ---
    try:
        connection.ensure_connection()
        add(Check("قاعدة البيانات", "ok", f"متصلة ({connection.vendor})."))
    except Exception as exc:  # noqa: BLE001
        add(Check("قاعدة البيانات", "fail", f"تعذّر الاتصال: {exc}"))
        return out
    if connection.vendor != "postgresql":
        add(Check("قاعدة البيانات", "warn", "تعمل على SQLite.", "للأرشيف الكبير وعدة محررين استخدم PostgreSQL."))
    else:
        with connection.cursor() as cur:
            cur.execute("SELECT to_tsvector('simple', 'اختبار')::text, pg_encoding_to_char(encoding) "
                        "FROM pg_database WHERE datname = current_database()")
            vector, encoding = cur.fetchone()
        if "اختبار" not in (vector or "") or encoding != "UTF8":
            add(Check("البحث", "fail", f"قاعدة البيانات لا تفهرس العربية (الترميز {encoding}).",
                      "أنشئ القاعدة بترميز UTF8 وlocale ‎C.UTF-8‎ ثم أعد الفهرسة."))
        else:
            add(Check("البحث", "ok", "PostgreSQL يفهرس النص العربي (UTF8)."))
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    add(Check("قاعدة البيانات", "fail" if pending else "ok", f"ترحيلات معلّقة: {len(pending)}" if pending else "الجداول محدَّثة."))

    from arcms.content.models import Article, SearchDocument

    articles = Article.objects.count()
    indexed = SearchDocument.objects.count()
    if articles and indexed < articles:
        add(Check("البحث", "warn", f"مفهرس {indexed} من {articles} مادة.", "شغّل manage.py arcms_reindex"))
    else:
        add(Check("البحث", "ok", f"الفهرس يغطي {indexed} مادة."))

    # --- العامل الخلفي ---
    from .models import Job, WorkerHeartbeat

    if WorkerHeartbeat.healthy():
        add(Check("العامل الخلفي", "ok", "يعمل (النشر المجدول والتوزيع والنشرة)."))
    else:
        add(Check("العامل الخلفي", "fail", "لا نبض للعامل الخلفي منذ 3 دقائق.", "شغّل manage.py arcms_worker كخدمة دائمة."))
    failed = Job.objects.filter(status=Job.Status.FAILED, finished_at__gte=timezone.now() - timedelta(days=1)).count()
    if failed:
        add(Check("العامل الخلفي", "warn", f"{failed} مهمة فشلت خلال 24 ساعة.", "راجع صفحة التوزيع لمعرفة السبب."))

    # --- الأمان ---
    from arcms.accounts.models import User

    staff = User.objects.filter(is_active=True)
    missing = [u.username for u in staff if u.requires_2fa and not u.has_2fa]
    if missing:
        add(Check("الأمان", "warn", f"مستخدمون لم يفعّلوا التحقق الثنائي بعد: {', '.join(missing[:8])}",
                  "سيُجبرون على التفعيل عند أول دخول."))
    else:
        add(Check("الأمان", "ok", "كل من يلزمه التحقق الثنائي فعّله."))
    from arcms.audit.services import verify_chain

    ok, count, broken = verify_chain()
    add(Check("سجل التدقيق", "ok" if ok else "fail",
              f"السلسلة سليمة ({count} قيد)." if ok else f"السلسلة مكسورة عند القيد #{broken.pk}.",
              "" if ok else "تعديل مباشر في قاعدة البيانات؛ حقّق فوراً."))

    # --- النسخ الاحتياطي ---
    from arcms.backups.models import BackupRecord

    if not settings.ARCMS_BACKUP_PUBLIC_KEY:
        add(Check("النسخ الاحتياطي", "fail", "لا مفتاح عام للنسخ المشفّرة.", "manage.py arcms_backup_keygen"))
    else:
        last = BackupRecord.objects.filter(status=BackupRecord.Status.OK).first()
        if not last:
            add(Check("النسخ الاحتياطي", "warn", "لم تُنشأ أي نسخة بعد.", "manage.py arcms_backup ثم جرّب الاستعادة على خادم آخر."))
        elif timezone.now() - last.created_at > timedelta(hours=36):
            add(Check("النسخ الاحتياطي", "warn", f"آخر نسخة ناجحة قديمة: {timezone.localtime(last.created_at):%Y-%m-%d %H:%M}"))
        else:
            add(Check("النسخ الاحتياطي", "ok", f"آخر نسخة: {last.filename}"))
    backup_dir = Path(settings.ARCMS_BACKUP_DIR)
    if backup_dir.exists():
        free = shutil.disk_usage(backup_dir).free / 1024**3
        add(Check("التخزين", "warn" if free < 5 else "ok", f"المساحة الحرة: {free:.1f} غيغابايت"))
    if connection.vendor == "postgresql" and not shutil.which("pg_dump"):
        add(Check("النسخ الاحتياطي", "fail", "pg_dump غير مثبت.", "ثبّت postgresql-client بالإصدار نفسه للخادم."))

    # --- القنوات ---
    from arcms.distribution.models import Channel, ChannelConfig

    tg = ChannelConfig.get(Channel.TELEGRAM)
    if tg.enabled:
        if not settings.ARCMS_TELEGRAM_BOT_TOKEN:
            add(Check("تيليجرام", "fail", "القناة مفعّلة بلا رمز بوت (ARCMS_TELEGRAM_BOT_TOKEN)."))
        elif not tg.chat_ids():
            add(Check("تيليجرام", "fail", "لا معرّفات قنوات."))
        elif live:
            from arcms.distribution.channels import telegram_check

            try:
                add(Check("تيليجرام", "ok", f"البوت يعمل: {telegram_check()}"))
            except Exception as exc:  # noqa: BLE001
                add(Check("تيليجرام", "fail", str(exc)))
        else:
            add(Check("تيليجرام", "ok", "مضبوط (لم يُختبر الاتصال؛ استخدم --live)."))
    wa = ChannelConfig.get(Channel.WHATSAPP)
    if wa.enabled and not (settings.ARCMS_WHATSAPP_TOKEN and settings.ARCMS_WHATSAPP_PHONE_NUMBER_ID and wa.whatsapp_template):
        add(Check("واتساب", "fail", "القناة مفعّلة وبياناتها ناقصة (الرمز، رقم الهاتف، القالب)."))
    push = ChannelConfig.get(Channel.PUSH)
    if push.enabled and not (settings.ARCMS_VAPID_PUBLIC_KEY and settings.ARCMS_VAPID_PRIVATE_KEY):
        add(Check("الإشعارات", "fail", "الإشعارات مفعّلة بلا مفاتيح VAPID.", "manage.py arcms_keys يولّدها."))
    nl = ChannelConfig.get(Channel.NEWSLETTER)
    if nl.enabled:
        if "console" in settings.EMAIL_BACKEND or settings.EMAIL_HOST in ("", "localhost"):
            add(Check("البريد", "warn", "خادم البريد غير مضبوط؛ النشرة لن تصل.", "اضبط ARCMS_SMTP_*"))
        elif live:
            try:
                from django.core.mail import get_connection

                conn = get_connection()
                conn.open()
                conn.close()
                add(Check("البريد", "ok", f"الاتصال بـ {settings.EMAIL_HOST} ناجح."))
            except Exception as exc:  # noqa: BLE001
                add(Check("البريد", "fail", f"تعذّر الاتصال بخادم البريد: {exc}"))
    return out
