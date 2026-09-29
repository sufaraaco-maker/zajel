import io

from django.core.files.storage import default_storage
from django.urls import reverse
from PIL import Image

from arcms.accounts.roles import Role
from arcms.content import cards
from arcms.content.imaging import store_image
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user


def two_tone(size=(900, 1600)) -> bytes:
    """نصف أعلى أحمر ونصف أسفل أزرق، ليظهر أين وقع القص."""
    img = Image.new("RGB", size, (20, 40, 220))
    img.paste((220, 30, 30), (0, 0, size[0], size[1] // 2))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def dominant(path_or_bytes) -> str:
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else default_storage.open(path_or_bytes).read()
    r, g, b = Image.open(io.BytesIO(data)).convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return "red" if r > b else "blue"


class FocalTests(ArcmsTestCase):
    def test_default_style_is_empty(self):
        asset, _ = store_image(two_tone())
        self.assertEqual(asset.focal_style, "")
        asset.focal_x, asset.focal_y = 0.25, 0.9
        self.assertEqual(asset.focal_style, "object-position:25.0% 90.0%")
        asset.focal_x = 7  # يُحصر ضمن الصورة
        self.assertEqual(asset.focal, (1.0, 0.9))

    def test_editor_sets_focal_and_social_crop_follows(self):
        user = make_user("photo", Role.EDITOR)
        login(self.client, user)
        asset, _ = store_image(two_tone(), user=user)
        seen = {asset.renditions["social"]}
        for fy, expected in (("0.02", "red"), ("0.98", "blue")):
            resp = self.client.post(reverse("studio:media_edit", args=[asset.pk]), {
                "title": "", "caption": "", "credit": "", "alt_text": "", "focal_x": "0.5", "focal_y": fy})
            self.assertEqual(resp.status_code, 302)
            asset.refresh_from_db()
            self.assertEqual(dominant(asset.renditions["social"]), expected)
            self.assertNotIn(asset.renditions["social"], seen)  # رابط جديد لكل قصّ
            seen.add(asset.renditions["social"])
        # لا تبقى إلا نسخة القص الحالية على القرص
        self.assertEqual([p for p in seen if default_storage.exists(p)], [asset.renditions["social"]])

    def test_share_card_and_templates_use_focal(self):
        asset, _ = store_image(two_tone())
        article = make_article("خبر بصورة طويلة", featured_image=asset)
        site = SiteSettings.load()
        v1 = cards.article_version(article, site)
        asset.focal_y = 0.0
        asset.save()
        top = cards.render_article(article, site, "wide")
        self.assertNotEqual(v1, cards.article_version(article, site))
        asset.focal_y = 1.0
        asset.save()
        bottom = cards.render_article(article, site, "wide")
        # أعلى البطاقة (فوق التعتيم) يأخذ لون موضع التركيز
        def top_band(data):
            img = Image.open(io.BytesIO(data)).convert("RGB").crop((500, 150, 700, 250)).resize((1, 1))
            r, _, b = img.getpixel((0, 0))
            return "red" if r > b else "blue"
        self.assertEqual((top_band(top), top_band(bottom)), ("red", "blue"))
        asset.focal_x, asset.focal_y = 0.2, 0.3
        asset.save()
        from arcms.core.models import HomeBlock

        HomeBlock.objects.create(kind=HomeBlock.Kind.LATEST, count=5, order=1)
        self.assertContains(self.client.get("/"), "object-position:20.0% 30.0%")
