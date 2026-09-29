from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Appointment, BookingBlock, WorkingHour, WorkshopDayCapacity

RESERVING_STATUSES = ("new", "confirmed")
ACTIVE_ORDER_STATUSES = ("accepted", "diagnostic", "repair")
PAUSED_ORDER_STATUSES = ("approval", "waiting_part")
PHYSICAL_ORDER_STATUSES = ACTIVE_ORDER_STATUSES + PAUSED_ORDER_STATUSES + ("ready",)


def defaults():
    return {
        "capacity_minutes": settings.WORKSHOP_DEFAULT_CAPACITY_MINUTES,
        "reserve_minutes": settings.WORKSHOP_DEFAULT_RESERVE_MINUTES,
        "manual_adjustment_minutes": 0,
    }


def day_capacity(day):
    override = WorkshopDayCapacity.objects.filter(date=day).first()
    if override:
        return {"date": day, "capacity": override.capacity_minutes, "reserve": override.reserve_minutes,
                "adjustment": override.manual_adjustment_minutes, "effective": override.effective_capacity,
                "closed": override.is_closed, "note": override.note, "override": override}
    value = defaults()
    effective = max(0, value["capacity_minutes"] - value["reserve_minutes"])
    return {"date": day, "capacity": value["capacity_minutes"], "reserve": value["reserve_minutes"],
            "adjustment": 0, "effective": effective, "closed": False, "note": "", "override": None}


def is_working_day(day):
    return WorkingHour.objects.filter(weekday=day.weekday()).exists() and not day_capacity(day)["closed"]


def appointment_workload(appointment):
    total = appointment.items.aggregate(total=Sum("duration_min"))["total"]
    if total is not None:
        return int(total)
    return int((appointment.end - appointment.start).total_seconds() // 60)


def appointment_reserved_minutes(day):
    appointments = Appointment.objects.filter(
        status__in=RESERVING_STATUSES, start__date=day, crm_order__isnull=True,
    ).prefetch_related("items")
    return sum(appointment_workload(appointment) for appointment in appointments)


def active_crm_backlog_minutes():
    from crm.models import CRMOrder
    return int(CRMOrder.objects.filter(status__in=ACTIVE_ORDER_STATUSES).aggregate(
        total=Sum("estimated_work_minutes_snapshot"))["total"] or 0)


def project_load(start_date, days):
    backlog = active_crm_backlog_minutes()
    result = {}
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        cap = day_capacity(day)
        reserved = appointment_reserved_minutes(day)
        if not is_working_day(day):
            result[day] = {**cap, "reserved": reserved, "backlog": 0, "load": reserved, "available": 0}
            continue
        room = max(0, cap["effective"] - reserved)
        backlog_here = backlog
        backlog = max(0, backlog - room)
        load = reserved + backlog_here
        result[day] = {**cap, "reserved": reserved, "backlog": backlog_here, "load": load,
                       "available": max(0, cap["effective"] - load)}
    return result


def slot_is_available(slot_start, workload_minutes, *, appointment_duration_minutes=None):
    day = timezone.localtime(slot_start).date()
    cap = day_capacity(day)
    if cap["closed"] or not WorkingHour.objects.filter(weekday=day.weekday()).exists():
        return False
    projected = project_load(timezone.localdate(), max(1, (day - timezone.localdate()).days + 1)).get(day)
    if not projected or workload_minutes > projected["available"]:
        return False
    duration = timedelta(minutes=appointment_duration_minutes or settings.BOOKING_INTAKE_SLOT_MINUTES)
    slot_end = slot_start + duration
    blocked = BookingBlock.objects.filter(date=day, is_active=True)
    local_start, local_end = timezone.localtime(slot_start), timezone.localtime(slot_end)
    if any(b.start_time < local_end.time() and b.end_time > local_start.time() for b in blocked):
        return False
    overlaps = Appointment.objects.filter(status__in=RESERVING_STATUSES, start__lt=slot_end, end__gt=slot_start).count()
    return overlaps < settings.REPAIRS_MAX_PARALLEL_APPOINTMENTS


def lock_day(day):
    values = defaults()
    WorkshopDayCapacity.objects.get_or_create(date=day, defaults=values)
    return WorkshopDayCapacity.objects.select_for_update().get(date=day)


def capacity_dashboard(days=7):
    from crm.models import CRMOrder
    today = timezone.localdate()
    projected = project_load(today, days)
    rows = []
    for day, row in projected.items():
        effective = row["effective"]
        row["percent"] = round(row["load"] * 100 / effective) if effective else (100 if row["load"] else 0)
        row["overload"] = max(0, row["load"] - effective)
        rows.append(row)
    return {"today": rows[0], "days": rows,
            "paused_count": CRMOrder.objects.filter(status__in=PAUSED_ORDER_STATUSES).count(),
            "physical_count": CRMOrder.objects.filter(status__in=PHYSICAL_ORDER_STATUSES).count()}
