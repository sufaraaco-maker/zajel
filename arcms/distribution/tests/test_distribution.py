from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.content.models import BreakingNews
from arcms.core.jobs import PermanentError, run_pending
from arcms.core.models import Job
from arcms.core.testing import ArcmsTestCase, make_article, make_user
from arcms.distribution import channels
from arcms.distribution.dispatch import on_article_published, on_breaking_created
from arcms.distribution.models import (
    Channel,
    ChannelConfig,
    Delivery,
    NewsletterSubscriber,
    PushSubscription,
    WhatsAppSubscriber,
)
from arcms.distribution.newsletter import build_issue, send_issue


def fake_response(status=200, payload=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {"ok": True, "result": {"message_id": 77}}
    resp.text = ""
    return resp


def enable(channel, **fields):
    cfg = ChannelConfig.get(channel)
    cfg.enabled = True
    for k, v in fields.items():
        setattr(cfg, k, v)
    cfg.save()
    return cfg


@override_settings(ARCMS_TELEGRAM_BOT_TOKEN="123:ABC", SITE_URL="https://news.example.org")
class TelegramTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        enable(Channel.TELEGRAM, telegram_chat_ids="@news, -100123", with_image=False)
        self.article = make_article("خبر للتوزيع", subtitle="تفاصيل <مهمة>")

    def test_publish_queues_one_delivery_per_channel_id(self):
        created = on_article_published(self.article.pk)
        self.assertEqual(sorted(d.target for d in created), ["-100123", "@news"])
        self.assertEqual(Job.objects.filter(kind="dist.telegram").count(), 2)
        # إعادة النشر لا تُغرق القنوات
        self.assertEqual(on_article_published(self.article.pk), [])

    def test_send_success_uses_html_and_short_link(self):
        on_article_published(self.article.pk)
        with mock.patch("arcms.distribution.channels.requests.post", return_value=fake_response()) as post:
            run_pending()
        self.assertEqual(Delivery.objects.filter(status=Delivery.Status.SENT).count(), 2)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("<b>خبر للتوزيع</b>", payload["text"])
        self.assertIn("&lt;مهمة&gt;", payload["text"])
        self.assertIn(f"https://news.example.org/s/{self.article.pk}", payload["text"])
        self.assertIn("/bot123:ABC/sendMessage", post.call_args.args[0])

    def test_permanent_error_marks_failed_without_retry(self):
        on_article_published(self.article.pk)
        bad = fake_response(400, {"ok": False, "description": "Bad Request: chat not found"})
        with mock.patch("arcms.distribution.channels.requests.post", return_value=bad):
            run_pending()
        self.assertEqual(Delivery.objects.filter(status=Delivery.Status.FAILED).count(), 2)
        self.assertIn("chat not found", Delivery.objects.first().error)
        self.assertEqual(Job.objects.filter(status=Job.Status.FAILED).count(), 2)

    def test_rate_limit_is_retried(self):
        on_article_published(self.article.pk)
        limited = fake_response(429, {"ok": False, "parameters": {"retry_after": 5}})
        with mock.patch("arcms.distribution.channels.requests.post", return_value=limited):
            run_pending()
        job = Job.objects.filter(kind="dist.telegram").first()
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertGreater(job.run_after, timezone.now())
        self.assertEqual(job.attempts, 1)

    def test_breaking_goes_to_telegram(self):
        chief = make_user("chief", Role.CHIEF)
        item = BreakingNews.objects.create(text="عاجل: خبر مهم", created_by=chief, send_push=False)
        self.assertEqual(len(on_breaking_created(item.pk)), 2)

    def test_missing_token_is_permanent(self):
        with override_settings(ARCMS_TELEGRAM_BOT_TOKEN=""):
            with self.assertRaises(PermanentError):
                channels.telegram_api("getMe", {})


@override_settings(ARCMS_WHATSAPP_TOKEN="tok", ARCMS_WHATSAPP_PHONE_NUMBER_ID="999")
class WhatsAppTests(ArcmsTestCase):
    def test_template_sent_to_active_subscribers(self):
        enable(Channel.WHATSAPP, whatsapp_template="daily_news")
        WhatsAppSubscriber.objects.create(phone="970599000001")
        WhatsAppSubscriber.objects.create(phone="970599000002")
        WhatsAppSubscriber.objects.create(phone="970599000003", is_active=False)
        article = make_article("خبر واتساب", send_whatsapp=True, send_telegram=False)
        on_article_published(article.pk)
        ok = fake_response(200, {"messages": [{"id": "wamid.1"}]})
        with mock.patch("arcms.distribution.channels.requests.post", return_value=ok) as post:
            run_pending()
        self.assertEqual(post.call_count, 2)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["template"]["name"], "daily_news")
        self.assertEqual(body["template"]["components"][0]["parameters"][0]["text"], "خبر واتساب")
        self.assertEqual(Delivery.objects.get().recipients, 2)


def push_keys() -> dict:
    import base64
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    def b64(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    return {"p256dh": b64(point), "auth": b64(os.urandom(16))}


@override_settings(ARCMS_VAPID_PRIVATE_KEY="x", ARCMS_VAPID_PUBLIC_KEY="y")
class PushTests(ArcmsTestCase):
    def test_gone_subscriptions_deactivated(self):
        enable(Channel.PUSH)
        live = PushSubscription.objects.create(endpoint="https://push.example/a", p256dh="k", auth="a")
        gone = PushSubscription.objects.create(endpoint="https://push.example/b", p256dh="k", auth="a")
        article = make_article("عاجل للإشعار", is_breaking=True, send_telegram=False)
        on_article_published(article.pk)

        def fake_send(sub, payload):
            self.assertIn("عاجل للإشعار", payload["body"])
            if sub.pk == gone.pk:
                raise channels.SubscriptionGone()

        with mock.patch("arcms.distribution.tasks.channels.push_send", side_effect=fake_send):
            run_pending()
        gone.refresh_from_db()
        live.refresh_from_db()
        self.assertFalse(gone.is_active)
        self.assertIsNotNone(live.last_success_at)
        self.assertEqual(Delivery.objects.get().recipients, 1)

    def test_subscribe_endpoint(self):
        good = push_keys()
        url = reverse("public:push_subscribe")
        resp = self.client.post(url, {"endpoint": "https://fcm.googleapis.com/fcm/send/z", "keys": good},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(PushSubscription.objects.filter(endpoint="https://fcm.googleapis.com/fcm/send/z").exists())
        bad = [
            {"endpoint": "http://insecure"},
            {"endpoint": "https://10.0.0.5/internal", "keys": good},  # لا طلبات إلى عناوين داخلية
            {"endpoint": "https://fcm.googleapis.com.evil.example/x", "keys": good},
            {"endpoint": "https://fcm.googleapis.com:8443/x", "keys": good},
            {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/x", "keys": {"p256dh": "!!!", "auth": "!!!"}},
            {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/x", "keys": {"p256dh": "", "auth": ""}},
            {"endpoint": "https://web.push.apple.com/x", "keys": "nope"},
        ]
        for body in bad:
            self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 400, body)
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_malformed_subscription_does_not_break_delivery(self):
        """اشتراك مشوّه (من قبل التحقق) يُعطَّل، ويصل الإشعار لغيره مرة واحدة."""
        import base64

        from cryptography.hazmat.primitives.asymmetric import ec

        raw = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
        pem = base64.urlsafe_b64encode(raw).decode().rstrip("=")  # بصيغة arcms_keys
        enable(Channel.PUSH)
        broken = PushSubscription.objects.create(endpoint="https://fcm.googleapis.com/fcm/send/a", p256dh="!!!", auth="!!!")
        ok = PushSubscription.objects.create(endpoint="https://fcm.googleapis.com/fcm/send/b", **push_keys())
        article = make_article("عاجل", is_breaking=True, send_telegram=False)
        on_article_published(article.pk)
        with override_settings(ARCMS_VAPID_PRIVATE_KEY=pem), \
                mock.patch("pywebpush.requests.post", return_value=fake_response(201, {})) as post:
            run_pending()
        self.assertEqual(post.call_count, 1)
        broken.refresh_from_db()
        ok.refresh_from_db()
        self.assertFalse(broken.is_active)
        self.assertTrue(ok.is_active)
        self.assertEqual(Delivery.objects.get().recipients, 1)


@override_settings(SITE_URL="https://news.example.org")
class NewsletterTests(ArcmsTestCase):
    def test_double_opt_in_and_unsubscribe(self):
        resp = self.client.post(reverse("public:newsletter_subscribe"), {"email": "Reader@Example.org"},
                                HTTP_ORIGIN="http://testserver")
        self.assertEqual(resp.status_code, 302)
        sub = NewsletterSubscriber.objects.get(email="reader@example.org")
        self.assertIsNone(sub.confirmed_at)
        self.assertEqual(len(mail.outbox), 0)  # الصفحة لا تنتظر خادم البريد
        run_pending()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(sub.token, mail.outbox[0].body)
        self.client.get(reverse("public:newsletter_confirm", args=[sub.token]))
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
        self.client.post(reverse("public:newsletter_unsubscribe", args=[sub.token]))
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    def test_mail_outage_does_not_break_the_page(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError("smtp down")):
            resp = self.client.post(reverse("public:newsletter_subscribe"), {"email": "later@example.org"},
                                    HTTP_ORIGIN="http://testserver")
            self.assertEqual(resp.status_code, 302)
            run_pending()  # تفشل المحاولة وتُعاد لاحقاً
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(NewsletterSubscriber.objects.filter(email="later@example.org").exists())

    def test_honeypot_blocks_bots(self):
        self.client.post(reverse("public:newsletter_subscribe"), {"email": "bot@example.org", "website": "spam"},
                         HTTP_ORIGIN="http://testserver")
        self.assertFalse(NewsletterSubscriber.objects.exists())

    def test_daily_issue(self):
        make_article("أبرز خبر", priority=5)
        make_article("خبر ثانٍ")
        make_article("خبر قديم", published_at=timezone.now() - timedelta(days=3))
        make_article("ليس للنشرة", in_newsletter=False)
        for n in range(3):
            NewsletterSubscriber.objects.create(email=f"r{n}@example.org", confirmed_at=timezone.now())
        NewsletterSubscriber.objects.create(email="pending@example.org")
        issue = build_issue(timezone.localdate())
        self.assertEqual(len(issue.article_ids), 2)
        self.assertIn("أبرز خبر", issue.html)
        self.assertNotIn("خبر قديم", issue.html)
        result = send_issue(issue)
        self.assertEqual(result["sent"], 3)
        msg = mail.outbox[-1]
        self.assertIn("List-Unsubscribe", msg.extra_headers)
        self.assertIn("/newsletter/unsubscribe/", msg.alternatives[0][0])
        self.assertNotIn("{{unsubscribe_url}}", msg.body)

    def test_scheduler_enqueues_once_per_day(self):
        from arcms.core.models import SiteSettings
        from arcms.distribution.tasks import schedule_newsletter

        enable(Channel.NEWSLETTER)
        site = SiteSettings.load()
        site.newsletter_hour = 0
        site.save()
        schedule_newsletter()
        schedule_newsletter()
        self.assertEqual(Job.objects.filter(kind="newsletter.send").count(), 1)


class ManualResendTests(ArcmsTestCase):
    @override_settings(ARCMS_TELEGRAM_BOT_TOKEN="1:x")
    def test_social_editor_can_resend(self):
        from arcms.core.testing import login

        enable(Channel.TELEGRAM, telegram_chat_ids="@c")
        social = make_user("social", Role.SOCIAL)
        article = make_article("قديم", distributed_at=timezone.now())
        login(self.client, social)
        self.client.post(reverse("studio:article_distribute", args=[article.pk]), {"channel": ["telegram"]})
        self.assertEqual(Delivery.objects.filter(article=article).count(), 1)
