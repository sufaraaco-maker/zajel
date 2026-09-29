import datetime as dt
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.audit.models import AuditEntry
from arcms.content.models import Article, Status
from arcms.core.testing import ArcmsTestCase, login, make_category, make_user
from arcms.wires import feeds, services
from arcms.wires.models import WireItem, WireKeyword, WireSource

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>وكالة</title>
<item>
  <title>افتتاح &amp; توسعة مستشفى في غزة</title>
  <link>https://agency.example/news/1</link>
  <guid isPermaLink="false">agency-1</guid>
  <pubDate>Tue, 29 Sep 2026 08:15:00 +0300</pubDate>
  <description>&lt;p&gt;ملخص &lt;b&gt;الخبر&lt;/b&gt;&lt;/p&gt;</description>
  <content:encoded><![CDATA[<p>المتن الكامل</p><script>alert(1)</script><p onclick="x()">فقرة ثانية</p>]]></content:encoded>
  <media:content url="https://agency.example/1.jpg" type="image/jpeg"/>
</item>
<item>
  <title>خبر ثانٍ بلا معرّف</title>
  <link>https://agency.example/news/2</link>
  <description>نص قصير</description>
</item>
<item><title></title><description>بلا عنوان يُهمل</description></item>
</channel></rss>""".encode()

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Wire</title>
<entry>
  <id>tag:agency.example,2026:5</id>
  <title type="html">وزير الخارجية يصل إلى القاهرة</title>
  <link rel="alternate" href="https://agency.example/a/5"/>
  <link rel="enclosure" type="image/jpeg" href="https://agency.example/5.jpg"/>
  <published>2026-09-29T06:00:00Z</published>
  <summary>وصل الوزير صباح اليوم.</summary>
</entry>
</feed>""".encode()

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://agency.example/"><title>RDF</title></channel>
<item rdf:about="https://agency.example/r/1"><title>خبر RDF</title><link>https://agency.example/r/1</link>
<dc:date>2026-09-28T10:00:00+02:00</dc:date><description>وصف</description></item>
</rdf:RDF>""".encode()

LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""

EXTERNAL = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<rss><channel><item><title>&x;</title></item></channel></rss>"""


class FakeResponse:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def iter_content(self, size):
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("headers", {})))
        return self.responses.pop(0)


def public(host, port):
    return ["93.184.216.34"]


@mock.patch("arcms.wires.feeds._resolve", public)
class ParseTests(ArcmsTestCase):
    def test_rss_fields_are_clean(self):
        entries = feeds.parse(RSS)
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first.title, "افتتاح & توسعة مستشفى في غزة")
        self.assertEqual(first.guid, "agency-1")
        self.assertEqual(first.summary, "ملخص الخبر")
        self.assertIn("المتن الكامل", first.body)
        self.assertNotIn("<script", first.body)
        self.assertNotIn("onclick", first.body)
        self.assertEqual(first.image_url, "https://agency.example/1.jpg")
        self.assertEqual(first.published_at.astimezone(dt.timezone.utc).hour, 5)  # 08:15+03:00
        self.assertEqual(entries[1].guid, "https://agency.example/news/2")

    def test_atom_and_rdf(self):
        atom = feeds.parse(ATOM)[0]
        self.assertEqual(atom.guid, "tag:agency.example,2026:5")
        self.assertEqual(atom.link, "https://agency.example/a/5")
        self.assertEqual(atom.image_url, "https://agency.example/5.jpg")
        self.assertEqual(atom.summary, "وصل الوزير صباح اليوم.")
        rdf = feeds.parse(RDF)[0]
        self.assertEqual(rdf.title, "خبر RDF")
        self.assertEqual(rdf.guid, "https://agency.example/r/1")
        self.assertIsNotNone(rdf.published_at)

    def test_entities_and_garbage_rejected(self):
        for bad in (LAUGHS, EXTERNAL, b"<html><body>not a feed", b"<html></html>"):
            with self.assertRaises(feeds.FeedError):
                feeds.parse(bad)

    def test_future_dates_are_clamped(self):
        feed = RSS.replace(b"Tue, 29 Sep 2026 08:15:00 +0300", b"Fri, 01 Jan 2100 00:00:00 +0000")
        self.assertLessEqual(feeds.parse(feed)[0].published_at, timezone.now())


class UrlSafetyTests(ArcmsTestCase):
    def test_only_public_http_hosts(self):
        with mock.patch("arcms.wires.feeds._resolve", public):
            feeds.check_url("https://agency.example/rss")
            for bad in ("ftp://agency.example/rss", "file:///etc/passwd", "https://user:pw@agency.example/rss", "https:///x"):
                with self.assertRaises(feeds.FeedError, msg=bad):
                    feeds.check_url(bad)
        for addr in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "192.168.1.5", "100.64.0.1"):
            with mock.patch("arcms.wires.feeds._resolve", lambda h, p, a=addr: [a]):
                with self.assertRaises(feeds.FeedError, msg=addr):
                    feeds.check_url("http://intranet.example/rss")

    @override_settings(ARCMS_WIRE_ALLOW_PRIVATE=True)
    def test_private_allowed_when_configured(self):
        with mock.patch("arcms.wires.feeds._resolve", lambda h, p: ["10.1.2.3"]):
            feeds.check_url("http://intranet.example/rss")

    def test_redirect_to_internal_host_blocked(self):
        def resolve(host, port):
            return ["127.0.0.1"] if host == "internal.example" else ["93.184.216.34"]

        session = FakeSession(FakeResponse(302, headers={"Location": "http://internal.example/admin"}))
        with mock.patch("arcms.wires.feeds._resolve", resolve), self.assertRaises(feeds.FeedError):
            feeds.fetch("https://agency.example/rss", session=session)
        self.assertEqual(len(session.calls), 1)

    @mock.patch("arcms.wires.feeds._resolve", public)
    def test_conditional_get_and_size_limit(self):
        session = FakeSession(FakeResponse(304))
        result = feeds.fetch("https://agency.example/rss", etag='"abc"', modified="Tue", session=session)
        self.assertTrue(result.not_modified)
        self.assertEqual(session.calls[0][1]["If-None-Match"], '"abc"')
        big = FakeResponse(200, b"x" * (feeds.MAX_BYTES + 10))
        with self.assertRaises(feeds.FeedError):
            feeds.fetch("https://agency.example/rss", session=FakeSession(big))


@mock.patch("arcms.wires.feeds._resolve", public)
class PollTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.source = WireSource.objects.create(name="الوكالة", feed_url="https://agency.example/rss")

    def test_poll_adds_new_items_once_and_flags_alerts(self):
        WireKeyword.objects.create(word="غزة")
        session = FakeSession(FakeResponse(200, RSS, {"ETag": '"v1"'}), FakeResponse(200, RSS))
        self.assertEqual(services.poll(self.source, session=session), 2)
        self.assertEqual(services.poll(self.source, session=session), 0)
        self.assertEqual(WireItem.objects.count(), 2)
        alert = WireItem.objects.get(title__contains="مستشفى")
        self.assertTrue(alert.is_alert)  # «غزة» تطابق «غزة» بعد التطبيع
        self.assertFalse(WireItem.objects.get(title__contains="ثانٍ").is_alert)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_error, "")
        self.assertIsNotNone(self.source.last_ok_at)

    def test_alert_matches_attached_prepositions_and_hamza(self):
        WireKeyword.objects.create(word="القاهرة")
        WireKeyword.objects.create(word="وزير الخارجيه")
        services.poll(self.source, session=FakeSession(FakeResponse(200, ATOM)))
        self.assertTrue(WireItem.objects.get().is_alert)

    def test_errors_are_recorded_not_raised(self):
        services.poll(self.source, session=FakeSession(FakeResponse(500)))
        self.source.refresh_from_db()
        self.assertIn("500", self.source.last_error)
        services.poll(self.source, session=FakeSession(FakeResponse(200, b"<html>")))
        self.source.refresh_from_db()
        self.assertIn("XML", self.source.last_error)

    def test_poll_due_respects_interval_and_active(self):
        other = WireSource.objects.create(name="معطّل", feed_url="https://agency.example/2", is_active=False)
        session = FakeSession(FakeResponse(200, RSS))
        self.assertEqual(services.poll_due(session=session), 2)
        self.assertEqual(len(session.calls), 1)  # المعطّل لا يُجلب
        self.assertEqual(services.poll_due(session=session), 0)  # لم يحن موعده
        other.refresh_from_db()
        self.assertIsNone(other.last_polled_at)

    def test_watchwords_update_reflags_recent_items(self):
        services.poll(self.source, session=FakeSession(FakeResponse(200, RSS)))
        self.assertFalse(WireItem.objects.filter(is_alert=True).exists())
        words = services.set_watchwords("مستشفى\n\nمستشفى، الوكالة")
        self.assertEqual(words, ["مستشفى", "الوكالة"])
        self.assertTrue(WireItem.objects.get(title__contains="مستشفى").is_alert)

    def test_purge_keeps_adopted(self):
        services.poll(self.source, session=FakeSession(FakeResponse(200, RSS)))
        old = timezone.now() - timedelta(days=30)
        WireItem.objects.update(fetched_at=old)
        kept = WireItem.objects.first()
        kept.status = WireItem.Status.ADOPTED
        kept.save()
        self.assertEqual(services.purge_old(), 1)
        self.assertEqual(list(WireItem.objects.all()), [kept])


class DeskViewTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("arcms.wires.feeds._resolve", public)
        patcher.start()
        self.addCleanup(patcher.stop)
        cat = make_category("محليات")
        self.source = WireSource.objects.create(name="الوكالة", feed_url="https://agency.example/rss", category=cat,
                                                credit="وكالة الأنباء")
        services.poll(self.source, session=FakeSession(FakeResponse(200, RSS)))
        self.item = WireItem.objects.get(title__contains="مستشفى")

    def test_reporter_adopts_draft_with_attribution(self):
        user = make_user("rep", Role.REPORTER)
        login(self.client, user)
        self.assertContains(self.client.get(reverse("studio:wires")), "مستشفى")
        resp = self.client.post(reverse("studio:wires_action"), {"action": "adopt", "item": self.item.pk})
        article = Article.objects.get()
        self.assertRedirects(resp, reverse("studio:article_edit", args=[article.pk]), fetch_redirect_response=False)
        editor = self.client.get(resp.url)
        self.assertContains(editor, "وكالة الأنباء")
        self.assertContains(editor, "مستشفى")
        self.assertEqual(article.status, Status.DRAFT)
        self.assertEqual(article.source, "وكالة الأنباء")
        self.assertEqual(article.source_url, "https://agency.example/news/1")
        self.assertEqual(article.category.name, "محليات")
        self.assertEqual(article.created_by, user)
        self.assertIn("المتن الكامل", article.body)
        self.assertNotIn("script", article.body)
        # الاعتماد مرة ثانية لا يكرر المسودة
        self.client.post(reverse("studio:wires_action"), {"action": "adopt", "item": self.item.pk})
        self.assertEqual(Article.objects.count(), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WireItem.Status.ADOPTED)
        self.assertTrue(AuditEntry.objects.filter(message__contains="مكتب الوكالات").exists())

    def test_search_filters_and_ignore_restore(self):
        login(self.client, make_user("ed", Role.EDITOR))
        resp = self.client.get(reverse("studio:wires"), {"q": "مستشفي", "tab": "all"})  # ي/ى
        self.assertContains(resp, "مستشفى")
        self.assertNotContains(resp, "خبر ثانٍ")
        ids = list(WireItem.objects.values_list("pk", flat=True))
        self.client.post(reverse("studio:wires_action"), {"action": "ignore", "ids": ids})
        self.assertEqual(WireItem.objects.filter(status=WireItem.Status.IGNORED).count(), 2)
        self.assertNotContains(self.client.get(reverse("studio:wires")), "مستشفى")
        self.client.post(reverse("studio:wires_action"), {"action": "restore", "item": self.item.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WireItem.Status.NEW)

    def test_permissions(self):
        login(self.client, make_user("soc", Role.SOCIAL))
        self.assertEqual(self.client.get(reverse("studio:wires")).status_code, 403)
        self.client.post(reverse("studio:wires_action"), {"action": "adopt", "item": self.item.pk})
        self.assertFalse(Article.objects.exists())
        login(self.client, make_user("ed2", Role.EDITOR))
        self.assertEqual(self.client.get(reverse("studio:wiresources_list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("studio:wire_keywords"), {"keywords": "x"}).status_code, 403)
        login(self.client, make_user("boss", Role.CHIEF))
        resp = self.client.get(reverse("studio:wiresources_list"))
        self.assertContains(resp, "الوكالة")
        self.client.post(reverse("studio:wire_keywords"), {"keywords": "غزة"})
        self.assertEqual(list(WireKeyword.objects.values_list("word", flat=True)), ["غزة"])
        self.assertContains(self.client.get(reverse("studio:home")), 'title="تنبيهات جديدة"')

    def test_source_form_rejects_internal_url(self):
        login(self.client, make_user("boss2", Role.CHIEF))
        with mock.patch("arcms.wires.feeds._resolve", lambda h, p: ["127.0.0.1"]):
            resp = self.client.post(reverse("studio:wiresources_new"), {
                "name": "داخلي", "feed_url": "http://localhost:8000/rss", "poll_minutes": 5, "is_active": "on",
            })
        self.assertContains(resp, "عنوان داخلي")
        self.assertFalse(WireSource.objects.filter(name="داخلي").exists())

    def test_health_warns_about_failing_source(self):
        from arcms.core.health import run_checks

        self.source.last_error = "ردّ الخادم برمز 500."
        self.source.last_ok_at = timezone.now() - timedelta(hours=3)
        self.source.save()
        self.assertTrue(any(c.area == "مكتب الوكالات" and c.level == "warn" for c in run_checks()))
