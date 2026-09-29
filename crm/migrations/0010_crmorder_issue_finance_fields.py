from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crm", "0009_crmdevice_imei_crmorder_intake_snapshots")]

    operations = [
        migrations.AddField(
            model_name="crmorder", name="final_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Итоговая сумма"),
        ),
        migrations.AddField(
            model_name="crmorder", name="paid_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Оплачено клиентом"),
        ),
        migrations.AddField(
            model_name="crmorder", name="finance_repair",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="crm_order", to="finance.repairfinance", verbose_name="Финансовый ремонт"),
        ),
    ]
