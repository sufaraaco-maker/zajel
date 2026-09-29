"""ترميز CBOR (RFC 8949) بالقدر الذي يحتاجه WebAuthn: أعداد وبايتات ونصوص ومصفوفات وخرائط
وقيم بسيطة. المفكّك صارم: لا أطوال غير محددة، ولا أعماق مفرطة، ولا بيانات زائدة غير متوقعة."""

from __future__ import annotations

import struct

MAX_DEPTH = 16
MAX_ITEMS = 4096


class CBORError(ValueError):
    pass


def _length(data: bytes, pos: int, info: int) -> tuple[int, int]:
    if info < 24:
        return info, pos
    sizes = {24: 1, 25: 2, 26: 4, 27: 8}
    if info not in sizes:
        raise CBORError("طول غير مدعوم")
    n = sizes[info]
    if pos + n > len(data):
        raise CBORError("بيانات مقطوعة")
    return int.from_bytes(data[pos:pos + n], "big"), pos + n


def decode_first(data: bytes, pos: int = 0, depth: int = 0):
    """يفك أول عنصر ويعيد (القيمة، موضع ما بعده)."""
    if depth > MAX_DEPTH:
        raise CBORError("تداخل عميق")
    if pos >= len(data):
        raise CBORError("بيانات مقطوعة")
    initial = data[pos]
    major, info = initial >> 5, initial & 0x1F
    pos += 1
    if major == 7:
        if info == 20:
            return False, pos
        if info == 21:
            return True, pos
        if info in (22, 23):
            return None, pos
        if info == 25:
            return struct.unpack(">e", data[pos:pos + 2])[0], pos + 2
        if info == 26:
            return struct.unpack(">f", data[pos:pos + 4])[0], pos + 4
        if info == 27:
            return struct.unpack(">d", data[pos:pos + 8])[0], pos + 8
        raise CBORError("قيمة بسيطة غير مدعومة")
    value, pos = _length(data, pos, info)
    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major in (2, 3):
        if pos + value > len(data):
            raise CBORError("بيانات مقطوعة")
        raw = data[pos:pos + value]
        return (bytes(raw) if major == 2 else raw.decode("utf-8")), pos + value
    if value > MAX_ITEMS:
        raise CBORError("عناصر كثيرة")
    if major == 4:
        items = []
        for _ in range(value):
            item, pos = decode_first(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        result = {}
        for _ in range(value):
            key, pos = decode_first(data, pos, depth + 1)
            if isinstance(key, (list, dict)):
                raise CBORError("مفتاح غير صالح")
            result[key], pos = decode_first(data, pos, depth + 1)
        return result, pos
    if major == 6:  # وسم: نتجاهله ونعيد القيمة
        return decode_first(data, pos, depth + 1)
    raise CBORError("نوع غير مدعوم")


def decode(data: bytes):
    value, pos = decode_first(data)
    if pos != len(data):
        raise CBORError("بيانات زائدة")
    return value


def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    raise CBORError("عدد كبير")


def encode(value) -> bytes:
    """مرمّز بسيط (للاختبارات ولمحاكاة المفاتيح)."""
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, bytes):
        return _head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _head(3, len(raw)) + raw
    if isinstance(value, (list, tuple)):
        return _head(4, len(value)) + b"".join(encode(v) for v in value)
    if isinstance(value, dict):
        return _head(5, len(value)) + b"".join(encode(k) + encode(v) for k, v in value.items())
    raise CBORError(f"نوع غير مدعوم: {type(value).__name__}")
