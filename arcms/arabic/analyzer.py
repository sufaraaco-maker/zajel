"""محلّل البحث: يحوّل النص إلى مفاتيح فهرسة، والاستعلام إلى مجموعات مفاتيح.

كل كلمة في النص تُنتج «مجموعة مفاتيح» صغيرة:
  - الصورة المطبَّعة           «بالاعتقال» → «بالاعتقال»
  - الجذع الخفيف               → «اعتقال»
  - الهيكل بلا همزات وجذعه     «مسؤول» → «مسول»
  - صورة بلا حرف جر متصل       «بغزة» → «غزه» (وجذعها «غز»)

يُفهرس النص بكل المفاتيح، ويُبحث عن كل كلمة من الاستعلام بأي من مفاتيحها
(OR)، ثم تُربط كلمات الاستعلام معاً بـ AND. هكذا يجد البحث «بالاعتقال»
حين يكتب المستخدم «اعتقال»، ويتسامح مع التشكيل والهمزات.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import hamza_skeleton, normalize, normalize_digits, strip_diacritics
from .stemmer import clitic_variant, light_stem

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# كلمات وظيفية شائعة (بعد التطبيع) لا تفيد في البحث وتضخّم الفهرس.
STOPWORDS = frozenset(
    normalize(w)
    for w in """
    في من على إلى الى عن مع أن ان إن لن لم لا ما هذا هذه ذلك تلك هناك هنا التي الذي الذين اللذين اللتين
    اللواتي اللاتي كان كانت يكون تكون قد لقد كل بعض أي او أو ثم بل حتى إذا اذا إذ اذ منذ عند عندما بين
    حيث كما لكن لكنه غير أيضا ايضا هو هي هم هن نحن أنا انا انت أنت ذات وقد وفي ومن وعلى وإلى وعن وأن
    وان ولا وما فيه فيها منه منها عليه عليها إليه اليه به بها له لها لهم وهو وهي خلال ضمن نحو لدى
    """.split()
)

MAX_TOKEN_LEN = 40


def tokenize(text: str) -> list[str]:
    """يقطّع النص إلى كلمات بعد حذف التشكيل (حتى لا تنقسم الكلمة عند الحركة)."""
    if not text:
        return []
    text = normalize_digits(strip_diacritics(text))
    return [t for t in _TOKEN_RE.findall(text) if len(t) <= MAX_TOKEN_LEN]


def word_keys(word: str) -> set[str]:
    """مجموعة مفاتيح البحث لكلمة واحدة (غير مطبَّعة)."""
    norm = normalize(word)
    if not norm:
        return set()
    keys = {norm, light_stem(norm)}
    skel = hamza_skeleton(word)
    if skel and skel != norm:
        keys.add(skel)
        keys.add(light_stem(skel))
    variant = clitic_variant(norm)
    if variant:
        keys.add(variant)
        keys.add(light_stem(variant))
    return {k for k in keys if k}


def index_terms(text: str) -> list[str]:
    """قائمة مفاتيح الفهرسة لنص كامل (مع التكرار، لأن التكرار يؤثر في الترتيب)."""
    out: list[str] = []
    for tok in tokenize(text):
        if normalize(tok) in STOPWORDS:
            continue
        out.extend(sorted(word_keys(tok)))
    return out


def index_text(text: str) -> str:
    """نص المفاتيح مفصولاً بمسافات، جاهزاً لـ tsvector أو FTS5."""
    return " ".join(index_terms(text))


@dataclass(frozen=True)
class ParsedQuery:
    """استعلام محلَّل: كل عنصر في groups مجموعة مفاتيح بديلة لكلمة واحدة."""

    raw: str
    groups: tuple[frozenset[str], ...]
    phrases: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.groups

    @property
    def all_keys(self) -> frozenset[str]:
        keys: set[str] = set()
        for g in self.groups:
            keys |= g
        return frozenset(keys)


_PHRASE_RE = re.compile(r'"([^"]+)"|«([^»]+)»')


def parse_query(raw: str) -> ParsedQuery:
    """يحلّل استعلام المستخدم. العبارات بين علامتي تنصيص تُحفظ للتصفية الدقيقة."""
    raw = (raw or "").strip()[:300]
    phrases = tuple(
        normalize(a or b).strip() for a, b in _PHRASE_RE.findall(raw) if (a or b).strip()
    )
    tokens = tokenize(raw)
    meaningful = [t for t in tokens if normalize(t) not in STOPWORDS]
    # إذا كان الاستعلام كله كلمات وظيفية نبحث بها كما هي.
    chosen = meaningful or tokens
    groups: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    for tok in chosen[:12]:
        keys = frozenset(word_keys(tok))
        if keys and keys not in seen:
            seen.add(keys)
            groups.append(keys)
    return ParsedQuery(raw=raw, groups=tuple(groups), phrases=phrases)


def matches(text: str, query: ParsedQuery) -> bool:
    """فحص مطابقة في الذاكرة (يُستخدم للتظليل وفي الاختبارات)."""
    keys = set(index_terms(text))
    return all(group & keys for group in query.groups)
