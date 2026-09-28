"""خرائط الموقع، وخريطة أخبار جوجل لآخر 48 ساعة."""

from datetime import timedelta

from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from arcms.content.models import Article, Category, Page
from arcms.core.models import SiteSettings


class ArticleSitemap(Sitemap):
    limit = 5000
    changefreq = "weekly"

    def items(self):
        return Article.objects.published().filter(allow_indexing=True).only("pk", "slug", "content_updated_at", "published_at").order_by("-published_at")

    def lastmod(self, item):
        return item.content_updated_at or item.published_at


class CategorySitemap(Sitemap):
    changefreq = "hourly"
    priority = 0.8

    def items(self):
        return Category.objects.filter(is_active=True)


class PageSitemap(Sitemap):
    changefreq = "monthly"

    def items(self):
        return Page.objects.filter(is_published=True)

    def lastmod(self, item):
        return item.updated_at


SITEMAPS = {"articles": ArticleSitemap, "sections": CategorySitemap, "pages": PageSitemap}


def news_sitemap(request):
    since = timezone.now() - timedelta(hours=48)
    items = Article.objects.published().filter(allow_indexing=True, published_at__gte=since).order_by("-published_at")[:1000]
    xml = render_to_string("public/news_sitemap.xml", {"items": items, "site": SiteSettings.load()}, request=request)
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")
