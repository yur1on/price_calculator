from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from finance.models import PartItem, PayrollCalculation, PayrollPeriod, SalaryPayment, Supplier
from finance.services import ZERO, payroll_preview

from .access import account_landing_url, approved_master_required, is_client, is_tehsfera_admin, master_employee, profile_for, workspace_required
from .forms import AccountAuthenticationForm, RegistrationForm, ClientProfileForm, PhoneCodeForm
from .models import AccountProfile
from .selectors import appointments_for_client_account, orders_for_client_account
from .services import request_phone_verification, verify_phone_code
from .referrals import client_referral_context
from repairs.referrals import enroll_client


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = AccountAuthenticationForm

    def get_success_url(self):
        return self.get_redirect_url() or account_landing_url(self.request.user)


def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:workspace")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        if user.account_profile.role == AccountProfile.Role.CLIENT:
            login(request, user)
            return redirect("accounts:client_dashboard")
        return redirect("accounts:registration_pending")
    return render(request, "accounts/register.html", {"form": form})


def account_logout(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    return redirect("home")


def registration_pending(request):
    return render(request, "accounts/pending.html", {"profile": profile_for(request.user)})


@workspace_required
def workspace(request):
    if is_tehsfera_admin(request.user):
        return redirect("crm:dashboard")
    if master_employee(request.user):
        return redirect("crm:work_queue")
    if is_client(request.user):
        return redirect("accounts:client_dashboard")
    profile = profile_for(request.user)
    if profile and profile.role == AccountProfile.Role.MASTER:
        return redirect("accounts:registration_pending")
    raise PermissionDenied


@workspace_required
def client_dashboard(request):
    if not is_client(request.user):
        raise PermissionDenied
    orders = orders_for_client_account(request.user)
    profile = request.user.account_profile
    active = orders.exclude(status__in=["issued", "canceled"])
    appointments = appointments_for_client_account(request.user)
    return render(request, "accounts/client_dashboard.html", {
        "profile": profile, "active_orders": active[:4], "recent_orders": orders.order_by("-accepted_at")[:4],
        "active_count": active.count(), "ready_count": active.filter(status="ready").count(),
        "device_count": profile.crm_client.devices.count() if profile.crm_client_id else 0,
        "warranty_count": orders.filter(warranty_days__gt=0, warranty_started_at__isnull=False).count(),
        "upcoming_appointments": appointments.filter(status__in=["new", "confirmed"], crm_order__isnull=True).order_by("start")[:3],
    })


@workspace_required
def client_referrals(request):
    if not is_client(request.user) or request.user.is_staff or request.user.is_superuser:
        raise PermissionDenied
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        try:
            # Enrollment itself performs the safe existing-partner link under a lock.
            enroll_client(request.user)
        except ValidationError:
            messages.error(request, "Не удалось подключить участие. Проверьте подтверждение полного номера телефона. "
                           "Если вы уже участвуете через Telegram, подтвердите там свой номер или обратитесь в мастерскую.")
        else:
            messages.success(request, "Вы участвуете в реферальной программе. Ваш код готов.")
        return redirect("accounts:client_referrals")
    return render(request, "accounts/client_referrals.html", client_referral_context(
        request.user.account_profile, request.GET.get("page", 1),
    ))


@workspace_required
def client_repairs(request):
    if not is_client(request.user): raise PermissionDenied
    orders = orders_for_client_account(request.user).order_by("-accepted_at")
    return render(request, "accounts/client_repairs.html", {"active_orders": orders.exclude(status__in=["issued", "canceled"]), "history_orders": orders.filter(status__in=["issued", "canceled"])})


@workspace_required
def client_appointments(request):
    if not is_client(request.user): raise PermissionDenied
    items = appointments_for_client_account(request.user).order_by("-start")
    return render(request, "accounts/client_appointments.html", {
        "upcoming": items.filter(status__in=["new", "confirmed"], crm_order__isnull=True),
        "history": items.exclude(status__in=["new", "confirmed"]) | items.filter(crm_order__isnull=False),
    })


@workspace_required
def client_appointment_detail(request, pk):
    if not is_client(request.user): raise PermissionDenied
    appointment = get_object_or_404(appointments_for_client_account(request.user), pk=pk)
    return render(request, "accounts/client_appointment_detail.html", {"appointment": appointment, "now": timezone.now()})


@workspace_required
def client_appointment_cancel(request, pk):
    if not is_client(request.user): raise PermissionDenied
    if request.method != "POST": return HttpResponseNotAllowed(["POST"])
    appointment = get_object_or_404(appointments_for_client_account(request.user), pk=pk, status__in=["new", "confirmed"], crm_order__isnull=True, start__gt=timezone.now())
    appointment.status = "cancelled"; appointment.save(update_fields=["status"])
    messages.success(request, "Запись отменена.")
    return redirect("accounts:client_appointments")


@workspace_required
def client_repair_detail(request, pk):
    if not is_client(request.user): raise PermissionDenied
    return render(request, "accounts/client_repair_detail.html", {"order": get_object_or_404(orders_for_client_account(request.user), pk=pk)})


@workspace_required
def client_devices(request):
    if not is_client(request.user): raise PermissionDenied
    profile = request.user.account_profile
    devices = profile.crm_client.devices.prefetch_related("orders").all() if profile.crm_client_id else []
    return render(request, "accounts/client_devices.html", {"devices": devices})


@workspace_required
def client_warranties(request):
    if not is_client(request.user): raise PermissionDenied
    orders = [o for o in orders_for_client_account(request.user).filter(warranty_days__gt=0, warranty_started_at__isnull=False).order_by("-warranty_started_at")]
    return render(request, "accounts/client_warranties.html", {"orders": orders})


@workspace_required
def client_profile(request):
    if not is_client(request.user): raise PermissionDenied
    profile = request.user.account_profile
    initial = {"first_name": request.user.first_name, "last_name": request.user.last_name, "email": request.user.email, "phone": profile.phone}
    form = ClientProfileForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        old_phone = profile.phone
        request.user.first_name=form.cleaned_data["first_name"]; request.user.last_name=form.cleaned_data["last_name"]
        request.user.email=form.cleaned_data["email"]; request.user.username=form.cleaned_data["email"]
        request.user.save(update_fields=["first_name", "last_name", "email", "username"])
        profile.phone=form.cleaned_data["phone"]
        if old_phone != profile.phone: profile.phone_verified_at=None; profile.crm_client=None
        profile.save()
        messages.success(request, "Профиль сохранён.")
        return redirect("accounts:client_profile")
    return render(request, "accounts/client_profile.html", {"profile": profile, "form": form})


@workspace_required
def phone_verify(request):
    if not is_client(request.user): raise PermissionDenied
    profile=request.user.account_profile; form=PhoneCodeForm(request.POST or None)
    if request.method == "POST" and "request_code" in request.POST:
        if not settings.DEBUG:
            messages.error(request, "Подтверждение телефона пока недоступно. Обратитесь в мастерскую.")
            return redirect("accounts:phone_verify")
        try:
            verification, code=request_phone_verification(profile)
            messages.success(request, "Код создан и записан в журнал локального сервера.")
        except ValidationError as exc: messages.error(request, exc.message)
        return redirect("accounts:phone_verify")
    if request.method == "POST" and form.is_valid():
        try:
            result=verify_phone_code(profile, form.cleaned_data["code"])
            messages.success(request, "Телефон подтверждён." if result != "multiple" else "Телефон подтверждён. Для подключения истории требуется помощь сотрудника.")
            return redirect("accounts:client_dashboard")
        except ValidationError as exc: form.add_error("code", exc.message)
    return render(request, "accounts/phone_verify.html", {"profile": profile, "form": form})


@approved_master_required
def master_dashboard(request):
    return redirect("crm:work_queue")


@approved_master_required
def my_salary(request):
    employee = request.master_employee
    open_period = PayrollPeriod.objects.filter(status=PayrollPeriod.Status.OPEN).order_by("-start_date").first()
    current = payroll_preview(open_period, employee) if open_period else None
    history = list(
        PayrollCalculation.objects.filter(employee=employee, period__status=PayrollPeriod.Status.CLOSED)
        .select_related("period").order_by("period__calculated_at", "created_at", "pk")
    )
    payments = list(SalaryPayment.objects.filter(employee=employee).order_by("date", "created_at", "pk"))
    accrued = sum((item.salary_amount for item in history), ZERO)
    preliminary = current["salary_amount"] if current else ZERO
    earned = accrued + preliminary
    paid = sum((item.amount for item in payments), ZERO)
    ledger = []
    for calculation in history:
        occurred_at = calculation.period.calculated_at or calculation.created_at
        ledger.append({
            "date": timezone.localdate(occurred_at), "sort_date": timezone.localdate(occurred_at),
            "sort_created_at": occurred_at, "kind": "accrual", "calculation": calculation,
            "basis": calculation.period, "accrued": calculation.salary_amount, "paid": ZERO,
        })
    for payment in payments:
        ledger.append({
            "date": payment.date, "sort_date": payment.date, "sort_created_at": payment.created_at,
            "kind": "payment", "payment": payment, "basis": payment.comment or "Выплата мастеру",
            "accrued": ZERO, "paid": payment.amount,
        })
    ledger.sort(key=lambda item: (item["sort_date"], item["sort_created_at"], item["kind"], item.get("calculation", item.get("payment")).pk))
    balance = ZERO
    for item in ledger:
        balance += item["accrued"] - item["paid"]
        item["balance"] = balance
    return render(request, "accounts/my_salary.html", {
        "employee": employee, "open_period": open_period, "current": current,
        "history": history, "payments": payments[:30], "ledger": ledger,
        "accrued": accrued, "preliminary": preliminary, "earned": earned,
        "paid": paid, "remaining": earned - paid,
    })


@approved_master_required
def salary_calculation_detail(request, pk):
    calculation = get_object_or_404(
        PayrollCalculation.objects.select_related("period").prefetch_related("repair_snapshots", "allocations"),
        pk=pk, employee=request.master_employee, period__status=PayrollPeriod.Status.CLOSED,
    )
    return render(request, "accounts/salary_calculation_detail.html", {"calculation": calculation})


@approved_master_required
def master_stock(request):
    query = (request.GET.get("q") or "").strip()
    items = PartItem.objects.select_related("receipt__part", "receipt__supplier").filter(status=PartItem.Status.IN_STOCK)
    if query:
        items = items.filter(Q(receipt__part__brand__icontains=query) | Q(receipt__part__device_model__icontains=query) | Q(receipt__part__name__icontains=query) | Q(receipt__supplier__name__icontains=query))
    return render(request, "accounts/master_stock.html", {"items": items[:100], "q": query})


@approved_master_required
def master_suppliers(request):
    query = (request.GET.get("q") or "").strip()
    suppliers = Supplier.objects.filter(is_active=True)
    if query:
        suppliers = suppliers.filter(Q(name__icontains=query) | Q(contact_person__icontains=query) | Q(phone__icontains=query))
    return render(request, "accounts/master_suppliers.html", {"suppliers": suppliers[:100], "q": query})
