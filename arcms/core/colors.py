"""حساب الألوان: التباين (WCAG)، ولون النص المقروء على أي خلفية، ومزج الألوان،
واستخراج ألوان الهوية من الشعار. بلا اعتماد على Django."""

from __future__ import annotations

import re

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DARK_TEXT = "#111418"
LIGHT_TEXT = "#ffffff"
DARK_BG = "#0f1113"  # خلفية الوضع الداكن في الموقع
DARK_SURFACE = "#1f2327"  # أفتح سطح في الوضع الداكن (البطاقات والخط الزمني): أصعب خلفية للنص الملوّن


def is_hex(value: str) -> bool:
    return bool(value and HEX.match(value))


def to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def luminance(value: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = to_rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(bg: str) -> str:
    """أبيض أو داكن، أيهما أوضح على هذه الخلفية."""
    return LIGHT_TEXT if contrast(bg, LIGHT_TEXT) >= contrast(bg, DARK_TEXT) else DARK_TEXT


def mix(a: str, b: str, t: float) -> str:
    """مزيج بنسبة t من b (0 = a كما هو، 1 = b)."""
    ra, rb = to_rgb(a), to_rgb(b)
    return to_hex(x + (y - x) * t for x, y in zip(ra, rb))


def muted_on(bg: str) -> str:
    """نص ثانوي هادئ فوق خلفية ملوّنة (أقل حدة من النص الأساسي، ومقروء)."""
    fg = readable_on(bg)
    return mix(fg, bg, 0.22)


def for_dark_mode(value: str, *, min_ratio: float = 3.0) -> str:
    """يفتّح اللون حتى يُقرأ على خلفية الوضع الداكن، دون تغيير درجته."""
    color = value
    for step in range(1, 11):
        if contrast(color, DARK_BG) >= min_ratio:
            return color
        color = mix(value, "#ffffff", step * 0.08)
    return color


def for_text_on(value: str, bg: str, *, min_ratio: float = 4.5) -> str:
    """درجة من اللون تُقرأ نصاً على هذه الخلفية: تغميق على الفاتح وتفتيح على الداكن، مع بقاء الدرجة."""
    target = "#000000" if luminance(bg) > 0.4 else "#ffffff"
    color = value
    for step in range(1, 13):
        if contrast(color, bg) >= min_ratio:
            return color
        color = mix(value, target, step * 0.07)
    return color


def is_light(value: str) -> bool:
    return luminance(value) > 0.6


def palette_from_image(path_or_file, count: int = 6) -> list[str]:
    """ألوان الشعار الغالبة، بلا الشفاف ولا الأبيض والأسود القريبين، مرتبة بالانتشار."""
    from PIL import Image

    img = Image.open(path_or_file)
    img.thumbnail((200, 200))
    img = img.convert("RGBA")
    pixels = [
        (r, g, b)
        for r, g, b, a in img.getdata()
        if a > 200 and not (r > 235 and g > 235 and b > 235) and not (r < 20 and g < 20 and b < 20)
    ]
    if not pixels:
        return []
    sample = Image.new("RGB", (len(pixels), 1))
    sample.putdata(pixels)
    quant = sample.quantize(colors=min(12, max(2, count * 2)), method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    result: list[str] = []
    for _n, index in counts:
        rgb = palette[index * 3 : index * 3 + 3]
        hex_value = to_hex(rgb)
        if all(_distance(hex_value, other) > 48 for other in result):
            result.append(hex_value)
        if len(result) >= count:
            break
    return result


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(to_rgb(a), to_rgb(b))) ** 0.5
