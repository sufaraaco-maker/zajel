"""مدقق الأسلوب: ما يلتقطه المدقق اللغوي في غرفة الأخبار قبل النشر.

- الترقيم: الفاصلة والفاصلة المنقوطة وعلامة الاستفهام اللاتينية وسط نص عربي، والمسافات حولها.
- همزة الوصل في مصادر الأفعال المزيدة وأفعالها («انتخابات» لا «إنتخابات»)، وهمزة القطع المنسية
  في كلمات شائعة («أو» لا «او»).
- أخطاء إملائية متكررة («هذا» لا «هاذا»، «لكن» لا «لاكن»، «في» لا «فى»).
- الكلمة المكررة سهواً، والتطويل، وعلامات التنصيص الإنجليزية.
- دليل أسلوب المؤسسة: مصطلحاتها المفضلة والمتجنبة، بسطر لكل قاعدة.

المدقق لا يغيّر شيئاً بنفسه: يعيد ملاحظات بمواضعها والتصحيح المقترح، والمحرر يقرر.
كل فحص مبني على أنماط محافظة؛ ما يحتمل وجهين لا يُعلَّم («ان» قد تكون «أن» أو «إن»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

AR = "\u0621-\u063a\u0641-\u064a\u0671-\u06d3"  # الحروف بلا التطويل
MARKS = "\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed"  # الحركات والتنوين والشدة
TATWEEL = "\u0640"
HS = "[ \t\xa0]"  # مسافة أفقية
_STRIP = re.compile(f"[{MARKS}{TATWEEL}]")
_URL = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.]+", re.IGNORECASE)

CHECKS: dict[str, str] = {
    "punctuation": "علامات الترقيم العربية (، ؛ ؟) بدل اللاتينية",
    "spacing": "المسافات حول علامات الترقيم والأقواس",
    "hamza": "همزة الوصل في المصادر والأفعال، وهمزة القطع في الكلمات الشائعة",
    "typos": "أخطاء إملائية شائعة (هاذا، لاكن، فى…)",
    "repeat": "الكلمة المكررة سهواً",
    "tatweel": "التطويل داخل الكلمات («الـــقدس»)",
    "quotes": "علامات التنصيص «» بدل \"\"",
}
HOUSE = "house"
# أسماء قصيرة تظهر فوق كل ملاحظة في لوحة المحرر
LABELS = {"punctuation": "الترقيم", "spacing": "المسافات", "hamza": "الهمزات", "typos": "الإملاء",
          "repeat": "التكرار", "tatweel": "التطويل", "quotes": "التنصيص", HOUSE: "دليل الأسلوب"}
# عند تداخل ملاحظتين تبقى الأعلى أولوية؛ الباقية تظهر بعد تصحيح الأولى.
PRIORITY = [HOUSE, "typos", "hamza", "repeat", "punctuation", "spacing", "tatweel", "quotes"]
MAX_ISSUES = 300
MAX_RULES = 500


@dataclass
class Issue:
    start: int
    end: int
    text: str
    fix: str | None
    rule: str
    message: str

    def as_dict(self, before: str = "", after: str = "") -> dict:
        return {"start": self.start, "end": self.end, "text": self.text, "fix": self.fix, "rule": self.rule,
                "label": LABELS.get(self.rule, ""), "message": self.message, "before": before, "after": after}


# --- نسخة بلا تشكيل مع خريطة المواضع ---


class _Skeleton:
    """النص بلا حركات ولا تطويل، مع موضع كل حرف في الأصل: الأنماط تُطابق هنا والمواضع تُردّ إلى الأصل."""

    def __init__(self, text: str):
        self.text = text
        chars, index = [], []
        for i, ch in enumerate(text):
            if not _STRIP.match(ch):
                chars.append(ch)
                index.append(i)
        index.append(len(text))
        self.value = "".join(chars)
        self.index = index

    def span(self, start: int, end: int) -> tuple[int, int]:
        return self.index[start], (self.index[end - 1] + 1) if end > start else self.index[start]


# --- الترقيم والمسافات والتنصيص والتطويل (على النص الأصلي) ---

_AR_CHAR = re.compile(f"[{AR}{MARKS}]")
_LATIN_PUNCT = re.compile(f"{HS}*([,;]){HS}*")
_QMARK = re.compile(fr"{HS}*\?")
_ARABIC_PUNCT = {",": "،", ";": "؛"}
_SPACE_BEFORE = re.compile(fr"(?<=[{AR}{MARKS})»]){HS}+(?=[،؛؟:!]|\.(?![\d.]*\d))")
_SPACE_AFTER = re.compile(fr"(?<=[{AR}{MARKS}])(?:([،؛])(?=[{AR}A-Za-z\d«(])|([؟!])(?=[{AR}A-Za-z«(])|([.:])(?=[{AR}]))")
_MULTI_SPACE = re.compile(fr"(?<=\S){HS}{{2,}}(?=\S)")
_INNER_OPEN = re.compile(fr"(?<=[(«\[]){HS}+")
_INNER_CLOSE = re.compile(fr"(?<=\S){HS}+(?=[)»\]])")
_QUOTES = re.compile(r"[\"“”]([^\"“”\n]{1,400}?)[\"“”]")
_TATWEEL_RE = re.compile(fr"(?<=[{AR}{MARKS}]){TATWEEL}+(?=[{AR}])")


def _is_ar(ch: str) -> bool:
    return bool(ch) and bool(_AR_CHAR.match(ch))


def _punctuation(text: str):
    for m in _LATIN_PUNCT.finditer(text):
        prev = text[m.start() - 1] if m.start() else ""
        nxt = text[m.end()] if m.end() < len(text) else ""
        if not (_is_ar(prev) or _is_ar(nxt)) or (prev.isdigit() and nxt.isdigit()):
            continue
        mark = _ARABIC_PUNCT[m.group(1)]
        fix = mark + (" " if nxt and nxt not in "\n\r" and not nxt.isspace() else "")
        name = "الفاصلة" if mark == "،" else "الفاصلة المنقوطة"
        yield Issue(m.start(), m.end(), m.group(0), fix, "punctuation", f"{name} العربية «{mark}» في النص العربي.")
    for m in _QMARK.finditer(text):
        prev = text[m.start() - 1] if m.start() else ""
        nxt = text[m.end()] if m.end() < len(text) else ""
        if (_is_ar(prev) or prev == "»") and not (nxt and nxt.isascii() and (nxt.isalnum() or nxt in "=&")):
            yield Issue(m.start(), m.end(), m.group(0), "؟", "punctuation", "علامة الاستفهام العربية «؟».")


def _spacing(text: str):
    for m in _SPACE_BEFORE.finditer(text):
        mark = text[m.end()]
        after = text[m.end() + 1] if m.end() + 1 < len(text) else ""
        if _is_ar(after):  # «غادر .قال»: المسافة في غير موضعها، فتُنقل بعد العلامة
            yield Issue(m.start(), m.end() + 1, m.group(0) + mark, mark + " ", "spacing", f"المسافة بعد «{mark}» لا قبلها.")
        else:
            yield Issue(m.start(), m.end(), m.group(0), "", "spacing", f"لا مسافة قبل «{mark}».")
    for m in _SPACE_AFTER.finditer(text):
        mark = m.group(0)
        yield Issue(m.start(), m.end(), mark, mark + " ", "spacing", f"مسافة بعد «{mark}».")
    for m in _INNER_OPEN.finditer(text):
        yield Issue(m.start(), m.end(), m.group(0), "", "spacing", "لا مسافة بعد القوس أو علامة التنصيص.")
    for m in _INNER_CLOSE.finditer(text):
        yield Issue(m.start(), m.end(), m.group(0), "", "spacing", "لا مسافة قبل القوس أو علامة التنصيص.")
    for m in _MULTI_SPACE.finditer(text):  # بعد ما سبق: عند التطابق تبقى الملاحظة الأدق
        yield Issue(m.start(), m.end(), m.group(0), " ", "spacing", "مسافات متتالية.")


def _quotes(text: str):
    for m in _QUOTES.finditer(text):
        inner = m.group(1)
        if any(_is_ar(c) for c in inner):
            yield Issue(m.start(), m.end(), m.group(0), f"«{inner.strip()}»", "quotes", "علامات التنصيص العربية «».")


def _tatweel(text: str):
    for m in _TATWEEL_RE.finditer(text):
        yield Issue(m.start(), m.end(), m.group(0), "", "tatweel", "التطويل لا يُكتب داخل الكلمة.")


# --- الهمزات والأخطاء الإملائية والتكرار (على النسخة بلا تشكيل) ---

C = "بتثجحخدذرزسشصضطظعغفقكلمنهويءئؤ"
CE = C + "ى"
_PRE_ALL = r"(?P<pre>[وف]?(?:[بكل]?ال|لل|[بكل])?)"
_PRON = r"(?:ه|ها|هم|هما|هن|ك|كم|كما|ي|نا)"
_WASL = re.compile(
    r"(?<!\w)" + _PRE_ALL + r"(?P<w>(?!إستراتيج)(?:"
    fr"إن[{C}][{C}]ا[{C}]"                    # انفعال: انسحاب، انتخاب، انفجار
    fr"|إ[{C}][تطد][{C}]ا[{C}]"                # افتعال: اعتقال، اجتماع، اضطراب، ازدحام
    fr"|إست[{C}][{C}]ا[{C}]|إست[{C}]ا[{C}]ة"  # استفعال: استخدام، استقالة
    fr"|إ[{C}][تطد][{C}][{CE}](?!\w)"          # أفعالها الماضية: اجتمع، انتهى
    fr"|إست[{C}][{C}][{CE}](?!\w)"             # استقبل، استهدف
    r"|إ(?:تفاق|تصال|تهام|تجاه|تحاد|تخاذ|تساع|تضاح|تباع|تكال|تزان|تقاء|دعاء|دخار|طلاع)"
    fr"|إ(?:بن|بنة|سم|ثنان|ثنين|ثنتان|ثنتين|مرأة|مرأه){_PRON}?(?!\w)"
    fr")[{AR}]*)"
)
# همزة قطع تسقط كثيراً في كلمات لا تحتمل وجهاً آخر.
_QAT = {
    "او": "أو", "اي": "أي", "اول": "أول", "اكثر": "أكثر", "اكبر": "أكبر", "اقل": "أقل", "امس": "أمس",
    "ايضا": "أيضا", "اثناء": "أثناء", "احد": "أحد", "اين": "أين", "ايام": "أيام", "اذا": "إذا",
}
# «واحد» و«واو» و«واي فاي» و«فاو» كلمات قائمة: هذه الثلاث لا تُقبل بعد الواو والفاء.
_QAT_BARE = ("او", "اي", "احد")
_QAT_RE = re.compile(
    r"(?<!\w)(?:(?P<pre>[وف]?)(?P<w>" + "|".join(sorted(set(_QAT) - set(_QAT_BARE), key=len, reverse=True))
    + r")|(?P<w2>" + "|".join(_QAT_BARE) + r"))(?!\w)"
)
_ILA_RE = re.compile(r"(?<!\w)الى(?!\w)")

# (الخطأ، الصواب، السوابق المسموحة، اللواحق المسموحة)
_TYPOS: list[tuple[str, str, str, str]] = [
    ("هاذا", "هذا", "wf", ""), ("هاذه", "هذه", "wf", ""), ("هاذان", "هذان", "wf", ""), ("هاذين", "هذين", "wf", ""),
    ("هاؤلاء", "هؤلاء", "wf", ""), ("هائولاء", "هؤلاء", "wf", ""), ("ذالك", "ذلك", "all", ""),
    ("لاكن", "لكن", "wf", _PRON), ("اللذي", "الذي", "wf", ""), ("اللتي", "التي", "wf", ""),
    ("الذى", "الذي", "wf", ""), ("التى", "التي", "wf", ""), ("فى", "في", "", ""), ("حتي", "حتى", "wf", ""),
    ("شيئ", "شيء", "all", ""), ("لإن", "لأن", "wf", ""), ("لاسيما", "لا سيما", "wf", ""),
    ("إنشالله", "إن شاء الله", "wf", ""), ("إنشاء الله", "إن شاء الله", "wf", ""),
    ("انشاء الله", "إن شاء الله", "wf", ""), ("ان شاء الله", "إن شاء الله", "wf", ""),
]
_PRE_FOR = {"": "(?P<pre>)", "wf": "(?P<pre>[وف]?)", "all": "(?P<pre>[وف]?[بكل]?)"}
_TYPO_RES = [
    (re.compile(r"(?<!\w)" + _PRE_FOR[pre] + re.escape(wrong).replace(r"\ ", HS + "+")
                + (f"(?P<suf>{suf})?" if suf else "(?P<suf>)") + r"(?!\w)"), right)
    for wrong, right, pre, suf in _TYPOS
]
_REPEAT_RE = re.compile(fr"(?<!\w)([{AR}]{{2,}}){HS}+\1(?!\w)")
_REPEAT_OK = frozenset(
    "رويدا قليلا كثيرا شيئا خطوة يوما بيتا شبرا حجرا لا كلا نعم هيا جدا بعيدا حيا الله مرة ساعة دقيقة "
    "كلمة حرفا زنقة دار صفا".split()
)


def _swap_first(original: str, old: str, new: str) -> str:
    i = original.find(old)
    return original if i < 0 else original[:i] + new + original[i + len(old):]


def _hamza(sk: _Skeleton):
    text = sk.text
    for m in _WASL.finditer(sk.value):
        s, e = sk.span(m.start("w"), m.end("w"))
        word = text[s:e]
        plain = m.group("w").replace("إ", "ا", 1)  # الكلمة كاملة بلا السوابق
        s0 = sk.span(m.start(), m.end())[0]
        yield Issue(s0, e, text[s0:e], text[s0:s] + _swap_first(word, "إ", "ا"), "hamza",
                    f"همزة وصل: «{plain}» بلا همزة تحت الألف.")
    for m in _QAT_RE.finditer(sk.value):
        right = _QAT[m.group("w") or m.group("w2")]
        s, e = sk.span(m.start(), m.end())
        original = text[s:e]
        yield Issue(s, e, original, _swap_first(original, "ا", right[0]), "hamza", f"همزة قطع: «{right}».")
    for m in _ILA_RE.finditer(sk.value):
        s, e = sk.span(m.start(), m.end())
        yield Issue(s, e, text[s:e], "إلى", "hamza", "همزة قطع: «إلى».")


def _typos(sk: _Skeleton):
    for rx, right in _TYPO_RES:
        for m in rx.finditer(sk.value):
            s, e = sk.span(m.start(), m.end())
            yield Issue(s, e, sk.text[s:e], m.group("pre") + right + (m.group("suf") or ""), "typos",
                        f"الصواب: «{right}».")


def _repeat(sk: _Skeleton):
    for m in _REPEAT_RE.finditer(sk.value):
        if m.group(1) in _REPEAT_OK:
            continue
        s, e = sk.span(m.start(), m.end())
        first_end = sk.span(m.start(1), m.end(1))[1]
        yield Issue(s, e, sk.text[s:e], sk.text[s:first_end], "repeat", f"«{m.group(1)}» مكررة.")


# --- دليل أسلوب المؤسسة ---


@dataclass(frozen=True)
class HouseRule:
    term: str
    replacement: str
    note: str
    prefix: bool
    line: int


_SEPARATOR = re.compile(r"\s*(?:=>|->|⇐|←|→|=)\s*")


def parse_rules(text: str) -> tuple[list[HouseRule], list[str]]:
    """سطر لكل قاعدة: «الخطأ => الصواب | ملاحظة». بلا بديل تصبح تنبيهاً فقط، ونجمة في آخر الكلمة
    تعني «وما يتصل بها» («مسئول* => مسؤول» تصحح «مسئولين» أيضاً). السطر المبدوء بـ# تعليق."""
    rules: list[HouseRule] = []
    errors: list[str] = []
    seen: set[str] = set()
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        note = ""
        if "|" in line:
            line, note = (part.strip() for part in line.split("|", 1))
        parts = _SEPARATOR.split(line, maxsplit=1)
        term = " ".join(parts[0].split())
        replacement = " ".join(parts[1].split()) if len(parts) > 1 else ""
        prefix = term.endswith("*")
        term = _STRIP.sub("", term.rstrip("*").strip())
        replacement = replacement.rstrip("*").strip()
        if not term or not any(ch.isalpha() for ch in term):
            errors.append(f"السطر {n}: لا كلمة قبل «=>».")
        elif len(term) > 80 or len(replacement) > 120 or len(note) > 200:
            errors.append(f"السطر {n}: أطول من المسموح.")
        elif _STRIP.sub("", replacement) == term:
            errors.append(f"السطر {n}: البديل هو الكلمة نفسها.")
        elif term in seen:
            errors.append(f"السطر {n}: «{term}» مكررة في قاعدة سابقة.")
        else:
            seen.add(term)
            rules.append(HouseRule(term, replacement, note, prefix, n))
        if len(rules) >= MAX_RULES:
            errors.append(f"الحد الأقصى {MAX_RULES} قاعدة؛ أُهمل ما بعد السطر {n}.")
            break
    return rules, errors


def _rule_regex(rule: HouseRule) -> re.Pattern:
    def body(term: str) -> str:
        return (HS + "+").join(re.escape(w) for w in term.split())

    suffix = f"(?P<suf>[{AR}]*)" if rule.prefix else "(?P<suf>)"
    if rule.term.startswith("ال") and len(rule.term) > 3:
        core = f"(?P<pre>[وف]?[بك]?){body(rule.term)}|(?P<pre2>[وف]?)(?P<ll>لل){body(rule.term[2:])}"
    else:
        core = f"(?P<pre>[وف]?(?:[بكل]?ال|لل|[بكل])?){body(rule.term)}(?P<pre2>)(?P<ll>)"
    return re.compile(f"(?<!\\w)(?:{core}){suffix}(?!\\w)", re.IGNORECASE)


@lru_cache(maxsize=16)
def compile_rules(text: str) -> tuple[tuple[HouseRule, re.Pattern], ...]:
    rules, _ = parse_rules(text)
    return tuple((r, _rule_regex(r)) for r in rules)


def _house_fix(rule: HouseRule, m: re.Match) -> str | None:
    if not rule.replacement:
        return None
    repl, suf = rule.replacement, m.group("suf") or ""
    if m.group("ll"):
        pre = m.group("pre2") or ""
        return pre + ("لل" + repl[2:] if repl.startswith("ال") else "ل" + repl) + suf
    pre = m.group("pre") or ""
    if repl.startswith("ال") and pre.endswith("لل"):
        return pre + repl[2:] + suf
    if repl.startswith("ال") and pre.endswith("ال"):
        return pre[:-2] + repl + suf
    return pre + repl + suf


def _house(sk: _Skeleton, compiled):
    for rule, rx in compiled:
        for m in rx.finditer(sk.value):
            s, e = sk.span(m.start(), m.end())
            fix = _house_fix(rule, m)
            if rule.note:
                message = rule.note
            elif fix is None:
                message = f"«{rule.term}» يتجنبها دليل الأسلوب."
            else:
                message = f"دليل الأسلوب: «{rule.replacement}»."
            yield Issue(s, e, sk.text[s:e], fix, HOUSE, message)


# --- التجميع ---


def check(text: str, *, rules: str = "", disabled: frozenset[str] | set[str] | tuple = ()) -> list[Issue]:
    """ملاحظات النص مرتبة بالموضع، بلا تداخل، مع إسقاط ما يقع داخل الروابط والبريد."""
    if not text or not text.strip():
        return []
    disabled = set(disabled)
    sk = _Skeleton(text)
    found: list[Issue] = []
    if rules.strip():
        found += _house(sk, compile_rules(rules))
    plain = {"punctuation": _punctuation, "spacing": _spacing, "quotes": _quotes, "tatweel": _tatweel}
    skeletal = {"hamza": _hamza, "typos": _typos, "repeat": _repeat}
    for key in CHECKS:
        if key in disabled:
            continue
        found += plain[key](text) if key in plain else skeletal[key](sk)
    urls = [m.span() for m in _URL.finditer(text)]
    found = [i for i in found if i.fix != i.text and not any(i.start < e and s < i.end for s, e in urls)]
    rank = {k: n for n, k in enumerate(PRIORITY)}
    kept: list[Issue] = []
    for issue in sorted(found, key=lambda i: (rank.get(i.rule, 99), i.start)):
        if not any(issue.start < k.end and k.start < issue.end or issue.start == k.start for k in kept):
            kept.append(issue)
    kept.sort(key=lambda i: i.start)
    return kept[:MAX_ISSUES]


def _utf16(text: str):
    """مواضع المتصفح بوحدات UTF-16: تختلف عن مواضع بايثون بعد أي محرف خارج المستوى الأساسي (الرموز التعبيرية)."""
    if all(ord(ch) < 0x10000 for ch in text):
        return lambda i: i
    table = [0]
    for ch in text:
        table.append(table[-1] + (2 if ord(ch) >= 0x10000 else 1))
    return table.__getitem__


def report(text: str, *, rules: str = "", disabled=(), context: int = 28) -> list[dict]:
    """ملاحظات جاهزة للمتصفح: المواضع بوحدات UTF-16 مع شيء من السياق قبل الملاحظة وبعدها."""
    conv = _utf16(text or "")
    out = []
    for issue in check(text, rules=rules, disabled=disabled):
        before = text[max(0, issue.start - context):issue.start].split("\n")[-1]
        after = text[issue.end:issue.end + context].split("\n")[0]
        item = issue.as_dict(before=before, after=after)
        item["start"], item["end"] = conv(issue.start), conv(issue.end)
        out.append(item)
    return out
