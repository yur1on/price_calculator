from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crm", "0010_crmorder_issue_finance_fields")]

    operations = [
        migrations.AddField(
            model_name="crmdocumentsettings", name="diagnostic_warning_hours",
            field=models.PositiveSmallIntegerField(default=24, help_text="Внутренний индикатор внимания, не SLA для клиента.", verbose_name="Диагностика без изменений, часов"),
        ),
        migrations.AddField(
            model_name="crmdocumentsettings", name="approval_warning_hours",
            field=models.PositiveSmallIntegerField(default=24, help_text="Внутренний индикатор внимания, не SLA для клиента.", verbose_name="Согласование без изменений, часов"),
        ),
        migrations.AddField(
            model_name="crmdocumentsettings", name="waiting_part_warning_days",
            field=models.PositiveSmallIntegerField(default=3, help_text="Внутренний индикатор внимания, не SLA для клиента.", verbose_name="Ожидание детали без изменений, дней"),
        ),
        migrations.AddField(
            model_name="crmdocumentsettings", name="ready_warning_days",
            field=models.PositiveSmallIntegerField(default=3, help_text="Внутренний индикатор внимания, не SLA для клиента.", verbose_name="Готов и не забран, дней"),
        ),
    ]
