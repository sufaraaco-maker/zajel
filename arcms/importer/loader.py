"""تحميل السجلات الموحّدة إلى قاعدة البيانات. إعادة التشغيل آمنة: السجل
الموجود (بالمعرّف القديم نفسه) يُحدَّث ولا يتكرر."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from arcms.audit.models import Action
from arcms.audit.services import acting_as, record, suppressed
from arcms.content.imaging import ImageRejected, store_image
from arcms.content.models import Article, ArticleKind, Author, Category, MediaAsset, Status, Tag
from arcms.content.sanitize import sanitize_html

from .middleware import normalize_legacy_path
from .models import ImportRun, LegacyRedirect
from .sources import Record

KIND_HINTS = (
    (r"مقال|رأي|آراء", ArticleKind.OPINION),
    (r"مدون", ArticleKind.BLOG),
    (r"فيديو|مرئي", ArticleKind.VIDEO),
    (r"تقرير|تقارير", ArticleKind.REPORT),
    (r"تحقيق", ArticleKind.INVESTIGATION),
    (r"حوار|مقابل", ArticleKind.INTERVIEW),
    (r"ترجم", ArticleKind.TRANSLATION),
    (r"إنفوجراف|انفوجراف|إنفوغراف|انفوغراف", ArticleKind.INFOGRAPHIC),
    (r"كاريكاتير", ArticleKind.CARTOON),
    (r"صور|معرض", ArticleKind.GALLERY),
)


def guess_kind(rec: Record, kind_map: dict[str, str]) -> str:
    if rec.kind in ArticleKind.values:
        return rec.kind
    for cat in rec.categories:
        if cat in kind_map:
            return kind_map[cat]
    for cat in rec.categories:
        for pattern, kind in KIND_HINTS:
            if re.search(pattern, cat):
                return kind
    return ArticleKind.NEWS


class Loader:
    def __init__(self, *, download_media: bool = False, dry_run: bool = False, kind_map: dict[str, str] | None = None,
                 default_category: str = "أخبار", source: str = "", filename: str = ""):
        self.download_media = download_media
        self.dry_run = dry_run
        self.kind_map = kind_map or {}
        self.default_category = default_category
        self.run = ImportRun(source=source, filename=filename, dry_run=dry_run)
        self._cats: dict[str, Category] = {}
        self._authors: dict[str, Author] = {}
        self._images: dict[str, MediaAsset | None] = {}
        self._session = requests.Session()

    # --- كيانات مساعدة ---

    def category(self, name: str) -> Category:
        name = name.strip() or self.default_category
        if name not in self._cats:
            cat = Category.objects.filter(name=name).first() or Category.objects.create(name=name)
            self._cats[name] = cat
        return self._cats[name]

    def author(self, name: str) -> Author:
        name = name.strip()
        if name not in self._authors:
            self._authors[name] = Author.objects.filter(name=name).first() or Author.objects.create(name=name)
        return self._authors[name]

    def image(self, url: str, caption: str = "") -> MediaAsset | None:
        if not url:
            return None
        if url in self._images:
            return self._images[url]
        asset = MediaAsset.objects.filter(legacy_url=url[:500]).first()
        if asset is None and self.download_media:
            try:
                resp = self._session.get(url, timeout=settings.ARCMS_HTTP_TIMEOUT, stream=True)
                resp.raise_for_status()
                data = resp.raw.read(25 * 1024 * 1024 + 1, decode_content=True)
                asset, _ = store_image(data, caption=caption)
                if not asset.legacy_url:
                    asset.legacy_url = url[:500]
                    asset.save(update_fields=["legacy_url"])
            except (requests.RequestException, ImageRejected) as exc:
                self.run.errors.append(f"صورة {url}: {exc}"[:300])
                asset = None
        self._images[url] = asset
        return asset

    def _rewrite_inline_images(self, html: str) -> str:
        if not self.download_media:
            return html

        def repl(m: re.Match) -> str:
            asset = self.image(m.group(2))
            return f"{m.group(1)}{asset.rendition('large') if asset else m.group(2)}{m.group(3)}"

        return re.sub(r'(<img[^>]+src=")([^"]+)(")', repl, html)

    # --- التحميل ---

    def load_one(self, rec: Record) -> str:
        article = Article.objects.filter(legacy_id=rec.legacy_id).first()
        created = article is None
        if created:
            article = Article(legacy_id=rec.legacy_id)
        article.title = rec.title[:250]
        article.subtitle = rec.subtitle[:300]
        article.excerpt = rec.excerpt
        article.body = sanitize_html(self._rewrite_inline_images(rec.body_html))
        article.kind = guess_kind(rec, self.kind_map)
        article.source = rec.source[:150]
        article.category = self.category(rec.categories[0] if rec.categories else self.default_category)
        article.legacy_url = rec.legacy_url[:500]
        if rec.status == "published":
            when = rec.published_at or timezone.now()
            article.status = Status.PUBLISHED
            article.published_at = when
            article.first_published_at = article.first_published_at or when
            article.content_updated_at = when
            article.distributed_at = article.distributed_at or timezone.now()  # الأرشيف لا يُعاد توزيعه
        else:
            article.status = Status.DRAFT
        if rec.published_at and created:
            article.created_at = rec.published_at
        image = self.image(rec.image_url, rec.image_caption)
        if image:
            article.featured_image = image
            article.image_caption = rec.image_caption[:400]
        article.slug = ""
        article.save()
        article.tags.set([Tag.get_or_create_by_name(t) for t in rec.tags if t.strip()][:30])
        article.authors.set([self.author(a) for a in rec.authors if a.strip()])
        if len(rec.categories) > 1:
            article.extra_categories.set([self.category(c) for c in rec.categories[1:6]])
        self._redirects(article, rec)
        return "created" if created else "updated"

    def _redirects(self, article: Article, rec: Record) -> None:
        paths = []
        if rec.legacy_url:
            parsed = urlparse(rec.legacy_url)
            paths.append(normalize_legacy_path(parsed.path, parsed.query))
        paths += rec.extra_legacy_paths
        new = article.get_absolute_url()
        for p in paths:
            if p and p not in ("/", new):
                LegacyRedirect.objects.update_or_create(old_path=p[:500], defaults={"new_path": new})

    def load(self, records: Iterable[Record], limit: int | None = None, batch: int = 200, progress=None) -> ImportRun:
        if not self.dry_run:
            self.run.save()
        buffer: list[Record] = []
        seen = 0

        def flush():
            if not buffer:
                return
            if self.dry_run:
                self.run.created += len(buffer)
                buffer.clear()
                return
            with transaction.atomic(), suppressed():
                for rec in buffer:
                    try:
                        with transaction.atomic():
                            result = self.load_one(rec)
                        setattr(self.run, result, getattr(self.run, result) + 1)
                    except Exception as exc:  # noqa: BLE001 - سجل تالف لا يوقف الاستيراد كله
                        self.run.skipped += 1
                        self.run.errors.append(f"{rec.legacy_id}: {exc}"[:300])
            buffer.clear()
            if progress:
                progress(self.run)

        with acting_as(label="الاستيراد"):
            for rec in records:
                buffer.append(rec)
                seen += 1
                if len(buffer) >= batch:
                    flush()
                if limit and seen >= limit:
                    break
            flush()
            self.run.finished_at = timezone.now()
            self.run.errors = self.run.errors[-500:]
            if not self.dry_run:
                self.run.save()
                record(
                    Action.IMPORT,
                    self.run,
                    object_repr=self.run.filename,
                    message=f"استيراد {self.run.source}: {self.run.created} جديدة، {self.run.updated} محدَّثة، {self.run.skipped} متجاوَزة",
                )
        return self.run
