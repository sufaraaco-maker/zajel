"""الإدارة: إعدادات الموقع، الصفحة الرئيسية، المستخدمون، التوزيع، الجمهور، التدقيق،
النسخ الاحتياطي، الاستيراد، وفحص الجاهزية."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from arcms.accounts.forms import UserForm
from arcms.accounts.models import User
from arcms.accounts.roles import CAPABILITY_LABELS, ROLE_CAPABILITIES, ROLES_REQUIRING_2FA, Cap, Role
from arcms.audit.models import Action, AuditEntry
from arcms.audit.services import record, verify_chain
from arcms.content.models import Article, Category, ContactMessage
from arcms.core.jobs import enqueue
from arcms.core.models import HomeBlock, Job, SiteSettings
from arcms.distribution.models import (
    Channel,
    ChannelConfig,
    Delivery,
    NewsletterIssue,
    NewsletterSubscriber,
    PushSubscription,
    WhatsAppSubscriber,
)

from .base import paginate, requires
from .forms import ChannelConfigForm, HomeBlockForm, SiteSettingsForm, WhatsAppSubscriberForm

# --- إعدادات الموقع ---


@requires(Cap.SETTINGS)
def site_settings(request):
    from arcms.core.presets import THEMES, apply_theme

    obj = SiteSettings.load()
    obj = SiteSettings.objects.get(pk=obj.pk)
    if request.method == "POST" and request.POST.get("apply_theme"):
        if apply_theme(obj, request.POST["apply_theme"]):
            messages.success(request, f"طُبّق مظهر «{THEMES[request.POST['apply_theme']]['label']}». عدّل ما شئت من الألوان أدناه.")
        return redirect("studio:settings")
    form = SiteSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "حُفظت إعدادات الموقع.")
        return redirect("studio:settings")
    return render(request, "studio/settings.html", {"form": form, "themes": THEMES})


# --- بنّاء الصفحة الرئيسية ---


@requires(Cap.HOMEPAGE)
def homepage(request):
    blocks = HomeBlock.objects.select_related("category", "dossier", "ad_slot").prefetch_related("categories")
    return render(request, "studio/homepage.html", {"blocks": blocks, "kinds": HomeBlock.Kind.choices})


@requires(Cap.HOMEPAGE)
def homepage_block(request, pk: int | None = None):
    block = get_object_or_404(HomeBlock, pk=pk) if pk else None
    initial = {}
    if block is None:
        initial["kind"] = request.GET.get("kind", HomeBlock.Kind.CATEGORY)
        initial["order"] = (HomeBlock.objects.aggregate(m=Max("order"))["m"] or 0) + 10
    form = HomeBlockForm(request.POST or None, instance=block, initial=initial, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "حُفظت الكتلة.")
        return redirect("studio:homepage")
    return render(request, "studio/homepage_block.html",
                  {"form": form, "block": block, "field_kinds": HomeBlockForm.FIELD_KINDS})


@requires(Cap.HOMEPAGE)
@require_POST
def homepage_action(request, pk: int):
    block = get_object_or_404(HomeBlock, pk=pk)
    action = request.POST.get("action")
    blocks = list(HomeBlock.objects.all())
    idx = next(i for i, b in enumerate(blocks) if b.pk == block.pk)
    if action in ("up", "down"):
        swap = idx - 1 if action == "up" else idx + 1
        if 0 <= swap < len(blocks):
            blocks[idx], blocks[swap] = blocks[swap], blocks[idx]
        for n, b in enumerate(blocks):
            if b.order != (n + 1) * 10:
                b.order = (n + 1) * 10
                b.save(update_fields=["order"])
    elif action == "toggle":
        block.is_active = not block.is_active
        block.save()
    elif action == "delete":
        block.delete()
    return redirect("studio:homepage")


# --- المستخدمون ---


@requires(Cap.USERS)
def users(request):
    qs = User.objects.annotate(n_articles=Count("articles")).order_by("-is_active", "role", "username")
    matrix = [
        {"cap": label, "roles": [cap in ROLE_CAPABILITIES[r] for r, _ in Role.choices]}
        for cap, label in CAPABILITY_LABELS.items()
    ]
    return render(
        request,
        "studio/users.html",
        {"users": qs, "roles": Role.choices, "matrix": matrix, "forced_2fa": ROLES_REQUIRING_2FA},
    )


@requires(Cap.USERS)
def user_edit(request, pk: int | None = None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        if obj and obj.pk == request.user.pk and not form.cleaned_data["is_active"]:
            messages.error(request, "لا يمكنك تعطيل حسابك بنفسك.")
        else:
            user = form.save()
            if user.pk == request.user.pk and form.cleaned_data.get("password"):
                update_session_auth_hash(request, user)
            desks = request.POST.getlist("desks")
            user.desks.set(Category.objects.filter(pk__in=[d for d in desks if d.isdigit()]))
            messages.success(request, f"حُفظ المستخدم {user}.")
            return redirect("studio:users")
    return render(
        request,
        "studio/user_form.html",
        {
            "form": form,
            "obj": obj,
            "categories": Category.objects.filter(is_active=True),
            "user_desks": set(obj.desks.values_list("pk", flat=True)) if obj else set(),
        },
    )


@requires(Cap.USERS)
@require_POST
def user_reset_2fa(request, pk: int):
    obj = get_object_or_404(User, pk=pk)
    obj.reset_2fa()
    record(Action.TWO_FA, obj, message=f"أعاد {request.user} ضبط التحقق الثنائي وأنهى جلساته")
    messages.success(request, f"أُلغي جهاز التحقق لـ {obj} وأُنهيت جلساته. سيُطلب منه تفعيل جهاز جديد عند دخوله.")
    return redirect("studio:user_edit", pk=pk)


@requires(Cap.USERS)
@require_POST
def user_sessions_end(request, pk: int):
    obj = get_object_or_404(User, pk=pk)
    obj.end_all_sessions(request)
    record(Action.SECURITY, obj, message=f"أنهى {request.user} كل جلسات الحساب")
    messages.success(request, f"أُنهيت كل جلسات {obj} المفتوحة.")
    return redirect("studio:user_edit", pk=pk)


# --- الحساب الشخصي ---


@requires()
def profile(request):
    from arcms.content.models import Author

    if request.method == "POST" and request.POST.get("action") == "notifications":
        request.user.email_notifications = request.POST.get("email_notifications") == "on"
        request.user.save(update_fields=["email_notifications"])
        messages.success(request, "حُفظ تفضيل التنبيهات.")
        return redirect("studio:profile")
    return render(
        request,
        "studio/profile.html",
        {
            "author": Author.objects.filter(user=request.user).first(),
            "recent_logins": AuditEntry.objects.filter(actor=request.user, action__in=[Action.LOGIN, Action.LOGIN_FAILED])[:10],
            "caps": [CAPABILITY_LABELS[c] for c in sorted(request.user.capabilities)],
        },
    )


# --- التوزيع ---


@requires(Cap.DIST_MANAGE, Cap.DIST_SEND)
def distribution(request):
    configs = [ChannelConfig.get(c) for c in Channel.values]
    since = timezone.now() - timedelta(days=7)
    stats = {
        r["channel"]: r
        for r in Delivery.objects.filter(created_at__gte=since)
        .values("channel")
        .annotate(total=Count("id"), sent=Count("id", filter=Q(status=Delivery.Status.SENT)),
                  failed=Count("id", filter=Q(status=Delivery.Status.FAILED)))
    }
    ctx = {
        "configs": configs,
        "stats": stats,
        "deliveries": Delivery.objects.select_related("article", "breaking")[:40],
        "subscribers": {
            "newsletter": NewsletterSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True).count(),
            "newsletter_pending": NewsletterSubscriber.objects.filter(confirmed_at__isnull=True).count(),
            "push": PushSubscription.objects.filter(is_active=True).count(),
            "whatsapp": WhatsAppSubscriber.objects.filter(is_active=True).count(),
        },
        "issues": NewsletterIssue.objects.all()[:10],
        "secrets": {
            "telegram": bool(settings.ARCMS_TELEGRAM_BOT_TOKEN),
            "whatsapp": bool(settings.ARCMS_WHATSAPP_TOKEN and settings.ARCMS_WHATSAPP_PHONE_NUMBER_ID),
            "push": bool(settings.ARCMS_VAPID_PRIVATE_KEY and settings.ARCMS_VAPID_PUBLIC_KEY),
            "newsletter": "console" not in settings.EMAIL_BACKEND,
        },
    }
    return render(request, "studio/distribution.html", ctx)


@requires(Cap.DIST_MANAGE)
def channel_edit(request, channel: str):
    if channel not in Channel.values:
        raise Http404
    config = ChannelConfig.get(channel)
    form = ChannelConfigForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        record(Action.SETTINGS, config, message=f"إعدادات قناة {config}")
        messages.success(request, "حُفظت إعدادات القناة.")
        return redirect("studio:distribution")
    return render(request, "studio/channel_edit.html", {"form": form, "config": config})


@requires(Cap.DIST_MANAGE)
@require_POST
def channel_test(request, channel: str):
    from arcms.distribution import channels

    try:
        if channel == Channel.TELEGRAM:
            name = channels.telegram_check()
            config = ChannelConfig.get(channel)
            for chat in config.chat_ids():
                channels.telegram_send(chat, f"✅ رسالة اختبار من غرفة التحرير ({request.user})", silent=True)
            messages.success(request, f"البوت {name} يعمل، وأُرسلت رسالة اختبار إلى {len(config.chat_ids())} قناة.")
        elif channel == Channel.NEWSLETTER:
            from django.core.mail import send_mail

            send_mail("اختبار النشرة", "رسالة اختبار من غرفة التحرير.", None, [request.user.email])
            messages.success(request, f"أُرسلت رسالة اختبار إلى {request.user.email}.")
        else:
            messages.warning(request, "الاختبار المباشر متاح لتيليجرام والبريد فقط.")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"فشل الاختبار: {exc}")
    return redirect("studio:distribution")


@requires(Cap.DIST_MANAGE)
@require_POST
def newsletter_send_now(request):
    today = timezone.localdate().isoformat()
    job = enqueue("newsletter.send", {"date": today}, dedupe_key=f"newsletter:{today}")
    if job and job.status != Job.Status.QUEUED:
        messages.warning(request, "نشرة اليوم أُرسلت أو قيد الإرسال بالفعل.")
    else:
        messages.success(request, "أُضيفت نشرة اليوم إلى الطابور.")
    return redirect("studio:distribution")


@requires(Cap.DIST_MANAGE)
def newsletter_preview(request):
    from arcms.distribution.newsletter import build_issue

    issue = build_issue(timezone.localdate(), force=True)
    if issue is None:
        messages.warning(request, "لا مواد منشورة خلال 24 ساعة لتكوين نشرة.")
        return redirect("studio:distribution")
    from django.http import HttpResponse

    return HttpResponse(issue.html.replace("{{unsubscribe_url}}", "#"))


@requires(Cap.DIST_MANAGE)
def whatsapp_subscribers(request):
    form = WhatsAppSubscriberForm(request.POST or None)
    if request.method == "POST":
        if request.FILES.get("csv"):
            added = 0
            data = request.FILES["csv"].read().decode("utf-8-sig", errors="replace")
            for row in csv.reader(io.StringIO(data)):
                if not row:
                    continue
                phone = "".join(ch for ch in row[0] if ch.isdigit())
                if 8 <= len(phone) <= 15:
                    _, created = WhatsAppSubscriber.objects.get_or_create(
                        phone=phone, defaults={"name": row[1][:80] if len(row) > 1 else "", "source": "csv"}
                    )
                    added += created
            messages.success(request, f"أُضيف {added} رقماً.")
            return redirect("studio:whatsapp_subscribers")
        if form.is_valid():
            form.save()
            messages.success(request, "أُضيف الرقم.")
            return redirect("studio:whatsapp_subscribers")
    page = paginate(request, WhatsAppSubscriber.objects.all(), 50)
    return render(request, "studio/whatsapp_subscribers.html", {"form": form, "page": page})


# --- الجمهور ---


@requires(Cap.ANALYTICS)
def analytics(request):
    from arcms.analytics import queries
    from arcms.analytics.models import PageView

    try:
        days = int(request.GET.get("days", 7))
    except ValueError:
        days = 7
    days = days if days in (1, 7, 30) else 7
    series = queries.series(30)
    top = queries.top_articles(days, 15)
    articles = {a.pk: a for a in Article.objects.filter(pk__in=[t[0] for t in top]).select_related("category")}
    top_rows = [{"article": articles[aid], "views": v, "visitors": u} for aid, v, u in top if aid in articles]
    bd = queries.breakdown(days)
    cats = {str(c.pk): c.name for c in Category.objects.all()}
    source_labels = dict(PageView.Source.choices)
    device_labels = dict(PageView.Device.choices)
    from arcms.analytics.charts import daily_columns

    site = SiteSettings.load()
    chart = daily_columns(series, style=site.date_style, digits=site.digits)
    ctx = {
        "chart": chart,
        "days": days,
        "today": queries.today_numbers(),
        "realtime": queries.realtime(),
        "series": series,
        "top_rows": top_rows,
        "sources": [(source_labels.get(k, k), v) for k, v in bd["sources"].most_common()],
        "devices": [(device_labels.get(k, k), v) for k, v in bd["devices"].most_common()],
        "referrers": bd["referrers"].most_common(10),
        "categories": [(cats.get(k, "—"), v) for k, v in bd["categories"].most_common(10)],
        "published_today": Article.objects.published().filter(
            published_at__gte=timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        ).count(),
        "subscribers": {
            "newsletter": NewsletterSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True).count(),
            "push": PushSubscription.objects.filter(is_active=True).count(),
            "whatsapp": WhatsAppSubscriber.objects.filter(is_active=True).count(),
        },
    }
    ctx["sources_total"] = sum(v for _, v in ctx["sources"]) or 1
    ctx["devices_total"] = sum(v for _, v in ctx["devices"]) or 1
    return render(request, "studio/analytics.html", ctx)


# --- التدقيق ---


@requires(Cap.AUDIT)
def audit(request):
    qs = AuditEntry.objects.select_related("actor")
    action = request.GET.get("action", "")
    actor = request.GET.get("actor", "")
    q = request.GET.get("q", "").strip()
    if action in Action.values:
        qs = qs.filter(action=action)
    if actor.isdigit():
        qs = qs.filter(actor_id=int(actor))
    if q:
        qs = qs.filter(Q(object_repr__icontains=q) | Q(message__icontains=q) | Q(object_id=q))
    page = paginate(request, qs, 50)
    chain_ok, chain_count, broken = verify_chain()
    return render(
        request,
        "studio/audit.html",
        {
            "page": page,
            "actions": Action.choices,
            "actors": User.objects.all(),
            "f": {"action": action, "actor": actor, "q": q},
            "chain_ok": chain_ok,
            "chain_count": chain_count,
            "broken": broken,
        },
    )


# --- النسخ الاحتياطي ---


@requires(Cap.BACKUPS)
def backups(request):
    from arcms.backups import crypto
    from arcms.backups.models import BackupRecord

    key = settings.ARCMS_BACKUP_PUBLIC_KEY
    return render(
        request,
        "studio/backups.html",
        {
            "records": BackupRecord.objects.all()[:30],
            "has_key": bool(key),
            "fingerprint": crypto.fingerprint(key) if key else "",
            "backup_dir": settings.ARCMS_BACKUP_DIR,
            "keep": settings.ARCMS_BACKUP_KEEP,
        },
    )


@requires(Cap.BACKUPS)
@require_POST
def backup_now(request):
    if not settings.ARCMS_BACKUP_PUBLIC_KEY:
        messages.error(request, "لا يوجد مفتاح عام للنسخ. ولّده بالأمر manage.py arcms_backup_keygen.")
    else:
        enqueue("backup.run", {"trigger": f"يدوي: {request.user}", "media": request.POST.get("media") == "1"}, max_attempts=1)
        messages.success(request, "بدأ إنشاء نسخة مشفّرة في الخلفية.")
    return redirect("studio:backups")


@requires(Cap.BACKUPS)
def backup_download(request, pk: int):
    from arcms.backups.models import BackupRecord

    rec = get_object_or_404(BackupRecord, pk=pk, status=BackupRecord.Status.OK)
    path = Path(settings.ARCMS_BACKUP_DIR) / rec.filename
    if not path.is_file() or path.resolve().parent != Path(settings.ARCMS_BACKUP_DIR).resolve():
        raise Http404
    record(Action.BACKUP, rec, message="تنزيل نسخة احتياطية مشفّرة")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=rec.filename)


# --- الاستيراد ---


@requires(Cap.IMPORT)
def importer(request):
    from arcms.importer.models import ImportRun, LegacyRedirect

    if request.method == "POST" and request.FILES.get("file"):
        upload = request.FILES["file"]
        name = upload.name.lower()
        source = "wxr" if name.endswith(".xml") else "jsonl" if name.endswith((".jsonl", ".json")) else None
        if source is None:
            messages.error(request, "ارفع ملف تصدير ووردبريس (.xml) أو ملف JSON Lines (.jsonl).")
        else:
            inbox = Path(settings.ARCMS_BACKUP_DIR).parent / "imports"
            inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = inbox / f"{timezone.now():%Y%m%d-%H%M%S}-{source}{Path(name).suffix}"
            with open(target, "wb") as fh:
                for chunk in upload.chunks():
                    fh.write(chunk)
            enqueue(
                "import.run",
                {
                    "path": str(target),
                    "source": source,
                    "download_media": request.POST.get("download_media") == "1",
                    "dry_run": request.POST.get("dry_run") == "1",
                },
                max_attempts=1,
            )
            messages.success(request, "رُفع الملف وبدأ الاستيراد في الخلفية. تابع النتيجة أدناه.")
        return redirect("studio:importer")
    return render(
        request,
        "studio/importer.html",
        {"runs": ImportRun.objects.all()[:20], "redirects": LegacyRedirect.objects.count()},
    )


# --- الرسائل والجاهزية ---


@requires(Cap.SETTINGS)
def inbox(request):
    page = paginate(request, ContactMessage.objects.all(), 30)
    opened = None
    if request.GET.get("open", "").isdigit():
        opened = ContactMessage.objects.filter(pk=int(request.GET["open"])).first()
        if opened and not opened.is_read:
            opened.is_read = True
            opened.save(update_fields=["is_read"])
    return render(request, "studio/inbox.html", {"page": page, "opened": opened})


@requires(Cap.SETTINGS, Cap.BACKUPS)
def health(request):
    from arcms.core.health import run_checks

    checks = run_checks(live=request.GET.get("live") == "1")
    summary = {lvl: sum(1 for c in checks if c.level == lvl) for lvl in ("ok", "warn", "fail")}
    return render(request, "studio/health.html", {"checks": checks, "summary": summary})


def forbidden(request, exception=None):
    return render(request, "studio/403.html", {"message": str(exception or "")}, status=403)
