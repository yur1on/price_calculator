from functools import wraps

from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Employee, PayrollCalculation, PayrollPeriod, SalaryPayment
from .services import ZERO, payroll_preview


def _employee_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    try:
        employee = user.employee_profile
    except Employee.DoesNotExist:
        return None
    return employee if employee.is_active and not employee.is_owner else None


def master_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        employee = _employee_for(request.user)
        if employee is None:
            if not request.user.is_authenticated:
                return redirect(f"{reverse('master_portal:login')}?next={request.path}")
            raise PermissionDenied("К этому пользователю не привязан активный мастер.")
        request.master_employee = employee
        return view(request, *args, **kwargs)
    return wrapped


def master_login(request):
    if _employee_for(request.user):
        return redirect("master_portal:dashboard")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if _employee_for(user) is None:
            form.add_error(None, "Для этого аккаунта не настроен активный кабинет мастера.")
        else:
            login(request, user)
            return redirect("master_portal:dashboard")
    return render(request, "master/login.html", {"form": form})


def master_logout(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    return redirect("master_portal:login")


def _closed_row(calculation):
    calculation.distributions = calculation.allocations.all()
    calculation.repairs = calculation.repair_snapshots.all()
    return calculation


@master_required
def dashboard(request):
    employee = request.master_employee
    open_period = PayrollPeriod.objects.filter(status=PayrollPeriod.Status.OPEN).order_by("-start_date").first()
    current = payroll_preview(open_period, employee) if open_period else None
    history = list(
        PayrollCalculation.objects.filter(employee=employee, period__status=PayrollPeriod.Status.CLOSED)
        .select_related("period").order_by("-period__start_date")
    )
    payments = SalaryPayment.objects.filter(employee=employee).order_by("-date", "-created_at")
    accrued = sum((item.salary_amount for item in history), ZERO)
    paid = payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return render(request, "master/dashboard.html", {
        "employee": employee, "open_period": open_period, "current": current,
        "history": history, "payments": payments, "accrued": accrued,
        "paid": paid, "remaining": accrued - paid,
    })


@master_required
def period_detail(request, pk):
    employee = request.master_employee
    period = get_object_or_404(PayrollPeriod, pk=pk)
    if period.status == PayrollPeriod.Status.CLOSED:
        calculation = get_object_or_404(
            PayrollCalculation.objects.select_related("period", "employee").prefetch_related(
                "repair_snapshots", "allocations__distributed_expense"
            ), period=period, employee=employee,
        )
        row, is_preview = _closed_row(calculation), False
    else:
        row, is_preview = payroll_preview(period, employee), True
    payments = SalaryPayment.objects.filter(employee=employee, date__range=(period.start_date, period.end_date)).order_by("date")
    paid = payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return render(request, "master/period_detail.html", {
        "employee": employee, "period": period, "row": row, "is_preview": is_preview,
        "payments": payments, "paid": paid, "remaining": row["salary_amount"] - paid if is_preview else row.salary_amount - paid,
    })
