from unittest import TestCase

from arcms.arabic import style


def found(text, **kw):
    return [(i.rule, i.text, i.fix) for i in style.check(text, **kw)]


def fixed(text, **kw):
    """يطبّق كل التصحيحات من الآخر إلى الأول، كما يفعل زر «صحّح الكل» في المحرر."""
    for issue in reversed(style.check(text, **kw)):
        if issue.fix is not None:
            text = text[:issue.start] + issue.fix + text[issue.end:]
    return text


class PunctuationTests(TestCase):
    def test_latin_comma_semicolon_question(self):
        self.assertEqual(fixed("قال الوزير , إن القرار نهائي ;وأضاف:هل من جديد?"),
                         "قال الوزير، إن القرار نهائي؛ وأضاف: هل من جديد؟")

    def test_spacing_around_arabic_marks(self):
        self.assertEqual(fixed("وصل الوفد ، ثم غادر .قال د.أحمد:نعم"), "وصل الوفد، ثم غادر. قال د. أحمد: نعم")
        self.assertEqual(fixed("قال (  نعم ) و«  لا »  أيضاً"), "قال (نعم) و«لا» أيضاً")

    def test_latin_text_numbers_and_links_untouched(self):
        for text in ("Hello, world? Yes; fine.", "بلغ السعر 1,500 دولار و٣,٥ مليون",
                     "الرابط https://example.com/a,b?q=1 وبريد info@example.com مهم",
                     "النسبة 3.5 بالمئة", "قال: «نعم».", "ثم...", "رويداً رويداً"):
            self.assertEqual(found(text), [], text)

    def test_quotes_and_tatweel(self):
        self.assertEqual(fixed('قال "نحن هنا" في الـــقدس'), "قال «نحن هنا» في القدس")
        self.assertEqual(found('said "hello" there'), [])
        # التطويل بعد حرف جر قبل تنصيص أو رقم مقبول: «بـ«حماس»» و«بـ 5»
        self.assertEqual(found("اتصل بـ«الوكالة» وتبرع بـ 5 دولارات"), [])


class HamzaTests(TestCase):
    def test_wasl_masdars_and_verbs(self):
        cases = {
            "الإنتخابات": "الانتخابات", "والإعتقالات": "والاعتقالات", "بالإستخدام": "بالاستخدام",
            "إستقالة": "استقالة", "إجتمع": "اجتمع", "إنتهى": "انتهى", "الإتفاق": "الاتفاق",
            "الإتحاد": "الاتحاد", "إضطراب": "اضطراب", "إزدحام": "ازدحام", "إبنه": "ابنه", "إسمها": "اسمها",
            "إثنان": "اثنان", "للإنسحاب": "للانسحاب",
        }
        for wrong, right in cases.items():
            self.assertEqual(fixed(wrong), right, wrong)

    def test_diacritics_are_kept(self):
        self.assertEqual(fixed("إستخدَمَ الجيشُ"), "استخدَمَ الجيشُ")

    def test_correct_hamza_words_untouched(self):
        words = ("إيران إسرائيل إسطنبول إستونيا الإستراتيجية إنجاز إنتاج إعداد إتمام إطلاق إنجيل إنكار إصدار "
                 "إيطاليا إندونيسيا إنستغرام إسماعيل إبراهيم الإنسان إنشاء المشروع إصلاح إدلب الإنترنت الإسترليني")
        self.assertEqual(found(words), [])

    def test_missing_qat_hamza_on_unambiguous_words(self):
        self.assertEqual(fixed("وصل الى المدينة او القرية امس وايضا اذا"), "وصل إلى المدينة أو القرية أمس وأيضا إذا")
        # «ان» و«الا» و«اما» تحتمل وجهين فلا تُعلَّم، و«واحد» و«واو» و«واي فاي» كلمات قائمة
        self.assertEqual(found("قال ان الا اما ليوم واحد وواو العطف وواي فاي ومنظمة فاو"), [])


class TyposAndRepeatTests(TestCase):
    def test_common_typos(self):
        self.assertEqual(fixed("هاذا ولاكنه لذالك فى البيت حتي ولاسيما هاؤلاء شيئ"),
                         "هذا ولكنه لذلك في البيت حتى ولا سيما هؤلاء شيء")
        self.assertEqual(fixed("إنشاء الله نلتقي"), "إن شاء الله نلتقي")
        self.assertEqual(found("إنشاء مدرسة وشيئاً فشيئاً"), [])

    def test_repeated_words(self):
        self.assertEqual(fixed("ذهب في في الصباح"), "ذهب في الصباح")
        self.assertEqual(found("رويداً رويداً وخطوة خطوة ولا لا"), [])


class HouseRuleTests(TestCase):
    RULES = """# تعليق
مسئول* => مسؤول | نكتب الهمزة على الواو
الكيان الصهيوني => الاحتلال
تم | «تمّ» + مصدر ضعيفة
أكد على -> أكد
"""

    def test_parse_and_errors(self):
        rules, errors = style.parse_rules(self.RULES + "=> شيء\nكلمة => كلمة\nمسئول => مسؤول\n")
        self.assertEqual([r.term for r in rules], ["مسئول", "الكيان الصهيوني", "تم", "أكد على"])
        self.assertTrue(rules[0].prefix)
        self.assertEqual(rules[2].replacement, "")
        self.assertEqual(len(errors), 3)

    def test_prefixes_suffixes_and_flags(self):
        text = "قال المسئولين وللمسئول إن الكيان الصهيوني وللكيان الصهيوني صعّد. وتم الاعتقال وأكد على الموقف."
        self.assertEqual(fixed(text, rules=self.RULES),
                         "قال المسؤولين وللمسؤول إن الاحتلال وللاحتلال صعّد. وتم الاعتقال وأكد الموقف.")
        flags = [i for i in style.check(text, rules=self.RULES) if i.fix is None]
        self.assertEqual([(i.text, i.message) for i in flags], [("وتم", "«تمّ» + مصدر ضعيفة")])

    def test_rule_matches_through_diacritics_but_not_hamza(self):
        self.assertEqual(fixed("المَسْئُول", rules="مسئول => مسؤول"), "المسؤول")
        self.assertEqual(found("المسؤول", rules="مسئول => مسؤول"), [])
        self.assertEqual(found("يتم", rules="تم"), [])

    def test_house_rule_wins_over_builtin(self):
        issues = style.check("الإنتخابات", rules="الإنتخابات => الاقتراع")
        self.assertEqual([(i.rule, i.fix) for i in issues], [("house", "الاقتراع")])


class ReportTests(TestCase):
    def test_disabled_checks_and_no_overlap(self):
        text = 'قال , "هاذا" في في'
        self.assertEqual({i.rule for i in style.check(text, disabled={"punctuation", "typos"})}, {"quotes", "repeat"})
        issues = style.check(text)
        for a, b in zip(issues, issues[1:]):
            self.assertLessEqual(a.end, b.start)

    def test_utf16_offsets_after_emoji(self):
        text = "😀 قال ,نعم"
        item = style.report(text)[0]
        self.assertEqual((item["start"], item["end"]), (6, 8))  # الرمز التعبيري وحدتان في UTF-16
        self.assertEqual(item["text"], " ,")
        self.assertEqual(item["before"], "😀 قال")
        self.assertEqual(item["label"], "الترقيم")

    def test_empty_and_huge_inputs(self):
        self.assertEqual(style.check(""), [])
        self.assertEqual(style.check("   \n "), [])
        self.assertEqual(len(style.check("كلمة , " * 2000)), style.MAX_ISSUES)
