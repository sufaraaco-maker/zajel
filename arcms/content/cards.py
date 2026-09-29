"""بطاقات المشاركة بهوية الموقع: صورة 1200×630 فيها العنوان بخط الموقع ولونه وشعاره، تظهر
عند مشاركة الرابط على المنصات وتُرسل مع المادة إلى تيليجرام؛ وبطاقة «عاجل» بلا صورة.

الرسم بـ Pillow مع تشكيل النص العربي واتجاهه (libraqm). إن غابت المكتبة عن الخادم تعود
المنصة إلى الصورة الرئيسية للمادة دون أن يتعطل شيء. الخطوط كاملة (عربي ولاتيني وأرقام)
بترخيص SIL OFL في card_fonts/.
"""

from __future__ import annotations

import hashlib
import io
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageDraw, ImageFont, ImageOps, features

from arcms.core import colors

FONT_DIR = Path(__file__).resolve().parent / "card_fonts"
FONT_FILES = {
    "plex": "ibm-plex-sans-arabic-bold.ttf",
    "kufi": "noto-kufi-arabic.ttf",
    "naskh": "noto-naskh-arabic.ttf",
    "cairo": "cairo.ttf",
    "tajawal": "tajawal-bold.ttf",
    "almarai": "almarai-bold.ttf",
    "amiri": "amiri-bold.ttf",
}
SIZES = {"wide": (1200, 630), "square": (1080, 1080), "story": (1080, 1920)}
FORMAT_LABELS = {"wide": ("عريضة", "للروابط وتيليجرام (1200×630)"), "square": ("مربعة", "لإنستغرام وفيسبوك (1080×1080)"),
                 "story": ("قصة", "للقصص والحالات (1080×1920)")}
VERSION = "1"
WHITE = (255, 255, 255)


def available() -> bool:
    """تشكيل العربية يحتاج libraqm؛ دونه تخرج الحروف مقطّعة فلا نرسم بطاقات."""
    return features.check("raqm")


@lru_cache(maxsize=96)
def _font(key: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / FONT_FILES.get(key, FONT_FILES["plex"])
    font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
    try:  # الخطوط المتغيرة: الوزن العريض بالاسم
        names = [n.decode() if isinstance(n, bytes) else n for n in font.get_variation_names()]
        for wanted in ("Bold", "ExtraBold", "SemiBold"):
            if wanted in names:
                font.set_variation_by_name(wanted)
                break
    except OSError:
        pass
    return font


def _length(font, text: str) -> float:
    return font.getlength(text, direction="rtl", language="ar")


def wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and _length(font, trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def fit(text: str, key: str, max_width: int, max_lines: int, start: int, minimum: int):
    """أكبر خط يتسع فيه العنوان في عدد الأسطر المسموح، وإلا يُقصّ بعلامة حذف."""
    size = start
    while True:
        font = _font(key, size)
        lines = wrap(text, font, max_width)
        if len(lines) <= max_lines or size <= minimum:
            break
        size -= 4
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _length(font, last + "…") > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last + "…"
    return font, size, lines


def _rgb(value: str) -> tuple[int, int, int]:
    return colors.to_rgb(value)


def _open_asset(asset) -> Image.Image | None:
    if not asset:
        return None
    try:
        with asset.file.open("rb") as fh:
            img = Image.open(io.BytesIO(fh.read()))
            img.load()
        return img
    except (OSError, ValueError):
        return None


def _gradient(size: tuple[int, int], start: float, strength: int) -> Image.Image:
    """قناع تعتيم يبدأ من نسبة start من الارتفاع ويشتد نحو الأسفل."""
    w, h = size
    column = Image.new("L", (1, h))
    top = int(h * start)
    for y in range(h):
        t = 0 if y < top else ((y - top) / max(1, h - top)) ** 1.2
        column.putpixel((0, y), int(strength * t))
    return column.resize((w, h))


def _top_shade(size: tuple[int, int], extent: float, strength: int) -> Image.Image:
    w, h = size
    column = Image.new("L", (1, h))
    stop = max(1, int(h * extent))
    for y in range(h):
        t = max(0.0, 1 - y / stop)
        column.putpixel((0, y), int(strength * t ** 1.6))
    return column.resize((w, h))


def _logo(site, *, on_dark: bool, height: int) -> tuple[Image.Image | None, bool]:
    """الشعار بالارتفاع المطلوب؛ يعيد أيضاً هل يحتاج خلفية فاتحة (شعار فاتح فوق صورة داكنة)."""
    asset = (site.logo_dark or site.logo) if on_dark else site.logo
    img = _open_asset(asset)
    if img is None:
        return None, False
    img = img.convert("RGBA")
    ratio = height / img.height
    img = img.resize((max(1, int(img.width * ratio)), height), Image.Resampling.LANCZOS)
    needs_plate = on_dark and not site.logo_dark
    return img, needs_plate


def _paste_logo(canvas, site, *, on_dark: bool, height: int, right: int, top: int, max_width: int) -> int:
    logo, plate = _logo(site, on_dark=on_dark, height=height)
    draw = ImageDraw.Draw(canvas)
    if logo is None:  # بلا شعار: اسم الموقع بخط العناوين
        font = _font(site.font_headings, int(height * 0.62))
        draw.text((right, top + height // 2), site.short_name or site.name, font=font, fill=WHITE if on_dark else (17, 20, 24),
                  direction="rtl", language="ar", anchor="rm")
        return height
    if logo.width > max_width:
        logo = logo.resize((max_width, int(logo.height * max_width / logo.width)), Image.Resampling.LANCZOS)
    x = right - logo.width
    if plate:
        pad = 14
        draw.rounded_rectangle((x - pad, top - pad, right + pad, top + logo.height + pad), radius=14, fill=WHITE)
    canvas.alpha_composite(logo, (x, top))
    return logo.height


def _pill(draw, text: str, *, right: int, top: int, font, bg, fg) -> int:
    pad_x, pad_y = int(font.size * 0.55), int(font.size * 0.28)
    width = int(_length(font, text))
    height = int(font.size * 1.25)
    draw.rounded_rectangle((right - width - 2 * pad_x, top, right, top + height + 2 * pad_y), radius=10, fill=bg)
    draw.text((right - pad_x, top + pad_y + height // 2), text, font=font, fill=fg, direction="rtl", language="ar",
              anchor="rm")
    return height + 2 * pad_y


def _domain() -> str:
    from urllib.parse import urlsplit

    host = (urlsplit(settings.SITE_URL).hostname or "").removeprefix("www.")
    return "" if host in ("localhost", "127.0.0.1", "testserver") else host


def _headline(draw, lines, font, size, *, right: int, bottom: int, fill=WHITE) -> int:
    """يرسم الأسطر محاذاة لليمين فوق خط bottom ويعيد أعلى نقطة."""
    line_h = int(size * 1.5)
    top = bottom - line_h * len(lines)
    for i, line in enumerate(lines):
        draw.text((right, top + i * line_h + line_h // 2), line, font=font, fill=fill, direction="rtl", language="ar",
                  anchor="rm")
    return top


LAYOUT = {
    # الهامش، ارتفاع الشعار، حجم العنوان الأقصى والأدنى، أقصى أسطر، حجم الشارة
    "wide": (60, 84, 64, 40, 3, 30),
    "square": (72, 108, 80, 48, 5, 36),
    "story": (84, 132, 108, 64, 7, 44),
}


def render_article(article, site, fmt: str = "wide") -> bytes:
    w, h = SIZES[fmt]
    margin, logo_h, big, small, max_lines, pill_size = LAYOUT[fmt]
    p = site.palette()
    photo = _open_asset(article.featured_image) if article.featured_image_id else None
    if photo is not None:
        canvas = ImageOps.fit(photo.convert("RGB"), (w, h), Image.Resampling.LANCZOS,
                              centering=article.featured_image.focal).convert("RGBA")
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        canvas = Image.composite(shade, canvas, _gradient((w, h), 0.18 if fmt == "wide" else 0.3, 235))
        canvas = Image.composite(shade, canvas, _top_shade((w, h), 0.32, 120))  # يوضّح الشعار فوق الصور الفاتحة
    else:
        base = colors.mix(p["primary"], "#000000", 0.55)
        canvas = Image.new("RGBA", (w, h), _rgb(base) + (255,))
        glow = Image.new("RGBA", (w, h), _rgb(p["primary"]) + (255,))
        canvas = Image.composite(glow, canvas, _gradient((w, h), 0, 200).transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    draw = ImageDraw.Draw(canvas)
    right = w - margin
    _paste_logo(canvas, site, on_dark=True, height=logo_h, right=right, top=margin - 8, max_width=int(w * 0.42))
    domain = _domain()
    if domain:
        draw.text((margin, margin - 8 + logo_h // 2), domain, font=_font("plex", int(pill_size * 0.85)),
                  fill=(255, 255, 255, 215), anchor="lm")
    bar = max(10, h // 60)
    breaking = getattr(article, "is_breaking", False)
    draw.rectangle((0, h - bar, w, h), fill=_rgb(p["breaking"] if breaking else p["primary"]))
    font, size, lines = fit(article.title, site.font_headings, w - 2 * margin, max_lines, big, small)
    top = _headline(draw, lines, font, size, right=right, bottom=h - bar - int(margin * 0.9))
    kicker = "عاجل" if breaking else (article.display_kicker or (article.category.name if article.category_id else ""))
    if kicker:
        bg, fg = (p["breaking"], p["on_breaking"]) if breaking else (p["primary"], p["on_primary"])
        pill_font = _font(site.font_headings, pill_size)
        pill_h = int(pill_size * 1.25) + 2 * int(pill_size * 0.28)
        _pill(draw, kicker[:40], right=right, top=top - pill_h - int(size * 0.3), font=pill_font, bg=_rgb(bg), fg=_rgb(fg))
    return _jpeg(canvas)


def render_breaking(text: str, when, site, fmt: str = "wide") -> bytes:
    from arcms.arabic.dates import format_time

    w, h = SIZES[fmt]
    margin, logo_h, big, small, max_lines, pill_size = LAYOUT[fmt]
    p = site.palette()
    bg = p["accent"] if colors.luminance(p["accent"]) < 0.08 else "#111418"
    canvas = Image.new("RGBA", (w, h), _rgb(bg) + (255,))
    glow = Image.new("RGBA", (w, h), _rgb(colors.mix(bg, p["breaking"], 0.22)) + (255,))
    canvas = Image.composite(glow, canvas, _gradient((w, h), 0.35, 255))
    draw = ImageDraw.Draw(canvas)
    right = w - margin
    label_font = _font(site.font_headings, int(pill_size * 1.6))
    label_h = _pill(draw, site.ticker_label or "عاجل", right=right, top=margin, font=label_font,
                    bg=_rgb(p["breaking"]), fg=_rgb(p["on_breaking"]))
    if when:
        stamp = format_time(when, site.date_style)
        draw.text((margin, margin + label_h // 2), stamp, font=_font("plex", int(pill_size * 0.95)),
                  fill=(255, 255, 255, 200), anchor="lm")
    bar = max(12, h // 50)
    draw.rectangle((0, h - bar, w, h), fill=_rgb(p["breaking"]))
    logo_top = h - bar - margin - logo_h + 8
    _paste_logo(canvas, site, on_dark=True, height=logo_h, right=right, top=logo_top, max_width=int(w * 0.42))
    font, size, lines = fit(text, site.font_headings, w - 2 * margin, max_lines, int(big * 1.1), small)
    block = int(size * 1.5) * len(lines)
    space_top, space_bottom = margin + label_h + 30, logo_top - 30
    bottom = space_top + (space_bottom - space_top + block) // 2
    _headline(draw, lines, font, size, right=right, bottom=min(bottom, space_bottom))
    return _jpeg(canvas)


def _jpeg(canvas: Image.Image) -> bytes:
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue()


# --- الإصدار والتخزين ---


def _site_key(site) -> str:
    return f"{site.updated_at.isoformat() if site.updated_at else ''}|{site.logo_id}|{site.logo_dark_id}"


def article_version(article, site) -> str:
    raw = "|".join([
        VERSION, article.title, article.display_kicker or "", str(article.category_id or ""),
        str(article.featured_image_id or ""),
        "%.3f,%.3f" % article.featured_image.focal if article.featured_image_id else "",
        str(article.is_breaking), _site_key(site),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def breaking_version(breaking, site) -> str:
    raw = "|".join([VERSION, breaking.text, breaking.created_at.isoformat(), _site_key(site)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _cached(name: str, prefix: str, render) -> bytes:
    """يحفظ البطاقة في التخزين ويحذف إصداراتها السابقة."""
    path = f"cards/{name}"
    if default_storage.exists(path):
        with default_storage.open(path, "rb") as fh:
            return fh.read()
    data = render()
    try:
        _, files = default_storage.listdir("cards")
        for old in files:
            if old.startswith(prefix) and old != name:
                default_storage.delete(f"cards/{old}")
    except (FileNotFoundError, NotImplementedError, OSError):
        pass
    default_storage.save(path, ContentFile(data))
    return data


def article_card(article, site, fmt: str = "wide") -> bytes:
    version = article_version(article, site)
    prefix = f"a{article.pk}-{fmt}-"
    return _cached(f"{prefix}{version}.jpg", prefix, lambda: render_article(article, site, fmt))


def breaking_card(breaking, site, fmt: str = "wide") -> bytes:
    version = breaking_version(breaking, site)
    prefix = f"b{breaking.pk}-{fmt}-"
    return _cached(f"{prefix}{version}.jpg", prefix,
                   lambda: render_breaking(breaking.text, breaking.created_at, site, fmt))


def article_card_url(article, site) -> str:
    """رابط البطاقة بإصدارها (يتغير مع العنوان والصورة والهوية فتعيد المنصات جلبها)، أو فارغ إن عُطّلت."""
    from django.urls import reverse

    if not (site.share_cards and article.pk and available()):
        return ""
    return reverse("public:article_card", args=[article.pk]) + "?v=" + article_version(article, site)


def breaking_card_url(breaking, site) -> str:
    from django.urls import reverse

    if not (site.share_cards and breaking.pk and available()):
        return ""
    return reverse("public:breaking_card", args=[breaking.pk]) + "?v=" + breaking_version(breaking, site)
