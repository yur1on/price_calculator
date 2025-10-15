# repairs/templatetags/repairs_extras.py
import re
from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()
_PARENS = re.compile(r"\(([^)]+)\)")

@register.filter
def shrink_parens(value: str) -> str:
    """
    Делает текст в скобках визуально тише:
    "iPhone 11 (2019)" -> 'iPhone 11 <span class="paren">(2019)</span>'
    """
    if not value:
        return ""
    safe = conditional_escape(str(value))
    html = _PARENS.sub(r'<span class="paren">(\1)</span>', safe)
    return mark_safe(html)

# -------------------------------
# НОВОЕ: человекочитаемая длительность
# -------------------------------

def _ru_plural(n: int, one: str, two: str, five: str) -> str:
    """
    Русское склонение: 1 час, 2 часа, 5 часов
    """
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return five
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return two
    return five

@register.filter(name="human_minutes")
def human_minutes(total_min):
    """
    Превращает минуты в компактный русский формат:
      120 -> '2 часа'
      150 -> '2ч 30мин'
      45  -> '45мин'
      0/None/некорректно -> '—'
    """
    try:
        m = int(total_min)
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"

    h, mm = divmod(m, 60)
    if mm == 0 and h > 0:
        return f"{h} {_ru_plural(h, 'час', 'часа', 'часов')}"
    parts = []
    if h > 0:
        parts.append(f"{h}ч")
    if mm > 0:
        parts.append(f"{mm}мин")
    return " ".join(parts)
