# repairs/views_analytics.py
from __future__ import annotations


import re
from urllib.parse import urlparse
from datetime import date, datetime, timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.db.models.functions import TruncDate, ExtractHour, ExtractIsoWeekDay
from django.shortcuts import render
from django.utils import timezone
from django.core.paginator import Paginator

from .models import PageView

def _parse_date(s: str | None, default: date) -> date:
    """Безопасный парсер YYYY-MM-DD → date (на невалидных значениях возвращает default)."""
    if not s:
        return default
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return default


def _daterange(d0: date, d1: date):
    """Итерируем все даты включительно [d0; d1]."""
    total = (d1 - d0).days
    for i in range(total + 1):
        yield d0 + timedelta(days=i)


@staff_member_required
def analytics_view(request):
    """
    Простая аналитика посещений (PageView):
      • тренд по дням за период
      • активность по часам (гистограмма)
      • суммы по дням недели (вместо теплокарты — понятнее)
      • источники трафика (direct / google / yandex / instagram / tiktok / other)
      • топ-страницы и топ-рефереры (таблицы)

    Параметры:
      ?from=YYYY-MM-DD&to=YYYY-MM-DD
    По умолчанию: последние 14 дней, включая сегодня.
    """
    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # Период по умолчанию: 14 дней
    default_from = today - timedelta(days=13)
    default_to = today

    date_from = _parse_date(request.GET.get("from"), default_from)
    date_to = _parse_date(request.GET.get("to"), default_to)
    if date_from > date_to:
        date_from, date_to = date_to, date_from  # на всякий случай меняем местами

    base_qs = PageView.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )

    # ===== 1) Тренд по дням =====
    daily_raw = (
        base_qs
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(n=Count("id"))
        .order_by("d")
    )
    by_day = {row["d"]: row["n"] for row in daily_raw}
    daily_labels: list[str] = []
    daily_counts: list[int] = []
    for d in _daterange(date_from, date_to):
        daily_labels.append(d.strftime("%d.%m"))
        daily_counts.append(int(by_day.get(d, 0)))

    # ===== 2) Активность по часам суток =====
    by_hour = (
        base_qs
        .annotate(h=ExtractHour("created_at"))
        .values("h")
        .annotate(n=Count("id"))
        .order_by("h")
    )
    hours_labels = [f"{h:02d}:00" for h in range(24)]
    hours_counts = [0] * 24
    for r in by_hour:
        h = int(r["h"] or 0)
        if 0 <= h <= 23:
            hours_counts[h] = int(r["n"])

    # ===== 3) ДеньНедели × Час (теплокарта в данных) + агрегат по дням недели =====
    # ISO: 1=Пн … 7=Вс → индексы 0..6
    heat_raw = (
        base_qs
        .annotate(dow_iso=ExtractIsoWeekDay("created_at"), hour=ExtractHour("created_at"))
        .values("dow_iso", "hour")
        .annotate(n=Count("id"))
        .order_by("dow_iso", "hour")
    )
    heatmap = [[0] * 24 for _ in range(7)]  # [день][час]
    for r in heat_raw:
        dow_idx = int((r["dow_iso"] or 1) - 1)  # 0..6
        hour = int(r["hour"] or 0)
        if 0 <= dow_idx <= 6 and 0 <= hour <= 23:
            heatmap[dow_idx][hour] = int(r["n"])
    weekday_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    weekday_counts = [sum(row) for row in heatmap]  # свод по дням недели (понятная колонка/диаграмма)

    # ===== 4) Источники трафика (рефереры по корзинам) =====
    # buckets: direct / google / yandex / instagram / tiktok / other
    ref_qs = base_qs.values_list("referer", flat=True)
    buckets = {"Прямые": 0, "Google": 0, "Яндекс": 0, "Instagram": 0, "TikTok": 0, "Другие": 0}
    for ref in ref_qs:
        if not ref:
            buckets["Прямые"] += 1
            continue
        try:
            netloc = urlparse(ref).netloc.lower()
        except Exception:
            netloc = ""
        host = re.sub(r"^www\.", "", netloc)

        if "google." in host:
            buckets["Google"] += 1
        elif "yandex." in host:
            buckets["Яндекс"] += 1
        elif "instagram." in host or "instagr.am" in host:
            buckets["Instagram"] += 1
        elif "tiktok." in host:
            buckets["TikTok"] += 1
        elif host:
            buckets["Другие"] += 1
        else:
            buckets["Прямые"] += 1

    ref_source_labels = list(buckets.keys())
    ref_source_counts = [buckets[k] for k in ref_source_labels]

    # ===== 5) Топ страниц и рефереры (сырые таблицы) =====
    top_paths = (
        base_qs.values("path")
        .annotate(n=Count("id"))
        .order_by("-n")[:20]
    )
    top_referrers = (
        base_qs.exclude(referer__isnull=True).exclude(referer="")
        .values("referer")
        .annotate(n=Count("id"))
        .order_by("-n")[:20]
    )

    context = {
        # период
        "date_from": date_from,
        "date_to": date_to,

        # график «по дням»
        "daily_labels": daily_labels,   # ["22.09","23.09",...]
        "daily_counts": daily_counts,   # [12, 8, 0, ...]

        # гистограмма «по часам»
        "hours_labels": hours_labels,   # ["00:00","01:00",...]
        "hours_counts": hours_counts,   # [5, 2, 0, ...]

        # теплокарта (если вдруг пригодится) и «понятная» агрегация по дням недели
        "weekday_labels": weekday_labels,   # ["Пн",...,"Вс"]
        "heatmap": heatmap,                 # 7×24 матрица
        "weekday_counts": weekday_counts,   # [числа по дням недели]

        # источники
        "ref_source_labels": ref_source_labels,
        "ref_source_counts": ref_source_counts,

        # таблицы
        "top_paths": top_paths,           # [{path, n}, ...]
        "top_referrers": top_referrers,   # [{referer, n}, ...]
    }
    return render(request, "repairs/analytics.html", context)

@staff_member_required
def analytics_pages_view(request):
    """
    Полный список страниц за период с количеством просмотров (с поиском и пагинацией).
      GET:
        from, to — даты
        q — фильтр подсроки по path
        page — номер страницы
    """
    today = timezone.localdate()
    default_from = today - timedelta(days=13)
    default_to = today

    def _parse_date(s, default):
        try:
            return datetime.fromisoformat(s).date() if s else default
        except Exception:
            return default

    date_from = _parse_date(request.GET.get("from"), default_from)
    date_to = _parse_date(request.GET.get("to"), default_to)
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    q = (request.GET.get("q") or "").strip()

    base_qs = PageView.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    if q:
        base_qs = base_qs.filter(path__icontains=q)

    rows = (
        base_qs.values("path")
        .annotate(n=Count("id"))
        .order_by("-n", "path")
    )

    paginator = Paginator(rows, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    ctx = {
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
        "page_obj": page_obj,
    }
    return render(request, "repairs/analytics_pages.html", ctx)


@staff_member_required
def analytics_page_detail_view(request):
    """
    Деталка конкретной страницы: последние просмотры (время, реферер, IP, user_agent).
      GET:
        path — обязательный параметр (точное совпадение)
        from, to — опциональные даты
    """
    path = request.GET.get("path", "")
    if not path:
        # мягко переадресуем на список
        return render(request, "repairs/analytics_page_detail.html", {"error": "Не передан параметр path"})

    today = timezone.localdate()
    default_from = today - timedelta(days=13)
    default_to = today

    def _parse_date(s, default):
        try:
            return datetime.fromisoformat(s).date() if s else default
        except Exception:
            return default

    date_from = _parse_date(request.GET.get("from"), default_from)
    date_to = _parse_date(request.GET.get("to"), default_to)
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    qs = (PageView.objects
          .filter(path=path,
                  created_at__date__gte=date_from,
                  created_at__date__lte=date_to)
          .order_by("-created_at"))

    paginator = Paginator(qs, 100)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    ctx = {
        "path": path,
        "date_from": date_from,
        "date_to": date_to,
        "page_obj": page_obj,
        "total": qs.count(),
    }
    return render(request, "repairs/analytics_page_detail.html", ctx)