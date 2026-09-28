from django.db import migrations, models


def copy_jid_to_numbers(apps, schema_editor):
    Person = apps.get_model("ledger", "Person")
    for p in Person.objects.exclude(whatsapp_jid=""):
        local = p.whatsapp_jid.split("@", 1)[0].split(":", 1)[0]
        d = "".join(c for c in local if c.isdigit())
        if d:
            p.whatsapp_numbers = [d]
            p.save(update_fields=["whatsapp_numbers"])


class Migration(migrations.Migration):
    dependencies = [("ledger", "0004_fastpos_token")]

    operations = [
        migrations.AddField(model_name="person", name="whatsapp_numbers",
                            field=models.JSONField(blank=True, default=list)),
        migrations.RunPython(copy_jid_to_numbers, migrations.RunPython.noop),
        migrations.RemoveField(model_name="person", name="whatsapp_jid"),
    ]
