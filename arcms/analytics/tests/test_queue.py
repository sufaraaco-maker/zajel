from unittest import mock

from django.test import override_settings

from arcms.analytics import collector
from arcms.analytics.models import PageView
from arcms.core import ratelimit
from arcms.core.testing import ArcmsTestCase, make_article

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Mobile Safari/537.36"


@override_settings(ARCMS_ANALYTICS_QUEUE=True)
@mock.patch.object(collector, "_ensure_flusher", lambda: None)  # الدفعة تُكتب يدوياً هنا لا بخيط خلفي
class ViewQueueTests(ArcmsTestCase):
    def test_views_written_in_one_batch_with_counts(self):
        a, b = make_article("أ"), make_article("ب")
        for article in (a, a, b, None):
            collector.queue_view(ip="203.0.113.5", user_agent=UA, path="/x", referrer="", article_id=article and article.pk)
        collector.queue_view(ip="203.0.113.6", user_agent="Googlebot/2.1", path="/x", referrer="")
        self.assertEqual(PageView.objects.count(), 0)
        self.assertEqual(collector.flush_views(), 4)
        self.assertEqual(PageView.objects.count(), 4)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.view_count, b.view_count), (2, 1))
        self.assertEqual(collector.flush_views(), 0)

    def test_queue_is_bounded(self):
        with mock.patch.object(collector, "MAX_QUEUED", 3):
            for _ in range(5):
                collector.queue_view(ip="203.0.113.5", user_agent=UA, path="/x", referrer="")
            self.assertEqual(collector.flush_views(), 3)


class LocalRateLimitTests(ArcmsTestCase):
    def test_counts_per_source_and_window(self):
        results = [ratelimit.exceeded_local("t", "1.2.3.4", limit=3, window=60) for _ in range(4)]
        self.assertEqual(results, [False, False, False, True])
        self.assertFalse(ratelimit.exceeded_local("t", "5.6.7.8", limit=3, window=60))
        self.assertFalse(ratelimit.exceeded_local("t", None, limit=0, window=60))
        self.assertFalse(any("1.2.3.4" in str(k) for k in ratelimit._local_counts))
