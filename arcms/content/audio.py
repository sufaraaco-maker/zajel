"""تجهيز الملفات الصوتية (البودكاست والموجز الصوتي) قبل حفظها.

ملف MP3 قد يحمل وسوم ID3: اسم معدّ الحلقة، والبرنامج والجهاز، وأحياناً صورة
وتعليقاً. ننزع وسوم ID3v2 من البداية وID3v1 وAPE من النهاية، ونحفظ باسم عشوائي.
صيغتا M4A وOGG تُقبلان كما هما مع تنبيه المحرر.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

MAX_AUDIO_BYTES = 60 * 1024 * 1024


class AudioRejected(ValueError):
    pass


@dataclass
class CleanAudio:
    content: bytes
    ext: str
    removed: list[str] = field(default_factory=list)
    warning: str = ""

    @property
    def filename(self) -> str:
        return f"{uuid.uuid4().hex}.{self.ext}"


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _is_mp3_frame(data: bytes) -> bool:
    return len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def strip_mp3(data: bytes) -> tuple[bytes, list[str]]:
    removed: list[str] = []
    while data[:3] == b"ID3" and len(data) >= 10:
        size = _syncsafe(data[6:10]) + 10 + (10 if data[5] & 0x10 else 0)
        data = data[size:]
        removed.append("وسوم ID3v2 (العنوان، المعدّ، البرنامج، الصورة المضمَّنة)")
    if data[-128:-125] == b"TAG":
        data = data[:-128]
        removed.append("وسوم ID3v1")
    ape = data.rfind(b"APETAGEX", max(0, len(data) - 64 * 1024))
    if ape != -1:
        data = data[:ape]
        removed.append("وسوم APE")
    return data, removed


def clean_audio(data: bytes) -> CleanAudio:
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioRejected("حجم الملف الصوتي يتجاوز 60 ميغابايت.")
    if data[:3] == b"ID3" or _is_mp3_frame(data):
        body, removed = strip_mp3(data)
        if not _is_mp3_frame(body):
            raise AudioRejected("ملف MP3 غير صالح.")
        return CleanAudio(body, "mp3", removed)
    if data[4:8] == b"ftyp":
        return CleanAudio(data, "m4a", warning="لم تُنزع بيانات ملف M4A الوصفية؛ صدّره MP3 إن كان يحمل أسماء.")
    if data[:4] == b"OggS":
        return CleanAudio(data, "ogg", warning="لم تُنزع بيانات ملف OGG الوصفية.")
    raise AudioRejected("نقبل الملفات الصوتية بصيغ MP3 وM4A وOGG فقط.")
