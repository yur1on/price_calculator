from datetime import timedelta

from django.db.models import DateTimeField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import CRMDocumentSettings, CRMEvent, CRMOrder


WATCHED_STATUSES = {
    CRMOrder.Status.DIAGNOSTIC,
    CRMOrder.Status.APPROVAL,
    CRMOrder.Status.WAITING_PART,
    CRMOrder.Status.READY,
}


def with_status_changed_at(queryset):
    latest = CRMEvent.objects.filter(
        order_id=OuterRef("pk"), event_type=CRMEvent.Type.STATUS,
    ).order_by("-created_at", "-id").values("created_at")[:1]
    return queryset.annotate(
        status_changed_at=Coalesce(
            Subquery(latest, output_field=DateTimeField()), "accepted_at",
        ),
    )


def _thresholds(configuration):
    return {
        CRMOrder.Status.DIAGNOSTIC: timedelta(hours=configuration.diagnostic_warning_hours),
        CRMOrder.Status.APPROVAL: timedelta(hours=configuration.approval_warning_hours),
        CRMOrder.Status.WAITING_PART: timedelta(days=configuration.waiting_part_warning_days),
        CRMOrder.Status.READY: timedelta(days=configuration.ready_warning_days),
    }


def human_age(delta):
    seconds = max(int(delta.total_seconds()), 0)
    if seconds >= 86400:
        days = seconds // 86400
        return f"{days} дн."
    hours = max(seconds // 3600, 1)
    return f"{hours} ч."


def decorate_stale_orders(orders, configuration=None, now=None):
    configuration = configuration or CRMDocumentSettings.load()
    thresholds = _thresholds(configuration)
    now = now or timezone.now()
    result = []
    for order in orders:
        changed_at = getattr(order, "status_changed_at", None) or order.accepted_at
        age = now - changed_at
        order.status_age = age
        order.status_age_label = human_age(age)
        order.workshop_age_label = human_age(now - order.accepted_at)
        order.needs_attention = order.status in thresholds and age >= thresholds[order.status]
        if order.needs_attention:
            result.append(order)
    return result
