"""فهرس البحث: عمود tsvector مع GIN في PostgreSQL، أو جدول FTS5 في SQLite."""

from django.db import migrations


def forwards(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        if conn.vendor == "postgresql":
            cur.execute("ALTER TABLE content_searchdocument ADD COLUMN IF NOT EXISTS vector tsvector")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS content_searchdocument_vector_gin "
                "ON content_searchdocument USING GIN (vector)"
            )
        elif conn.vendor == "sqlite":
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS content_search_fts USING fts5("
                "title, lead, body, tokenize = 'unicode61 remove_diacritics 0')"
            )


def backwards(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        if conn.vendor == "postgresql":
            cur.execute("DROP INDEX IF EXISTS content_searchdocument_vector_gin")
            cur.execute("ALTER TABLE content_searchdocument DROP COLUMN IF EXISTS vector")
        elif conn.vendor == "sqlite":
            cur.execute("DROP TABLE IF EXISTS content_search_fts")


class Migration(migrations.Migration):
    dependencies = [("content", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
