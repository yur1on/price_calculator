from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crm", "0011_crmdocumentsettings_stale_thresholds")]

    operations = [
        migrations.AddField(
            model_name="crmdocumentsettings",
            name="website",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Сайт"),
        ),
    ]
