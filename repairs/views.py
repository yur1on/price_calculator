"""Представления (views) приложения repairs.

Эти представления реализуют многошаговый процесс: пользователь
выбирает бренд и модель телефона, тип ремонта, затем подходящее
время и отправляет данные для бронирования.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from .forms import BookingForm


from datetime import datetime, date, timedelta
from decimal import Decimal
from collections import OrderedDict

from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from .forms import BookingForm
from .models import (
    PhoneBrand,
    PhoneModel,
    RepairType,
    ModelRepairPrice,
    ReferralPartner,
    ReferralRedemption,
    Appointment,
    WorkingHour,  # важно: импорт есть
)


from .forms import BookingForm


from django.db.models import Q  # наверху файла, если ещё не импортирован

def brand_list(request):
    """Список брендов только по выбранной категории ?cat=phone|tablet|watch.
    Если параметр не передан или некорректен — по умолчанию 'phone'.
    """
    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    brands = (
        PhoneBrand.objects
        .filter(models__category=sel)
        .distinct()
        .order_by("name")
    )

    return render(request, "repairs/brand_list.html", {
        "brands": brands,
        "categories": choices,   # только три категории
        "selected_cat": sel,
    })


def model_list(request, brand_slug: str):
    """Список моделей бренда по выбранной категории (без варианта 'all')."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)

    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    models_qs = brand.models.filter(category=sel).order_by("name")

    return render(request, "repairs/model_list.html", {
        "brand": brand,
        "models": models_qs,
        "categories": choices,   # только три категории
        "selected_cat": sel,
    })


def repair_list(request, brand_slug: str, model_slug: str):
    """Показать список типов ремонта и цен для выбранной модели."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    prices = (
        ModelRepairPrice.objects
        .filter(phone_model=model, is_active=True)
        .select_related("repair_type")
    )
    return render(
        request,
        "repairs/repair_list.html",
        {"brand": brand, "model": model, "prices": prices},
    )


from datetime import datetime, timedelta
from decimal import Decimal
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect

# ...

def get_available_slots(
    phone_model: PhoneModel,
    repair_type: RepairType,
    days: int = 7,
    start_date: date | None = None,
    tz=None,
) -> list[datetime]:
    """Вернуть список доступных слотов от start_date на days дней вперёд.

    Учитывает рабочие часы (WorkingHour) и пересечения с Appointment.
    Длительность берётся из ModelRepairPrice или RepairType.default_duration_min.
    """
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
            # Python 3.10: делаем naive -> make_aware
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
                conflict = appointments.filter(start__lt=end_slot, end__gt=current_slot).exists()
                if not conflict:
                    slots.append(current_slot)
                current_slot += duration

    return slots


from datetime import datetime, date, timedelta
from django.utils import timezone
from django.shortcuts import render, get_object_or_404

# ... остальные импорты уже есть выше (PhoneBrand, PhoneModel, RepairType, ModelRepairPrice, Appointment, WorkingHour, и т.д.)

def slot_select(request, brand_slug: str, model_slug: str, repair_slug: str):
    """Месячный календарь (6 недель), без изменения маршрутов/URL."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # month=YYYY-MM (например, 2025-08). Если не задан — текущий месяц.
    month_str = request.GET.get("month")
    if month_str:
        try:
            y, m = month_str.split("-")
            month_start = date(int(y), int(m), 1)
        except Exception:
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    # Старт сетки: понедельник недели, куда входит 1-е число
    first_weekday = 0  # 0 = Monday
    offset = (month_start.weekday() - first_weekday) % 7
    grid_start = month_start - timedelta(days=offset)

    # Собираем доступные слоты на 6 недель (42 дня), сгруппуем по дате
    days_span = 42
    all_slots = get_available_slots(model, repair_type, days=days_span, start_date=grid_start, tz=tz)
    slots_by_date: dict[date, list[datetime]] = {}
    for s in all_slots:
        d = s.date()
        slots_by_date.setdefault(d, []).append(s)

    # Формируем 6 недель по 7 дней с уже готовыми полями для шаблона
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

    # Ссылки для навигации между месяцами
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)

    return render(request, "repairs/slot_select.html", {
        "brand": brand,
        "model": model,
        "repair_type": repair_type,
        "month_start": month_start,
        "prev_month": prev_month,
        "next_month": next_month,
        "weeks": calendar_weeks,  # список недель; у недели список day-словарей {date, in_month, slots}
    })

def book(request, brand_slug: str, model_slug: str, repair_slug: str):
    """Обработка формы бронирования для выбранного слота."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    slot_str = request.GET.get("slot")
    if not slot_str:
        return redirect(
            "repairs:slot_select",
            brand_slug=brand.slug,
            model_slug=model.slug,
            repair_slug=repair_type.slug,
        )

    # Подстраховка на случай, если '+' в часовом поясе превратился в пробел
    # (например, "+02:00" стало " 02:00" при неправильном кодировании ссылки)
    slot_str = slot_str.replace(" ", "+")

    # Парсим ISO-дату и приводим к aware-datetime
    try:
        slot_dt = datetime.fromisoformat(slot_str)
        if slot_dt.tzinfo is None:
            slot_dt = timezone.make_aware(slot_dt)
    except ValueError:
        return redirect(
            "repairs:slot_select",
            brand_slug=brand.slug,
            model_slug=model.slug,
            repair_slug=repair_type.slug,
        )

    # Цена и длительность
    try:
        price_entry = ModelRepairPrice.objects.get(
            phone_model=model, repair_type=repair_type, is_active=True
        )
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

            # Применяем скидку по коду (если валиден)
            app.apply_referral()

            if not app.price_final:
                app.price_final = app.price_original - app.discount_amount

            app.save()

            # Фиксируем использование реферального кода (для отчётности)
            if app.referral_code and app.discount_amount > 0:
                try:
                    partner = ReferralPartner.objects.get(code__iexact=app.referral_code)
                    commission = app.price_original * partner.partner_commission_pct / Decimal("100.0")
                    ReferralRedemption.objects.create(
                        partner=partner,
                        phone=app.customer_phone,
                        appointment=app,
                        discount_amount=app.discount_amount,
                        commission_amount=commission.quantize(Decimal("0.01")),
                    )
                except ReferralPartner.DoesNotExist:
                    pass

            return redirect("repairs:booking_success", appointment_id=app.id)
    else:
        form = BookingForm()

    return render(
        request,
        "repairs/booking_form.html",
        {
            "brand": brand,
            "model": model,
            "repair_type": repair_type,
            "slot": slot_dt,
            "duration": duration_min,
            "price": price,
            "form": form,
        },
    )

def booking_success(request, appointment_id: int):
    """Страница подтверждения после успешного бронирования."""
    appointment = get_object_or_404(Appointment, id=appointment_id)
    return render(request, "repairs/booking_success.html", {"appointment": appointment})
