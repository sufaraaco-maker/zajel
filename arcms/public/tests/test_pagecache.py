import threading
import time
from unittest import mock
from urllib.parse import unquote

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content.models import Article, Page, Status
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user
from arcms.public import pagecache


class ReaderCacheTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.article = make_article("خبر للقرّاء")
        self.url = self.article.get_absolute_url()

    def test_miss_then_hit_with_same_page_and_headers(self):
        first = self.client.get(self.url)
        second = self.client.get(self.url)
        self.assertEqual((first["X-Page-Cache"], second["X-Page-Cache"]), ("miss", "hit"))
        self.assertEqual(first.content, second.content)
        for resp in (first, second):
            self.assertEqual(resp["Cache-Control"], "public, max-age=0, s-maxage=10")
            self.assertIn("Content-Security-Policy", resp)
            self.assertFalse(resp.cookies)
        self.assertEqual(self.client.get(self.url + "?utm_source=telegram&fbclid=x")["X-Page-Cache"], "hit")
        self.assertEqual(self.client.head(self.url)["X-Page-Cache"], "hit")

    def test_hits_are_precompressed_for_gzip_clients(self):
        import gzip

        plain = self.client.get(self.url).content
        packed = self.client.get(self.url, HTTP_ACCEPT_ENCODING="gzip, br")
        self.assertEqual(packed["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", packed["Vary"])
        self.assertEqual(gzip.decompress(packed.content), plain)
        identity = self.client.get(self.url)
        self.assertNotIn("Content-Encoding", identity)
        self.assertEqual(identity.content, plain)

    def test_other_processes_share_pages(self):
        self.client.get(self.url)
        pagecache.clear_local()  # كأنها عملية أخرى: الصفحة من المخزن المشترك
        self.assertEqual(self.client.get(self.url)["X-Page-Cache"], "hit")

    def test_publishing_shows_new_content_at_once(self):
        latest = reverse("public:latest")
        self.client.get(latest)
        self.assertEqual(self.client.get(latest)["X-Page-Cache"], "hit")
        with self.captureOnCommitCallbacks(execute=True):
            make_article("عاجل جديد على الرئيسية")
        resp = self.client.get(latest)
        self.assertEqual(resp["X-Page-Cache"], "miss")
        self.assertContains(resp, "عاجل جديد على الرئيسية")

    def test_previous_copy_while_another_server_rebuilds(self):
        from arcms.content.signals import invalidate_public_cache

        before = self.client.get(self.url).content
        invalidate_public_cache()
        key = f"arcms:resp:http://testserver{unquote(self.url)}?"
        cache.add(f"{key}:rebuild", 1, 15)  # خادم آخر يعيد بناءها الآن
        resp = self.client.get(self.url)
        self.assertEqual((resp["X-Page-Cache"], resp.content), ("stale", before))
        cache.delete(f"{key}:rebuild")
        self.assertEqual(self.client.get(self.url)["X-Page-Cache"], "miss")
        self.assertEqual(self.client.get(self.url)["X-Page-Cache"], "hit")

    def test_unpublished_article_is_gone_at_once(self):
        self.client.get(self.url)
        with self.captureOnCommitCallbacks(execute=True):
            Article.objects.filter(pk=self.article.pk).update(status=Status.DRAFT)
            from arcms.content.signals import invalidate_public_cache

            invalidate_public_cache()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        cache.add(f"arcms:resp:http://testserver{unquote(self.url)}?:rebuild", 1, 15)  # حتى مع قفل إعادة البناء
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_and_messages_bypass(self):
        self.client.get(self.url)
        login(self.client, make_user("ed", Role.EDITOR))
        resp = self.client.get(self.url)
        self.assertNotIn("X-Page-Cache", resp)
        self.assertNotIn("public", resp.get("Cache-Control", ""))

    def test_pages_with_csrf_forms_are_never_stored(self):
        Page.objects.create(title="اتصل بنا", slug="contact", body="<p>راسلونا</p>", is_published=True,
                            show_contact_form=True)
        url = reverse("public:page", args=["contact"])
        for _ in range(2):
            resp = self.client.get(url)
            self.assertNotIn("X-Page-Cache", resp)
            self.assertIn("csrfmiddlewaretoken", resp.content.decode())
        Page.objects.create(title="من نحن", slug="about", body="<p>نحن</p>", is_published=True)
        self.client.get(reverse("public:page", args=["about"]))
        self.assertEqual(self.client.get(reverse("public:page", args=["about"]))["X-Page-Cache"], "hit")

    def test_errors_redirects_and_disabled_cache(self):
        self.assertNotIn("X-Page-Cache", self.client.get("/post/999999/x"))
        self.assertEqual(self.client.get(f"/post/{self.article.pk}/").status_code, 301)
        site = SiteSettings.load()
        site.home_cache_seconds = 0
        site.save()
        pagecache.clear_local()
        self.assertNotIn("X-Page-Cache", self.client.get(self.url))

    def test_article_without_slug_does_not_redirect_to_itself(self):
        # مواد أُدرجت جماعياً (استيراد) بلا رابط نصي: /post/5/- هو رابطها، لا حلقة تحويل
        Article.objects.bulk_create([Article(title="!!!", status=Status.PUBLISHED, category=make_category("قسم"),
                                             published_at=self.article.published_at)])
        bare = Article.objects.get(title="!!!")
        self.assertEqual(bare.get_absolute_url(), f"/post/{bare.pk}/-")
        self.assertEqual(self.client.get(bare.get_absolute_url()).status_code, 200)


class SingleBuildTests(ArcmsTestCase):
    def test_concurrent_misses_build_once(self):
        calls = []

        def slow_view(request):
            calls.append(1)
            time.sleep(0.2)
            return HttpResponse("<p>صفحة</p>")

        view = pagecache.reader_cache(slow_view)
        # الخيوط هنا لا تلمس القاعدة (قاعدة الاختبار لا تحتمل الكتابة المتزامنة)
        self.enterContext(mock.patch.object(pagecache, "_ttl", lambda: 30))
        self.enterContext(mock.patch.object(pagecache, "_public_version", lambda: 1))
        factory = RequestFactory()
        results = []

        def hit():
            results.append(view(factory.get("/some-page/"))["X-Page-Cache"])

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(results), ["miss"] + ["wait"] * 7)


class PushKeyTests(ArcmsTestCase):
    def test_key_is_in_the_page_only_when_push_is_on(self):
        from arcms.distribution.models import Channel, ChannelConfig

        self.assertNotContains(self.client.get("/"), "data-push")
        with override_settings(ARCMS_VAPID_PUBLIC_KEY="BPublicKey", ARCMS_VAPID_PRIVATE_KEY="x"):
            cfg = ChannelConfig.get(Channel.PUSH)
            cfg.enabled = True
            cfg.save()  # يبطل الصفحات المخزنة
            self.assertContains(self.client.get("/"), 'data-push="BPublicKey"')
            self.assertEqual(self.client.get(reverse("public:push_config")).json(), {"enabled": True, "key": "BPublicKey"})
        cache.clear()
