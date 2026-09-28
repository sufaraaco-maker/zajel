from django.contrib.sitemaps.views import index as sitemap_index
from django.contrib.sitemaps.views import sitemap
from django.urls import path, re_path

from arcms.tips import views as tips

from . import feeds, sitemaps, views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("post/<int:pk>/", views.article_detail, name="article_noslug"),
    re_path(r"^post/(?P<pk>\d+)/(?P<slug>[^/]+)/?$", views.article_detail, name="article"),
    path("s/<int:pk>", views.short_link, name="short"),
    re_path(r"^category/(?P<slug>[^/]+)/?$", views.category_detail, name="category"),
    re_path(r"^tag/(?P<slug>[^/]+)/?$", views.tag_detail, name="tag"),
    re_path(r"^author/(?P<slug>[^/]+)/?$", views.author_detail, name="author"),
    re_path(r"^file/(?P<slug>[^/]+)/?$", views.dossier_detail, name="dossier"),
    path("type/<slug:kind>/", views.kind_list, name="kind"),
    path("latest/", views.latest_list, name="latest"),
    path("search", views.search_view, name="search"),
    path("live/", views.live_list, name="live_list"),
    re_path(r"^live/(?P<slug>[^/]+)/entries\.json$", views.live_entries_json, name="live_entries"),
    re_path(r"^live/(?P<slug>[^/]+)/?$", views.live_detail, name="live"),
    re_path(r"^p/(?P<slug>[^/]+)/?$", views.page_detail, name="page"),
    path("tips/", tips.submit, name="tips"),
    path("tips/follow/", tips.follow, name="tips_follow"),
    path("newsletter/subscribe", views.newsletter_subscribe, name="newsletter_subscribe"),
    path("newsletter/confirm/<str:token>", views.newsletter_confirm, name="newsletter_confirm"),
    path("newsletter/unsubscribe/<str:token>", views.newsletter_unsubscribe, name="newsletter_unsubscribe"),
    path("push/config", views.push_config, name="push_config"),
    path("push/subscribe", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe", views.push_unsubscribe, name="push_unsubscribe"),
    path("a/v", views.beacon, name="beacon"),
    path("api/breaking", views.breaking_json, name="breaking_json"),
    path("rss", feeds.LatestFeed(), name="feed"),
    re_path(r"^rss/category/(?P<slug>[^/]+)$", feeds.CategoryFeed(), name="feed_category"),
    path("rss/type/<slug:kind>", feeds.KindFeed(), name="feed_kind"),
    path("sitemap.xml", sitemap_index, {"sitemaps": sitemaps.SITEMAPS, "sitemap_url_name": "public:sitemap_section"}),
    path("sitemap-<section>.xml", sitemap, {"sitemaps": sitemaps.SITEMAPS}, name="sitemap_section"),
    path("news-sitemap.xml", sitemaps.news_sitemap, name="news_sitemap"),
    path("robots.txt", views.robots_txt),
    path("favicon.ico", views.favicon),
    path("manifest.webmanifest", views.web_manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
]
