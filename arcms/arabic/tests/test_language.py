from datetime import date, datetime, timedelta
from datetime import timezone as dt_tz

from django.test import SimpleTestCase, override_settings

from arcms.arabic.analyzer import index_terms, matches, parse_query, word_keys
from arcms.arabic.dates import DateStyle, duration_phrase, format_date, format_hijri, format_time, relative_time
from arcms.arabic.highlight import highlight, snippet
from arcms.arabic.normalize import hamza_skeleton, normalize, strip_diacritics
from arcms.arabic.numbers import compact_number, count_phrase, to_digits
from arcms.arabic.stemmer import light_stem
from arcms.arabic.text import arabic_slugify, clean_headline


class NormalizeTests(SimpleTestCase):
    def test_diacritics_and_tatweel(self):
        self.assertEqual(strip_diacritics("فِلَسْطِينُ"), "فلسطين")
        self.assertEqual(normalize("مـــدرســة"), "مدرسه")

    def test_alef_forms_and_taa_marbuta(self):
        for word in ("أحمد", "إحمد", "آحمد", "احمد"):
            self.assertEqual(normalize(word)[0], "ا")
        self.assertEqual(normalize("مدرسة"), normalize("مدرسه"))
        self.assertEqual(normalize("مستشفى"), normalize("مستشفي"))

    def test_digits(self):
        self.assertEqual(normalize("٢٠٢٦"), "2026")
        self.assertEqual(normalize("۱۴۴۸"), "1448")

    def test_hamza_skeleton_unifies_spellings(self):
        self.assertEqual(hamza_skeleton("مسؤول"), hamza_skeleton("مسئول"))
        self.assertEqual(hamza_skeleton("شؤون"), hamza_skeleton("شئون"))

    def test_invisible_marks_removed(self):
        self.assertEqual(clean_headline("‏خبر   عاجل‎"), "خبر عاجل")


class StemmerTests(SimpleTestCase):
    def test_light_stem_examples(self):
        cases = {
            "بالاعتقال": "اعتقال",
            "الاعتقالات": "اعتقال",
            "واعتقال": "اعتقال",
            "للاعتقال": "اعتقال",
            "والاعتقال": "اعتقال",
            "المستوطنين": "مستوطن",
        }
        for word, stem in cases.items():
            self.assertEqual(light_stem(normalize(word)), stem, word)

    def test_short_words_untouched(self):
        self.assertEqual(light_stem("من"), "من")
        self.assertEqual(light_stem("gaza"), "gaza")


class AnalyzerTests(SimpleTestCase):
    def test_request_example_bil_itiqal(self):
        """المتطلب الأصلي: البحث عن «اعتقال» يجد «بالاعتقال»."""
        self.assertTrue(matches("قوات تقوم بالاعتقال في المدينة", parse_query("اعتقال")))
        self.assertTrue(matches("استمرت الاعتقالات حتى الفجر", parse_query("اعتقال")))

    def test_diacritics_and_hamza_tolerance(self):
        self.assertTrue(matches("وصل وفد إلى فِلَسْطِين", parse_query("فلسطين")))
        self.assertTrue(matches("قال مسئول محلي", parse_query("مسؤول")))
        self.assertTrue(matches("عقد مؤتمر صحفي", parse_query("موتمر")))
        self.assertTrue(matches("إسرائيل", parse_query("اسرائيل")))

    def test_clitic_prepositions(self):
        self.assertTrue(matches("افتتحت مكتبة بغزة", parse_query("غزة")))
        self.assertTrue(matches("وصلت المساعدات لفلسطين", parse_query("فلسطين")))

    def test_all_words_required(self):
        q = parse_query("مكتبة نابلس")
        self.assertTrue(matches("افتتاح مكتبة في نابلس", q))
        self.assertFalse(matches("افتتاح مكتبة في جنين", q))

    def test_stopwords_dropped(self):
        q = parse_query("في من غزة")
        self.assertEqual(len(q.groups), 1)
        self.assertNotIn("في", index_terms("في غزة"))

    def test_phrases_extracted(self):
        q = parse_query('«الاعتقال الإداري» ندوة')
        self.assertEqual(q.phrases, ("الاعتقال الاداري",))

    def test_word_keys_are_small(self):
        self.assertLessEqual(len(word_keys("وبالاعتقالات")), 6)

    def test_highlight_marks_inflected_forms(self):
        html = highlight("قامت بالاعتقال <فجراً>", parse_query("اعتقال"))
        self.assertIn("<mark>بالاعتقال</mark>", html)
        self.assertIn("&lt;فجراً&gt;", html)  # نص آمن

    def test_snippet_centers_on_match(self):
        text = "كلام تمهيدي " * 40 + "وجرت الاعتقالات فجراً"
        self.assertIn("<mark>الاعتقالات</mark>", snippet(text, parse_query("اعتقال"), 120))


class NumberTests(SimpleTestCase):
    def test_count_phrase_rules(self):
        args = ("دقيقة", "دقيقتين", "دقائق", "دقيقة")
        self.assertEqual(count_phrase(1, *args), "دقيقة")
        self.assertEqual(count_phrase(2, *args), "دقيقتين")
        self.assertEqual(count_phrase(5, *args), "5 دقائق")
        self.assertEqual(count_phrase(10, *args), "10 دقائق")
        self.assertEqual(count_phrase(11, *args), "11 دقيقة")
        self.assertEqual(count_phrase(100, *args), "100 دقيقة")
        self.assertEqual(count_phrase(103, *args), "103 دقائق")

    def test_masculine_tamyeez(self):
        self.assertEqual(duration_phrase(15, "day"), "15 يومًا")
        self.assertEqual(duration_phrase(3, "day"), "3 أيام")

    def test_digits(self):
        self.assertEqual(to_digits("2026", "arabic"), "٢٠٢٦")
        self.assertEqual(to_digits("٢٠٢٦", "latin"), "2026")

    def test_compact(self):
        self.assertEqual(compact_number(1284), "1,284")
        self.assertEqual(compact_number(12900), "12.9 ألف")
        self.assertEqual(compact_number(4_200_000), "4.2 مليون")


@override_settings(TIME_ZONE="Asia/Hebron")
class DateTests(SimpleTestCase):
    when = datetime(2026, 9, 28, 19, 30, tzinfo=dt_tz.utc)  # 22:30 بتوقيت فلسطين

    def test_month_styles(self):
        d = date(2026, 9, 28)
        self.assertEqual(format_date(d, DateStyle(months="levant")), "الاثنين 28 أيلول 2026")
        self.assertEqual(format_date(d, DateStyle(months="egypt")), "الاثنين 28 سبتمبر 2026")
        self.assertEqual(format_date(d, DateStyle(months="dual")), "الاثنين 28 أيلول/سبتمبر 2026")
        self.assertEqual(format_date(d, DateStyle(months="morocco"), weekday=False), "28 شتنبر 2026")
        self.assertEqual(format_date(date(2026, 1, 5), DateStyle(months="algeria"), weekday=False), "5 جانفي 2026")

    def test_arabic_indic_digits(self):
        self.assertEqual(format_date(date(2026, 9, 28), DateStyle(months="egypt", digits="arabic"), weekday=False), "٢٨ سبتمبر ٢٠٢٦")

    def test_time_uses_local_zone_and_arabic_period(self):
        self.assertEqual(format_time(self.when, DateStyle()), "10:30 مساءً")
        self.assertEqual(format_time(self.when, DateStyle(clock="24")), "22:30")

    def test_hijri(self):
        self.assertEqual(format_hijri(date(2026, 9, 28)), "17 ربيع الآخر 1448 هـ")
        self.assertEqual(format_hijri(date(2026, 9, 28), DateStyle(hijri_adjust=1)), "18 ربيع الآخر 1448 هـ")

    def test_relative(self):
        now = self.when
        self.assertEqual(relative_time(now - timedelta(seconds=20), now), "الآن")
        self.assertEqual(relative_time(now - timedelta(minutes=1), now), "منذ دقيقة")
        self.assertEqual(relative_time(now - timedelta(minutes=2), now), "منذ دقيقتين")
        self.assertEqual(relative_time(now - timedelta(minutes=5), now), "منذ 5 دقائق")
        self.assertEqual(relative_time(now - timedelta(minutes=25), now), "منذ 25 دقيقة")
        self.assertEqual(relative_time(now - timedelta(hours=2), now), "منذ ساعتين")
        self.assertTrue(relative_time(now - timedelta(hours=26), now).startswith("أمس"))
        self.assertEqual(relative_time(now - timedelta(days=4), now), "منذ 4 أيام")
        self.assertIn("2026", relative_time(now - timedelta(days=40), now))


class SlugTests(SimpleTestCase):
    def test_arabic_slug(self):
        self.assertEqual(arabic_slugify("غزّة: افتتاح مكتبة، جديدة!"), "غزة-افتتاح-مكتبة-جديدة")

    def test_length_limit_on_word_boundary(self):
        slug = arabic_slugify("كلمة " * 50, max_length=30)
        self.assertLessEqual(len(slug), 30)
        self.assertFalse(slug.endswith("-"))
