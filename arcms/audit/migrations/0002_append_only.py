"""في PostgreSQL: مشغّل يمنع تعديل أو حذف قيود التدقيق حتى من داخل قاعدة البيانات
عبر التطبيق. (مدير القاعدة يستطيع تعطيله، لكن كسر السلسلة يظهر عند الفحص.)"""

from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION audit_auditentry_readonly() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_auditentry_no_change ON audit_auditentry;
CREATE TRIGGER audit_auditentry_no_change BEFORE UPDATE OR DELETE ON audit_auditentry
    FOR EACH ROW EXECUTE FUNCTION audit_auditentry_readonly();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_auditentry_no_change ON audit_auditentry;
DROP FUNCTION IF EXISTS audit_auditentry_readonly();
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_auditentry "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )
        schema_editor.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_auditentry "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE)
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS audit_no_update")
        schema_editor.execute("DROP TRIGGER IF EXISTS audit_no_delete")


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
