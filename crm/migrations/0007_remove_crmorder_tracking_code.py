from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0006_crmorder_tracking_code"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="crmorder",
            name="tracking_code",
        ),
    ]
