from django.core.cache import cache
from django.utils import timezone

from arcms.content.signals import public_cache_version

from .models import MenuItem, SiteSettings


def _menus():
    key = f"arcms:menus:{public_cache_version()}"
    menus = cache.get(key)
    if menus is None:
        items = list(
            MenuItem.objects.filter(is_active=True)
            .select_related("category", "dossier", "page", "tag")
            .order_by("location", "order", "id")
        )
        by_parent = {}
        for item in items:
            by_parent.setdefault(item.parent_id, []).append(item)
        menus = {}
        for item in by_parent.get(None, []):
            item.url_cached = item.get_url()
            item.children_cached = []
            for child in by_parent.get(item.pk, []):
                child.url_cached = child.get_url()
                item.children_cached.append(child)
            menus.setdefault(item.location, []).append(item)
        cache.set(key, menus, 600)
    return menus


def _tips_url() -> str:
    from django.conf import settings
    from django.urls import reverse

    return (settings.ARCMS_TIPS_ORIGIN + "/tips/") if settings.ARCMS_TIPS_ORIGIN else reverse("public:tips")


def site(request):
    settings_obj = SiteSettings.load()
    ctx = {"site": settings_obj, "now": timezone.now(), "tips_url": _tips_url()}
    path = request.path
    if not path.startswith(("/studio", "/accounts")):
        ctx["menus"] = _menus()
    elif path.startswith(("/studio", "/accounts/keys")) and request.user.is_authenticated:
        ctx.update(_studio(request.user))
    return ctx


def _studio(user) -> dict:
    from arcms.accounts.roles import Cap
    from arcms.content.models import Article, Status

    caps = user.capabilities
    counts = {"returned": Article.objects.filter(created_by=user, status=Status.CHANGES).count()}
    if Cap.ARTICLE_REVIEW in caps:
        qs = Article.objects.filter(status=Status.IN_REVIEW).exclude(created_by=user)
        desks = user.desk_ids()
        if desks:
            qs = qs.filter(category_id__in=desks)
        counts["review"] = qs.count()
    if Cap.ARTICLE_PUBLISH in caps:
        counts["approved"] = Article.objects.filter(status=Status.APPROVED).count()
    if Cap.TIPS in caps:
        from arcms.tips.models import Tip

        counts["tips"] = Tip.objects.filter(unread=True).count()
    if Cap.WIRES in caps:
        from datetime import timedelta

        from arcms.wires.models import WireItem

        counts["wires"] = WireItem.objects.filter(
            is_alert=True, status=WireItem.Status.NEW, fetched_at__gte=timezone.now() - timedelta(hours=12)
        ).count()
    return {"user_caps": caps, "nav_counts": counts}
