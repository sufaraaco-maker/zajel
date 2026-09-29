from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.analytics import searches
from arcms.analytics.models import SearchStat
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user

UA = {"HTTP_USER_AGENT": "Mozilla/5.0 (Linux; Android 14) Mobile Safari"}


class SearchInsightTests(ArcmsTestCase):
    def search(self, q, **extra):
        return self.client.get(reverse("public:search"), {"q": q, **extra}, **UA)

    def test_aggregates_normalized_queries_per_day(self):
        with self.captureOnCommitCallbacks(execute=True):  # الفهرسة بعد الحفظ
            make_article("افتتاح مكتبة عامة في المدينة")
        for q in ("مكتبة", "مكتبه", "  مَكْتَبَة "):  # التاء المربوطة والتشكيل والمسافات
            self.search(q)
        self.search("زلزال")
        row = SearchStat.objects.get(query=searches.key("مكتبة"))
        self.assertEqual(row.searches, 3)
        self.assertGreater(row.results, 0)
        self.assertEqual(SearchStat.objects.get(query="زلزال").results, 0)
        self.assertEqual([r["query"] for r in searches.unanswered()], ["زلزال"])
        self.assertEqual(searches.top()[0]["n"], 3)

    def test_private_bots_staff_filters_and_settings_skipped(self):
        self.search("0599123456")
        self.search("someone@example.org")
        self.search("x")
        self.client.get(reverse("public:search"), {"q": "روبوت"}, HTTP_USER_AGENT="Googlebot/2.1")
        self.search("مع تصفية", kind="news")
        self.search("صفحة ثانية", page=2)
        self.client.get(reverse("public:search"), {"q": "لا تتبعني"}, HTTP_DNT="1", **UA)
        login(self.client, make_user("ed", Role.EDITOR))
        self.search("بحث المحرر")
        self.assertFalse(SearchStat.objects.exists())
        self.client.logout()
        SiteSettings.objects.update(analytics_enabled=False)
        SiteSettings.forget_local()
        from django.core.cache import cache

        cache.clear()
        self.search("القياس معطّل")
        self.assertFalse(SearchStat.objects.exists())

    def test_retention_and_studio_panel(self):
        SearchStat.objects.create(day=timezone.localdate() - timedelta(days=100), query="قديم", sample="قديم", searches=4)
        SearchStat.objects.create(day=timezone.localdate(), query="جديد", sample="جديد", searches=2)
        searches.purge()
        self.assertEqual(list(SearchStat.objects.values_list("query", flat=True)), ["جديد"])
        login(self.client, make_user("chief", Role.CHIEF))
        resp = self.client.get(reverse("studio:analytics"))
        self.assertContains(resp, "بحثوا ولم يجدوا")
        self.assertContains(resp, "جديد")

    def test_concurrent_first_insert_is_safe(self):
        """زميل أنشأ صف اليوم بين محاولة التحديث والإنشاء: لا يضيع البحث ولا يفشل الطلب."""
        from django.db.models.query import QuerySet

        SearchStat.objects.create(day=timezone.localdate(), query="سباق", sample="سباق", searches=1)
        real_update = QuerySet.update
        calls = []

        def first_update_misses(qs, **kwargs):
            calls.append(1)
            return 0 if len(calls) == 1 else real_update(qs, **kwargs)

        with mock.patch.object(QuerySet, "update", first_update_misses):
            self.assertTrue(searches.record("سباق", 0, user_agent=UA["HTTP_USER_AGENT"]))
        self.assertEqual(SearchStat.objects.get(query="سباق").searches, 2)
