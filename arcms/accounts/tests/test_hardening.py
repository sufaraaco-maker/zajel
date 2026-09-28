from django.test import Client
from django.urls import reverse

from arcms.accounts import totp
from arcms.accounts.models import User
from arcms.accounts.roles import Role
from arcms.audit.models import Action, AuditEntry
from arcms.core.testing import ArcmsTestCase, login, make_user

PASSWORD = "a-long-test-password"
NEW_PASSWORD = "another-long-passphrase-2026"


class PasswordChangeTests(ArcmsTestCase):
    def test_requires_current_password(self):
        user = make_user("pw", Role.REPORTER)
        login(self.client, user)
        data = {"old_password": "wrong", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
        resp = self.client.post(reverse("accounts:password_change"), data)
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))
        data["old_password"] = PASSWORD
        resp = self.client.post(reverse("accounts:password_change"), data)
        self.assertEqual(resp.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        # الجلسة الحالية باقية
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)

    def test_change_ends_other_sessions(self):
        user = make_user("pw2", Role.REPORTER)
        other = Client()
        login(other, user)
        login(self.client, user)
        self.client.post(
            reverse("accounts:password_change"),
            {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )
        self.assertEqual(other.get(reverse("studio:home")).status_code, 302)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)


class ReenrollTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("mover", Role.EDITOR)
        self.old_secret = self.user.totp_secret
        login(self.client, self.user)

    def _new_code(self):
        self.client.get(reverse("accounts:enroll") + "?reset=1")
        return totp.totp(self.client.session["arcms_enroll_secret"])

    def test_open_session_alone_cannot_move_device(self):
        resp = self.client.post(reverse("accounts:enroll"), {"code": self._new_code(), "current": "000000"})
        self.assertContains(resp, "رمز الجهاز الحالي غير صحيح")
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, self.old_secret)
        resp = self.client.post(reverse("accounts:enroll"), {"code": self._new_code()})
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, self.old_secret)

    def test_move_with_current_code_ends_other_sessions(self):
        other = Client()
        login(other, self.user)
        code = self._new_code()
        resp = self.client.post(reverse("accounts:enroll"), {"code": code, "current": totp.totp(self.old_secret)})
        self.assertContains(resp, "رموز الاسترداد")
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.totp_secret, self.old_secret)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)
        self.assertEqual(other.get(reverse("studio:home")).status_code, 302)
        self.assertTrue(AuditEntry.objects.filter(action=Action.TWO_FA, message__contains="جهاز جديد").exists())

    def test_move_with_recovery_code(self):
        codes = totp.generate_recovery_codes()
        self.user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
        self.user.save()
        resp = self.client.post(reverse("accounts:enroll"), {"code": self._new_code(), "current": codes[0]})
        self.assertContains(resp, "رموز الاسترداد")


class SessionEpochTests(ArcmsTestCase):
    def test_end_other_sessions_from_profile(self):
        user = make_user("epoch", Role.REPORTER)
        other = Client()
        login(other, user)
        login(self.client, user)
        resp = self.client.post(reverse("accounts:sessions_end"))
        self.assertRedirects(resp, reverse("studio:profile"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)
        self.assertEqual(other.get(reverse("studio:home")).status_code, 302)
        self.assertTrue(AuditEntry.objects.filter(action=Action.SECURITY, message__contains="الجلسات الأخرى").exists())

    def test_admin_ends_user_sessions_and_2fa_reset_logs_out(self):
        admin = make_user("root", Role.ADMIN)
        victim = make_user("lost-phone", Role.EDITOR)
        victim_client = Client()
        login(victim_client, victim)
        login(self.client, admin)
        self.client.post(reverse("studio:user_sessions_end", args=[victim.pk]))
        self.assertEqual(victim_client.get(reverse("studio:home")).status_code, 302)
        # جلسة جديدة ثم إعادة ضبط التحقق بعد فقدان الهاتف
        login(victim_client, User.objects.get(pk=victim.pk))
        self.assertEqual(victim_client.get(reverse("studio:home")).status_code, 200)
        self.client.post(reverse("studio:user_reset_2fa", args=[victim.pk]))
        self.assertEqual(victim_client.get(reverse("studio:home")).status_code, 302)
        # جلسة المدير نفسه لم تتأثر
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)

    def test_epoch_zero_keeps_existing_sessions_valid(self):
        user = make_user("legacy", Role.REPORTER)
        self.assertEqual(user.session_epoch, 0)
        base = super(User, user)._get_session_auth_hash()
        self.assertEqual(user.get_session_auth_hash(), base)
