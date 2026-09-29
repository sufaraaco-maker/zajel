import io
from unittest import mock

from django.urls import reverse
from PIL import Image

from arcms.accounts.roles import Role
from arcms.content import cards
from arcms.content.imaging import store_image
from arcms.content.models import MediaAsset
from arcms.content.sensitive import mark_body
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user
from arcms.distribution.models import Channel, Delivery


def photo(color=(160, 20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color).save(buf, "JPEG")
    return buf.getvalue()


class SensitiveImageTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.asset, _ = store_image(photo())
        self.asset.sensitive = True
        self.asset.save()
        self.article = make_article("قصف على حي سكني", featured_image=self.asset,
                                    body=f'<p>نص</p><p><img src="{self.asset.large}"></p>')

    def page(self):
        return self.client.get(self.article.get_absolute_url()).content.decode()

    def test_article_page_blurs_featured_and_body_images(self):
        html = self.page()
        self.assertEqual(html.count("<img data-sensitive"), 2)
        self.assertNotIn(self.asset.social, html)  # لا og:image ولا JSON-LD بالصورة نفسها
        self.assertIsNone(self.article.share_image)

    def test_unflagging_applies_everywhere_without_resaving_articles(self):
        self.asset.sensitive = False
        self.asset.save()
        html = self.page()
        self.assertNotIn("data-sensitive", html)
        self.assertIn(self.asset.social, html)

    def test_listing_cards_are_marked(self):
        html = self.client.get(self.article.category.get_absolute_url()).content.decode()
        self.assertIn("<img data-sensitive", html)

    def test_share_card_drops_the_photo(self):
        site = SiteSettings.load()
        with mock.patch.object(cards, "_open_asset", wraps=cards._open_asset) as opened:
            cards.render_article(self.article, site)
        self.assertNotIn(self.asset, [c.args[0] for c in opened.call_args_list])
        before = cards.article_version(self.article, site)
        self.asset.sensitive = False
        self.asset.save()
        self.article.refresh_from_db()
        self.assertNotEqual(cards.article_version(self.article, site), before)

    def test_platforms_feed_and_newsletter_skip_the_photo(self):
        from arcms.distribution.tasks import _content

        site = SiteSettings.load()
        site.share_cards = False
        site.save()
        delivery = Delivery.objects.create(article=self.article, channel=Channel.TELEGRAM, target="@x")
        self.assertEqual(_content(delivery)["image"], "")
        feed = self.client.get(reverse("public:feed")).content.decode()
        self.assertNotIn(self.asset.card, feed)

    def test_mark_body_matches_paths_and_absolute_urls(self):
        other, _ = store_image(photo((20, 120, 40)))
        html = (f'<img src="https://news.example{self.asset.card}" alt="">'
                f'<img alt="x" src="{other.card}"><img src="{self.asset.url}">')
        marked = mark_body(html)
        self.assertEqual(marked.count("data-sensitive"), 2)
        self.assertIn(f'<img alt="x" src="{other.card}">', marked)
        self.assertEqual(mark_body("<p>بلا صور</p>"), "<p>بلا صور</p>")


class MediaEditorTests(ArcmsTestCase):
    def test_editor_flags_image_and_library_shows_it(self):
        user = make_user("photo", Role.CHIEF)
        login(self.client, user)
        asset, _ = store_image(photo(), user=user)
        resp = self.client.post(reverse("studio:media_edit", args=[asset.pk]), {
            "title": "", "caption": "", "credit": "", "alt_text": "", "sensitive": "on",
            "focal_x": "0.5", "focal_y": "0.4"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(MediaAsset.objects.get(pk=asset.pk).sensitive)
        self.assertContains(self.client.get(reverse("studio:media")), "قاسية")
        picked = self.client.get(reverse("studio:media_picker") + f"?id={asset.pk}").json()
        self.assertTrue(picked["items"][0]["sensitive"])
