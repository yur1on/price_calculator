from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def proportional(total, rows, field):
    total = money(total)
    weights = [max(Decimal(getattr(row, field) or 0), ZERO) for row in rows]
    total_weight = sum(weights, ZERO)
    if not rows or not total_weight:
        return [ZERO for _ in rows]
    values, assigned = [], ZERO
    for index, weight in enumerate(weights):
        if index == len(rows) - 1:
            value = money(total - assigned)
        else:
            value = money(total * weight / total_weight)
            assigned += value
        values.append(value)
    return values


def backfill_snapshot_allocation(apps, schema_editor):
    Calculation = apps.get_model("finance", "PayrollCalculation")
    Snapshot = apps.get_model("finance", "PayrollRepairSnapshot")
    for calculation in Calculation.objects.all().iterator():
        rows = list(Snapshot.objects.filter(calculation_id=calculation.pk).order_by("repair_date", "id"))
        shares = proportional(calculation.distributed_costs, rows, "salary_base")
        for row, share in zip(rows, shares):
            row._allocation_weight = max(Decimal(row.salary_base or 0) - share, ZERO)
        final_weights = [row._allocation_weight for row in rows]
        if not sum(final_weights, ZERO):
            for row in rows:
                row._allocation_weight = max(Decimal(row.salary_base or 0), ZERO)
        finals = proportional(calculation.salary_amount, rows, "_allocation_weight")
        for row, share, final in zip(rows, shares, finals):
            Snapshot.objects.filter(pk=row.pk).update(
                distributed_cost_share=share,
                final_salary_amount=final,
            )


class Migration(migrations.Migration):
    dependencies = [("finance", "0012_payroll_snapshot_allocation")]

    operations = [migrations.RunPython(backfill_snapshot_allocation, migrations.RunPython.noop)]
