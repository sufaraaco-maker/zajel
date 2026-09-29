import io

from django.urls import reverse
from PIL import Image

from arcms.accounts.roles import Role
from arcms.core import colors
from arcms.core.models import SiteSettings
from arcms.core.presets import apply_theme
from arcms.core.testing import ArcmsTestCase, login, make_user


class ColorMathTests(ArcmsTestCase):
    def test_contrast_and_readable_text(self):
        self.assertAlmostEqual(colors.contrast("#000000", "#ffffff"), 21, places=1)
        self.assertEqual(colors.readable_on("#ffd400"), colors.DARK_TEXT)  # أصفر: نص داكن
        self.assertEqual(colors.readable_on("#0b1f3a"), colors.LIGHT_TEXT)  # كحلي: نص أبيض
        self.assertEqual(colors.readable_on("#b0101c"), colors.LIGHT_TEXT)

    def test_dark_mode_variant_is_readable(self):
        dark_red = "#5a0a10"
        lifted = colors.for_dark_mode(dark_red)
        self.assertGreaterEqual(colors.contrast(lifted, colors.DARK_BG), 3)
        self.assertEqual(colors.for_dark_mode("#ffd400"), "#ffd400")  # مقروء أصلاً

    def test_text_shade_of_light_brand_color(self):
        shade = colors.for_text_on("#ffc400", "#ffffff")
        self.assertGreaterEqual(colors.contrast(shade, "#ffffff"), 4.5)
        self.assertNotEqual(shade, "#ffc400")
        self.assertEqual(colors.for_text_on("#0b1f3a", "#ffffff"), "#0b1f3a")  # مقروء أصلاً
        light = colors.for_text_on("#5a0a10", colors.DARK_BG)
        self.assertGreaterEqual(colors.contrast(light, colors.DARK_BG), 4.5)

    def test_palette_from_logo(self):
        img = Image.new("RGBA", (120, 60), (255, 255, 255, 0))
        for x in range(0, 80):
            for y in range(60):
                img.putpixel((x, y), (20, 99, 216, 255))
        for x in range(80, 120):
            for y in range(60):
                img.putpixel((x, y), (212, 160, 23, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        found = colors.palette_from_image(buf)
        self.assertGreaterEqual(len(found), 2)
        self.assertLess(colors._distance(found[0], "#1463d8"), 20)  # الأكثر انتشاراً أولاً
        self.assertTrue(any(colors._distance(c, "#d4a017") < 20 for c in found))


class SitePaletteTests(ArcmsTestCase):
    def test_defaults_and_overrides_render(self):
        site = SiteSettings.load()
        site.primary_color = "#ffd400"
        site.nav_color = "#0b1f3a"
        site.footer_color = "#f4f1ea"
        site.page_color = "#fbfaf7"
        site.save()
        p = site.palette()
        self.assertEqual(p["on_primary"], colors.DARK_TEXT)
        self.assertEqual(p["on_nav"], colors.LIGHT_TEXT)
        self.assertEqual(p["breaking"], "#ffd400")  # تلقائي من الرئيسي
        self.assertEqual(p["topbar"], site.accent_color)
        html = self.client.get("/").content.decode()
        self.assertIn("--on-primary:#111418", html)
        self.assertIn("--nav-bg:#0b1f3a;--nav-fg:#ffffff", html)
        self.assertIn("--bg:#fbfaf7", html)
        self.assertIn(":root[data-theme=\"dark\"]{--primary:", html)

    def test_colored_text_readable_on_every_surface(self):
        """النص الملوّن (الأوقات والعناوين الصغيرة) يقع على البطاقات لا على الخلفية وحدها، في الوضعين."""
        site = SiteSettings.load()
        for primary in ("#1f7a3e", "#7a1f2b", "#ffd400", "#0a58ca", "#b0101c"):
            for page in ("", "#f4efe6"):
                site.primary_color, site.page_color = primary, page
                p = site.palette()
                for bg in (p["page"], p["surface"], p["surface2"]):
                    self.assertGreaterEqual(colors.contrast(p["primary_text"], bg), 4.5, (primary, page, bg))
                for bg in (colors.DARK_BG, "#171a1d", colors.DARK_SURFACE):
                    self.assertGreaterEqual(colors.contrast(p["primary_text_dark"], bg), 4.5, (primary, bg))
                    self.assertGreaterEqual(colors.contrast(p["link_dark"], bg), 4.5, (primary, bg))

    def test_header_class_follows_contrast(self):
        site = SiteSettings.load()
        site.header_color = "#0b1f3a"
        site.save()
        html = self.client.get("/").content.decode()
        self.assertIn('class="site-header dark', html)
        site.header_color = "#fff8e1"
        site.save()
        self.assertNotIn('class="site-header dark', self.client.get("/").content.decode())

    def test_warnings(self):
        site = SiteSettings.load()
        site.primary_color = "#ffe680"  # فاتح جداً على خلفية بيضاء
        self.assertTrue(any("اللون الرئيسي فاتح" in w for w in site.color_warnings()))
        site.primary_color = "#b0101c"
        self.assertEqual(site.color_warnings(), [])

    def test_theme_resets_area_colors(self):
        site = SiteSettings.load()
        site.nav_color = "#123456"
        site.save()
        apply_theme(site, "classic-red")
        self.assertEqual(SiteSettings.objects.get(pk=1).nav_color, "")


class SettingsColorFormTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        login(self.client, make_user("root", Role.ADMIN))

    def _post(self, **overrides):
        from arcms.studio.forms import SiteSettingsForm

        site = SiteSettings.objects.get(pk=SiteSettings.load().pk)
        form = SiteSettingsForm(instance=site)
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif value is not None:
                data[name] = getattr(value, "pk", value)
        data.update(overrides)
        return self.client.post(reverse("studio:settings"), data, follow=True)

    def test_page_shows_preview_and_saves_area_colors(self):
        resp = self.client.get(reverse("studio:settings"))
        self.assertContains(resp, "data-brand-preview")
        self.assertContains(resp, "الألوان والهوية البصرية")
        resp = self._post(nav_color="#0B1F3A", breaking_color="", link_color="#0a58ca")
        site = SiteSettings.objects.get(pk=1)
        self.assertEqual((site.nav_color, site.breaking_color, site.link_color), ("#0b1f3a", "", "#0a58ca"))

    def test_dark_page_background_rejected_and_warning_shown(self):
        resp = self._post(page_color="#101010")
        self.assertContains(resp, "خلفية الصفحة يجب أن تكون فاتحة")
        self.assertEqual(SiteSettings.objects.get(pk=1).page_color, "")
        resp = self._post(primary_color="#ffe680")
        self.assertContains(resp, "اللون الرئيسي فاتح")

    def test_logo_palette_offered(self):
        from arcms.content.imaging import store_image

        img = Image.new("RGB", (300, 120), (20, 99, 216))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        logo, _ = store_image(buf.getvalue())
        site = SiteSettings.load()
        site.logo = logo
        site.save()
        resp = self.client.get(reverse("studio:settings"))
        self.assertContains(resp, "ألوان من شعارك")
        self.assertContains(resp, 'data-set-color="id_primary_color"')
