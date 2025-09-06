import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_PARENS = re.compile(r"\(([^)]+)\)")

@register.filter
def shrink_parens(value: str) -> str:
    """
    Оборачивает любые (…в скобках…) в <span class="paren">(...)</span>.
    Пример: "iPhone 14 (A2890)" -> 'iPhone 14 <span class="paren">(A2890)</span>'
    """
    if not value:
        return ""
    html = _PARENS.sub(r'<span class="paren">(\1)</span>', str(value))
    return mark_safe(html)
