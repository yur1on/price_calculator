import json
import mimetypes
from functools import wraps
from datetime import timedelta
from io import BytesIO
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import FileResponse, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from finance.models import Employee, PartItem
from finance.services import resolve_period
from repairs.models import Appointment
from accounts.models import AccountProfile

from .forms import (
    AttachmentForm, BrandForm, CommentForm, DeviceModelForm, DeviceTypeForm,
    DocumentSettingsForm, IntakeForm, IssueOrderForm, IssueTemplateForm, OrderEditForm,
    WarrantyTermForm, WorkItemForm, WorkTemplateForm, ClientForm, WorkshopDayCapacityForm,
)
from .models import (
    CRMAttachment, CRMBrand, CRMClient, CRMComment, CRMDevice, CRMDeviceModel,
    CRMDeviceType, CRMDocumentSettings, CRMEvent, CRMIssueTemplate, CRMOrder, CRMOrderPartUsage,
    CRMWarrantyTerm, CRMWorkItem, CRMWorkTemplate, normalize_phone,
)
from .permissions import crm_permission_required
from .selectors import filter_clients, filter_orders, order_queryset, orders_for_user
from accounts.access import is_approved_master, is_tehsfera_admin, master_employee
from repairs.booking_capacity import (
    ACTIVE_ORDER_STATUSES, PAUSED_ORDER_STATUSES, PHYSICAL_ORDER_STATUSES,
    appointment_workload, capacity_dashboard,
)
from repairs.models import WorkshopDayCapacity
from .services import add_event, change_status, install_part, issue_order, return_part, sync_finance_repair
from .stale import decorate_stale_orders, with_status_changed_at
from .analytics import build_analytics
from .document_context import document_contact_context


def analytics_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not is_tehsfera_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


@analytics_admin_required
def analytics_view(request):
    if request.GET.get("period") == "7days":
        end, preset = timezone.localdate(), "7days"
        start = end - timedelta(days=6)
    else:
        start, end, preset = resolve_period(request)
    data = build_analytics(start, end)
    data.update({"start": start, "end": end, "period": preset})
    return render(request, "crm/analytics.html", data)


@analytics_admin_required
def analytics_export(request):
    from openpyxl import Workbook
    if request.GET.get("period") == "7days":
        end, preset = timezone.localdate(), "7days"
        start = end - timedelta(days=6)
    else:
        start, end, preset = resolve_period(request)
    data = build_analytics(start, end)
    book = Workbook()
    summary = book.active
    summary.title = "Summary"
    summary.append(["Аналитика Техсфера", f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"])
    summary.append(["Выручка", data["summary"]["revenue"]])
    summary.append(["Чистый результат", data["summary"]["net_profit"]])
    summary.append(["Принято", data["accepted_count"]])
    summary.append(["Выдано", data["issued_count"]])
    summary.append(["Средний чек", data["average_check"]])
    sheets = {
        "Repairs": (["Дата", "Номер", "Описание", "Мастер", "Выручка", "Детали", "Зарплата", "Результат"],
                    [[r.date, r.crm_order.number if hasattr(r, "crm_order") else "", r.description, r.employee.name,
                      r.revenue, r.part_cost, r.master_salary, r.workshop_profit] for r in data["finance_repairs"]]),
        "Masters": (["Мастер", "Выдано", "Нагрузка, мин", "Средний цикл", "Гарантийные", "Начислено"],
                    [[r["employee"].name, r["issued"], r["minutes"], r["duration"], r["warranty"], r["salary"]] for r in data["master_rows"]]),
        "Appointments": (["Создано", "Стали ремонтом", "No show", "Отменено", "Конверсия, %"],
                         [[data["appointment_stats"][key] for key in ("total", "converted", "no_show", "canceled", "conversion")]]),
        "Warranty": (["Гарантийных обращений", "Связано с исходным ремонтом"], [[data["warranty_count"], data["warranty_linked"]]]),
        "Suppliers": (["Поставщик", "Поступлений", "Деталей", "Закупка", "Гарантий"],
                      [[r["supplier__name"], r["receipts"], r["parts"], r["purchased"], r["warranties"]] for r in data["supplier_rows"]]),
    }
    for title, (headers, rows) in sheets.items():
        sheet = book.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="tehsfera-analytics-{start:%Y%m%d}-{end:%Y%m%d}.xlsx"'
    return response


def _status_counts(queryset=None):
    source = queryset if queryset is not None else CRMOrder.objects.all()
    counts = dict(source.values_list("status").annotate(total=Count("id")))
    return [{"value": value, "label": label, "count": counts.get(value, 0)} for value, label in CRMOrder.Status.choices]


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _toast(response, message, level="success"):
    response["HX-Trigger"] = json.dumps({"workbench:toast": {"message": message, "level": level}})
    return response


def _safe_per_page(value, default=25):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed in {25, 50, 100} else default


def _notify_accepted_order(order_id):
    try:
        from notify_tg.services import notify_order_status
        notify_order_status(order_id, CRMOrder.Status.ACCEPTED)
    except Exception:
        # Acceptance must remain successful even when Telegram is unavailable.
        pass


@crm_permission_required()
def dashboard(request):
    scoped_orders = orders_for_user(request.user)
    orders = filter_orders(request.GET, request.user)
    paginator = Paginator(orders, _safe_per_page(request.GET.get("per_page")))
    today = timezone.localdate()
    today_appointments = Appointment.objects.filter(
        start__date=today, status__in=["new", "confirmed"], crm_order__isnull=True,
    ).select_related("phone_model__brand", "repair_type").order_by("start")
    nearest_appointments = Appointment.objects.filter(
        start__gte=timezone.now(), status__in=["new", "confirmed"], crm_order__isnull=True,
    ).select_related("phone_model__brand", "repair_type").order_by("start")[:5]
    attention_candidates = list(with_status_changed_at(
        scoped_orders.filter(status__in=[
            CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.APPROVAL,
            CRMOrder.Status.WAITING_PART, CRMOrder.Status.READY,
        ]).select_related("client", "device", "employee")
    ).order_by("accepted_at"))
    attention_orders = decorate_stale_orders(attention_candidates)[:8]
    context = {
        "page_obj": paginator.get_page(request.GET.get("page")), "status_cards": _status_counts(scoped_orders),
        "active_count": scoped_orders.exclude(status__in=[CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED]).count(),
        "issued_today": scoped_orders.filter(issued_at__date=today).count(),
        "employees": Employee.objects.filter(is_active=True), "statuses": CRMOrder.Status.choices,
        "types": CRMOrder.Type.choices, "filters": request.GET,
        "today_appointments": today_appointments[:8],
        "nearest_appointments": nearest_appointments,
        "today_appointment_count": today_appointments.count(),
        "today_expected_count": today_appointments.count(),
        "today_accepted_count": scoped_orders.filter(accepted_at__date=today).count(),
        "capacity": capacity_dashboard(),
        "attention_orders": attention_orders,
    }
    template = "crm/partials/order_results.html" if _is_htmx(request) else "crm/dashboard.html"
    return render(request, template, context)


@crm_permission_required()
def order_list(request):
    return dashboard(request)


WORK_GROUPS = (
    (CRMOrder.Status.ACCEPTED, "Нужно начать", "fa-play"),
    (CRMOrder.Status.DIAGNOSTIC, "Диагностика", "fa-stethoscope"),
    (CRMOrder.Status.REPAIR, "В ремонте", "fa-screwdriver-wrench"),
    (CRMOrder.Status.APPROVAL, "Ожидают согласования", "fa-comments"),
    (CRMOrder.Status.WAITING_PART, "Ждут запчасть", "fa-box"),
    (CRMOrder.Status.READY, "Готовы к выдаче", "fa-circle-check"),
)

WORK_ACTIONS = {
    CRMOrder.Status.ACCEPTED: ((CRMOrder.Status.DIAGNOSTIC, "Начать диагностику"),),
    CRMOrder.Status.DIAGNOSTIC: (
        (CRMOrder.Status.APPROVAL, "На согласование"),
        (CRMOrder.Status.REPAIR, "Начать ремонт"),
    ),
    CRMOrder.Status.APPROVAL: ((CRMOrder.Status.REPAIR, "Согласовано → в ремонт"),),
    CRMOrder.Status.WAITING_PART: ((CRMOrder.Status.REPAIR, "Деталь получена → в ремонт"),),
    CRMOrder.Status.REPAIR: ((CRMOrder.Status.READY, "Ремонт завершён"),),
    CRMOrder.Status.READY: (),
}


def _work_queue_context(request, params):
    employee = master_employee(request.user)
    is_master = employee is not None
    physical_wip = orders_for_user(request.user).filter(status__in=PHYSICAL_ORDER_STATUSES).count()
    qs = orders_for_user(request.user).filter(status__in=PHYSICAL_ORDER_STATUSES).annotate(
        attachment_count=Count("attachments", distinct=True),
        installed_part_count=Count(
            "part_usages", filter=Q(part_usages__status=CRMOrderPartUsage.Status.INSTALLED), distinct=True,
        ),
    )
    mode = (params.get("mode") or ("mine" if is_master else "all")).strip()
    selected_employee = (params.get("employee") or "").strip()
    if is_master:
        if mode == "unassigned":
            qs = qs.filter(employee__isnull=True)
        elif mode != "all":
            mode = "mine"
            qs = qs.filter(employee=employee)
    elif selected_employee == "unassigned":
        qs = qs.filter(employee__isnull=True)
    elif selected_employee.isdigit():
        qs = qs.filter(employee_id=selected_employee)

    query = (params.get("q") or "").strip()
    if query:
        digits = "".join(ch for ch in query if ch.isdigit())
        condition = (
            Q(number__icontains=query) | Q(client__name__icontains=query) |
            Q(client__phone__icontains=query) | Q(device__brand__icontains=query) |
            Q(device__model__icontains=query) | Q(device__imei__icontains=query) |
            Q(device__serial_number__icontains=query) | Q(issue_description__icontains=query)
        )
        if digits:
            condition |= Q(client__normalized_phone__icontains=digits)
        qs = qs.filter(condition)

    orders = list(with_status_changed_at(qs).order_by("accepted_at", "id"))
    decorate_stale_orders(orders)
    grouped = []
    for status, title, icon in WORK_GROUPS:
        items = [order for order in orders if order.status == status]
        for order in items:
            order.work_actions = [action for action in WORK_ACTIONS.get(status, ()) if action[0] in order.allowed_next_statuses]
        grouped.append({"status": status, "title": title, "icon": icon, "orders": items})
    counters = {status: sum(order.status == status for order in orders) for status, _, _ in WORK_GROUPS}
    productive_minutes = sum(
        order.estimated_work_minutes_snapshot or 0 for order in orders if order.status in ACTIVE_ORDER_STATUSES
    )
    return {
        "work_groups": grouped, "work_counters": counters, "work_total": len(orders),
        "physical_wip": physical_wip,
        "count_accepted": counters[CRMOrder.Status.ACCEPTED],
        "count_diagnostic": counters[CRMOrder.Status.DIAGNOSTIC],
        "count_repair": counters[CRMOrder.Status.REPAIR],
        "count_approval": counters[CRMOrder.Status.APPROVAL],
        "count_waiting_part": counters[CRMOrder.Status.WAITING_PART],
        "count_ready": counters[CRMOrder.Status.READY],
        "productive_minutes": productive_minutes, "productive_hours": productive_minutes // 60,
        "productive_remainder": productive_minutes % 60, "mode": mode, "q": query,
        "selected_employee": selected_employee, "employees": Employee.objects.filter(is_active=True),
        "work_is_master": is_master, "current_employee": employee,
    }


@crm_permission_required()
def work_queue(request):
    context = _work_queue_context(request, request.GET)
    template = "crm/partials/work_queue_content.html" if _is_htmx(request) else "crm/work_queue.html"
    return render(request, template, context)


@crm_permission_required("crm.change_order_status")
def work_queue_status(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    try:
        if request.POST.get("status") == CRMOrder.Status.ISSUED:
            raise ValidationError("Выдайте устройство через экран финальной проверки.")
        change_status(order, request.POST.get("status"), request.user)
        message, level = "Статус ремонта обновлён", "success"
    except ValidationError as exc:
        message, level = exc.messages[0], "error"
    if _is_htmx(request):
        response = render(request, "crm/partials/work_queue_content.html", _work_queue_context(request, request.POST))
        return _toast(response, message, level)
    getattr(messages, level)(request, message)
    return redirect(_work_queue_redirect(request.POST))


def _work_queue_redirect(params):
    query = params.urlencode() if hasattr(params, "urlencode") else ""
    for key in ("csrfmiddlewaretoken", "status"):
        if hasattr(params, "copy"):
            params = params.copy(); params.pop(key, None)
    query = params.urlencode() if hasattr(params, "urlencode") else ""
    return f"{reverse('crm:work_queue')}?{query}" if query else reverse("crm:work_queue")


@crm_permission_required("crm.change_crmorder")
@transaction.atomic
def work_queue_take(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    employee = master_employee(request.user)
    if employee is None:
        raise PermissionDenied
    order = get_object_or_404(
        CRMOrder.objects.select_for_update().filter(status__in=PHYSICAL_ORDER_STATUSES), pk=pk,
    )
    if order.employee_id is not None:
        message, level = "Ремонт уже назначен другому мастеру.", "error"
    else:
        order.employee = employee
        order.save(update_fields=["employee", "updated_at"])
        add_event(order, CRMEvent.Type.EMPLOYEE, "Мастер взял ремонт в работу.", request.user, "", employee)
        message, level = "Ремонт назначен вам", "success"
    if _is_htmx(request):
        response = render(request, "crm/partials/work_queue_content.html", _work_queue_context(request, request.POST))
        return _toast(response, message, level)
    getattr(messages, level)(request, message)
    return redirect(_work_queue_redirect(request.POST))


@crm_permission_required("crm.add_crmorder")
@transaction.atomic
def order_create(request):
    warranty_source = None
    appointment = None
    appointment_id = request.GET.get("appointment") or request.POST.get("appointment")
    if appointment_id:
        appointment = get_object_or_404(Appointment.objects.select_related("account__account_profile", "phone_model__brand", "repair_type"), pk=appointment_id)
        if hasattr(appointment, "crm_order"):
            messages.info(request, f"Эта запись уже принята в ремонт {appointment.crm_order.number}.")
            return redirect("crm:order_detail", pk=appointment.crm_order.pk)
    source_id = request.GET.get("warranty_source") or request.POST.get("warranty_source")
    if source_id:
        warranty_source = get_object_or_404(CRMOrder, pk=source_id, status=CRMOrder.Status.ISSUED)
    initial = {}
    if not warranty_source and request.GET.get("client"):
        initial["existing_client"] = get_object_or_404(CRMClient, pk=request.GET["client"])
    if appointment:
        profile = getattr(appointment.account, "account_profile", None) if appointment.account_id else None
        if profile and profile.crm_client_id:
            initial["existing_client"] = profile.crm_client
        initial.update({"client_name": appointment.customer_name, "phone": appointment.customer_phone,
                        "device_type": appointment.phone_model.get_category_display(), "brand": appointment.phone_model.brand.name,
                        "device_model": appointment.phone_model.name, "issue_description": appointment.services_display,
                        "agreed_price": appointment.price_final, "source": "Онлайн-запись",
                        "estimated_work_minutes_snapshot": appointment_workload(appointment)})
    form = IntakeForm(request.POST or None, initial=initial, warranty_source=warranty_source)
    if request.method == "POST" and form.is_valid():
        if appointment:
            appointment = Appointment.objects.select_for_update(of=("self",)).select_related("account__account_profile").get(pk=appointment.pk)
            if hasattr(appointment, "crm_order"):
                messages.info(request, f"Эта запись уже принята в ремонт {appointment.crm_order.number}.")
                return redirect("crm:order_detail", pk=appointment.crm_order.pk)
        data = form.cleaned_data
        client = warranty_source.client if warranty_source else data.get("existing_client")
        profile = getattr(appointment.account, "account_profile", None) if appointment and appointment.account_id else None
        if profile:
            # Profile may be linked to a CRM client below; lock its existing row
            # separately instead of locking the nullable OUTER JOIN.
            profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
        if profile and profile.crm_client_id:
            client = profile.crm_client
        if not client:
            client = CRMClient.objects.create(
                name=data["client_name"], phone=data["phone"], additional_phone=data["additional_phone"],
                email=data["email"], source=data["source"], city=data["city"], note=data["client_note"],
            )
        device = warranty_source.device if warranty_source else data.get("existing_device")
        if not device:
            device = CRMDevice.objects.create(
                client=client, device_type=data["device_type"], brand=data["brand"], model=data["device_model"],
                imei=data["imei"], serial_number=data["serial_number"], color=data["color"], condition=data["condition"],
                included_items=data["included_items"], unlock_code=data["unlock_code"], note=data["device_note"],
            )
        order = CRMOrder.objects.create(
            client=client, device=device, employee=data["employee"], order_type=data["order_type"],
            warranty_source_order=warranty_source, accepted_at=data.get("accepted_at") or timezone.now(),
            issue_description=data["issue_description"], internal_note=data["internal_note"],
            condition_on_intake=data["condition"], accessories_on_intake=data["included_items"],
            agreed_price=data["agreed_price"], warranty_days=data["warranty_days"], source_appointment=appointment,
            estimated_work_minutes_snapshot=(
                appointment_workload(appointment)
                if appointment else data["estimated_work_minutes_snapshot"]
            ),
        )
        add_event(order, CRMEvent.Type.CREATED, f"Заказ принят в ремонт.{f' Создан из онлайн-записи #{appointment.pk}.' if appointment else ''}", request.user)
        transaction.on_commit(lambda order_id=order.pk: _notify_accepted_order(order_id))
        if appointment:
            appointment.status = "done"; appointment.save(update_fields=["status"])
            if profile and not profile.crm_client_id:
                profile.crm_client = client; profile.save(update_fields=["crm_client", "updated_at"])
        messages.success(request, f"Заказ {order.number} создан.")
        return redirect("crm:order_detail", pk=order.pk)
    context = {
        "form": form, "warranty_source": warranty_source, "appointment": appointment,
        "device_types": CRMDeviceType.objects.filter(is_active=True),
        "brands": CRMBrand.objects.filter(is_active=True),
        "device_models_list": CRMDeviceModel.objects.filter(is_active=True, brand__is_active=True).select_related("brand"),
        "warranty_terms": CRMWarrantyTerm.objects.filter(is_active=True),
        "issue_templates_data": list(CRMIssueTemplate.objects.filter(is_active=True).values("id", "text")),
        "auto_open_modal": True,
        "modal_direct": not _is_htmx(request),
    }
    template = "crm/partials/intake_wizard.html" if _is_htmx(request) else "crm/order_form.html"
    response = render(request, template, context)
    if _is_htmx(request) and request.method == "POST" and form.errors:
        response.status_code = 422
    return response


@crm_permission_required()
def appointment_list(request):
    items = Appointment.objects.select_related("phone_model__brand", "repair_type", "account", "crm_order").prefetch_related("items__repair_type")
    q = (request.GET.get("q") or "").strip(); period = request.GET.get("period", "upcoming"); status = request.GET.get("status", "")
    if q:
        items = items.filter(
            Q(customer_name__icontains=q) | Q(customer_phone__icontains=q)
            | Q(phone_model__name__icontains=q) | Q(phone_model__brand__name__icontains=q)
            | Q(crm_order__number__icontains=q)
        )
    today = timezone.localdate()
    now = timezone.now()
    start_today = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    if period == "today": items = items.filter(start__date=today)
    elif period == "tomorrow": items = items.filter(start__date=today + timezone.timedelta(days=1))
    elif period == "upcoming": items = items.filter(status__in=["new", "confirmed"], start__gte=start_today, crm_order__isnull=True)
    elif period in {"past", "history"}: items = items.filter(start__lt=start_today)
    elif period == "cancelled": items = items.filter(status="cancelled")
    elif period == "no_show": items = items.filter(status="no_show")
    elif period == "converted": items = items.filter(Q(status="done") | Q(crm_order__isnull=False))
    if status: items = items.filter(status=status)
    active = Appointment.objects.filter(status__in=["new", "confirmed"], crm_order__isnull=True)
    ordering = "-start" if period in {"past", "history", "cancelled", "no_show", "converted"} else "start"
    return render(request, "crm/appointment_list.html", {
        "appointments": items.order_by(ordering), "statuses": Appointment.STATUS_CHOICES,
        "filters": request.GET, "period": period, "now": now,
        "appointment_kpis": {
            "today": active.filter(start__date=today).count(),
            "tomorrow": active.filter(start__date=today + timezone.timedelta(days=1)).count(),
            "waiting": active.filter(start__gte=now).count(),
            "overdue": active.filter(start__lt=now).count(),
        },
    })


@crm_permission_required()
def appointment_detail(request, pk):
    appointment = get_object_or_404(Appointment.objects.select_related("phone_model__brand", "repair_type", "account", "crm_order", "crm_order__device").prefetch_related("items__repair_type"), pk=pk)
    return render(request, "crm/appointment_detail.html", {"appointment": appointment, "now": timezone.now()})


@crm_permission_required()
@transaction.atomic
def appointment_no_show(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    appointment = get_object_or_404(
        Appointment.objects.select_for_update(of=("self",)), pk=pk,
        status__in=["new", "confirmed"], crm_order__isnull=True,
    )
    appointment.status = "no_show"
    appointment.save(update_fields=["status"])
    messages.success(request, "Запись отмечена: клиент не пришёл.")
    return redirect("crm:appointment_detail", pk=pk)


@crm_permission_required()
def capacity_view(request):
    raw_day = request.GET.get("date") or request.POST.get("date")
    selected_day = parse_date(raw_day) if raw_day else timezone.localdate()
    if selected_day is None:
        selected_day = timezone.localdate()
    day_value = str(selected_day)
    if request.method == "POST" and not is_tehsfera_admin(request.user):
        raise PermissionDenied
    instance = WorkshopDayCapacity.objects.filter(date=day_value).first()
    initial = {"date": day_value}
    if instance is None:
        initial.update({
            "capacity_minutes": settings.WORKSHOP_DEFAULT_CAPACITY_MINUTES,
            "reserve_minutes": settings.WORKSHOP_DEFAULT_RESERVE_MINUTES,
        })
    form = WorkshopDayCapacityForm(request.POST or None, instance=instance, initial=initial)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Мощность дня сохранена.")
        return redirect(f"{reverse('crm:capacity')}?date={day_value}")
    return render(request, "crm/capacity.html", {"capacity": capacity_dashboard(), "form": form,
                  "can_edit": is_tehsfera_admin(request.user), "selected_date": day_value})


@crm_permission_required()
def client_search(request):
    query = (request.GET.get("q") or "").strip()
    results = []
    if len(query) >= 2:
        clients = filter_clients(query, request.user).prefetch_related("devices")[:10]
        results = [{
            "id": client.pk, "name": client.name, "phone": client.phone,
            "devices": [{"id": d.pk, "name": d.display_name, "serial": d.serial_number} for d in client.devices.all()],
        } for client in clients]
    return JsonResponse({"results": results, "normalized": normalize_phone(query)})


@crm_permission_required()
def device_models(request):
    brand = (request.GET.get("brand") or "").strip()
    items = CRMDeviceModel.objects.filter(is_active=True, brand__is_active=True)
    if brand:
        items = items.filter(brand__name__iexact=brand)
    return JsonResponse({"results": list(items.values("id", "name", "brand__name")[:100])})


@crm_permission_required()
def order_detail(request, pk):
    order = get_object_or_404(orders_for_user(request.user).prefetch_related("work_items", "comments__author", "events__author", "attachments", "part_usages__part_item__receipt__part", "part_usages__part_item__receipt__supplier", "part_usages__installed_by", "part_usages__removed_by"), pk=pk)
    from notify_tg.services import client_has_telegram
    return render(request, "crm/order_detail.html", {
        "order": order, "work_form": WorkItemForm(), "comment_form": CommentForm(),
        "attachment_form": AttachmentForm(), "status_choices": CRMOrder.Status.choices, "statuses": CRMOrder.Status.choices,
        "employees": Employee.objects.filter(is_active=True),
        "work_templates_data": list(CRMWorkTemplate.objects.filter(is_active=True).values("id", "name", "default_price")),
        "client_telegram_linked": client_has_telegram(order.client.phone),
        "notification_resend_available": order.status in {
            CRMOrder.Status.ACCEPTED, CRMOrder.Status.APPROVAL,
            CRMOrder.Status.WAITING_PART, CRMOrder.Status.READY, CRMOrder.Status.ISSUED,
        },
    })


@crm_permission_required("crm.change_order_status")
def order_notification_resend(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(orders_for_user(request.user).select_related("client", "device"), pk=pk)
    from notify_tg.services import client_has_telegram, notify_order_status
    if not client_has_telegram(order.client.phone):
        messages.info(request, "Telegram клиента не привязан.")
    elif notify_order_status(order.pk, order.status):
        add_event(order, CRMEvent.Type.COMMENT, "Уведомление клиенту повторно отправлено.", request.user)
        messages.success(request, "Уведомление клиенту отправлено повторно.")
    else:
        add_event(order, CRMEvent.Type.COMMENT, "Не удалось повторно отправить уведомление клиенту.", request.user)
        messages.error(request, "Telegram временно недоступен. Статус ремонта не изменён.")
    return redirect("crm:order_detail", pk=pk)


def _parts_context(order):
    return {"order": order, "part_usages": order.part_usages.select_related(
        "part_item__receipt__part", "part_item__receipt__supplier", "installed_by", "removed_by"
    )}


@crm_permission_required("crm.change_crmorder")
def order_part_search(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    query = (request.GET.get("q") or "").strip()
    items = PartItem.objects.filter(status=PartItem.Status.IN_STOCK).select_related("receipt__part", "receipt__supplier")
    if query:
        items = items.filter(
            Q(receipt__part__name__icontains=query) | Q(receipt__part__brand__icontains=query) |
            Q(receipt__part__device_model__icontains=query) | Q(receipt__supplier__name__icontains=query) |
            Q(receipt__order_number__icontains=query)
        )
    return render(request, "crm/partials/order_part_modal.html", {"order": order, "items": items[:30], "q": query})


@crm_permission_required("crm.change_crmorder")
def order_part_install(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    try:
        install_part(order, request.POST.get("part_item"), request.user)
    except (ValidationError, PartItem.DoesNotExist) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else "Деталь уже отсутствует на складе."
        if _is_htmx(request):
            response = render(request, "crm/partials/order_parts.html", _parts_context(order), status=422)
            return _toast(response, message, "error")
        messages.error(request, message)
        return redirect("crm:order_detail", pk=pk)
    if _is_htmx(request):
        response = render(request, "crm/partials/order_parts.html", _parts_context(order))
        response["HX-Trigger"] = json.dumps({"workbench:modal-close": {}, "workbench:toast": {"message": "Деталь добавлена", "level": "success"}})
        return response
    messages.success(request, "Деталь добавлена.")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_crmorder")
def order_part_return(request, pk, usage_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    usage = get_object_or_404(CRMOrderPartUsage, pk=usage_pk, order=order)
    try:
        return_part(usage, request.user)
    except ValidationError as exc:
        if _is_htmx(request):
            return _toast(render(request, "crm/partials/order_parts.html", _parts_context(order), status=422), exc.messages[0], "error")
        messages.error(request, exc.messages[0])
        return redirect("crm:order_detail", pk=pk)
    if _is_htmx(request):
        return _toast(render(request, "crm/partials/order_parts.html", _parts_context(order)), "Деталь возвращена на склад")
    messages.success(request, "Деталь возвращена на склад.")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_crmorder")
def order_edit(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    old = {"employee": order.employee, "diagnostic": order.diagnostic_result, "price": order.agreed_price, "warranty": order.warranty_days}
    form = OrderEditForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        order = form.save()
        sync_finance_repair(order)
        changes = [
            (CRMEvent.Type.EMPLOYEE, "Мастер изменён.", old["employee"], order.employee),
            (CRMEvent.Type.DIAGNOSTIC, "Результат диагностики изменён.", old["diagnostic"], order.diagnostic_result),
            (CRMEvent.Type.PRICE, "Согласованная цена изменена.", old["price"], order.agreed_price),
            (CRMEvent.Type.WARRANTY, "Гарантия изменена.", old["warranty"], order.warranty_days),
        ]
        for event_type, description, before, after in changes:
            if before != after:
                add_event(order, event_type, description, request.user, before, after)
        messages.success(request, "Заказ обновлён.")
        return redirect("crm:order_detail", pk=pk)
    return render(request, "crm/order_edit.html", {"form": form, "order": order})


@crm_permission_required("crm.change_order_status")
def order_status(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        if request.POST.get("status") == CRMOrder.Status.ISSUED:
            raise ValidationError("Выдайте устройство через экран финальной проверки.")
        change_status(order, request.POST.get("status"), request.user)
        messages.success(request, "Статус обновлён.")
        if _is_htmx(request):
            return _toast(render(request, "crm/partials/order_status.html", {"order": order, "statuses": CRMOrder.Status.choices}), "Статус ремонта обновлён")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        if _is_htmx(request):
            response = render(request, "crm/partials/order_status.html", {"order": order, "statuses": CRMOrder.Status.choices}, status=422)
            return _toast(response, exc.messages[0], "error")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_order_status")
def order_issue(request, pk):
    order = get_object_or_404(
        orders_for_user(request.user).select_related("client", "device", "employee", "finance_repair").prefetch_related(
            "work_items", "part_usages__part_item__receipt__part",
        ),
        pk=pk,
    )
    if order.status != CRMOrder.Status.READY:
        messages.error(request, "Выдача доступна только для ремонта со статусом «Готово к выдаче».")
        return redirect("crm:order_detail", pk=pk)
    form = IssueOrderForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        try:
            issued = issue_order(
                order.pk,
                final_price=form.cleaned_data["final_price"],
                paid_amount=form.cleaned_data["paid_amount"],
                warranty_days=form.cleaned_data["warranty_days"],
                author=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, "Устройство выдано. Оплата и гарантия зафиксированы.")
            return redirect("crm:order_detail", pk=issued.pk)
    installed_parts = [
        usage for usage in order.part_usages.all()
        if usage.status == CRMOrderPartUsage.Status.INSTALLED
    ]
    return render(request, "crm/order_issue.html", {
        "order": order,
        "form": form,
        "installed_parts": installed_parts,
    })


@crm_permission_required("crm.change_crmorder")
def order_assignee(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    old_employee = order.employee
    employee_id = request.POST.get("employee")
    employee = get_object_or_404(Employee, pk=employee_id, is_active=True) if employee_id else None
    if old_employee != employee:
        order.employee = employee
        order.save(update_fields=["employee", "updated_at"])
        sync_finance_repair(order)
        add_event(order, CRMEvent.Type.EMPLOYEE, "Мастер изменён.", request.user, old_employee, employee)
    if _is_htmx(request):
        return _toast(render(request, "crm/partials/order_assignee.html", {
            "order": order, "employees": Employee.objects.filter(is_active=True),
        }), "Мастер назначен")
    messages.success(request, "Мастер назначен.")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required()
def global_search(request):
    query = (request.GET.get("q") or "").strip()
    orders, clients, parts, suppliers = [], [], [], []
    if len(query) >= 2:
        orders = list(filter_orders({"q": query}, request.user)[:6])
        clients = list(filter_clients(query, request.user)[:6])
        if is_approved_master(request.user) or (request.user.is_staff and request.user.has_module_perms("finance")):
            from finance.models import PartItem, Supplier
            parts = list(PartItem.objects.select_related("receipt__part", "receipt__supplier").filter(
                Q(receipt__part__brand__icontains=query) | Q(receipt__part__device_model__icontains=query) |
                Q(receipt__part__name__icontains=query) | Q(receipt__supplier__name__icontains=query)
            )[:5])
            suppliers = list(Supplier.objects.filter(
                Q(name__icontains=query) | Q(contact_person__icontains=query) | Q(phone__icontains=query)
            )[:5])
    return render(request, "crm/partials/global_search_results.html", {
        "query": query, "orders": orders, "clients": clients, "parts": parts, "suppliers": suppliers,
    })


@crm_permission_required("crm.change_crmorder")
def work_add(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    form = WorkItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        work = form.save(commit=False); work.order = order; work.save()
        add_event(order, CRMEvent.Type.WORK, f"Добавлена работа «{work.name}» на {work.total} BYN.", request.user, "", work.total)
    else:
        messages.error(request, "Проверьте данные работы.")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_crmorder")
def work_edit(request, pk, work_pk):
    get_object_or_404(orders_for_user(request.user), pk=pk)
    work = get_object_or_404(CRMWorkItem, pk=work_pk, order_id=pk)
    old = f"{work.name}: {work.quantity} × {work.unit_price}"
    form = WorkItemForm(request.POST or None, instance=work)
    if request.method == "POST" and form.is_valid():
        work = form.save()
        new = f"{work.name}: {work.quantity} × {work.unit_price}"
        add_event(work.order, CRMEvent.Type.WORK, f"Работа «{work.name}» изменена.", request.user, old, new)
        return redirect("crm:order_detail", pk=pk)
    return render(request, "crm/work_edit.html", {"form": form, "order": work.order, "work": work})


@crm_permission_required("crm.change_crmorder")
def work_delete(request, pk, work_pk):
    get_object_or_404(orders_for_user(request.user), pk=pk)
    work = get_object_or_404(CRMWorkItem, pk=work_pk, order_id=pk)
    if request.method == "POST":
        name, total, order = work.name, work.total, work.order
        work.delete()
        add_event(order, CRMEvent.Type.WORK, f"Удалена работа «{name}».", request.user, total, "")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_crmorder")
def comment_add(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    form = CommentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        comment = form.save(commit=False); comment.order = order; comment.author = request.user; comment.save()
        add_event(order, CRMEvent.Type.COMMENT, "Добавлен комментарий.", request.user)
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required("crm.change_crmorder")
def attachment_add(request, pk):
    order = get_object_or_404(orders_for_user(request.user), pk=pk)
    form = AttachmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        attachment = form.save(commit=False); attachment.order = order; attachment.uploaded_by = request.user
        attachment.original_name = Path(attachment.file.name).name; attachment.save()
        add_event(order, CRMEvent.Type.ATTACHMENT, f"Добавлен файл «{attachment.original_name}».", request.user)
    else:
        messages.error(request, "Не удалось загрузить файл.")
    return redirect("crm:order_detail", pk=pk)


@crm_permission_required()
def attachment_open(request, pk):
    attachment = get_object_or_404(CRMAttachment, pk=pk)
    get_object_or_404(orders_for_user(request.user), pk=attachment.order_id)
    content_type = mimetypes.guess_type(attachment.original_name)[0] or "application/octet-stream"
    return FileResponse(attachment.file.open("rb"), content_type=content_type, filename=attachment.original_name)


@crm_permission_required()
def client_list(request):
    page = Paginator(filter_clients(request.GET.get("q", ""), request.user), 40).get_page(request.GET.get("page"))
    return render(request, "crm/client_list.html", {"page_obj": page, "q": request.GET.get("q", "")})


@crm_permission_required("crm.add_crmclient")
def client_create(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        messages.success(request, "Клиент создан.")
        return redirect("crm:client_detail", pk=client.pk)
    return render(request, "crm/client_form.html", {"form": form, "title": "Новый клиент"})


@crm_permission_required()
def client_detail(request, pk):
    allowed_ids = filter_clients("", request.user).values_list("pk", flat=True)
    client = get_object_or_404(CRMClient.objects.prefetch_related(
        "devices", Prefetch("orders", queryset=orders_for_user(request.user).select_related("device", "employee"))
    ), pk=pk, pk__in=allowed_ids)
    return render(request, "crm/client_detail.html", {"client": client})


@crm_permission_required("crm.change_crmclient")
def client_edit(request, pk):
    allowed_ids = filter_clients("", request.user).values_list("pk", flat=True)
    client = get_object_or_404(CRMClient, pk=pk, pk__in=allowed_ids)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Данные клиента обновлены.")
        return redirect("crm:client_detail", pk=client.pk)
    return render(request, "crm/client_form.html", {"form": form, "title": "Редактировать клиента", "client": client})


SETTINGS_SECTIONS = {
    "issues": (CRMIssueTemplate, IssueTemplateForm),
    "works": (CRMWorkTemplate, WorkTemplateForm),
    "device_types": (CRMDeviceType, DeviceTypeForm),
    "brands": (CRMBrand, BrandForm),
    "models": (CRMDeviceModel, DeviceModelForm),
    "warranty": (CRMWarrantyTerm, WarrantyTermForm),
}


@crm_permission_required("crm.manage_crm_settings")
def settings_view(request):
    section = request.GET.get("section") or request.POST.get("section") or "general"
    edit_id = request.GET.get("edit")
    forms = {key: form_class(prefix=key) for key, (_model, form_class) in SETTINGS_SECTIONS.items()}
    if section in SETTINGS_SECTIONS and edit_id:
        model, form_class = SETTINGS_SECTIONS[section]
        forms[section] = form_class(instance=get_object_or_404(model, pk=edit_id), prefix=section)
    document_settings = CRMDocumentSettings.load()
    document_form = DocumentSettingsForm(instance=document_settings, prefix="documents")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_documents":
            document_form = DocumentSettingsForm(request.POST, instance=document_settings, prefix="documents")
            if document_form.is_valid():
                document_form.save(); messages.success(request, "Настройки документов сохранены.")
                return redirect(f"{reverse('crm:settings')}?section=documents")
        elif section in SETTINGS_SECTIONS:
            model, form_class = SETTINGS_SECTIONS[section]
            instance = get_object_or_404(model, pk=request.POST.get("pk")) if request.POST.get("pk") else None
            if action == "toggle" and instance:
                instance.is_active = not instance.is_active; instance.save(update_fields=["is_active"])
                return redirect(f"{reverse('crm:settings')}?section={section}")
            form = form_class(request.POST, instance=instance, prefix=section)
            forms[section] = form
            if form.is_valid():
                form.save(); messages.success(request, "Настройка сохранена.")
                return redirect(f"{reverse('crm:settings')}?section={section}")

    return render(request, "crm/settings.html", {
        "section": section, "forms": forms, "document_form": document_form,
        "issues": CRMIssueTemplate.objects.all(), "works": CRMWorkTemplate.objects.all(),
        "device_types": CRMDeviceType.objects.all(), "brands": CRMBrand.objects.all(),
        "device_models": CRMDeviceModel.objects.select_related("brand"),
        "warranty_terms": CRMWarrantyTerm.objects.all(),
        "employees": Employee.objects.all(), "last_order": CRMOrder.objects.first(),
    })


def _document(request, pk, kind, template):
    order = get_object_or_404(orders_for_user(request.user).prefetch_related(
        "work_items", "part_usages__part_item__receipt__part",
    ), pk=pk)
    add_event(order, CRMEvent.Type.DOCUMENT, f"Открыт документ: {kind}.", request.user)
    configuration = CRMDocumentSettings.load()
    return render(request, template, {
        "order": order, "settings": configuration, "document_title": kind,
        **document_contact_context(configuration, request),
    })


@crm_permission_required("crm.manage_documents")
def receipt(request, pk): return _document(request, pk, "Квитанция", "crm/documents/receipt.html")

@crm_permission_required("crm.manage_documents")
def act(request, pk): return _document(request, pk, "Акт", "crm/documents/act.html")

@crm_permission_required("crm.manage_documents")
def warranty(request, pk): return _document(request, pk, "Гарантийный талон", "crm/documents/warranty.html")
