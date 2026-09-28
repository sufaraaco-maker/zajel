"""أدوات مشتركة للاختبارات."""

from __future__ import annotations

import io
import shutil

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from arcms.accounts import totp
from arcms.accounts.middleware import SESSION_VERIFIED
from arcms.accounts.models import User
from arcms.content.models import Article, Category, Status


class ArcmsTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        from django.core.cache import cache

        cache.clear()


def make_user(username: str, role: str = "reporter", *, with_2fa: bool = True, password: str = "a-long-test-password") -> User:
    user = User.objects.create_user(username=username, password=password, role=role, display_name=username)
    if with_2fa:
        user.totp_secret = totp.generate_secret()
        user.totp_confirmed_at = timezone.now()
        user.save()
    return user


def login(client, user: User) -> None:
    client.force_login(user)
    session = client.session
    session[SESSION_VERIFIED] = True
    session.save()


def make_category(name: str = "أخبار") -> Category:
    return Category.objects.get_or_create(name=name)[0]


def make_article(title: str = "عنوان تجريبي", *, body: str = "<p>نص</p>", status: str = Status.PUBLISHED,
                 category: Category | None = None, created_by=None, **extra) -> Article:
    now = timezone.now()
    fields = {"published_at": now, "first_published_at": now} if status == Status.PUBLISHED else {}
    fields.update(extra)
    return Article.objects.create(
        title=title, body=body, status=status, category=category or make_category(), created_by=created_by, **fields
    )


def image_bytes(fmt: str = "JPEG", size=(640, 480), gps: bool = False, color=(180, 30, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if gps and fmt == "JPEG":
        exif = img.getexif()
        exif[0x010F] = "PhoneMaker"
        exif[0x0110] = "Model X"
        exif[0xA431] = "SERIAL-123"
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd.update({1: "N", 2: (31.0, 46.0, 30.0), 3: "E", 4: (35.0, 13.0, 45.0)})
        img.save(buf, fmt, exif=exif)
    else:
        img.save(buf, fmt)
    return buf.getvalue()
