from django.db.models import Count, Max, Q

from .models import CRMClient, CRMOrder
from accounts.access import is_approved_master


def order_queryset():
    return CRMOrder.objects.select_related("client", "device", "employee", "warranty_source_order", "source_appointment")


def orders_for_user(user):
    qs = order_queryset()
    if user.is_superuser or user.is_staff or is_approved_master(user) or user.has_perm("crm.view_all_orders"):
        return qs
    try:
        employee = user.employee_profile
    except Exception:
        return qs.none()
    return qs.filter(employee=employee) if employee.is_active else qs.none()


def filter_orders(params, user=None):
    qs = orders_for_user(user) if user is not None else order_queryset()
    query = (params.get("q") or "").strip()
    if query:
        digits = "".join(ch for ch in query if ch.isdigit())
        condition = (
            Q(number__icontains=query) | Q(client__name__icontains=query) |
            Q(client__phone__icontains=query) | Q(device__brand__icontains=query) |
            Q(device__model__icontains=query) | Q(device__serial_number__icontains=query) |
            Q(issue_description__icontains=query)
        )
        if digits:
            condition |= Q(client__normalized_phone__icontains=digits)
        qs = qs.filter(condition)
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("employee"):
        qs = qs.filter(employee_id=params["employee"])
    if params.get("order_type"):
        qs = qs.filter(order_type=params["order_type"])
    if params.get("date_from"):
        qs = qs.filter(accepted_at__date__gte=params["date_from"])
    if params.get("date_to"):
        qs = qs.filter(accepted_at__date__lte=params["date_to"])
    if params.get("active") == "1":
        qs = qs.exclude(status__in=[CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED])
    return qs


def filter_clients(query="", user=None):
    qs = CRMClient.objects.annotate(
        devices_count=Count("devices", distinct=True),
        orders_count=Count("orders", distinct=True),
        last_visit=Max("orders__accepted_at"),
    )
    if user is not None and not (user.is_superuser or user.is_staff or is_approved_master(user) or user.has_perm("crm.view_all_orders")):
        try:
            qs = qs.filter(orders__employee=user.employee_profile)
        except Exception:
            qs = qs.none()
    query = query.strip()
    if query:
        digits = "".join(ch for ch in query if ch.isdigit())
        condition = Q(name__icontains=query) | Q(phone__icontains=query) | Q(devices__imei__icontains=query) | Q(devices__serial_number__icontains=query)
        if digits:
            condition |= Q(normalized_phone__icontains=digits)
        qs = qs.filter(condition)
    return qs.distinct().order_by("name")
