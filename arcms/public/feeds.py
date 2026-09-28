"""خلاصات RSS: عامة، ولكل قسم، ولكل نوع مادة."""

from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.utils.feedgenerator import Rss201rev2Feed

from arcms.content.models import KIND_PLURALS, Article, ArticleKind, Category
from arcms.core.models import SiteSettings


class ArabicRss(Rss201rev2Feed):
    def root_attributes(self):
        attrs = super().root_attributes()
        attrs["xmlns:media"] = "http://search.yahoo.com/mrss/"
        return attrs

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if item.get("image"):
            handler.addQuickElement("media:content", "", {"url": item["image"], "medium": "image"})


class LatestFeed(Feed):
    feed_type = ArabicRss
    language = "ar"

    def title(self, obj=None):
        return SiteSettings.load().name

    def link(self, obj=None):
        return "/"

    def description(self, obj=None):
        return SiteSettings.load().description

    def base_queryset(self):
        return Article.objects.public_list()

    def items(self, obj=None):
        return self.base_queryset()[:40]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.summary

    def item_pubdate(self, item):
        return item.published_at

    def item_updateddate(self, item):
        return item.content_updated_at

    def item_author_name(self, item):
        a = item.primary_author
        return a.name if a else None

    def item_categories(self, item):
        return [item.category.name] if item.category_id else []

    def item_extra_kwargs(self, item):
        from arcms.core.utils import absolute_url

        if item.featured_image_id:
            return {"image": absolute_url(item.featured_image.card)}
        return {}


class CategoryFeed(LatestFeed):
    def get_object(self, request, slug):
        return get_object_or_404(Category, slug=slug)

    def title(self, obj=None):
        return f"{SiteSettings.load().name} - {obj.name}"

    def link(self, obj=None):
        return obj.get_absolute_url()

    def items(self, obj=None):
        return self.base_queryset().filter(category_id__in=obj.family_ids())[:40]


class KindFeed(LatestFeed):
    def get_object(self, request, kind):
        if kind not in ArticleKind.values:
            from django.http import Http404

            raise Http404
        return kind

    def title(self, obj=None):
        return f"{SiteSettings.load().name} - {KIND_PLURALS.get(obj, obj)}"

    def link(self, obj=None):
        from django.urls import reverse

        return reverse("public:kind", args=[obj])

    def items(self, obj=None):
        return self.base_queryset().filter(kind=obj)[:40]
