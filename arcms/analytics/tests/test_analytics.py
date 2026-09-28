from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from arcms.analytics import queries
from arcms.analytics.charts import daily_columns, nice_ticks
from arcms.analytics.collector import classify_source, device_class, is_bot, record_view, visitor_hash
from arcms.analytics.models import DailySalt, DailySiteStat, PageView
from arcms.analytics.tasks import aggregate_day, purge
from arcms.content.models import Article
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, make_article

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0"


class CollectorTests(ArcmsTestCase):
    def test_source_classification(self):
        self.assertEqual(classify_source("", "news.org")[0], "direct")
        self.assertEqual(classify_source("https://www.google.com/search?q=x", "news.org")[0], "search")
        self.assertEqual(classify_source("https://t.me/channel/5", "news.org")[0], "messaging")
        self.assertEqual(classify_source("https://l.facebook.com/", "news.org")[0], "social")
        self.assertEqual(classify_source("https://news.org/post/1/", "news.org")[0], "internal")
        self.assertEqual(classify_source("https://blog.example/x", "news.org"), ("referral", "blog.example"))
        self.assertEqual(classify_source("", "news.org", "push")[0], "push")

    def test_device_and_bots(self):
        self.assertEqual(device_class(MOBILE_UA), "mobile")
        self.assertEqual(device_class(DESKTOP_UA), "desktop")
        self.assertTrue(is_bot("Googlebot/2.1"))
        self.assertTrue(is_bot(""))
        self.assertFalse(is_bot(MOBILE_UA))

    def test_visitor_hash_rotates_daily_and_stores_no_ip(self):
        h1 = visitor_hash("203.0.113.9", MOBILE_UA)
        self.assertEqual(h1, visitor_hash("203.0.113.9", MOBILE_UA))
        self.assertNotIn("203.0.113.9", h1)
        view = record_view(ip="203.0.113.9", user_agent=MOBILE_UA, path="/", referrer="")
        self.assertFalse(any("203.0.113" in str(getattr(view, f.attname)) for f in PageView._meta.fields))
        from datetime import timedelta
        from unittest import mock

        from django.core.cache import cache
        from django.utils import timezone

        from arcms.analytics import collector
        from arcms.analytics.tasks import purge

        tomorrow = timezone.localdate() + timedelta(days=1)
        with mock.patch("arcms.analytics.collector.timezone.localdate", return_value=tomorrow):
            self.assertNotEqual(h1, visitor_hash("203.0.113.9", MOBILE_UA))
            self.assertEqual(list(collector._salts), [tomorrow])  # لا يبقى في الذاكرة ملح يوم مضى
            cache.set(f"arcms:salt:{timezone.localdate():%Y-%m-%d}", "legacy", 3600)  # بقايا إصدار سابق
            purge()
        self.assertEqual(list(DailySalt.objects.values_list("day", flat=True)), [tomorrow])
        self.assertIsNone(cache.get(f"arcms:salt:{timezone.localdate():%Y-%m-%d}"))

    def test_beacon_endpoint_and_view_count(self):
        article = make_article("خبر")
        resp = self.client.post(reverse("public:beacon"), data=f'{{"p": "/post/{article.pk}/", "a": {article.pk}}}',
                                content_type="application/json", HTTP_USER_AGENT=MOBILE_UA)
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(PageView.objects.count(), 1)
        self.assertEqual(Article.objects.get(pk=article.pk).view_count, 1)

    def test_beacon_respects_dnt_and_toggle(self):
        self.client.post(reverse("public:beacon"), data="{}", content_type="application/json",
                         HTTP_USER_AGENT=MOBILE_UA, HTTP_DNT="1")
        self.assertEqual(PageView.objects.count(), 0)
        site = SiteSettings.load()
        site.analytics_enabled = False
        site.save()
        self.client.post(reverse("public:beacon"), data="{}", content_type="application/json", HTTP_USER_AGENT=MOBILE_UA)
        self.assertEqual(PageView.objects.count(), 0)

    def test_bots_not_counted(self):
        self.client.post(reverse("public:beacon"), data="{}", content_type="application/json", HTTP_USER_AGENT="curl/8")
        self.assertEqual(PageView.objects.count(), 0)


class AggregationTests(ArcmsTestCase):
    def test_aggregate_and_top(self):
        a = make_article("الأكثر")
        b = make_article("الأقل")
        for n in range(5):
            record_view(ip=f"10.0.0.{n}", user_agent=MOBILE_UA, path="/x", referrer="https://t.me/", article_id=a.pk)
        record_view(ip="10.0.0.1", user_agent=DESKTOP_UA, path="/y", referrer="", article_id=b.pk)
        stat = aggregate_day(timezone.localdate())
        self.assertEqual(stat.views, 6)
        self.assertEqual(stat.visitors, 6)
        self.assertEqual(stat.by_source["messaging"], 5)
        top = queries.top_articles(1, 5)
        self.assertEqual(top[0][:2], (a.pk, 5))
        self.assertEqual(queries.most_read_ids(1, 1)[0], a.pk)
        self.assertEqual(queries.today_numbers()["views"], 6)
        self.assertEqual(queries.realtime()["active"], 6)

    def test_purge_old_salts_and_raw_views(self):
        DailySalt.objects.create(day=timezone.localdate() - timedelta(days=1), salt="old")
        old = PageView.objects.create(path="/", visitor="x")
        PageView.objects.filter(pk=old.pk).update(ts=timezone.now() - timedelta(days=400))
        purge()
        self.assertFalse(DailySalt.objects.filter(salt="old").exists())
        self.assertFalse(PageView.objects.filter(pk=old.pk).exists())

    def test_series_fills_missing_days(self):
        DailySiteStat.objects.create(day=timezone.localdate() - timedelta(days=2), views=40, visitors=10)
        series = queries.series(7)
        self.assertEqual(len(series), 7)
        self.assertEqual(series[-3]["views"], 40)


class ChartTests(ArcmsTestCase):
    def test_ticks_are_clean(self):
        self.assertEqual(nice_ticks(1523), [0, 500, 1000, 1500, 2000])
        self.assertEqual(nice_ticks(0), [0, 1])

    def test_geometry_rtl_and_strings(self):
        today = timezone.localdate()
        series = [{"day": today - timedelta(days=2 - i), "views": v, "visitors": v // 2} for i, v in enumerate([10, 0, 30])]
        chart = daily_columns(series)
        xs = [float(b["x"]) for b in chart["bars"]]
        self.assertGreater(xs[0], xs[-1])  # الأقدم على اليمين
        self.assertEqual(chart["bars"][1]["path"], "")  # يوم بلا زيارات لا يُرسم له عمود
        self.assertTrue(all("," not in b["hit_x"] for b in chart["bars"]))
