from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "arcms.accounts"
    label = "accounts"
    verbose_name = "الحسابات"

    def ready(self):
        from arcms.audit.services import register

        from .models import User

        register(User, exclude=("last_login", "last_seen_at", "totp_last_counter"))
