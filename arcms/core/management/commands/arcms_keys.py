import base64
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "يولّد الأسرار اللازمة لملف البيئة: المفتاح السري، مفتاح تشفير الحقول، ومفاتيح VAPID للإشعارات."

    def handle(self, *args, **opts):
        key = ec.generate_private_key(ec.SECP256R1())
        raw_priv = key.private_numbers().private_value.to_bytes(32, "big")
        raw_pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
        self.stdout.write("# انسخ هذه الأسطر إلى ملف .env على الخادم ولا تشاركها.")
        self.stdout.write(f"ARCMS_SECRET_KEY={secrets.token_urlsafe(50)}")
        self.stdout.write(f"ARCMS_FIELD_KEY={Fernet.generate_key().decode()}")
        self.stdout.write(f"ARCMS_VAPID_PUBLIC_KEY={b64(raw_pub)}")
        self.stdout.write(f"ARCMS_VAPID_PRIVATE_KEY={b64(raw_priv)}")
        self.stdout.write("# مفتاح النسخ الاحتياطي يُولَّد منفصلاً: manage.py arcms_backup_keygen")
