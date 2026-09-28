from django.test import override_settings
from django.urls import reverse

from arcms.accounts import totp
from arcms.accounts.models import LoginAttempt, User
from arcms.accounts.roles import ROLE_CAPABILITIES, ROLES_REQUIRING_2FA, Cap, Role
from arcms.audit.models import Action, AuditEntry
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_user

PASSWORD = "a-long-test-password"


class TotpTests(ArcmsTestCase):
    # RFC 6238 الملحق B: السر "12345678901234567890" بترميز base32
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_rfc6238_vectors(self):
        self.assertEqual(totp.hotp(self.SECRET, 59 // 30, digits=8), "94287082")
        self.assertEqual(totp.hotp(self.SECRET, 1111111109 // 30, digits=8), "07081804")
        self.assertEqual(totp.totp(self.SECRET, at=59), "287082")

    def test_window_and_replay(self):
        code = totp.totp(self.SECRET, at=1000)
        counter = totp.verify(self.SECRET, code, at=1000)
        self.assertIsNotNone(counter)
        self.assertIsNotNone(totp.verify(self.SECRET, code, at=1000 + 30))  # فرق ساعة مقبول
        self.assertIsNone(totp.verify(self.SECRET, code, last_counter=counter, at=1000))  # لا إعادة استخدام
        self.assertIsNone(totp.verify(self.SECRET, "000000", at=1000))

    def test_corrupted_secret_rejected_gracefully(self):
        self.assertIsNone(totp.verify("⚠ تعذّر فك التشفير", "123456"))

    def test_arabic_indic_digits_accepted(self):
        code = totp.totp(self.SECRET, at=500)
        arabic = code.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        self.assertIsNotNone(totp.verify(self.SECRET, arabic, at=500))

    def test_recovery_code_single_use(self):
        user = make_user("r1")
        codes = totp.generate_recovery_codes()
        user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
        user.save()
        self.assertTrue(user.use_recovery_code(codes[0].lower()))
        self.assertFalse(user.use_recovery_code(codes[0]))
        self.assertEqual(len(user.recovery_codes), 9)

    def test_secret_encrypted_at_rest(self):
        user = make_user("enc")
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT totp_secret FROM accounts_user WHERE id = %s", [user.pk])
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith("enc1:"))
        self.assertNotIn(user.totp_secret, raw)


class RoleTests(ArcmsTestCase):
    def test_matrix_invariants(self):
        self.assertNotIn(Cap.ARTICLE_PUBLISH, ROLE_CAPABILITIES[Role.REPORTER])
        self.assertNotIn(Cap.ARTICLE_PUBLISH, ROLE_CAPABILITIES[Role.EDITOR])
        self.assertIn(Cap.ARTICLE_PUBLISH, ROLE_CAPABILITIES[Role.DESK_HEAD])
        self.assertIn(Cap.SOURCES_VIEW, ROLE_CAPABILITIES[Role.CHIEF])
        self.assertNotIn(Cap.USERS, ROLE_CAPABILITIES[Role.CHIEF])
        self.assertEqual(ROLE_CAPABILITIES[Role.ADMIN], frozenset(ROLE_CAPABILITIES[Role.ADMIN]))
        for role in (Role.EDITOR, Role.DESK_HEAD, Role.CHIEF, Role.ADMIN):
            self.assertIn(role, ROLES_REQUIRING_2FA)

    def test_inactive_user_has_no_caps(self):
        user = make_user("gone", Role.CHIEF)
        user.is_active = False
        self.assertEqual(user.capabilities, frozenset())

    def test_editor_always_requires_2fa(self):
        site = SiteSettings.load()
        site.require_2fa_all_staff = False
        site.save()
        self.assertTrue(make_user("ed", Role.EDITOR, with_2fa=False).requires_2fa)
        self.assertFalse(make_user("rep", Role.REPORTER, with_2fa=False).requires_2fa)


class LoginFlowTests(ArcmsTestCase):
    def test_two_step_login(self):
        user = make_user("chief1", Role.CHIEF)
        resp = self.client.post(reverse("accounts:login"), {"username": "chief1", "password": PASSWORD})
        self.assertRedirects(resp, reverse("accounts:verify"), fetch_redirect_response=False)
        # لم يكتمل الدخول بعد
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 302)
        resp = self.client.post(reverse("accounts:verify"), {"code": "123456"})
        self.assertContains(resp, "الرمز غير صحيح")
        resp = self.client.post(reverse("accounts:verify"), {"code": totp.totp(user.totp_secret)})
        self.assertRedirects(resp, reverse("studio:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)
        self.assertTrue(AuditEntry.objects.filter(action=Action.LOGIN, actor=user).exists())
        self.assertTrue(AuditEntry.objects.filter(action=Action.LOGIN_FAILED).exists())

    def test_login_by_email(self):
        user = make_user("mail1", Role.REPORTER)
        user.email = "mail1@example.org"
        user.save()
        resp = self.client.post(reverse("accounts:login"), {"username": "MAIL1@example.org", "password": PASSWORD})
        self.assertRedirects(resp, reverse("accounts:verify"), fetch_redirect_response=False)

    def test_editor_without_device_forced_to_enroll(self):
        make_user("neweditor", Role.EDITOR, with_2fa=False)
        self.client.post(reverse("accounts:login"), {"username": "neweditor", "password": PASSWORD})
        resp = self.client.get(reverse("studio:articles"))
        self.assertRedirects(resp, reverse("accounts:enroll"), fetch_redirect_response=False)

    def test_enrollment_flow(self):
        user = make_user("enr", Role.EDITOR, with_2fa=False)
        self.client.force_login(user)
        self.client.get(reverse("accounts:enroll"))
        secret = self.client.session["arcms_enroll_secret"]
        resp = self.client.post(reverse("accounts:enroll"), {"code": totp.totp(secret)})
        self.assertContains(resp, "رموز الاسترداد")
        user.refresh_from_db()
        self.assertTrue(user.has_2fa)
        self.assertEqual(len(user.recovery_codes), 10)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)

    def test_session_without_verification_is_rejected(self):
        user = make_user("sneaky", Role.CHIEF)
        self.client.force_login(user)  # جلسة دون رمز
        resp = self.client.get(reverse("studio:home"))
        self.assertRedirects(resp, reverse("accounts:login"), fetch_redirect_response=False)

    @override_settings(ARCMS_LOGIN_MAX_FAILURES=3)
    def test_lockout_after_failures(self):
        make_user("victim", Role.REPORTER)
        for _ in range(3):
            self.client.post(reverse("accounts:login"), {"username": "victim", "password": "wrong-password"})
        resp = self.client.post(reverse("accounts:login"), {"username": "victim", "password": PASSWORD})
        self.assertContains(resp, "أُقفل الدخول مؤقتاً")
        self.assertTrue(LoginAttempt.is_locked("victim"))

    def test_temporary_password_must_change(self):
        user = make_user("temp", Role.REPORTER)
        user.must_change_password = True
        user.save()
        login(self.client, user)
        resp = self.client.get(reverse("studio:home"))
        self.assertRedirects(resp, reverse("accounts:password_change"), fetch_redirect_response=False)

    def test_logout_is_post_only(self):
        user = make_user("out", Role.REPORTER)
        login(self.client, user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 302)

    def test_open_redirect_blocked(self):
        user = make_user("redir", Role.REPORTER)
        resp = self.client.post(
            reverse("accounts:login") + "?next=https://evil.example/", {"username": "redir", "password": PASSWORD}
        )
        resp = self.client.post(reverse("accounts:verify"), {"code": totp.totp(user.totp_secret)})
        self.assertEqual(resp["Location"], reverse("studio:home"))


class UserAdminTests(ArcmsTestCase):
    def test_admin_creates_user_with_temp_password(self):
        admin = make_user("boss", Role.ADMIN)
        login(self.client, admin)
        resp = self.client.post(
            reverse("studio:user_new"),
            {"username": "newbie", "display_name": "صحفي جديد", "email": "", "role": Role.REPORTER, "phone": "",
             "is_active": "on", "password": "correct-horse-battery"},
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="newbie")
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password("correct-horse-battery"))

    def test_weak_password_rejected(self):
        admin = make_user("boss2", Role.ADMIN)
        login(self.client, admin)
        resp = self.client.post(
            reverse("studio:user_new"),
            {"username": "weak", "display_name": "x", "role": Role.REPORTER, "is_active": "on", "password": "12345"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_reset_2fa_audited(self):
        admin = make_user("boss3", Role.ADMIN)
        victim = make_user("lostphone", Role.EDITOR)
        login(self.client, admin)
        self.client.post(reverse("studio:user_reset_2fa", args=[victim.pk]))
        victim.refresh_from_db()
        self.assertFalse(victim.has_2fa)
        self.assertTrue(AuditEntry.objects.filter(action=Action.TWO_FA, object_id=str(victim.pk)).exists())

    def test_non_admin_cannot_manage_users(self):
        chief = make_user("chiefx", Role.CHIEF)
        login(self.client, chief)
        self.assertEqual(self.client.get(reverse("studio:users")).status_code, 403)
