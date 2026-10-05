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
    return _day_capacity(day, WorkshopDayCapacity.objects.filter(date=day).first())


def _day_capacity(day, override):
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
    items = list(appointment.items.all())
    if items:
        return sum(item.duration_min for item in items)
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


class CapacitySnapshot:
    """Request-local data only. Never reuse across booking transactions."""

    def __init__(self, start, end, *, overlap_until=None):
        self.working_hours = list(WorkingHour.objects.all())
        self.weekdays = {row.weekday for row in self.working_hours}
        self.capacities = {row.date: row for row in WorkshopDayCapacity.objects.filter(date__range=(start, end))}
        self.reserved = {}
        for appointment in Appointment.objects.filter(
            status__in=RESERVING_STATUSES, start__date__range=(start, end),
            crm_order__isnull=True,
        ).prefetch_related("items"):
            day = timezone.localtime(appointment.start).date()
            self.reserved[day] = self.reserved.get(day, 0) + appointment_workload(appointment)
        self.backlog = active_crm_backlog_minutes()
        self.blocks = {}
        for block in BookingBlock.objects.filter(date__range=(start, end), is_active=True):
            self.blocks.setdefault(block.date, []).append(block)
        lower = timezone.make_aware(datetime.combine(start, time.min))
        upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
        if overlap_until is not None:
            upper = max(upper, overlap_until)
        # Converted appointments still occupy TIME, unlike workload reservations.
        self.overlaps = list(Appointment.objects.filter(
            status__in=RESERVING_STATUSES, start__lt=upper, end__gt=lower,
        ).values_list("start", "end"))
        self.projection = project_load(start, (end - start).days + 1, snapshot=self)

    def service_fits_working_hours(self, slot_start, service_duration_minutes):
        """Return whether the complete service fits one configured work interval."""
        local_start = timezone.localtime(slot_start)
        local_end = timezone.localtime(
            slot_start + timedelta(minutes=service_duration_minutes)
        )
        if local_end.date() != local_start.date():
            return False
        return any(
            row.weekday == local_start.weekday()
            and row.start <= local_start.time()
            and local_end.time() <= row.end
            for row in self.working_hours
        )

    def slot_is_available(
        self,
        slot_start,
        workload_minutes,
        *,
        appointment_duration_minutes=None,
        service_duration_minutes=None,
    ):
        day = timezone.localtime(slot_start).date()
        projected = self.projection.get(day)
        if (not projected or projected["closed"] or day.weekday() not in self.weekdays
                or workload_minutes > projected["available"]):
            return False
        local_start = timezone.localtime(slot_start)
        effective_service_duration = (
            workload_minutes if service_duration_minutes is None else service_duration_minutes
        )
        if not self.service_fits_working_hours(slot_start, effective_service_duration):
            return False
        slot_end = slot_start + timedelta(minutes=appointment_duration_minutes or settings.BOOKING_INTAKE_SLOT_MINUTES)
        local_end = timezone.localtime(slot_end)
        if any(b.start_time < local_end.time() and b.end_time > local_start.time()
               for b in self.blocks.get(day, ())):
            return False
        return sum(start < slot_end and end > slot_start for start, end in self.overlaps) < settings.REPAIRS_MAX_PARALLEL_APPOINTMENTS


def project_load(start_date, days, *, snapshot=None):
    if days <= 0:
        return {}
    if snapshot is None:
        return CapacitySnapshot(start_date, start_date + timedelta(days=days - 1)).projection
    backlog = snapshot.backlog
    result = {}
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        cap = _day_capacity(day, snapshot.capacities.get(day))
        reserved = snapshot.reserved.get(day, 0)
        if day.weekday() not in snapshot.weekdays or cap["closed"]:
            result[day] = {**cap, "reserved": reserved, "backlog": 0, "load": reserved, "available": 0}
            continue
        room = max(0, cap["effective"] - reserved)
        backlog_here = backlog
        backlog = max(0, backlog - room)
        load = reserved + backlog_here
        result[day] = {**cap, "reserved": reserved, "backlog": backlog_here, "load": load,
                       "available": max(0, cap["effective"] - load)}
    return result


def slot_is_available(
    slot_start,
    workload_minutes,
    *,
    appointment_duration_minutes=None,
    service_duration_minutes=None,
):
    day = timezone.localtime(slot_start).date()
    today = timezone.localdate()
    if day < today:
        return False
    slot_end = slot_start + timedelta(minutes=appointment_duration_minutes or settings.BOOKING_INTAKE_SLOT_MINUTES)
    return CapacitySnapshot(today, day, overlap_until=slot_end).slot_is_available(
        slot_start,
        workload_minutes,
        appointment_duration_minutes=appointment_duration_minutes,
        service_duration_minutes=service_duration_minutes,
    )


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
