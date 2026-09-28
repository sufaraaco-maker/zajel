"""هندسة الرسوم البيانية للوحة الجمهور (SVG يُرسم على الخادم، بلا مكتبات).

الزمن يسير من اليمين إلى اليسار كما يقرأ العربي: أقدم يوم على اليمين واليوم
على اليسار، ومحور القيم على اليمين.
"""

from __future__ import annotations

import math

from arcms.arabic.dates import format_date
from arcms.arabic.numbers import compact_number


def nice_ticks(max_value: float, count: int = 4) -> list[int]:
    if max_value <= 0:
        return [0, 1]
    raw = max_value / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    step = max(1, int(step))
    top = step * math.ceil(max_value / step)
    return list(range(0, top + 1, step))


def _column_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """عمود بطرف علوي مستدير 4px وقاعدة مربعة."""
    base = y + h
    r = min(r, w / 2, h)
    if h <= 0:
        return ""
    return (
        f"M{x:.1f},{base:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{base:.1f} Z"
    )


def daily_columns(series: list[dict], width: int = 880, height: int = 260, style=None, digits: str = "latin") -> dict:
    pad_top, pad_bottom, pad_axis, pad_left = 22, 28, 52, 8
    plot_w = width - pad_axis - pad_left
    plot_h = height - pad_top - pad_bottom
    n = max(1, len(series))
    slot = plot_w / n
    bar_w = min(24.0, max(4.0, slot - 4))
    ticks = nice_ticks(max((s["views"] for s in series), default=0))
    top = ticks[-1] or 1
    bars = []
    for i, s in enumerate(series):
        # الأقدم على اليمين: الفهرس 0 يبدأ من حافة المحور.
        x_center = width - pad_axis - slot * (i + 0.5)
        h = plot_h * s["views"] / top
        y = pad_top + plot_h - h
        x = x_center - bar_w / 2
        bars.append(
            {
                "path": _column_path(x, y, bar_w, h),
                "hit_x": _n(x_center - slot / 2),
                "hit_w": _n(slot),
                "x": _n(x_center),
                "y": _n(y),
                "views": s["views"],
                "visitors": s["visitors"],
                "label": format_date(s["day"], style, weekday=True) if style else str(s["day"]),
                "short": f"{s['day'].day}/{s['day'].month}",
                "is_last": i == n - 1,
                "show_tick": (n - 1 - i) % 7 == 0,
                "value_label": compact_number(s["views"], digits),
            }
        )
    grid = [
        {"y": _n(pad_top + plot_h - plot_h * t / top), "label": compact_number(t, digits)}
        for t in ticks
    ]
    # كل الأبعاد نصوص بنقطة عشرية: القوالب تعرّب الأعداد العشرية فتكسر سمات SVG.
    return {
        "width": width,
        "height": height,
        "bars": bars,
        "grid": grid,
        "axis_x": _n(width - pad_axis + 8),
        "plot_left": _n(pad_left),
        "plot_right": _n(width - pad_axis),
        "baseline": _n(pad_top + plot_h),
        "label_y": _n(height - 8),
    }


def _n(value: float) -> str:
    return f"{value:.1f}"
