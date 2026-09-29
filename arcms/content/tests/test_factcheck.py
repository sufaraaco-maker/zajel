import json
import re
from datetime import date

from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content import cards
from arcms.content.models import Article, ArticleKind, Status
from arcms.content.workflow import publish_block_reason
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user


def factcheck(**extra):
    fields = {
        "kind": ArticleKind.FACTCHECK, "claim": "صورة تُظهر شوارع المدينة غارقة بعد أمطار أمس",
        "claimant": "منشورات متداولة", "claim_date": date(2026, 9, 28),
        "claim_url": "https://web.archive.org/web/2026/https://example.com/post", "verdict": "false",
    }
    fields.update(extra)
    return make_article("الصورة المتداولة قديمة ومن بلد آخر", **fields)


def ld_blocks(html: str) -> list:
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


class FactCheckPageTests(ArcmsTestCase):
    def test_verdict_box_and_claim_review(self):
        article = factcheck()
        html = self.client.get(article.get_absolute_url()).content.decode()
        self.assertIn('class="verdict-box v-false"', html)
        self.assertIn("«صورة تُظهر شوارع المدينة غارقة بعد أمطار أمس»", html)
        self.assertIn('rel="nofollow noopener noreferrer"', html)
        news, review = ld_blocks(html)
        self.assertEqual(news["@type"], "NewsArticle")
        self.assertEqual(review["@type"], "ClaimReview")
        self.assertEqual(review["reviewRating"], {"@type": "Rating", "alternateName": "زائف",
                                                  "ratingValue": 1, "bestRating": 5, "worstRating": 1})
        self.assertEqual(review["itemReviewed"]["author"]["name"], "منشورات متداولة")
        self.assertEqual(review["itemReviewed"]["datePublished"], "2026-09-28")
        self.assertEqual(review["itemReviewed"]["appearance"]["url"], article.claim_url)

    def test_unrated_verdicts_have_no_number(self):
        article = factcheck(verdict="satire", claimant="", claim_date=None, claim_url="")
        review = ld_blocks(self.client.get(article.get_absolute_url()).content.decode())[1]
        self.assertEqual(review["reviewRating"], {"@type": "Rating", "alternateName": "ساخر"})
        self.assertEqual(review["itemReviewed"], {"@type": "Claim"})

    def test_regular_article_has_single_block_and_no_box(self):
        article = make_article("خبر عادي")
        html = self.client.get(article.get_absolute_url()).content.decode()
        self.assertNotIn("verdict-box", html)
        self.assertEqual([b["@type"] for b in ld_blocks(html)], ["NewsArticle"])

    def test_cards_show_verdict_chip(self):
        article = factcheck(verdict="misleading")
        html = self.client.get(article.category.get_absolute_url()).content.decode()
        self.assertIn('<span class="verdict-chip v-misleading">مضلل</span>', html)

    def test_share_card_version_follows_verdict(self):
        article = factcheck()
        site = SiteSettings.load()
        before = cards.article_version(article, site)
        article.verdict = "true"
        self.assertNotEqual(cards.article_version(article, site), before)
        self.assertTrue(cards.render_article(article, site).startswith(b"\xff\xd8"))


class FactCheckWorkflowTests(ArcmsTestCase):
    def test_publish_needs_claim_and_verdict(self):
        chief = make_user("boss", Role.CHIEF)
        article = Article.objects.create(title="تدقيق", kind=ArticleKind.FACTCHECK, category=make_category(),
                                         status=Status.DRAFT, created_by=chief)
        self.assertIn("الادعاء", publish_block_reason(chief, article))
        article.claim, article.verdict = "ادعاء", "false"
        self.assertIsNone(publish_block_reason(chief, article))

    def test_editor_saves_fields_and_clears_verdict_for_other_kinds(self):
        user = make_user("rep", Role.REPORTER)
        login(self.client, user)
        cat = make_category()
        base = {"title": "تدقيق صورة", "body": "<p>نص</p>", "category": cat.pk, "action": "save", "priority": 0,
                "claim": "ادعاء متداول", "claimant": "حساب", "claim_date": "2026-09-01",
                "claim_url": "https://web.archive.org/x", "verdict": "false"}
        resp = self.client.post(reverse("studio:article_new"), {**base, "kind": "factcheck"})
        self.assertEqual(resp.status_code, 302, resp.context and resp.context["form"].errors)
        article = Article.objects.get(title="تدقيق صورة")
        self.assertEqual((article.verdict, article.claim_date), ("false", date(2026, 9, 1)))
        edit = self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertContains(edit, 'data-kind-only="factcheck"')
        self.assertContains(edit, 'value="false"')
        self.client.post(reverse("studio:article_edit", args=[article.pk]), {**base, "kind": "news"})
        article.refresh_from_db()
        self.assertEqual((article.kind, article.verdict, article.claim), ("news", "", "ادعاء متداول"))
