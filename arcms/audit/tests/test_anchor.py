from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.audit.models import AuditEntry
from arcms.audit.services import check_anchor, current_anchor, record
from arcms.audit.tasks import send_anchor
from arcms.core.jobs import run_pending
from arcms.core.testing import ArcmsTestCase, login, make_user


class AnchorTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        for n in range(3):
            record("security", message=f"قيد {n}")

    def test_anchor_detects_rewrite_and_deletion(self):
        anchor = current_anchor()
        self.assertTrue(check_anchor(anchor)[0])
        record("security", message="قيد لاحق")
        ok, message = check_anchor(anchor)
        self.assertTrue(ok)
        self.assertIn("1", message)
        entry_id = int(anchor.split(":")[0])
        # مهاجم يملك صلاحيات القاعدة: يعطّل الحماية، ويزوّر القيد المثبَّت، ويعيد حساب السلسلة كلها بعده
        with connection.cursor() as cur:
            if connection.vendor == "sqlite":
                cur.execute("DROP TRIGGER audit_no_update")
            else:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cur.execute("ALTER TABLE audit_auditentry DISABLE TRIGGER audit_auditentry_no_change")
            entries = list(AuditEntry.objects.filter(pk__gte=entry_id).order_by("id"))
            prev = entries[0].prev_hash
            for e in entries:
                if e.pk == entry_id:
                    e.message = "مزوَّر"
                e.prev_hash = prev
                e.hash = e.compute_hash()
                prev = e.hash
                cur.execute("UPDATE audit_auditentry SET message = %s, prev_hash = %s, hash = %s WHERE id = %s",
                            [e.message, e.prev_hash, e.hash, e.pk])
        from arcms.audit.services import verify_chain

        self.assertTrue(verify_chain()[0])  # السلسلة وحدها لا تكشف إعادة الكتابة الكاملة
        ok, message = check_anchor(anchor)  # والمرساة المحفوظة خارج الخادم تكشفها
        self.assertFalse(ok)
        self.assertIn("تغيّرت", message)
        self.assertFalse(check_anchor("garbage")[0])
        self.assertFalse(check_anchor(f"999999:{'a' * 64}")[0])

    def test_command(self):
        out = StringIO()
        call_command("arcms_audit_verify", "--anchor", stdout=out)
        anchor = out.getvalue().strip()
        self.assertEqual(anchor, current_anchor())
        out = StringIO()
        call_command("arcms_audit_verify", "--expect", anchor, stdout=out)
        self.assertIn("مطابقة", out.getvalue())
        with self.assertRaises(SystemExit):
            call_command("arcms_audit_verify", "--expect", f"{anchor.split(':')[0]}:{'b' * 64}", stdout=StringIO())

    def test_daily_email_to_auditors_once(self):
        admin = make_user("root", Role.ADMIN)
        admin.email = "root@newsroom.example"
        admin.save()
        chief = make_user("chief", Role.CHIEF)
        chief.email = "chief@newsroom.example"
        chief.save()
        send_anchor()
        send_anchor()
        run_pending()
        self.assertEqual([m.to[0] for m in mail.outbox], ["root@newsroom.example"])
        self.assertIn(current_anchor().split(":")[1][:20], mail.outbox[0].body)

    def test_audit_page_checks_pasted_anchor(self):
        login(self.client, make_user("root", Role.ADMIN))
        anchor = current_anchor()
        resp = self.client.get(reverse("studio:audit"), {"anchor": anchor})
        self.assertContains(resp, "المرساة مطابقة")
