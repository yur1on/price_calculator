# repairs/views.py
from __future__ import annotations
from django.core.paginator import Paginator
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



# ---------- UNIVERSAL NAME PARSING & SORT KEY (with GT grouping) ----------

_num_re = re.compile(r"\d+")
_paren_re = re.compile(r"\([^)]*\)")

# --- iPhone: отдельная логика поколений ---
_apple_brand_re   = re.compile(r"\b(apple|iphone)\b", re.I)
_apple_digits_re  = re.compile(r"\b(\d{1,2})\b")
_apple_suffix_s_re= re.compile(r"\b(\d{1,2})\s*s\b|\b(\d{1,2})s\b", re.I)
_apple_x_family_re= re.compile(r"\bx(r|s)?\b", re.I)

def _apple_key(name: str, brand: str):
    name_lc, brand_lc = (name or "").lower(), (brand or "").lower()
    if not (_apple_brand_re.search(name_lc) or _apple_brand_re.search(brand_lc)):
        return None

    gen, hint = None, ""
    m = _apple_x_family_re.search(name_lc)
    if m:
        gen = 10
        hint = {"r": "xr", "s": "xs"}.get((m.group(1) or "").lower(), "x")

    if gen is None:
        sfx = _apple_suffix_s_re.search(name_lc)
        if sfx:
            gen = int((sfx.group(1) or sfx.group(2)))
            hint = "s"

    if gen is None:
        d = _apple_digits_re.search(name_lc)
        if d:
            gen = int(d.group(1))

    vmap = {
        "pro max": 0, "ultra":1, "pro":2, "plus":3, "max":4,
        "xs":5, "xr":6, "s":7, "se":8, "mini":9, "c":10, "x":11, "":12,
    }
    variant = ""
    t = name_lc
    if   "pro max" in t: variant = "pro max"
    elif "pro"     in t: variant = "pro"
    elif "plus"    in t: variant = "plus"
    elif "max"     in t: variant = "max"
    elif "mini"    in t: variant = "mini"
    elif re.search(r"\bse\b", t): variant = "se"
    elif re.search(r"\bc\b", t):  variant = "c"
    if not variant and hint: variant = hint

    if gen is None:
        return (2, 1, 0, name_lc)  # яблочные «без поколения» в хвост яблочной группы

    return (1, -gen, vmap.get(variant, 12), name_lc)

# --- вариативность (универсально) ---
_VARIANT_RANKS = {
    "ultra":0, "pro max":1, "pro+":2, "pro plus":2, "pro":3,
    "max":4, "plus":5, "edge":6, "player":7, "prime":8,
    "s":9, "fe":10, "se":11, "lite":12, "core":13, "5g":14, "base":15
}

def _variant_rank(text_lc: str, suffix_after_number: str = "") -> int:
    toks = set()
    suf = (suffix_after_number or "").lower()
    if suf in {"s","e"}:
        toks.add(suf)
    t = f" {text_lc} "
    if " ultra" in t: toks.add("ultra")
    if " pro max" in t: toks.add("pro max")
    if " pro+" in t or " pro plus" in t: toks.add("pro+")
    if re.search(r"\bpro\b", t): toks.add("pro")
    if re.search(r"\bmax\b", t): toks.add("max")
    if re.search(r"\bplus\b", t) or "+" in t: toks.add("plus")
    if re.search(r"\bedge\b", t): toks.add("edge")
    if re.search(r"\bplayer\b", t): toks.add("player")
    if re.search(r"\bprime\b", t): toks.add("prime")
    if re.search(r"\bfe\b", t): toks.add("fe")
    if re.search(r"\bse\b", t): toks.add("se")
    if re.search(r"\blite\b", t): toks.add("lite")
    if re.search(r"\bcore\b", t): toks.add("core")
    if re.search(r"\b5g\b", t): toks.add("5g")
    if not toks:
        toks.add("base")
    return min(_VARIANT_RANKS.get(x, 99) for x in toks)

def _normalize_name(name: str) -> str:
    base = _paren_re.sub(" ", name or "")
    base = re.sub(r"[^\w\+\- ]+", " ", base, flags=re.U)
    base = re.sub(r"\s+", " ", base).strip()
    return base

def _parse_family_number(text: str):
    """
    ('family', number:int, suffix_after_number:str) | (None,None,None)
    понимает: A52s, C30s, Narzo 70 Pro, GT Neo 5 240W, Galaxy A 53, 14 Pro, ...
    """
    s = _normalize_name(text).lower()

    # слитно: 'a52s', 'c30s', 'x50m'
    m = re.search(r"\b([a-z]+)(\d{1,3})([a-z]{1,2})?\b", s)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3) or "")

    # двусловная семья перед числом: 'gt neo 5', 'galaxy a 53'
    m = re.search(r"\b([a-z]+)\s+([a-z]+)\s+(\d{1,3})\b", s)
    if m:
        return (f"{m.group(1)} {m.group(2)}", int(m.group(3)), "")

    # одно слово перед числом: 'narzo 70', 'note 60x'
    m = re.search(r"\b([a-z]+)\s+(\d{1,3})([a-z]{1,2})?\b", s)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3) or "")

    # «14 Pro»
    m = re.search(r"\b(\d{1,3})\b", s)
    if m:
        return ("", int(m.group(1)), "")

    return (None, None, None)

# --- НОВОЕ: извлекаем primary/subfamily, чтобы склеить GT и GT Neo ---
def _family_primary_sub(fam: str) -> tuple[str,str]:
    if not fam:
        return ("", "")
    parts = fam.split()
    if len(parts) == 1:
        return (parts[0], "")
    # Спец-правила, чтобы группы выглядели «ожидаемо»
    if parts[0] == "gt":
        # 'gt neo', 'gt master' => primary 'gt', sub: 'neo' | 'master'
        return ("gt", " ".join(parts[1:]))
    if parts[0] == "galaxy" and len(parts) >= 2:
        # у Samsung хотим группировку по A/S/… (а не по слову 'galaxy')
        return (parts[1], " ".join(parts[2:]))
    # общее эвристическое правило
    if len(parts[-1]) <= 3:
        return (parts[-1], " ".join(parts[:-1]))
    return (parts[0], " ".join(parts[1:]))

def _subfamily_rank(primary: str, sub: str) -> int:
    sub = (sub or "").strip()
    if primary == "gt":
        # base GT (пустой sub) → затем GT Neo → затем GT Master → прочее
        table = {"": 0, "neo": 1, "master": 2}
        return table.get(sub, 3)
    return 0

def _family_order_key(primary: str) -> tuple:
    """
    Порядок групп сверху: короткие серии + 'gt', затем прочие по алфавиту.
    Пустую семью ('') отправляем в самый низ (для «14 Pro» и т.п.).
    """
    if not primary:
        return (9, "zzz")
    simple = (
        bool(re.fullmatch(r"[a-z]{1,2}", primary)) or
        primary in {"gt"}
    )
    return (0 if simple else 1, primary)

def _model_sort_key(m: PhoneModel):
    """
    1) iPhone — по поколению (X=10) ↓, затем вариант.
    2) Остальные — primary-семья → подсемейство (для GT) → номер ↓ → «сила» варианта → имя.
    3) Без числа — в хвост по имени.
    """
    name = (m.name or "").strip()
    brand = getattr(m.brand, "name", "") or ""
    name_lc = name.lower()

    # iPhone как раньше
    apple_key = _apple_key(name, brand)
    if apple_key is not None:
        return apple_key

    fam, num, suf = _parse_family_number(name)
    if num is not None:
        primary, sub = _family_primary_sub(fam or "")
        var_rank = _variant_rank(name_lc, suf)
        return (0, _family_order_key(primary), _subfamily_rank(primary, sub), -num, var_rank, name_lc)

    # fallback, если совсем без чисел
    mnum = _num_re.search(_normalize_name(name))
    has_num = 0 if mnum else 1
    num = int(mnum.group()) if mnum else -1
    return (3, (9, "zzz"), -num, 99, name_lc)

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
    """Список моделей бренда: плитка/список, поиск, пагинация."""
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)

    choices = list(PhoneModel.CATEGORY_CHOICES)
    valid = {k for k, _ in choices}
    sel = request.GET.get("cat")
    if sel not in valid:
        sel = "phone"

    view_mode = (request.GET.get("view") or "grid").lower()  # grid | list
    if view_mode not in {"grid", "list"}:
        view_mode = "grid"

    q = (request.GET.get("q") or "").strip()

    qs = brand.models.filter(category=sel)
    if q:
        qs = qs.filter(name__icontains=q)

    models_qs = list(qs.order_by())  # сбрасываем Meta.ordering
    if not q:
        models_qs.sort(key=_model_sort_key)
    else:
        models_qs.sort(key=lambda m: _natural_key(m.name))

    per_page = 112 if view_mode == "grid" else 80
    paginator = Paginator(models_qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "repairs/model_list.html", {
        "brand": brand,
        "categories": choices,
        "selected_cat": sel,
        "view_mode": view_mode,
        "q": q,
        "page_obj": page_obj,
        "models": page_obj.object_list,  # совместимо с вашим циклом
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

from datetime import date, datetime, timedelta
from typing import List
from django.conf import settings
from django.utils import timezone

def get_available_slots(
    phone_model: "PhoneModel",
    repair_type: "RepairType",
    days: int = 7,
    start_date: date | None = None,
    tz=None,
) -> List[datetime]:
    """
    Возвращает список ДАТ/ВРЕМЕН (aware datetime) возможных стартов записи.
    Сетка — с фиксированным шагом (по умолчанию 60 минут).

    Учитывается:
      • длительность конкретной услуги для модели (ModelRepairPrice) либо default у RepairType
      • рабочие часы (WorkingHour) на каждый день недели
      • существующие заявки (кроме отменённых)
      • глобальная ёмкость (settings.REPAIRS_MAX_PARALLEL_APPOINTMENTS)
      • текущее время: прошлое не показывается
      • шаг сетки: settings.BOOKING_TIME_STEP_MIN (по умолчанию 60)
      • опциональные буферы ДО и ПОСЛЕ (settings.BOOKING_PREP_BUFFER_MIN / BOOKING_CLEANUP_BUFFER_MIN)

    Правило валидности слота:
      интервал [slot_start, slot_start + duration] должен полностью
      попадать в рабочее окно дня и при этом интервал
      [slot_start - prep_buffer, slot_end + cleanup_buffer] не должен
      превышать ёмкость по пересечениям с уже существующими заявками.
    """
    # --- 1) Длительность услуги ---
    try:
        price_entry = ModelRepairPrice.objects.get(
            phone_model=phone_model, repair_type=repair_type, is_active=True
        )
        duration_min = price_entry.duration_min
    except ModelRepairPrice.DoesNotExist:
        duration_min = repair_type.default_duration_min or 60  # безопасный дефолт
    duration = timedelta(minutes=int(duration_min))

    # --- 2) Настройки шаг/буферы/ёмкость ---
    step_min = int(getattr(settings, "BOOKING_TIME_STEP_MIN", 60))
    prep_min = int(getattr(settings, "BOOKING_PREP_BUFFER_MIN", 0))
    cleanup_min = int(getattr(settings, "BOOKING_CLEANUP_BUFFER_MIN", 0))
    step = timedelta(minutes=max(1, step_min))
    prep_buf = timedelta(minutes=max(0, prep_min))
    cleanup_buf = timedelta(minutes=max(0, cleanup_min))
    capacity = int(getattr(settings, "REPAIRS_MAX_PARALLEL_APPOINTMENTS", 1))

    # --- 3) TZ/сейчас/стартовая дата ---
    tz = tz or timezone.get_current_timezone()
    now = timezone.localtime(timezone.now(), tz)
    if start_date is None:
        start_date = now.date()

    # --- 4) Границы диапазона для предзагрузки существующих заявок ---
    # Берём чуть шире с учётом буферов
    from datetime import time as _time
    range_start = timezone.make_aware(datetime.combine(start_date, _time.min), tz) - prep_buf
    range_end = timezone.make_aware(datetime.combine(start_date + timedelta(days=days), _time.min), tz) + cleanup_buf

    existing = list(
        Appointment.objects.filter(
            status__in=["new", "confirmed", "done"],
            start__lt=range_end,
            end__gt=range_start,
        ).values_list("start", "end")
    )
    # Преобразуем к локальной TZ (на всякий)
    existing = [(timezone.localtime(s, tz), timezone.localtime(e, tz)) for s, e in existing]

    # --- 5) Рабочие часы ---
    working_hours = list(WorkingHour.objects.all())

    slots: List[datetime] = []

    # --- 6) Проход по дням ---
    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        weekday = current_date.weekday()
        day_hours = [wh for wh in working_hours if wh.weekday == weekday]
        if not day_hours:
            continue

        for wh in day_hours:
            # Рабочее окно дня
            day_start_naive = datetime.combine(current_date, wh.start)
            day_end_naive = datetime.combine(current_date, wh.end)
            day_start = timezone.make_aware(day_start_naive, tz)
            day_end = timezone.make_aware(day_end_naive, tz)

            # Старт итерации — ближайшая точка сетки ≥ day_start
            # (чтобы шаг сетки был ровно по N минут от начала дня)
            # Привяжем сетку к началу рабочего окна:
            first_slot = day_start
            # Если нужно привязать сетку к «круглому часу», раскомментируй:
            # first_slot = (day_start.replace(minute=0, second=0, microsecond=0)
            #               + (((day_start - day_start.replace(minute=0, second=0, microsecond=0))
            #                   // step) * step))

            current_slot = first_slot
            while True:
                slot_start = current_slot

                # Не показываем прошлое
                if slot_start < now:
                    current_slot += step
                    # проверка выхода за рабочее окно сделана после вычисления end
                    # но здесь тоже можно сэкономить:
                    if current_slot >= day_end:
                        break
                    continue

                slot_end = slot_start + duration

                # Слот должен полностью влезать в рабочее окно
                if slot_end > day_end:
                    break  # дальше только позже — тоже выйдет за окно

                # С учётом буферов проверяем пересечения
                check_start = slot_start - prep_buf
                check_end = slot_end + cleanup_buf

                overlaps = sum(
                    1
                    for s, e in existing
                    if s < check_end and e > check_start
                )

                if overlaps < capacity:
                    slots.append(slot_start)

                # Следующий шаг по сетке
                current_slot += step

    return slots

def slot_select(request, brand_slug: str, model_slug: str, repair_slug: str):
    """
    Месячный календарь (до 6 недель) с лимитом записи на MAX_BOOK_AHEAD_DAYS вперёд,
    скрытием полностью «прошедших» верхних недель и скрытием дней до сегодня в первой видимой неделе.

    ДОП: если длительность услуги > 560 мин — онлайн-запись недоступна (редирект на список услуг).
    """
    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    # --- Проверка длительности: > 560 мин — запись только по согласованию ---
    try:
        price_entry = ModelRepairPrice.objects.get(
            phone_model=model, repair_type=repair_type, is_active=True
        )
        effective_duration_min = price_entry.duration_min
    except ModelRepairPrice.DoesNotExist:
        effective_duration_min = repair_type.default_duration_min

    if effective_duration_min and effective_duration_min > 560:
        messages.info(
            request,
            "Запись на эту услугу возможна только по согласованию. Позвоните нам, пожалуйста."
        )
        return redirect("repairs:repair_list", brand_slug=brand.slug, model_slug=model.slug)

    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # Скользящее окно записи: максимум на N дней вперёд
    limit_date = today + timedelta(days=MAX_BOOK_AHEAD_DAYS)
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

    # Не даём уйти дальше лимитного месяца
    if month_start > limit_month_start:
        month_start = limit_month_start

    # Сетка начинается с понедельника той недели, где находится 1-е число
    first_weekday = 0  # Пн
    offset = (month_start.weekday() - first_weekday) % 7
    grid_start = month_start - timedelta(days=offset)

    # Собираем слоты на 6 недель (42 дня) от grid_start
    days_span = 42
    all_slots = get_available_slots(
        model, repair_type, days=days_span, start_date=grid_start, tz=tz
    )
    # Обрезаем по лимитной дате
    all_slots = [s for s in all_slots if s.date() <= limit_date]

    # Группируем по датам
    slots_by_date: dict[date, list[datetime]] = {}
    for s in all_slots:
        d = timezone.localtime(s, tz).date()
        slots_by_date.setdefault(d, []).append(s)

    # Формируем 6 недель × 7 дней
    calendar_weeks: list[list[dict]] = []
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

    # === Удаляем полностью «прошедшие» верхние недели (только для текущего месяца) ===
    if is_current_month:
        while calendar_weeks:
            first_week = calendar_weeks[0]
            if all(cell["date"] < today for cell in first_week):
                calendar_weeks.pop(0)
            else:
                break

        # Скрываем дни до сегодняшнего в первой видимой неделе
        if calendar_weeks:
            for cell in calendar_weeks[0]:
                if cell["date"] < today:
                    cell["placeholder"] = True  # будет скрыт в шаблоне через .cell--placeholder

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
    + ограничение: запись максимум на MAX_BOOK_AHEAD_DAYS вперёд.

    ДОП: если длительность услуги > 560 мин — онлайн-запись запрещена.
    """
    from django.conf import settings

    MAX_BOOK_AHEAD_DAYS = int(getattr(settings, "REPAIRS_MAX_BOOK_AHEAD_DAYS", 30))

    brand = get_object_or_404(PhoneBrand, slug=brand_slug)
    model = get_object_or_404(PhoneModel, brand=brand, slug=model_slug)
    repair_type = get_object_or_404(RepairType, slug=repair_slug)

    # --- Правило «по согласованию»: блокируем онлайн-бронирование, если длительность > 560
    try:
        price_entry_for_check = ModelRepairPrice.objects.get(
            phone_model=model, repair_type=repair_type, is_active=True
        )
        effective_duration_min = price_entry_for_check.duration_min
    except ModelRepairPrice.DoesNotExist:
        effective_duration_min = repair_type.default_duration_min

    if effective_duration_min and effective_duration_min > 560:
        messages.error(request, "Онлайн-запись на эту услугу недоступна. Срок по согласованию.")
        return redirect("repairs:repair_list", brand_slug=brand.slug, model_slug=model.slug)

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

    # Лимит на дату записи
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
                app.save()

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
