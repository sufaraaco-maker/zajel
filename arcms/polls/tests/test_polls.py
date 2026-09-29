from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.core.models import HomeBlock
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user
from arcms.polls.models import Poll, PollOption

ORIGIN = {"HTTP_ORIGIN": "http://testserver"}


def make_poll(question="هل تؤيد توسيع المكتبات العامة؟", options=("نعم", "لا", "لا رأي"), **extra):
    poll = Poll.objects.create(question=question, **extra)
    for n, text in enumerate(options):
        PollOption.objects.create(poll=poll, text=text, order=n)
    return poll


class VoteTests(ArcmsTestCase):
    def vote(self, poll, option, client=None, **headers):
        client = client or self.client
        return client.post(reverse("public:poll_vote", args=[poll.pk]), {"option": option.pk},
                           HTTP_ACCEPT="application/json", **{**ORIGIN, **headers})

    def test_vote_counts_and_cookie_blocks_repeat(self):
        poll = make_poll()
        yes = poll.options.get(text="نعم")
        resp = self.vote(poll, yes)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["total"], 1)
        self.assertEqual({r["text"]: r["pct"] for r in data["results"]}["نعم"], 100)
        self.assertEqual(resp.cookies[f"arcms_poll_{poll.pk}"].value, "1")
        again = self.vote(poll, yes)
        self.assertFalse(again.json()["ok"])
        poll.refresh_from_db()
        self.assertEqual(poll.total_votes, 1)

    def test_cross_site_and_foreign_option_rejected(self):
        poll, other = make_poll(), make_poll("سؤال آخر")
        resp = self.vote(poll, poll.options.first(), HTTP_ORIGIN="https://evil.example")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(reverse("public:poll_vote", args=[poll.pk]), {"option": poll.options.first().pk},
                                HTTP_ACCEPT="application/json")  # بلا Origin ولا Referer
        self.assertEqual(resp.status_code, 403)
        resp = self.vote(poll, other.options.first())
        self.assertEqual(resp.status_code, 400)
        poll.refresh_from_db()
        self.assertEqual(poll.total_votes, 0)

    def test_closed_poll(self):
        poll = make_poll(closes_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(self.vote(poll, poll.options.first()).json()["ok"])
        poll = make_poll(is_open=False)
        self.assertFalse(self.vote(poll, poll.options.first()).json()["ok"])

    @mock.patch.dict("arcms.public.views.RATE_LIMITS", {"poll": (2, 3600)})
    def test_rate_limit_per_network(self):
        from django.test import Client

        poll = make_poll()
        results = [self.vote(poll, poll.options.first(), client=Client()).json()["ok"] for _ in range(3)]
        self.assertEqual(results, [True, True, False])

    def test_no_voter_data_stored(self):
        poll = make_poll()
        self.vote(poll, poll.options.first(), REMOTE_ADDR="203.0.113.7")
        from django.apps import apps

        field_names = {f.name for m in apps.get_app_config("polls").get_models() for f in m._meta.get_fields()}
        self.assertFalse({"ip", "voter", "user", "session"} & field_names)

    def test_form_post_without_js_redirects_back(self):
        poll = make_poll()
        article = make_article("مادة فيها استطلاع", body=f"<p>مقدمة</p><p>{poll.shortcode}</p>")
        resp = self.client.post(reverse("public:poll_vote", args=[poll.pk]), {"option": poll.options.first().pk},
                                HTTP_REFERER=f"http://testserver{article.get_absolute_url()}")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith(f"#poll-{poll.pk}"))


class RenderTests(ArcmsTestCase):
    def test_article_shortcode_and_results_after_vote(self):
        poll = make_poll()
        article = make_article("مادة", body=f"<p>مقدمة</p><p>{poll.shortcode}</p><p>[poll:99999]</p>")
        page = self.client.get(article.get_absolute_url())
        html = page.content.decode()
        self.assertIn(f'id="poll-{poll.pk}"', html)
        self.assertNotIn("[poll:", html)  # لا في المتن ولا في الوصف وبيانات المشاركة
        self.assertIn("لا أصوات بعد", html)
        self.assertIn('class="poll-results" hidden', html)
        self.client.cookies[f"arcms_poll_{poll.pk}"] = "1"
        html = self.client.get(article.get_absolute_url()).content.decode()
        self.assertIn('class="poll-form" method="post" action="/poll/', html)
        self.assertIn(' hidden>', html.split('class="poll-form"')[1].split(">")[0] + ">")

    def test_home_block_shows_latest_open_poll(self):
        HomeBlock.objects.create(kind=HomeBlock.Kind.POLL, title="رأيك", order=1)
        make_poll("استطلاع مغلق", is_open=False)
        self.assertNotContains(self.client.get("/"), "poll-")
        make_poll("استطلاع مفتوح")
        from django.core.cache import cache

        cache.clear()
        self.assertContains(self.client.get("/"), "استطلاع مفتوح")


class StudioPollTests(ArcmsTestCase):
    def test_create_edit_and_protect_votes(self):
        login(self.client, make_user("desk", Role.DESK_HEAD))
        resp = self.client.post(reverse("studio:poll_new"), {
            "question": "أي الأقسام تقرأ أكثر؟", "options_text": "محليات\nرياضة\nاقتصاد", "is_open": "on",
        })
        self.assertEqual(resp.status_code, 302)
        poll = Poll.objects.get()
        self.assertEqual(list(poll.options.values_list("text", flat=True)), ["محليات", "رياضة", "اقتصاد"])
        PollOption.objects.filter(poll=poll, text="رياضة").update(votes=5)
        Poll.objects.filter(pk=poll.pk).update(total_votes=5)
        # بعد التصويت: الحذف ممنوع، وتعديل النص يحفظ الأصوات
        resp = self.client.post(reverse("studio:poll_edit", args=[poll.pk]), {
            "question": poll.question, "options_text": "محليات\nرياضة", "is_open": "on"})
        self.assertContains(resp, "لا يمكن حذف خيارات")
        self.client.post(reverse("studio:poll_edit", args=[poll.pk]), {
            "question": poll.question, "options_text": "محليات\nالرياضة\nاقتصاد\nثقافة", "is_open": "on"})
        self.assertEqual(PollOption.objects.get(poll=poll, order=1).text, "الرياضة")
        self.assertEqual(PollOption.objects.get(poll=poll, order=1).votes, 5)
        self.assertEqual(poll.options.count(), 4)
        self.assertContains(self.client.get(reverse("studio:polls")), poll.shortcode)

    def test_permissions(self):
        login(self.client, make_user("rep", Role.REPORTER))
        self.assertEqual(self.client.get(reverse("studio:polls")).status_code, 403)
        resp = self.client.post(reverse("studio:poll_new"), {"question": "x", "options_text": "a\nb"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Poll.objects.exists())
