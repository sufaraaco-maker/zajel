"""صور حقيقية للمواقع التجريبية من ويكيميديا كومنز، بتراخيص حرة فقط ومع نسبة كل صورة لصاحبها.

- تُقبل التراخيص التي تسمح بإعادة النشر: CC0 والملك العام وCC BY وCC BY-SA. يُرفض ما عداها
  (غير التجاري، ومنع الاشتقاق، والاستخدام العادل).
- تُستبعد الصور التي تحمل تحذير «حقوق الشخصية» (أشخاص يمكن التعرف عليهم)، وما يدل عنوانها أو
  تصنيفاتها على عنف أو عُري أو شعارات وخرائط؛ الصورة هنا توضيحية لخبر متخيَّل.
- كل ما يُنزَّل يُحفظ في مجلد مؤقت (var/demo-photos) فلا يتكرر التنزيل عند إعادة البناء.
- إن تعذّر الاتصال تعود المنصة إلى الصور المولَّدة دون أن يفشل الأمر.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "arcms-demo/1.0 (demo content loader for a self-hosted Arabic newsroom CMS)"
MAX_BYTES = 8 * 1024 * 1024

ALLOWED_LICENSE = re.compile(r"^(cc0|public domain|pd\b|pd-|cc[ -]by(-sa)?[ -]\d)", re.I)
BLOCKED = re.compile(
    r"(\bdead\b|death|corpse|\bkill|blood|injur|wound|victim|funeral|martyr|nude|naked|erotic|weapon|\bguns?\b|"
    r"rifle|soldier|\barmy\b|military|police|clash|riot|protest|attack|airstrike|\bbomb|explos|destroy|destruct|"
    r"rubble|\bwar\b|\btanks?\b|missile|prison|jail|execution|logo|\bmaps?\b|\bflags?\b|coat of arms|diagram|"
    r"poster|screenshot|portrait of|selfie)",
    re.I,
)

# عبارات البحث (بالإنجليزية لأنها أوفر نتائج في كومنز) لمواد المحتوى التجريبي المشترك.
# مقصودة عامة وهادئة: أماكن وأشياء لا وجوه، لأن الخبر متخيَّل.
QUERIES = {
    "ندوة حقوقية في رام الله تناقش أوضاع المعتقلين والاعتقال الإداري": "conference hall panel discussion",
    "افتتاح مكتبة عامة جديدة في نابلس تضم أكثر من 20 ألف كتاب": "public library reading room",
    "انطلاق موسم قطف الزيتون وسط توقعات بإنتاج وفير هذا العام": "olive harvest",
    "حملة تطوعية لتنظيف شاطئ غزة بمشاركة مئات الشبان": "beach cleanup volunteers",
    "البلدة القديمة في القدس تستعد لمهرجان الموسيقى التراثية": "oud musical instrument",
    "طلبة جامعيون يطوّرون تطبيقاً لتوثيق الأضرار في المنازل": "students laptop university",
    "مسؤول محلي: خطة لإعادة تأهيل شبكة المياه في مخيم بلاطة": "water pipes construction",
    "في معنى الصحافة المستقلة حين يضيق الهامش": "typewriter",
    "الشباب والسياسة: فجوة الثقة التي لا تسدها الشعارات": "university campus students walking",
    "المدينة التي تقرأ لا تُهزم": "bookshop interior",
    "اقتصاد الظل: كيف يعيش الموظفون بنصف راتب؟": "coins money",
    "يوميات مدوّن: مقهى صغير في حي قديم": "old cafe coffee cups",
    "رسالة إلى صديق هاجر": "airport departure hall",
    "كيف نكتب عن الحزن دون أن نستهلكه؟": "rain on window",
    "فيديو: جولة في أسواق البلدة القديمة قبيل العيد": "Jerusalem old city market",
    "فيديو: حرفي من الخليل يحافظ على صناعة الزجاج اليدوي": "Hebron glass",
    "فيديو: فريق كرة قدم نسائي يتحدى الظروف": "women's football match",
    "فيديو: مدرسة تعيد تدوير البلاستيك إلى مقاعد للطلبة": "plastic bottles recycling",
    "عائلات الأسرى تنظم معرضاً لرسائل كُتبت خلال سنوات الغياب": "handwritten letters",
    "محامٍ: زيارات ذوي المعتقلين يجب أن تكون حقاً ثابتاً": "courthouse building",
    "أمهات ينتظرن: حكايات من أمام قاعات المحاكم": "court building steps",
    "بلدية الناصرة تطلق مشروعاً لترميم البيوت التاريخية": "Nazareth old city",
    "معرض الكتاب العربي يستقبل آلاف الزوار في يومه الأول": "book fair",
    "ارتفاع أسعار الخضار في الأسواق مع بداية الخريف": "vegetable market",
    "مبادرة شبابية لتعليم كبار السن استخدام الهواتف الذكية": "smartphone hands",
    "معرض صور: وجوه من سوق الخضار": "vegetable market stall",
    "تحقيق: أين تذهب نفايات المدن الكبيرة؟": "landfill waste",
    "حوار مع روائية شابة: الكتابة فعل مقاومة للنسيان": "notebook fountain pen writing",
    "بدء التسجيل للفصل الدراسي الجديد في الجامعات": "university campus building",
    "دقيقة في سوق القطانين قبل الإفطار": "Cotton Merchants Market Jerusalem",
    "كيف يُعصر الزيت في معصرة حجرية عمرها قرن": "olive oil mill",
    "أطفال غزة يرسمون على جدار المدرسة الجديد": "mural painting wall",
    "صانع الفخار الأخير في الخليل": "pottery wheel potter",
    "جولة سريعة في مكتبة نابلس العامة": "library bookshelves",
    "من ملعب الحي إلى المنتخب: حكاية حارس مرمى": "goalkeeper football",
    "صيادو بحر غزة عند الفجر": "fishing boats harbor",
    "ترميم سبيل ماء تاريخي في البلدة القديمة": "sabil fountain",
    "معرض للصور القديمة لأحياء القدس في المركز الثقافي": "photography exhibition",
    "تجار البلدة القديمة يطلقون مبادرة لتسويق المنتجات المحلية": "old city market shops",
    "ورشة لتعليم الخط العربي للأطفال في الصيف": "Arabic calligraphy",
    "افتتاح مكتبة أطفال في حي الشيخ جراح": "children's books library",
    "مسابقة للتصوير الفوتوغرافي عن أبواب القدس": "Damascus Gate Jerusalem",
    "قرى بلا شبكة مياه: رحلة الصهاريج اليومية": "water tanker truck",
    "من الورشة إلى السوق: شباب يعيدون إحياء صناعة الأحذية": "shoemaker workshop",
    "مدارس الخيام: عام دراسي في الظروف الصعبة": "classroom desks",
    "البلدة القديمة ليلاً: من يحرس الأزقة؟": "old city alley night",
    "دفتر المراسل: ليلة بلا كهرباء": "candle light",
    "عن الجدات اللواتي يحفظن أسماء القرى": "stone village houses",
    "لماذا نحتاج صحافة البيانات الآن": "computer screen code",
}
# بديل حسب نوع المادة ثم القسم إن لم يكن للمادة عبارة خاصة
KIND_QUERIES = {"podcast": "microphone studio", "translation": "newspapers", "opinion": "newspaper reading"}
CATEGORY_QUERIES = {
    "القدس": "Jerusalem old city", "الضفة الغربية": "olive trees", "غزة": "Mediterranean sea", "محليات": "city street",
    "اقتصاد": "market", "رياضة": "stadium", "صحة": "hospital", "تكنولوجيا": "laptop", "ثقافة وفنون": "museum",
    "سياسة": "meeting room", "عربي ودولي": "city skyline", "مجتمع وثقافة": "old city street",
}
# أنواع لا تناسبها صورة فوتوغرافية
NO_PHOTO_KINDS = {"infographic", "cartoon"}


def query_for(article: dict, category: str = "") -> str:
    if article.get("kind") in NO_PHOTO_KINDS:
        return ""
    return (
        article.get("photo")
        or QUERIES.get(article.get("title", ""))
        or KIND_QUERIES.get(article.get("kind", ""))
        or CATEGORY_QUERIES.get(category or article.get("category", ""), "")
    )


@dataclass
class Photo:
    data: bytes
    title: str
    author: str
    license: str
    license_url: str
    page_url: str

    @property
    def credit(self) -> str:
        return f"{self.author} / ويكيميديا كومنز ({self.license})"[:150]


def _plain(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return " ".join(text.split())


def _meta(info: dict, key: str) -> str:
    return _plain(((info.get("extmetadata") or {}).get(key) or {}).get("value", ""))


def acceptable(page: dict, *, tall: bool = False) -> bool:
    """هل تصلح هذه النتيجة: ترخيص حر، وأبعاد مناسبة، وخالية من الموضوعات المستبعدة."""
    info = (page.get("imageinfo") or [{}])[0]
    if info.get("mime") != "image/jpeg" or not (info.get("thumburl") or info.get("url")):
        return False
    w, h = info.get("width", 0), info.get("height", 0)
    if tall:
        if h < 1000 or h < w * 1.1:
            return False
    elif w < 1200 or w < h * 1.25:
        return False
    if not ALLOWED_LICENSE.match(_meta(info, "LicenseShortName")):
        return False
    if _meta(info, "Restrictions"):  # حقوق الشخصية، علامات تجارية...
        return False
    haystack = " ".join([page.get("title", ""), _meta(info, "Categories"), _meta(info, "ObjectName")])
    return not BLOCKED.search(haystack)


class CommonsPhotos:
    def __init__(self, cache_dir: Path, *, session: requests.Session | None = None, timeout: int = 25, log=None):
        self.cache = Path(cache_dir)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout
        self.log = log or (lambda msg: None)
        self.used: set[str] = set()
        self.credits: list[Photo] = []
        self.offline = False
        self.failures = 0

    # --- البحث ---

    def _candidates(self, query: str, *, tall: bool) -> list[dict]:
        key = hashlib.sha1(f"{query}|{tall}".encode()).hexdigest()[:16]
        path = self.cache / "queries" / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if self.offline:
            return []
        params = {
            "action": "query", "format": "json", "formatversion": "2", "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "30",
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|Categories|ObjectName|Restrictions",
            "iiurlwidth": "900" if tall else "1600",
        }
        data = self._get_json(API, params)
        if data is None:
            return []
        pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
        found = []
        for page in pages:
            if not acceptable(page, tall=tall):
                continue
            info = page["imageinfo"][0]
            found.append({
                "title": page["title"],
                "url": info.get("thumburl") or info["url"],
                "page": info.get("descriptionurl", ""),
                "author": _meta(info, "Artist")[:80] or "مجهول",
                "license": _meta(info, "LicenseShortName"),
                "license_url": _meta(info, "LicenseUrl"),
            })
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return found

    def _get_json(self, url: str, params: dict) -> dict | None:
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            self._failed(exc)
            return None

    def _failed(self, exc: Exception) -> None:
        self.failures += 1
        if self.failures >= 3 and not self.offline:
            self.offline = True
            self.log(f"تعذّر الوصول إلى ويكيميديا كومنز ({type(exc).__name__})؛ ستُستخدم صور مولَّدة لبقية المواد.")

    # --- التنزيل ---

    def _download(self, cand: dict) -> bytes | None:
        path = self.cache / "files" / (hashlib.sha1(cand["title"].encode()).hexdigest()[:20] + ".jpg")
        if path.exists():
            return path.read_bytes()
        if self.offline:
            return None
        try:
            resp = self.session.get(cand["url"], timeout=self.timeout)
            resp.raise_for_status()
            data = resp.content
        except requests.RequestException as exc:
            self._failed(exc)
            return None
        if len(data) > MAX_BYTES or not _is_jpeg(data):
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass
        return data

    def get(self, query: str, *, tall: bool = False) -> Photo | None:
        """أول صورة مقبولة لم تُستخدم بعد لهذا البحث، أو None."""
        if not query:
            return None
        for cand in self._candidates(query, tall=tall):
            if cand["title"] in self.used:
                continue
            data = self._download(cand)
            if not data:
                continue
            self.used.add(cand["title"])
            photo = Photo(data, cand["title"], cand["author"], cand["license"], cand["license_url"], cand["page"])
            self.credits.append(photo)
            return photo
        return None


def _is_jpeg(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8"):
        return False
    from PIL import Image, UnidentifiedImageError

    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except (UnidentifiedImageError, OSError, SyntaxError):
        return False


def _https(url: str) -> bool:
    return bool(url) and url.startswith("https://")


def credits_html(photos: list[Photo]) -> str:
    """صفحة «مصادر الصور»: اسم الملف وصاحبه وترخيصه مع الروابط، كما تشترط تراخيص المشاع الإبداعي."""
    rows = []
    for p in photos:
        name = html.escape(p.title.removeprefix("File:"))
        link = f'<a href="{html.escape(p.page_url)}">{name}</a>' if _https(p.page_url) else name
        lic = html.escape(p.license)
        if _https(p.license_url):
            lic = f'<a href="{html.escape(p.license_url)}">{lic}</a>'
        rows.append(f"<li>{link} — {html.escape(p.author)} — {lic}</li>")
    intro = ("<p>الصور في هذا الموقع التجريبي من ويكيميديا كومنز بتراخيص حرة، وهي توضيحية: لا علاقة لها "
             "بالأخبار المتخيَّلة المنشورة معها. لكل صورة صاحبها وترخيصها أدناه.</p>")
    return intro + ("<ul>" + "".join(rows) + "</ul>" if rows else "<p>لا توجد صور منزّلة.</p>")
