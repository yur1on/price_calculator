# repairs/views.py
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q, Sum, Count
from django.db.models.functions import Lower
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from .forms import BookingForm
from .models import (
    PhoneBrand,
    PhoneModel,
    RepairType,
    ModelRepairPrice,
    Appointment,
    WorkingHour,
    ReferralPartner,
    ReferralRedemption,
)


# ---------- утилиты ----------

def _natural_key(s: str):
    """Ключ для натуральной сортировки: 'Model 2' < 'Model 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


def _parse_date_or(default_date: date, value: str | None) -> date:
    try:
        return datetime.fromisoformat(value).date() if value else default_date
    except ValueError:
        return default_date


# ---------- шаг 1: бренды ----------

def brand_list(request):
    """Список брендов по выбранной категории ?cat=phone|tablet|watch."""
    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    brands = (
        PhoneBrand.objects
        .filter(models__category=sel)
        .distinct()
        .annotate(name_lc=Lower("name"))
        .order_by("name_lc", "name")
    )

    return render(request, "repairs/brand_list.html", {
        "brands": brands,
        "categories": choices,
        "selected_cat": sel,
    })


# ---------- шаг 2: модели бренда ----------

def model_list(request, brand_slug: str):
    """Список моделей бренда по выбранной категории с натуральной сортировкой."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)

    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    # Берём из БД и сортируем в Python «по-человечески»
    models_qs = list(brand.models.filter(category=sel))
    models_qs.sort(key=lambda m: _natural_key(m.name))

    return render(request, "repairs/model_list.html", {
        "brand": brand,
        "models": models_qs,
        "categories": choices,
        "selected_cat": sel,
    })


# ---------- шаг 3: услуги/цены модели ----------

def repair_list(request, brand_slug: str, model_slug: str):
    """Показать список типов ремонта и цен для выбранной модели (отсортировано по названию услуги)."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    prices = (
        ModelRepairPrice.objects
        .filter(phone_model=model, is_active=True)
        .select_related("repair_type")
        .order_by("repair_type__name")
    )
    return render(request, "repairs/repair_list.html", {
        "brand": brand,
        "model": model,
        "prices": prices,
    })


# ---------- слоты и бронь ----------

def get_available_slots(
    phone_model: PhoneModel,
    repair_type: RepairType,
    days: int = 7,
    start_date: date | None = None,
    tz=None,
) -> list[datetime]:
    """Вернуть список доступных слотов от start_date на days дней вперёд."""
    # длительность
    try:
        price_entry = ModelRepairPrice.objects.get(
            phone_model=phone_model, repair_type=repair_type, is_active=True
        )
        duration_min = price_entry.duration_min
    except ModelRepairPrice.DoesNotExist:
        duration_min = repair_type.default_duration_min
    duration = timedelta(minutes=duration_min)

    # таймзона/сейчас
    tz = tz or timezone.get_current_timezone()
    now = timezone.localtime(timezone.now(), tz)

    # стартовая дата
    if start_date is None:
        start_date = now.date()

    slots: list[datetime] = []
    working_hours = list(WorkingHour.objects.all())
    appointments = Appointment.objects.filter(
        phone_model=phone_model,
        repair_type=repair_type,
        status__in=["new", "confirmed", "done"],
        start__gte=now - timedelta(days=1),
    )

    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        weekday = current_date.weekday()
        day_hours = [wh for wh in working_hours if wh.weekday == weekday]

        for wh in day_hours:
            naive_start = datetime.combine(current_date, wh.start)
            naive_end = datetime.combine(current_date, wh.end)
            slot_start_time = timezone.make_aware(naive_start, tz)
            slot_end_time = timezone.make_aware(naive_end, tz)

            current_slot = slot_start_time
            while current_slot + duration <= slot_end_time:
                if current_slot < now:
                    current_slot += duration
                    continue
                end_slot = current_slot + duration
                conflict = appointments.filter(
                    start__lt=end_slot,
                    end__gt=current_slot,
                ).exists()
                if not conflict:
                    slots.append(current_slot)
                current_slot += duration

    return slots


def slot_select(request, brand_slug: str, model_slug: str, repair_slug: str):
    """Месячный календарь (6 недель)."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # month=YYYY-MM (например, 2025-08)
    month_str = request.GET.get("month")
    if month_str:
        try:
            y, m = month_str.split("-")
            month_start = date(int(y), int(m), 1)
        except Exception:
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    # Начало сетки: понедельник недели, куда входит 1-е число
    first_weekday = 0  # Mon
    offset = (month_start.weekday() - first_weekday) % 7
    grid_start = month_start - timedelta(days=offset)

    # Доступные слоты на 6 недель (42 дня), сгруппуем по дате
    days_span = 42
    all_slots = get_available_slots(model, repair_type, days=days_span, start_date=grid_start, tz=tz)
    slots_by_date: dict[date, list[datetime]] = {}
    for s in all_slots:
        d = s.date()
        slots_by_date.setdefault(d, []).append(s)

    calendar_weeks = []
    for w in range(6):
        week = []
        for i in range(7):
            d = grid_start + timedelta(days=w * 7 + i)
            week.append({
                "date": d,
                "in_month": (d.month == month_start.month),
                "slots": slots_by_date.get(d, []),
            })
        calendar_weeks.append(week)

    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)

    return render(request, "repairs/slot_select.html", {
        "brand": brand,
        "model": model,
        "repair_type": repair_type,
        "month_start": month_start,
        "prev_month": prev_month,
        "next_month": next_month,
        "weeks": calendar_weeks,
    })


def book(request, brand_slug: str, model_slug: str, repair_slug: str):
    """Обработка формы бронирования для выбранного слота."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    slot_str = request.GET.get("slot")
    if not slot_str:
        return redirect("repairs:slot_select", brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    # Подстраховка: '+' мог превратиться в пробел
    slot_str = slot_str.replace(" ", "+")
    try:
        slot_dt = datetime.fromisoformat(slot_str)
        if slot_dt.tzinfo is None:
            slot_dt = timezone.make_aware(slot_dt)
    except ValueError:
        return redirect("repairs:slot_select", brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    try:
        price_entry = ModelRepairPrice.objects.get(phone_model=model, repair_type=repair_type, is_active=True)
        duration_min = price_entry.duration_min
        price = price_entry.price
    except ModelRepairPrice.DoesNotExist:
        duration_min = repair_type.default_duration_min
        price = Decimal("0.00")

    end_dt = slot_dt + timedelta(minutes=duration_min)

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            app = Appointment(
                phone_model=model,
                repair_type=repair_type,
                start=slot_dt,
                end=end_dt,
                customer_name=form.cleaned_data["customer_name"],
                customer_phone=form.cleaned_data["customer_phone"],
                referral_code=form.cleaned_data.get("referral_code", "").strip(),
                price_original=price,
            )
            app.apply_referral()
            if not app.price_final:
                app.price_final = app.price_original - app.discount_amount
            app.save()
            # ReferralRedemption создаст сигнал

            return redirect("repairs:booking_success", appointment_id=app.id)
    else:
        form = BookingForm()

    return render(request, "repairs/booking_form.html", {
        "brand": brand,
        "model": model,
        "repair_type": repair_type,
        "slot": slot_dt,
        "duration": duration_min,
        "price": price,
        "form": form,
    })


def booking_success(request, appointment_id: int):
    """Страница подтверждения после успешного бронирования."""
    appointment = get_object_or_404(Appointment, id=appointment_id)
    return render(request, "repairs/booking_success.html", {"appointment": appointment})


# ---------- отчёты по партнёрам ----------

def referrals_report(request):
    """Итоги по всем партнёрам за период (?from=YYYY-MM-DD&to=YYYY-MM-DD&status=...)."""
    today = timezone.localdate()
    month_start = today.replace(day=1)

    date_from = _parse_date_or(month_start, request.GET.get("from"))
    date_to = _parse_date_or(today, request.GET.get("to"))
    status = (request.GET.get("status") or "").strip()

    qs = ReferralRedemption.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )

    has_status = True
    try:
        ReferralRedemption._meta.get_field("status")
    except FieldDoesNotExist:
        has_status = False

    if has_status and status in {"pending", "accrued", "paid"}:
        qs = qs.filter(status=status)

    annotate_kwargs = {
        "uses": Count("id"),
        "total_discount": Sum("discount_amount"),
        "total_commission": Sum("commission_amount"),
    }
    if has_status:
        annotate_kwargs.update({
            "pending_commission": Sum("commission_amount", filter=Q(status="pending")),
            "accrued_commission": Sum("commission_amount", filter=Q(status="accrued")),
            "paid_commission": Sum("commission_amount", filter=Q(status="paid")),
        })

    rows = (
        qs.values("partner__id", "partner__name", "partner__code")
        .annotate(**annotate_kwargs)
        .order_by("partner__name")
    )

    totals = qs.aggregate(
        total_uses=Count("id"),
        total_discount=Sum("discount_amount"),
        total_commission=Sum("commission_amount"),
    )

    return render(request, "repairs/referrals_report.html", {
        "rows": rows,
        "totals": totals,
        "date_from": date_from,
        "date_to": date_to,
        "status": status if has_status else "",
        "has_status": has_status,
    })


def referrals_partner_report(request, code: str):
    """Деталка по одному партнёру (?from=YYYY-MM-DD&to=YYYY-MM-DD&status=...)."""
    partner = get_object_or_404(ReferralPartner, code__iexact=code)

    today = timezone.localdate()
    month_start = today.replace(day=1)

    date_from = _parse_date_or(month_start, request.GET.get("from"))
    date_to = _parse_date_or(today, request.GET.get("to"))
    status = (request.GET.get("status") or "").strip()

    qs = ReferralRedemption.objects.filter(
        partner=partner,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )

    has_status = True
    try:
        ReferralRedemption._meta.get_field("status")
    except FieldDoesNotExist:
        has_status = False

    if has_status and status in {"pending", "accrued", "paid"}:
        qs = qs.filter(status=status)

    totals = qs.aggregate(
        uses=Count("id"),
        total_discount=Sum("discount_amount"),
        total_commission=Sum("commission_amount"),
    )

    operations = (
        qs.select_related("appointment", "appointment__phone_model", "appointment__repair_type")
        .order_by("-created_at")
    )

    return render(request, "repairs/referrals_partner.html", {
        "partner": partner,
        "operations": operations,
        "totals": totals,
        "date_from": date_from,
        "date_to": date_to,
        "status": status if has_status else "",
        "has_status": has_status,
    })
