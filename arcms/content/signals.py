from __future__ import annotations

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save

from .models import Article

PUBLIC_CACHE_VERSION_KEY = "arcms:public-version"


def public_cache_version() -> int:
    version = cache.get(PUBLIC_CACHE_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(PUBLIC_CACHE_VERSION_KEY, version, None)
    return version


def invalidate_public_cache(*args, **kwargs) -> None:
    try:
        cache.incr(PUBLIC_CACHE_VERSION_KEY)
    except ValueError:
        cache.set(PUBLIC_CACHE_VERSION_KEY, 2, None)


def _reindex(pk: int) -> None:
    from .search import index_article

    article = Article.objects.filter(pk=pk).first()
    if article:
        index_article(article)


def article_saved(sender, instance: Article, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    if update_fields and set(update_fields) <= {"view_count", "distributed_at", "updated_at"}:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _reindex(pk))
    if instance.status == "published":
        transaction.on_commit(invalidate_public_cache)


def article_m2m_changed(sender, instance, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear") and isinstance(instance, Article):
        pk = instance.pk
        transaction.on_commit(lambda: _reindex(pk))


def article_deleted(sender, instance, **kwargs):
    from .search import remove_article

    remove_article(instance.pk)
    invalidate_public_cache()


def connect() -> None:
    post_save.connect(article_saved, sender=Article, dispatch_uid="arcms-article-index")
    post_delete.connect(article_deleted, sender=Article, dispatch_uid="arcms-article-unindex")
    for through in (Article.tags.through, Article.authors.through):
        m2m_changed.connect(article_m2m_changed, sender=through, dispatch_uid=f"arcms-m2m-{through.__name__}")

    from .models import BreakingNews, Category, LiveEntry, Page

    for model in (BreakingNews, Category, LiveEntry, Page):
        post_save.connect(invalidate_public_cache, sender=model, dispatch_uid=f"arcms-inv-{model.__name__}")
        post_delete.connect(invalidate_public_cache, sender=model, dispatch_uid=f"arcms-invd-{model.__name__}")
