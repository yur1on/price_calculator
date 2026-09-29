import secrets

from django.db import migrations, models


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def backfill_tracking_codes(apps, schema_editor):
    CRMOrder = apps.get_model("crm", "CRMOrder")
    used = set(CRMOrder.objects.exclude(tracking_code__isnull=True).values_list("tracking_code", flat=True))
    for order in CRMOrder.objects.filter(tracking_code__isnull=True).iterator():
        for _ in range(100):
            code = generate_code()
            if code not in used:
                CRMOrder.objects.filter(pk=order.pk).update(tracking_code=code)
                used.add(code)
                break
        else:
            raise RuntimeError("Unable to generate a unique CRM tracking code.")


class Migration(migrations.Migration):
    dependencies = [("crm", "0005_alter_crmattachment_file")]

    operations = [
        migrations.AddField(
            model_name="crmorder",
            name="tracking_code",
            field=models.CharField(blank=True, editable=False, max_length=9, null=True, unique=True, verbose_name="Код проверки"),
        ),
        migrations.RunPython(backfill_tracking_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="crmorder",
            name="tracking_code",
            field=models.CharField(editable=False, max_length=9, unique=True, verbose_name="Код проверки"),
        ),
    ]
