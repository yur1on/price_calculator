from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db import models
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.utils import timezone

from finance.models import (
    Employee, Expense, OtherIncome, PartItem, RepairFinance, RepairPart,
    StockReceipt, Supplier, WarrantyClaim,
)
from finance.services import ZERO, financial_summary, stock_summary
from repairs.models import Appointment

from .models import CRMEvent, CRMOrder, CRMOrderPartUsage, CRMWorkItem
from .stale import decorate_stale_orders, with_status_changed_at


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _avg_duration(rows, start_field, end_field):
    values = []
    for row in rows:
        start, end = getattr(row, start_field), getattr(row, end_field)
        if start and end and end >= start:
            values.append(end - start)
    if not values:
        return None
    return sum(values, timedelta()) / len(values)


def _duration_label(value):
    if value is None:
        return "Недостаточно данных"
    hours = value.total_seconds() / 3600
    return f"{hours:.1f} ч" if hours < 48 else f"{hours / 24:.1f} дн."


def _trend(start, end):
    trunc = TruncDay if (end - start).days <= 62 else TruncMonth
    repairs = RepairFinance.objects.filter(date__range=(start, end)).annotate(bucket=trunc("date")).values("bucket").annotate(
        revenue=Coalesce(Sum("revenue"), ZERO), profit=Coalesce(Sum("workshop_profit"), ZERO),
    ).order_by("bucket")
    expenses = Expense.objects.filter(date__range=(start, end)).annotate(bucket=trunc("date")).values("bucket").annotate(value=Coalesce(Sum("amount"), ZERO))
    income = OtherIncome.objects.filter(date__range=(start, end)).annotate(bucket=trunc("date")).values("bucket").annotate(value=Coalesce(Sum("amount"), ZERO))
    data = defaultdict(lambda: {"revenue": ZERO, "profit": ZERO, "expenses": ZERO, "income": ZERO})
    for row in repairs:
        data[row["bucket"]]["revenue"], data[row["bucket"]]["profit"] = row["revenue"], row["profit"]
    for row in expenses:
        data[row["bucket"]]["expenses"] = row["value"]
    for row in income:
        data[row["bucket"]]["income"] = row["value"]
    result = []
    for bucket, row in sorted(data.items()):
        result.append({"label": bucket.strftime("%d.%m" if trunc is TruncDay else "%m.%Y"),
                       "revenue": float(row["revenue"]),
                       "result": float(row["profit"] + row["income"] - row["expenses"])})
    return result


def _flow(start, end):
    trunc = TruncDay if (end - start).days <= 62 else TruncMonth
    data = defaultdict(lambda: {"accepted": 0, "issued": 0, "canceled": 0, "warranty": 0})
    for row in CRMOrder.objects.filter(accepted_at__date__range=(start, end)).annotate(bucket=trunc("accepted_at")).values("bucket").annotate(
        accepted=Count("id"), warranty=Count("id", filter=models.Q(order_type=CRMOrder.Type.WARRANTY))
    ):
        data[row["bucket"]].update(accepted=row["accepted"], warranty=row["warranty"])
    for row in CRMOrder.objects.filter(issued_at__date__range=(start, end)).annotate(bucket=trunc("issued_at")).values("bucket").annotate(issued=Count("id")):
        data[row["bucket"]]["issued"] = row["issued"]
    for row in CRMEvent.objects.filter(event_type=CRMEvent.Type.STATUS, new_value=CRMOrder.Status.CANCELED.label,
                                       created_at__date__range=(start, end)).annotate(bucket=trunc("created_at")).values("bucket").annotate(canceled=Count("id")):
        data[row["bucket"]]["canceled"] = row["canceled"]
    return [{"label": bucket.strftime("%d.%m" if trunc is TruncDay else "%m.%Y"), **row} for bucket, row in sorted(data.items())]


def _status_durations(order_ids):
    wanted = {CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.APPROVAL, CRMOrder.Status.WAITING_PART, CRMOrder.Status.REPAIR}
    labels = dict(CRMOrder.Status.choices)
    reverse = {label: value for value, label in CRMOrder.Status.choices}
    totals = defaultdict(list)
    events = CRMEvent.objects.filter(order_id__in=order_ids, event_type=CRMEvent.Type.STATUS).order_by("order_id", "created_at", "id")
    by_order = defaultdict(list)
    for event in events:
        by_order[event.order_id].append(event)
    samples = 0
    for rows in by_order.values():
        if not rows:
            continue
        samples += 1
        for index, event in enumerate(rows[:-1]):
            status = reverse.get(event.new_value)
            next_at = rows[index + 1].created_at
            if status in wanted and next_at >= event.created_at:
                totals[status].append(next_at - event.created_at)
    result = []
    for status in wanted:
        values = totals[status]
        avg = sum(values, timedelta()) / len(values) if values else None
        result.append({"status": status, "label": labels[status], "duration": _duration_label(avg), "samples": len(values),
                       "hours": round(avg.total_seconds() / 3600, 1) if avg else 0})
    return result, samples


def build_analytics(start, end):
    finance = financial_summary(start, end)
    stock = stock_summary(start, end)
    accepted = CRMOrder.objects.filter(accepted_at__date__range=(start, end))
    issued = list(CRMOrder.objects.filter(issued_at__date__range=(start, end)).select_related("employee"))
    ready = list(CRMOrder.objects.filter(ready_at__date__range=(start, end)).select_related("employee"))
    canceled = CRMEvent.objects.filter(event_type=CRMEvent.Type.STATUS, new_value=CRMOrder.Status.CANCELED.label,
                                       created_at__date__range=(start, end)).count()
    commercial = RepairFinance.objects.filter(date__range=(start, end), crm_order__order_type=CRMOrder.Type.PAID,
                                               crm_order__issued_at__isnull=False)
    commercial_total = commercial.aggregate(v=Coalesce(Sum("revenue"), ZERO))["v"]
    active = CRMOrder.objects.exclude(status__in=[CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED])
    attention = decorate_stale_orders(list(with_status_changed_at(active.select_related("client", "device", "employee"))))

    employee_data = defaultdict(lambda: {"issued": 0, "minutes": 0, "warranty": 0, "durations": []})
    for order in issued:
        if order.employee_id:
            row = employee_data[order.employee_id]
            row["issued"] += 1
            row["minutes"] += order.estimated_work_minutes_snapshot or 0
            row["warranty"] += order.order_type == CRMOrder.Type.WARRANTY
            if order.ready_at and order.ready_at >= order.accepted_at:
                row["durations"].append(order.ready_at - order.accepted_at)
    salaries = dict(RepairFinance.objects.filter(date__range=(start, end)).values_list("employee_id").annotate(v=Coalesce(Sum("master_salary"), ZERO)))
    master_rows = []
    for employee in Employee.objects.filter(id__in=set(employee_data) | set(salaries)).order_by("name"):
        row = employee_data[employee.id]
        duration = sum(row["durations"], timedelta()) / len(row["durations"]) if row["durations"] else None
        master_rows.append({"employee": employee, **row, "salary": _money(salaries.get(employee.id)), "duration": _duration_label(duration)})

    work_rows = CRMWorkItem.objects.filter(order__issued_at__date__range=(start, end)).values("name").annotate(
        count=Count("id"), quantity_total=Sum("quantity"), revenue=Sum(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=14, decimal_places=2))
    ).order_by("-count", "name")[:10]
    device_rows = accepted.values("device__brand", "device__model").annotate(count=Count("id")).order_by("-count")[:10]

    appointments = Appointment.objects.filter(created_at__date__range=(start, end))
    appointment_stats = {"total": appointments.count(), "converted": appointments.filter(crm_order__isnull=False).count(),
                         "no_show": appointments.filter(status="no_show").count(), "canceled": appointments.filter(status="cancelled").count()}
    appointment_stats["conversion"] = round(appointment_stats["converted"] / appointment_stats["total"] * 100, 1) if appointment_stats["total"] else 0

    purchase_value = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=14, decimal_places=2))
    supplier_rows = StockReceipt.objects.filter(date__range=(start, end)).values("supplier__name").annotate(
        receipts=Count("id"), parts=Sum("quantity"), purchased=Sum(purchase_value),
    ).order_by("-purchased")[:10]
    warranty_by_supplier = dict(WarrantyClaim.objects.filter(opened_at__range=(start, end)).values_list("part_item__receipt__supplier__name").annotate(v=Count("id")))
    for row in supplier_rows:
        row["warranties"] = warranty_by_supplier.get(row["supplier__name"], 0)

    status_rows = [{"value": value, "label": label, "count": active.filter(status=value).count()} for value, label in CRMOrder.Status.choices if value not in {CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED}]
    valid_ready_durations = [row.ready_at - row.accepted_at for row in ready if row.ready_at and row.ready_at >= row.accepted_at and row.status != CRMOrder.Status.CANCELED]
    status_duration_rows, history_sample = _status_durations([row.id for row in ready])
    finance_repairs = RepairFinance.objects.filter(date__range=(start, end)).select_related("employee", "crm_order").order_by("-date", "-id")[:50]
    return {
        "summary": finance, "stock": stock, "accepted_count": accepted.count(), "issued_count": len(issued),
        "canceled_count": canceled, "warranty_count": accepted.filter(order_type=CRMOrder.Type.WARRANTY).count(),
        "average_check": _money(commercial_total / commercial.count()) if commercial.count() else ZERO,
        "commercial_count": commercial.count(), "active_count": active.count(), "attention": attention[:12],
        "ready_duration": _duration_label(_avg_duration(ready, "accepted_at", "ready_at")),
        "ready_median": _duration_label(median(valid_ready_durations)) if valid_ready_durations else "Недостаточно данных",
        "issue_duration": _duration_label(_avg_duration(issued, "accepted_at", "issued_at")),
        "duration_sample": len(ready), "master_rows": master_rows, "work_rows": work_rows,
        "device_rows": device_rows, "appointment_stats": appointment_stats, "supplier_rows": supplier_rows,
        "status_rows": status_rows, "status_duration_rows": status_duration_rows, "history_sample": history_sample,
        "finance_repairs": finance_repairs, "trend": _trend(start, end), "flow": _flow(start, end),
        "warranty_linked": accepted.filter(order_type=CRMOrder.Type.WARRANTY, warranty_source_order__isnull=False).count(),
    }
