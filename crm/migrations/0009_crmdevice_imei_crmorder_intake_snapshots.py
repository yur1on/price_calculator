from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crm", "0008_crmorder_estimated_work_minutes_snapshot")]

    operations = [
        migrations.AddField(model_name="crmdevice", name="imei", field=models.CharField(blank=True, db_index=True, max_length=32, verbose_name="IMEI")),
        migrations.AlterField(model_name="crmdevice", name="serial_number", field=models.CharField(blank=True, db_index=True, max_length=120, verbose_name="Серийный номер")),
        migrations.AddField(model_name="crmorder", name="condition_on_intake", field=models.TextField(blank=True, verbose_name="Состояние при приёме")),
        migrations.AddField(model_name="crmorder", name="accessories_on_intake", field=models.TextField(blank=True, verbose_name="Комплектность при приёме")),
    ]
