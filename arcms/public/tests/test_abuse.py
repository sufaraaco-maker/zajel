import json
from unittest import mock

from django.core import mail
from django.urls import reverse

from arcms.content.models import ContactMessage, Page
from arcms.core.ratelimit import exceeded
from arcms.core.testing import ArcmsTestCase, make_article
from arcms.core.utils import json_script_safe
from arcms.distribution.models import NewsletterSubscriber, PushSubscription


class RateLimitTests(ArcmsTestCase):
    def test_counter_window(self):
        self.assertFalse(any(exceeded("t", "1.2.3.4", limit=3, window=60) for _ in range(3)))
        self.assertTrue(exceeded("t", "1.2.3.4", limit=3, window=60))
        self.assertFalse(exceeded("t", "5.6.7.8", limit=3, window=60))
        self.assertFalse(exceeded("t", None, limit=0, window=60))

    def test_keys_do_not_contain_raw_ip(self):
        from django.core.cache import cache

        exceeded("t", "203.0.113.9", limit=3, window=60)
        keys = list(getattr(cache, "_cache", {}).keys())
        self.assertTrue(keys)
        self.assertFalse(any("203.0.113.9" in str(k) for k in keys))


SMALL_LIMITS = {"beacon": (120, 60), "push": (20, 3600), "newsletter-ip": (10, 3600), "newsletter-mail": (3, 86400),
                "contact": (5, 3600)}


@mock.patch.dict("arcms.public.views.RATE_LIMITS", SMALL_LIMITS)
class NewsletterAbuseTests(ArcmsTestCase):
    def test_confirmation_mails_throttled_per_address(self):
        for _ in range(6):
            self.client.post(reverse("public:newsletter_subscribe"), {"email": "target@example.org"}, REMOTE_ADDR="198.51.100.1")
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(NewsletterSubscriber.objects.filter(email="target@example.org").count(), 1)

    def test_requests_throttled_per_ip(self):
        for n in range(12):
            self.client.post(reverse("public:newsletter_subscribe"), {"email": f"u{n}@example.org"}, REMOTE_ADDR="198.51.100.2")
        self.assertEqual(NewsletterSubscriber.objects.count(), 10)

    def test_redirect_back_only_to_same_site(self):
        resp = self.client.post(
            reverse("public:newsletter_subscribe"), {"email": "x@example.org"}, HTTP_REFERER="https://evil.example/phish"
        )
        self.assertEqual(resp["Location"], "/")
        resp = self.client.post(
            reverse("public:newsletter_subscribe"), {"email": "y@example.org"}, HTTP_REFERER="http://testserver/news/"
        )
        self.assertEqual(resp["Location"], "http://testserver/news/")


@mock.patch.dict("arcms.public.views.RATE_LIMITS", SMALL_LIMITS)
class EndpointAbuseTests(ArcmsTestCase):
    def test_push_subscribe_throttled(self):
        codes = []
        for n in range(22):
            body = json.dumps({"endpoint": f"https://push.example/{n}", "keys": {"p256dh": "k", "auth": "a"}})
            codes.append(self.client.post(reverse("public:push_subscribe"), body, content_type="application/json").status_code)
        self.assertEqual(codes.count(429), 2)
        self.assertEqual(PushSubscription.objects.count(), 20)

    def test_beacon_flood_not_counted(self):
        article = make_article()
        payload = json.dumps({"p": article.get_absolute_url(), "a": article.pk})
        with mock.patch("arcms.public.views.record_view") as rec:
            for _ in range(130):
                self.client.post(reverse("public:beacon"), payload, content_type="application/json")
        self.assertEqual(rec.call_count, 120)

    def test_contact_form_throttled(self):
        Page.objects.create(title="اتصل بنا", slug="contact", body="<p>راسلونا</p>", is_published=True, show_contact_form=True)
        url = reverse("public:page", args=["contact"])
        for n in range(7):
            self.client.post(url, {"name": "قارئ", "body": f"رسالة {n}"})
        self.assertEqual(ContactMessage.objects.count(), 5)


class JsonLdTests(ArcmsTestCase):
    def test_script_breakout_escaped(self):
        self.assertNotIn("</script>", json_script_safe({"h": "</script><script>alert(1)</script>"}))
        article = make_article("عنوان </script><script>alert(1)</script> & غيره")
        resp = self.client.get(article.get_absolute_url())
        html = resp.content.decode()
        ld = html.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
        self.assertIn("\\u003c/script\\u003e", ld)
        self.assertEqual(json.loads(ld)["headline"][:6], "عنوان ")
