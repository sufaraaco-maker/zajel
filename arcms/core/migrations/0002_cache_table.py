from django.db import migrations


def create_cache_table(apps, schema_editor):
    from django.core.management import call_command

    call_command("createcachetable", "arcms_cache", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
