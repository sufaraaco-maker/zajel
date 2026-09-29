"""نزع البيانات المخفية من الصور وتوليد المقاسات.

الصورة القادمة من هاتف مراسل قد تحمل إحداثيات GPS لمكان التصوير،
وطراز الهاتف ورقمه التسلسلي، واسم المالك، وتاريخ الالتقاط الدقيق،
وصورة مصغّرة مضمَّنة قد تكشف ما قُصّ من الصورة. كل ذلك قد يدل على
المصوّر أو المصدر. لذلك:

1. نقرأ ما في الصورة من بيانات لنخبر المحرر بما وُجد (دون حفظ القيم).
2. نطبّق اتجاه الصورة ثم نعيد بناءها من البكسلات فقط، فلا ينجو أي
   مقطع EXIF أو XMP أو IPTC أو تعليق أو ملف لوني.
3. نحفظ باسم عشوائي؛ اسم الملف الأصلي (IMG_Gaza_source.jpg) لا يُحفظ.
4. نعيد فتح الناتج ونتحقق من خلوّه، وإلا نرفض الرفع.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

try:  # صور iPhone بصيغة HEIC تحمل الموقع افتراضياً
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pillow_heif = None

Image.MAX_IMAGE_PIXELS = 60_000_000  # حد Pillow (يرفض فوق ضعفه)؛ ونحن نرفض قبل الفك فوق MAX_PIXELS
MAX_PIXELS = 40_000_000  # نحو 7700×5200: أكبر من أي كاميرا هاتف، وأصغر من «قنابل» الصور المضغوطة

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "HEIF", "HEIC", "TIFF", "MPO", "BMP"}

RENDITIONS = {
    "thumb": (400, None),
    "card": (800, None),
    "large": (1400, None),
    "social": (1200, 630),  # قصّ لمشاركات تيليجرام وفيسبوك وإكس
}

# وسوم EXIF التي نبلّغ عنها بالاسم لأنها تكشف هوية أو مكاناً.
_SENSITIVE_TAGS = {
    0x010F: "الشركة المصنّعة للكاميرا",
    0x0110: "طراز الجهاز",
    0x013B: "اسم المصوّر",
    0x8298: "حقوق النشر",
    0x0131: "البرنامج المستخدم",
    0x0132: "تاريخ التعديل",
    0xA430: "اسم مالك الكاميرا",
    0xA431: "الرقم التسلسلي للكاميرا",
    0xA435: "الرقم التسلسلي للعدسة",
    0xA434: "طراز العدسة",
    0x9003: "تاريخ الالتقاط الأصلي",
    0x927C: "بيانات الشركة المصنّعة",
    0xA420: "معرّف الصورة الفريد",
}


class ImageRejected(ValueError):
    pass


@dataclass
class CleanImage:
    content: bytes
    ext: str
    mime: str
    width: int
    height: int
    removed: list[str] = field(default_factory=list)


def inspect_metadata(img: Image.Image) -> list[str]:
    """قائمة وصفية بالبيانات المخفية الموجودة (لإبلاغ المحرر)."""
    found: list[str] = []
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001
        exif = {}
    if exif:
        try:
            gps = exif.get_ifd(0x8825)
        except Exception:  # noqa: BLE001
            gps = {}
        if gps:
            found.append("إحداثيات الموقع الجغرافي (GPS)")
        try:
            sub = exif.get_ifd(0x8769)
        except Exception:  # noqa: BLE001
            sub = {}
        for tag, label in _SENSITIVE_TAGS.items():
            if tag in exif or tag in sub:
                found.append(label)
        if not found:
            found.append("بيانات EXIF")
    info = img.info or {}
    if "xmp" in info or "XML:com.adobe.xmp" in info:
        found.append("بيانات XMP (قد تتضمن سجل التعديل والموقع)")
    if "photoshop" in info or "iptc" in info:
        found.append("بيانات IPTC (الكاتب، الموقع، الكلمات المفتاحية)")
    if "comment" in info:
        found.append("تعليق مضمَّن")
    if "icc_profile" in info:
        found.append("ملف الألوان للجهاز")
    raw_exif = info.get("exif", b"") or b""
    if isinstance(raw_exif, bytes) and b"\xff\xd8\xff" in raw_exif[6:]:
        found.append("صورة مصغّرة مضمَّنة (قد تُظهر ما قُصّ من الصورة)")
    return list(dict.fromkeys(found))


def _to_srgb(img: Image.Image) -> Image.Image:
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        mode = "RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"
        return ImageCms.profileToProfile(img.convert(mode), src, dst, outputMode=mode)
    except Exception:  # noqa: BLE001 - ملف ألوان تالف: نتجاهله
        return img


def _rebuild(img: Image.Image) -> Image.Image:
    """صورة جديدة من البكسلات فقط: لا info ولا exif ولا شيء آخر."""
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    mode = "RGBA" if has_alpha else "RGB"
    if img.mode != mode:
        img = img.convert(mode)
    clean = Image.frombytes(mode, img.size, img.tobytes())
    return clean


def strip_and_normalize(data: bytes) -> CleanImage:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected("حجم الصورة يتجاوز 25 ميغابايت.")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        if fmt not in ALLOWED_FORMATS:
            raise ImageRejected(f"صيغة غير مدعومة: {fmt or 'غير معروفة'}")
        if img.width * img.height > MAX_PIXELS:
            # يُفحص من الترويسة قبل فك البكسلات: ملف صغير قد يتمدد إلى غيغابايتات في الذاكرة.
            raise ImageRejected("أبعاد الصورة أكبر من المسموح.")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageRejected("الملف ليس صورة صالحة أو أنه تالف.") from exc

    removed = inspect_metadata(img)
    if getattr(img, "is_animated", False):
        img.seek(0)  # الصور المتحركة: نحتفظ بالإطار الأول فقط
    img = ImageOps.exif_transpose(img) or img
    img = _to_srgb(img)
    clean = _rebuild(img)

    out = io.BytesIO()
    if clean.mode == "RGBA":
        clean.save(out, "PNG", optimize=True)
        ext, mime = "png", "image/png"
    else:
        clean.save(out, "JPEG", quality=86, optimize=True, progressive=True)
        ext, mime = "jpg", "image/jpeg"
    content = out.getvalue()
    _assert_clean(content)
    return CleanImage(content, ext, mime, clean.width, clean.height, removed)


def _assert_clean(content: bytes) -> None:
    check = Image.open(io.BytesIO(content))
    leftovers = [k for k in ("exif", "xmp", "icc_profile", "photoshop", "comment") if k in check.info]
    if leftovers or len(check.getexif()):
        raise ImageRejected("تعذّر ضمان نزع البيانات المخفية؛ رُفضت الصورة احتياطاً.")


def _rendition(img: Image.Image, width: int, height: int | None, focal=(0.5, 0.4)) -> Image.Image:
    if height:
        return ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS, centering=focal)
    if img.width <= width:
        return img.copy()
    ratio = width / img.width
    return img.resize((width, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)


def _save_rendition(r: Image.Image, name: str, base_path: str) -> str:
    buf = io.BytesIO()
    if name == "social":
        r.convert("RGB").save(buf, "JPEG", quality=84, optimize=True, progressive=True)
        ext = "jpg"
    else:
        r.save(buf, "WEBP", quality=80, method=5)
        ext = "webp"
    return default_storage.save(f"{base_path}-{name}.{ext}", ContentFile(buf.getvalue()))


def build_renditions(clean: CleanImage, base_path: str, focal=(0.5, 0.4)) -> dict[str, str]:
    img = Image.open(io.BytesIO(clean.content))
    img.load()
    return {name: _save_rendition(_rendition(img, w, h, focal), name, base_path) for name, (w, h) in RENDITIONS.items()}


def recrop(asset) -> None:
    """يعيد بناء المقاسات ذات النسبة الثابتة بعد تغيير نقطة التركيز (الأخرى لا تُقصّ)."""
    with asset.file.open("rb") as fh:
        img = Image.open(io.BytesIO(fh.read()))
        img.load()
    renditions = dict(asset.renditions or {})
    # اسم جديد في كل مرة، حتى لا تعرض المتصفحات والمنصات القصّ القديم من ذاكرتها
    base = f"{asset.file.name.rsplit('.', 1)[0]}-{uuid.uuid4().hex[:6]}"
    for name, (w, h) in RENDITIONS.items():
        if not h:
            continue
        old = renditions.get(name)
        renditions[name] = _save_rendition(_rendition(img, w, h, asset.focal), name, base)
        if old and old != renditions[name]:
            default_storage.delete(old)
    asset.renditions = renditions
    asset.save(update_fields=["renditions"])


def store_image(data: bytes, *, user=None, title: str = "", caption: str = "", credit: str = "", alt: str = ""):
    """نقطة الدخول الوحيدة لحفظ أي صورة في المنصة."""
    from .models import MediaAsset

    clean = strip_and_normalize(data)
    digest = hashlib.sha256(clean.content).hexdigest()
    existing = MediaAsset.objects.filter(sha256=digest).first()
    if existing:
        return existing, False
    now = timezone.now()
    base = f"uploads/{now:%Y/%m}/{uuid.uuid4().hex}"
    path = default_storage.save(f"{base}.{clean.ext}", ContentFile(clean.content))
    asset = MediaAsset(
        file=path,
        title=title[:200],
        caption=caption[:400],
        credit=credit[:150],
        alt_text=(alt or caption or title)[:250],
        width=clean.width,
        height=clean.height,
        mime=clean.mime,
        size=len(clean.content),
        sha256=digest,
        removed_metadata=clean.removed,
        uploaded_by=user if getattr(user, "pk", None) else None,
    )
    asset.renditions = build_renditions(clean, base)
    asset.save()
    return asset, True
