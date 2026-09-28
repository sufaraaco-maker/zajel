from django.db import IntegrityError, connection, transaction

from arcms.audit.models import Action, AuditEntry
from arcms.audit.services import acting_as, record, suppressed, verify_chain
from arcms.content.models import Category
from arcms.core.testing import ArcmsTestCase, make_user


class AuditTests(ArcmsTestCase):
    def test_changes_recorded_with_actor_and_diff(self):
        user = make_user("auditor", "chief")
        with acting_as(user):
            cat = Category.objects.create(name="القدس")
            cat.name = "القدس الشريف"
            cat.save()
            cat.delete()
        entries = list(AuditEntry.objects.filter(object_type="content.category").order_by("id"))
        self.assertEqual([e.action for e in entries], [Action.CREATE, Action.UPDATE, Action.DELETE])
        self.assertEqual(entries[1].changes["name"], ["القدس", "القدس الشريف"])
        self.assertTrue(all(e.actor_id == user.pk for e in entries))

    def test_secrets_masked(self):
        user = make_user("masked", "editor")
        entry = AuditEntry.objects.get(object_type="accounts.user", object_id=str(user.pk), action=Action.CREATE)
        self.assertEqual(entry.changes.get("password"), "•••")
        user.set_password("another-long-password")
        user.save()
        update = AuditEntry.objects.filter(object_type="accounts.user", action=Action.UPDATE).first()
        self.assertEqual(update.changes["password"], ["•••", "•••"])

    def test_chain_valid(self):
        for n in range(5):
            record(Action.SECURITY, message=f"حدث {n}")
        ok, count, broken = verify_chain()
        self.assertTrue(ok)
        self.assertGreaterEqual(count, 5)
        self.assertIsNone(broken)

    def test_orm_cannot_modify_or_delete(self):
        entry = record(Action.SECURITY, message="ثابت")
        entry.message = "معدَّل"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_database_trigger_blocks_update(self):
        record(Action.SECURITY, message="محمي")
        from django.db import DatabaseError

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("UPDATE audit_auditentry SET message = 'x'")

    def test_tampering_detected(self):
        record(Action.SECURITY, message="أصلي")
        target = record(Action.SECURITY, message="سيُعبث به")
        record(Action.SECURITY, message="لاحق")
        # محاكاة عبث مباشر في القاعدة بعد تعطيل الحماية (كما قد يفعل مهاجم يملك صلاحيات القاعدة)
        with connection.cursor() as cur:
            if connection.vendor == "sqlite":
                cur.execute("DROP TRIGGER audit_no_update")
            else:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cur.execute("ALTER TABLE audit_auditentry DISABLE TRIGGER audit_auditentry_no_change")
            cur.execute("UPDATE audit_auditentry SET message = %s WHERE id = %s", ["مزوَّر", target.pk])
        ok, _, broken = verify_chain()
        self.assertFalse(ok)
        self.assertEqual(broken.pk, target.pk)

    def test_hash_unique(self):
        entry = record(Action.SECURITY, message="فريد")
        dup = AuditEntry(action=Action.SECURITY, hash=entry.hash)
        with self.assertRaises(IntegrityError), transaction.atomic():
            dup.save()

    def test_suppressed_context(self):
        before = AuditEntry.objects.count()
        with suppressed():
            Category.objects.create(name="صامت")
        self.assertEqual(AuditEntry.objects.count(), before)
