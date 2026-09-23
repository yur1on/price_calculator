from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    Employee, Expense, OtherIncome, PartItem, RepairFinance, SalaryPayment,
    StockReceipt, Supplier, SupplierPayment, WarrantyClaim, money,
    DistributedExpense, DistributedExpenseAllocation, PayrollCalculation, PayrollPeriod,
    PayrollRepairSnapshot,
)


ZERO = Decimal("0.00")


def resolve_period(request):
    today = timezone.localdate()
    preset = request.GET.get("period", "month")
    if preset == "today":
        start = end = today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif preset == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif preset == "year":
        start, end = today.replace(month=1, day=1), today
    elif preset == "custom":
        try:
            start = date.fromisoformat(request.GET.get("from", ""))
            end = date.fromisoformat(request.GET.get("to", ""))
        except ValueError:
            start, end, preset = today.replace(day=1), today, "month"
    else:
        start, end, preset = today.replace(day=1), today, "month"
    if start > end:
        start, end = end, start
    return start, end, preset


def financial_summary(start, end):
    repair_totals = RepairFinance.objects.filter(date__range=(start, end)).aggregate(
        revenue=Coalesce(Sum("revenue"), ZERO),
        parts=Coalesce(Sum("part_cost"), ZERO),
        margin=Coalesce(Sum("repair_margin"), ZERO),
        salary=Coalesce(Sum("master_salary"), ZERO),
        repair_profit=Coalesce(Sum("workshop_profit"), ZERO),
    )
    expenses = Expense.objects.filter(date__range=(start, end)).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
    other_income = OtherIncome.objects.filter(date__range=(start, end)).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
    return {
        **{key: money(value) for key, value in repair_totals.items()},
        "expenses": money(expenses),
        "other_income": money(other_income),
        "net_profit": money(repair_totals["repair_profit"] + other_income - expenses),
    }


def employee_rows(start, end, employee=None):
    employees = Employee.objects.all()
    if employee:
        employees = employees.filter(pk=employee.pk)
    rows = []
    for person in employees:
        repairs = person.repairs.filter(date__range=(start, end))
        totals = repairs.aggregate(
            repairs_count=Count("id"), revenue=Coalesce(Sum("revenue"), ZERO),
            parts=Coalesce(Sum("part_cost"), ZERO), margin=Coalesce(Sum("repair_margin"), ZERO),
            salary=Coalesce(Sum("master_salary"), ZERO), profit=Coalesce(Sum("workshop_profit"), ZERO),
        )
        paid = person.salary_payments.filter(date__range=(start, end)).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
        totals.update(employee=person, paid=money(paid), debt=money(totals["salary"] - paid))
        rows.append(totals)
    return rows


def monthly_chart(year):
    result = []
    for month in range(1, 13):
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        summary = financial_summary(start, end)
        result.append({"month": start.strftime("%b"), **summary})
    return result


def stock_summary(start, end):
    stock = PartItem.objects.filter(status=PartItem.Status.IN_STOCK)
    stock_value = stock.aggregate(v=Coalesce(Sum("receipt__unit_cost"), ZERO))["v"]
    purchase_expression = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=14, decimal_places=2))
    purchased = StockReceipt.objects.filter(date__range=(start, end)).aggregate(v=Coalesce(Sum(purchase_expression), ZERO))["v"]
    all_received = StockReceipt.objects.aggregate(v=Coalesce(Sum(purchase_expression), ZERO))["v"]
    paid = SupplierPayment.objects.aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
    from .supplier_ledger import supplier_ledger
    supplier_credits = sum((supplier_ledger(supplier)["summary"]["credits_total"] for supplier in Supplier.objects.all()), ZERO)
    net_supplier_balance = money(paid + supplier_credits - all_received)
    return {
        "stock_count": stock.count(), "stock_value": money(stock_value), "purchased": money(purchased),
        "supplier_balance": net_supplier_balance,
        "supplier_debt": money(max(all_received - paid - supplier_credits, ZERO)),
        "supplier_credit": money(max(paid + supplier_credits - all_received, ZERO)),
        "open_warranties": WarrantyClaim.objects.exclude(status=WarrantyClaim.Status.CLOSED).count(),
    }


def supplier_summary(supplier, start, end):
    from .supplier_ledger import supplier_ledger
    receipts = supplier.receipts.filter(date__range=(start, end))
    items = PartItem.objects.filter(receipt__in=receipts)
    received_value = sum((receipt.total_cost for receipt in receipts), ZERO)
    paid_period = supplier.payments.filter(date__range=(start, end)).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
    all_received = sum((receipt.total_cost for receipt in supplier.receipts.all()), ZERO)
    all_paid = supplier.payments.aggregate(v=Coalesce(Sum("amount"), ZERO))["v"]
    claims = WarrantyClaim.objects.filter(part_item__receipt__supplier=supplier, opened_at__range=(start, end))
    ledger = supplier_ledger(supplier, start, end)
    credits = supplier_ledger(supplier)["summary"]["credits_total"]
    balance = money(all_paid + credits - all_received)
    return {
        "received_count": items.count(), "received_value": money(received_value),
        "installed": items.filter(status=PartItem.Status.INSTALLED).count(),
        "in_stock": items.filter(status=PartItem.Status.IN_STOCK).count(),
        "warranties": claims.count(), "approved": claims.filter(status=WarrantyClaim.Status.APPROVED).count(),
        "rejected": claims.filter(status=WarrantyClaim.Status.REJECTED).count(),
        "pending": claims.exclude(status__in=[WarrantyClaim.Status.APPROVED, WarrantyClaim.Status.REJECTED, WarrantyClaim.Status.CLOSED]).count(),
        "paid_period": money(paid_period), "paid": money(all_paid), "balance": balance,
        "debt": money(max(all_received - all_paid - credits, ZERO)),
        "credit": money(max(all_paid + credits - all_received, ZERO)),
        "returns": ledger["summary"]["credits_total"],
    }


def payroll_preview(period, employee):
    repairs = employee.repairs.filter(date__range=(period.start_date, period.end_date)).order_by("date", "created_at")
    totals = repairs.aggregate(
        repairs_count=Count("id"), revenue=Coalesce(Sum("revenue"), ZERO),
        direct_costs=Coalesce(Sum("part_cost"), ZERO), repair_margin=Coalesce(Sum("repair_margin"), ZERO),
    )
    distributions = []
    for expense in DistributedExpense.objects.filter(
        is_active=True, start_date__lte=period.end_date, employees=employee,
    ).prefetch_related("employees"):
        if expense.used_periods >= expense.periods_count or expense.remaining_amount <= 0:
            continue
        employee_count = max(expense.employees.count(), 1)
        period_total = expense.remaining_amount if expense.used_periods == expense.periods_count - 1 else min(expense.per_period_amount, expense.remaining_amount)
        amount = money(period_total / employee_count)
        part_number = expense.used_periods + 1
        accounted_after = money(expense.distributed_amount + period_total)
        distributions.append({
            "expense": expense, "amount": amount, "period_total": period_total,
            "total_cost_snapshot": money(expense.total_cost), "part_number": part_number,
            "parts_count": expense.periods_count, "accounted_after": accounted_after,
            "remaining_after": money(max(Decimal(expense.total_cost) - accounted_after, ZERO)),
            "progress_percent": min(round(part_number / expense.periods_count * 100), 100),
        })
    distributed = money(sum((row["amount"] for row in distributions), ZERO))
    base = money(max(totals["repair_margin"] - distributed, ZERO))
    percent = Decimal("0.00") if employee.is_owner else Decimal(employee.default_percent)
    repair_rows = []
    for repair in repairs:
        repair_rows.append({
            "repair": repair, "repair_date": repair.date, "description": repair.description,
            "revenue": repair.revenue, "parts_cost": repair.part_cost,
            "other_direct_costs": ZERO, "direct_costs": repair.part_cost,
            "salary_base": repair.repair_margin, "percent": repair.master_percent,
            "salary_amount": repair.master_salary,
        })
    return {**totals, "repairs": repair_rows, "distributions": distributions, "distributed_costs": distributed,
            "salary_base": base, "percent": percent, "salary_amount": money(base * percent / Decimal("100"))}


@transaction.atomic
def close_payroll_period(period):
    period = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status == PayrollPeriod.Status.CLOSED:
        return list(period.calculations.all())
    calculations = []
    previews = [(employee, payroll_preview(period, employee)) for employee in Employee.objects.filter(is_active=True, is_owner=False)]
    for employee, data in previews:
        calc = PayrollCalculation.objects.create(
            period=period, employee=employee, repairs_count=data["repairs_count"],
            revenue=data["revenue"], direct_costs=data["direct_costs"], repair_margin=data["repair_margin"],
            distributed_costs=data["distributed_costs"], salary_base=data["salary_base"],
            percent=data["percent"], salary_amount=data["salary_amount"],
        )
        for row in data["distributions"]:
            DistributedExpenseAllocation.objects.create(
                calculation=calc, distributed_expense=row["expense"], amount=row["amount"],
                name_snapshot=row["expense"].name,
                total_cost_snapshot=row["total_cost_snapshot"], part_number=row["part_number"],
                parts_count=row["parts_count"], accounted_after=row["accounted_after"],
                remaining_after=row["remaining_after"],
            )
        PayrollRepairSnapshot.objects.bulk_create([
            PayrollRepairSnapshot(
                calculation=calc, repair=row["repair"], repair_date=row["repair_date"],
                description=row["description"], revenue=row["revenue"], parts_cost=row["parts_cost"],
                other_direct_costs=row["other_direct_costs"], salary_base=row["salary_base"],
                percent=row["percent"], salary_amount=row["salary_amount"],
            ) for row in data["repairs"]
        ])
        calculations.append(calc)
    period.status = PayrollPeriod.Status.CLOSED
    period.calculated_at = timezone.now()
    PayrollPeriod.objects.filter(pk=period.pk).update(status=period.status, calculated_at=period.calculated_at)
    for expense in DistributedExpense.objects.filter(is_active=True):
        if expense.used_periods >= expense.periods_count or expense.remaining_amount <= 0:
            DistributedExpense.objects.filter(pk=expense.pk).update(is_active=False, finished_at=period.end_date)
    return calculations
