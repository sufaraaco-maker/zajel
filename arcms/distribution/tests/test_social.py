from unittest import mock

from django.test import override_settings
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content.models import BreakingNews
from arcms.core.jobs import run_pending
from arcms.core.models import Job
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user
from arcms.distribution import channels
from arcms.distribution.dispatch import on_article_published, on_breaking_created
from arcms.distribution.models import Channel, Delivery
from arcms.distribution.tests.test_distribution import enable, fake_response

FB = {"ARCMS_FACEBOOK_PAGE_ID": "12345", "ARCMS_FACEBOOK_PAGE_TOKEN": "page-token"}
X = {"ARCMS_X_API_KEY": "ck", "ARCMS_X_API_SECRET": "cs", "ARCMS_X_ACCESS_TOKEN": "at", "ARCMS_X_ACCESS_SECRET": "as"}


class OAuthTests(ArcmsTestCase):
    def test_signature_matches_published_example(self):
        # مثال توثيق X/Twitter «Creating a signature»
        header = channels.oauth1_header(
            "POST", "https://api.twitter.com/1.1/statuses/update.json",
            consumer_key="xvz1evFS4wEEPTGEFPHBog",
            consumer_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
            extra_params={"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true"},
            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958",
        )
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', header)
        self.assertTrue(header.startswith("OAuth "))

    def test_social_text_fits_x_limit(self):
        text = channels.social_text(title="عنوان " * 80, summary="ملخص", url="https://news.example/s/1", kicker="عاجل",
                                    limit=channels.X_LIMIT)
        body, url = text.rsplit("\n\n", 1)
        self.assertEqual(url, "https://news.example/s/1")
        self.assertLessEqual(len(body) + 2 + channels.X_URL_LENGTH, channels.X_LIMIT)
        self.assertNotIn("ملخص", text)
        self.assertTrue(body.startswith("عاجل | "))


@override_settings(**FB, **X)
class SocialDeliveryTests(ArcmsTestCase):
    def test_article_goes_to_facebook_and_x(self):
        enable(Channel.FACEBOOK)
        enable(Channel.X)
        article = make_article("افتتاح مكتبة جديدة", subtitle="تضم عشرين ألف كتاب", send_telegram=False)
        created = on_article_published(article.pk)
        self.assertEqual({d.channel for d in created}, {"facebook", "x"})
        fb_ok = fake_response(200, {"id": "12345_999"})
        x_ok = fake_response(201, {"data": {"id": "1800000000000000000"}})
        with mock.patch("arcms.distribution.channels.requests.post", side_effect=[fb_ok, x_ok]) as post:
            run_pending()
        fb_call, x_call = post.call_args_list
        self.assertIn("graph.facebook.com/v21.0/12345/feed", fb_call.args[0])
        self.assertIn("تضم عشرين ألف كتاب", fb_call.kwargs["data"]["message"])
        self.assertEqual(fb_call.kwargs["data"]["access_token"], "page-token")
        self.assertEqual(x_call.args[0], "https://api.x.com/2/tweets")
        self.assertTrue(x_call.kwargs["headers"]["Authorization"].startswith("OAuth "))
        self.assertIn("افتتاح مكتبة جديدة".encode(), x_call.kwargs["data"])
        self.assertEqual(set(Delivery.objects.values_list("status", flat=True)), {Delivery.Status.SENT})
        self.assertEqual(Delivery.objects.get(channel="x").external_id, "1800000000000000000")

    def test_article_opt_out_and_disabled_channel(self):
        enable(Channel.FACEBOOK)
        article = make_article("خبر", send_facebook=False, send_x=True, send_telegram=False)
        self.assertEqual(on_article_published(article.pk), [])  # فيسبوك مستبعد، وإكس غير مفعّلة

    def test_expired_token_fails_permanently_rate_limit_retries(self):
        enable(Channel.FACEBOOK)
        on_article_published(make_article("أ", send_telegram=False, send_x=False).pk)
        expired = fake_response(400, {"error": {"code": 190, "message": "Error validating access token"}})
        with mock.patch("arcms.distribution.channels.requests.post", return_value=expired):
            run_pending()
        d = Delivery.objects.get()
        self.assertEqual(d.status, Delivery.Status.FAILED)
        self.assertIn("190", d.error)

        Delivery.objects.all().delete()
        Job.objects.all().delete()
        on_article_published(make_article("ب", send_telegram=False, send_x=False).pk)
        limited = fake_response(400, {"error": {"code": 4, "message": "Application request limit reached"}})
        with mock.patch("arcms.distribution.channels.requests.post", return_value=limited):
            run_pending()
        self.assertEqual(Delivery.objects.get().status, Delivery.Status.PENDING)
        self.assertEqual(Job.objects.get(kind="dist.facebook").status, Job.Status.QUEUED)

    def test_breaking_to_x(self):
        enable(Channel.X)
        item = BreakingNews.objects.create(text="عاجل: انقطاع الكهرباء", send_telegram=False, send_push=False, send_x=True)
        created = on_breaking_created(item.pk)
        self.assertEqual([d.channel for d in created], ["x"])

    def test_missing_credentials_fail_clearly(self):
        with override_settings(ARCMS_X_API_KEY=""):
            with self.assertRaises(Exception) as ctx:
                channels.x_post("نص")
        self.assertIn("ARCMS_X_API_KEY", str(ctx.exception))

    def test_channel_test_checks_without_posting(self):
        login(self.client, make_user("social", Role.SOCIAL))
        page_ok = fake_response(200, {"name": "شبكة الغد"})
        with mock.patch("arcms.distribution.channels.requests.get", return_value=page_ok), \
                mock.patch("arcms.distribution.channels.requests.post") as post:
            resp = self.client.post(reverse("studio:channel_test", args=["facebook"]), follow=True)
        self.assertContains(resp, "شبكة الغد")
        post.assert_not_called()
        me = fake_response(200, {"data": {"username": "alghad"}})
        with mock.patch("arcms.distribution.channels.requests.get", return_value=me):
            resp = self.client.post(reverse("studio:channel_test", args=["x"]), follow=True)
        self.assertContains(resp, "@alghad")


class SocialStudioTests(ArcmsTestCase):
    def test_channel_pages(self):
        login(self.client, make_user("chief", Role.CHIEF))
        resp = self.client.get(reverse("studio:distribution"))
        self.assertContains(resp, "صفحة فيسبوك")
        self.assertContains(resp, "إكس")
        for ch in ("facebook", "x"):
            self.assertEqual(self.client.get(reverse("studio:channel_edit", args=[ch])).status_code, 200)
        resp = self.client.get(reverse("studio:article_new"))
        self.assertContains(resp, 'name="send_facebook"')
        self.assertContains(resp, 'name="send_x"')
