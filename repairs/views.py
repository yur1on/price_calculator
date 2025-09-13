# repairs/views.py
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
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
MAX_BOOK_AHEAD_DAYS = int(getattr(settings, "REPAIRS_MAX_BOOK_AHEAD_DAYS", 30))
# ---------- утилиты ----------



# --- ВЕРХ ФАЙЛА (рядом с другими regex) ---
_num_re = re.compile(r"\d+")

# Samsung
_samsung_strip_re = re.compile(r"\b(samsung|galaxy)\b", re.I)
_samsung_family_re = re.compile(r"\b(a|s|m|f|note|tab)\s*-?\s*(\d{1,4})\b", re.I)

# Apple
_apple_brand_re = re.compile(r"\b(apple|iphone)\b", re.I)
_apple_digits_re = re.compile(r"\b(\d{1,2})\b")                         # 6, 11, 14...
_apple_suffix_s_re = re.compile(r"\b(\d{1,2})\s*s\b|\b(\d{1,2})s\b", re.I)  # 6 s / 6s
_apple_x_family_re = re.compile(r"\bx(r|s)?\b", re.I)                    # X / XR / XS

def _model_sort_key(m: PhoneModel):
    """
    Кастомная сортировка:
    - Samsung/Galaxy: A→S→M→F→Note→Tab; внутри семьи — номер по убыванию.
    - Apple/iPhone: поколение по убыванию; варианты: Pro Max > Pro > Plus > Max > s > SE > mini > c > base.
    - Остальные: «есть число» выше, затем число по убыванию, затем имя.
    """
    name = (m.name or "").strip()
    name_lc = name.lower()
    brand_lc = (getattr(m.brand, "name", "") or "").lower()

    # ---------- SAMSUNG / GALAXY ----------
    if "samsung" in brand_lc or "galaxy" in name_lc:
        core = _samsung_strip_re.sub("", name_lc).strip()
        m1 = _samsung_family_re.search(core)
        if m1:
            family = m1.group(1).lower()
            num = int(m1.group(2))
            family_order = {"a": 0, "s": 1, "m": 2, "f": 3, "note": 4, "tab": 5}
            fam_idx = family_order.get(family, 98)
            return (0, fam_idx, -num, name_lc)

    # ---------- APPLE / IPHONE ----------
    if _apple_brand_re.search(brand_lc) or _apple_brand_re.search(name_lc):
        gen = None
        variant_hint = ""

        # X / XR / XS → поколение 10
        x_match = _apple_x_family_re.search(name_lc)
        if x_match:
            gen = 10
            tail = (x_match.group(1) or "").lower()
            if tail == "r":
                variant_hint = "xr"
            elif tail == "s":
                variant_hint = "xs"
            else:
                variant_hint = "x"

        # 6s / 5s и т.п. — тут СРАЗУ ставим и поколение, и вариант
        if gen is None:
            s_match = _apple_suffix_s_re.search(name_lc)
            if s_match:
                gen = int((s_match.group(1) or s_match.group(2)))
                variant_hint = "s"

        # Обычные цифры поколения (если ещё не определили)
        if gen is None:
            d = _apple_digits_re.search(name_lc)
            if d:
                gen = int(d.group(1))

        # Карта приоритетов вариантов
        vmap = {
            "pro max": 0,
            "ultra":   1,
            "pro":     2,
            "plus":    3,
            "max":     4,
            "xs":      5,
            "xr":      6,
            "s":       7,
            "se":      8,
            "mini":    9,
            "c":       10,
            "x":       11,
            "":        12,
        }

        # Вычисляем реальный variant по тексту
        text = name_lc
        variant = ""
        if "pro max" in text:
            variant = "pro max"
        elif "pro" in text:
            variant = "pro"
        elif "plus" in text:
            variant = "plus"
        elif "max" in text:
            variant = "max"
        elif "mini" in text:
            variant = "mini"
        elif " se" in text or text.endswith("se") or "se " in text:
            variant = "se"
        elif " c" in text or text.endswith("c") or "c " in text:
            variant = "c"

        # Подсказка от X/XS/XR/«s»-суффикса
        if variant == "" and variant_hint:
            variant = variant_hint

        # Если поколение так и не нашли — в конец яблочной группы
        if gen is None:
            return (2, 1, 0, name_lc)

        # Главный порядок: поколение по убыванию, затем вариант
        return (1, -gen, vmap.get(variant, 12), name_lc)

    # ---------- ПРОЧИЕ БРЕНДЫ ----------
    mnum = _num_re.search(name)
    has_num = 0 if mnum else 1
    num = int(mnum.group()) if mnum else -1
    return (3, has_num, -num, name_lc)

def _natural_key(s: str):
    """Ключ для натуральной сортировки: 'Model 2' < 'Model 10'."""
    return [int(t) if t.isdigit() else (t or "").lower() for t in re.split(r"(\d+)", s or "")]

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
    """Список моделей бренда по выбранной категории с сортировкой «новые → старые»."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)

    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    # Берём из БД и сортируем в Python по описанному ключу
    models_qs = list(brand.models.filter(category=sel).order_by())  # сбрасываем Meta.ordering
    models_qs.sort(key=_model_sort_key)

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
) -> List[datetime]:
    """
    Вернуть список доступных слотов от start_date на days дней вперёд.

    Конфликты считаются ГЛОБАЛЬНО: любые заявки (кроме отменённых),
    независимо от модели/услуги, блокируют слот, если превышают ёмкость.

    Ёмкость читается из settings.REPAIRS_MAX_PARALLEL_APPOINTMENTS (по умолчанию 1).
    """
    # 1) длительность услуги
    try:
        price_entry = ModelRepairPrice.objects.get(
            phone_model=phone_model, repair_type=repair_type, is_active=True
        )
        duration_min = price_entry.duration_min
    except ModelRepairPrice.DoesNotExist:
        duration_min = repair_type.default_duration_min
    duration = timedelta(minutes=duration_min)

    # 2) таймзона/сейчас
    tz = tz or timezone.get_current_timezone()
    now = timezone.localtime(timezone.now(), tz)

    # 3) стартовая дата
    if start_date is None:
        start_date = now.date()

    # 4) заранее вытаскиваем все НЕ отменённые заявки, пересекающиеся с диапазоном сетки
    from datetime import time as _time
    range_start = timezone.make_aware(datetime.combine(start_date, _time.min), tz)
    range_end = timezone.make_aware(datetime.combine(start_date + timedelta(days=days), _time.min), tz)

    existing = list(
        Appointment.objects.filter(
            status__in=["new", "confirmed", "done"],
            start__lt=range_end,
            end__gt=range_start,
        ).values_list("start", "end")
    )

    # 5) ёмкость (сколько ремонтов можно вести параллельно)
    capacity = int(getattr(settings, "REPAIRS_MAX_PARALLEL_APPOINTMENTS", 1))

    # 6) собираем слоты по рабочим часам
    slots: List[datetime] = []
    working_hours = list(WorkingHour.objects.all())

    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        weekday = current_date.weekday()
        day_hours = [wh for wh in working_hours if wh.weekday == weekday]

        for wh in day_hours:
            # окно работы за день
            naive_start = datetime.combine(current_date, wh.start)
            naive_end = datetime.combine(current_date, wh.end)
            slot_start_time = timezone.make_aware(naive_start, tz)
            slot_end_time = timezone.make_aware(naive_end, tz)

            current_slot = slot_start_time
            while current_slot + duration <= slot_end_time:
                # не показываем прошлое
                if current_slot < now:
                    current_slot += duration
                    continue

                end_slot = current_slot + duration

                # сколько пересечений уже есть в этот интервал
                overlaps = sum(1 for s, e in existing if s < end_slot and e > current_slot)

                if overlaps < capacity:
                    slots.append(current_slot)

                current_slot += duration

    return slots

def slot_select(request, brand_slug: str, model_slug: str, repair_slug: str):
    """Месячный календарь (6 недель) с лимитом записи на 30 дней вперёд,
    скрытием дней до сегодня и удалением полностью «прошедших» верхних недель.
    """
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # Запись доступна максимум на 30 дней вперёд (скользящее окно)
    limit_date = today + timedelta(days=30)
    limit_month_start = limit_date.replace(day=1)
    current_month_start = today.replace(day=1)

    # month=YYYY-MM (например, 2025-08)
    month_str = request.GET.get("month")
    if month_str:
        try:
            y, m = month_str.split("-")
            month_start = date(int(y), int(m), 1)
        except Exception:
            month_start = current_month_start
    else:
        month_start = current_month_start

    # Если запросили месяц позже лимитного — показываем лимитный
    if month_start > limit_month_start:
        month_start = limit_month_start

    # Начало сетки: понедельник недели, куда входит 1-е число
    first_weekday = 0  # Пн
    offset = (month_start.weekday() - first_weekday) % 7
    grid_start = month_start - timedelta(days=offset)

    # Доступные слоты на 6 недель (42 дня)
    days_span = 42
    all_slots = get_available_slots(model, repair_type, days=days_span, start_date=grid_start, tz=tz)

    # Отфильтровать слоты, выходящие за предел limit_date
    all_slots = [s for s in all_slots if s.date() <= limit_date]

    # Сгрупповать слоты по дате
    slots_by_date: dict[date, list[datetime]] = {}
    for s in all_slots:
        d = timezone.localtime(s, tz).date()
        slots_by_date.setdefault(d, []).append(s)

    # Построить 6 недель × 7 дней
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

    # Навигация по месяцам
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)

    can_prev = month_start > current_month_start
    can_next = next_month <= limit_month_start
    is_current_month = (month_start.year == today.year and month_start.month == today.month)

    # === Удаляем полностью «прошедшие» верхние недели (ТОЛЬКО для текущего месяца) ===
    # Например, если сегодня 8-е, первая неделя с 1–7 числами уйдёт целиком.
    if is_current_month:
        while calendar_weeks:
            first_week = calendar_weeks[0]
            if all(cell["date"] < today for cell in first_week):
                calendar_weeks.pop(0)
            else:
                break

    return render(request, "repairs/slot_select.html", {
        "brand": brand,
        "model": model,
        "repair_type": repair_type,
        "month_start": month_start,
        "prev_month": prev_month,
        "next_month": next_month,
        "weeks": calendar_weeks,
        "today": today,
        "is_current_month": is_current_month,
        "limit_date": limit_date,
        "can_prev": can_prev,
        "can_next": can_next,
    })


def book(request, brand_slug: str, model_slug: str, repair_slug: str):
    """
    Создание брони для выбранного слота (с глобальной проверкой занятости и защитой от гонок)
    + ограничение: записываться можно максимум на N дней вперёд (по умолчанию 30).
    """
    from django.conf import settings

    MAX_BOOK_AHEAD_DAYS = int(getattr(settings, "REPAIRS_MAX_BOOK_AHEAD_DAYS", 30))

    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    # слот обязателен
    slot_str = request.GET.get("slot")
    if not slot_str:
        return redirect("repairs:slot_select",
                        brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    # подстраховка: '+' мог превратиться в пробел
    slot_str = slot_str.replace(" ", "+")
    try:
        slot_dt = datetime.fromisoformat(slot_str)
        if slot_dt.tzinfo is None:
            slot_dt = timezone.make_aware(slot_dt)
    except ValueError:
        messages.error(request, "Некорректный слот времени.")
        return redirect("repairs:slot_select",
                        brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    # Запрещаем прошлое
    now = timezone.now()
    if slot_dt <= now:
        messages.error(request, "Нельзя записаться на прошедшее время.")
        return redirect("repairs:slot_select",
                        brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    # Лимит на дату записи: не дальше чем MAX_BOOK_AHEAD_DAYS от сегодняшней локальной даты
    limit_date = timezone.localdate() + timedelta(days=MAX_BOOK_AHEAD_DAYS)
    slot_local_date = timezone.localtime(slot_dt).date()
    if slot_local_date > limit_date:
        messages.error(
            request,
            f"Записываться можно максимум на {MAX_BOOK_AHEAD_DAYS} дней вперёд (до {limit_date.strftime('%d.%m.%Y')}).",
        )
        return redirect("repairs:slot_select",
                        brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    # длительность и цена
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

    # первичная проверка занятости (глобально по всем активным заявкам)
    capacity = int(getattr(settings, "REPAIRS_MAX_PARALLEL_APPOINTMENTS", 1))
    overlaps = Appointment.objects.filter(
        status__in=["new", "confirmed", "done"],
        start__lt=end_dt,
        end__gt=slot_dt,
    ).count()
    if overlaps >= capacity:
        messages.error(request, "Этот слот уже занят. Пожалуйста, выберите другое время.")
        return redirect("repairs:slot_select",
                        brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            # повторная проверка в транзакции — защита от гонок
            with transaction.atomic():
                overlaps = (Appointment.objects
                            .select_for_update()
                            .filter(
                                status__in=["new", "confirmed", "done"],
                                start__lt=end_dt,
                                end__gt=slot_dt,
                            )
                            .count())
                if overlaps >= capacity:
                    messages.error(request, "К сожалению, этот слот только что заняли. Выберите другое время.")
                    return redirect("repairs:slot_select",
                                    brand_slug=brand.slug, model_slug=model.slug, repair_slug=repair_type.slug)

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
                # Применяем реф.код и считаем финальную цену
                app.apply_referral()
                if not app.price_final:
                    app.price_final = app.price_original - app.discount_amount
                app.save()  # (при сохранении может создаться ReferralRedemption по сигналу)

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


def contacts(request):
    ctx = {
        "address": "246050, г. Гомель, ул. Гагарина, д. 55, каб. 50",
        "phone": "+375 (44) 568-44-93",
        "work_hours": [
            ("Понедельник", "10:00–18:00"),
            ("Вторник", "10:00–18:00"),
            ("Среда", "10:00–18:00"),
            ("Четверг", "10:00–18:00"),
            ("Пятница", "10:00–18:00"),
            ("Суббота", "10:00–18:00"),
            ("Воскресенье", "выходной"),
        ],
        # ключевые фразы для поиска конкретно сервиса на Гагарина, 55
        "gmaps_query": "ремонт телефонов, Гомель, Гагарина 55",
        "ymaps_query": "ремонт телефонов Гагарина 55 Гомель",
    }
    return render(request, "repairs/contacts.html", ctx)
