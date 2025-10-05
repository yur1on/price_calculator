# repairs/templatetags/repairs_extras.py
import re
from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()
_PARENS = re.compile(r"\(([^)]+)\)")

@register.filter
def shrink_parens(value: str) -> str:
    if not value:
        return ""
    safe = conditional_escape(str(value))
    html = _PARENS.sub(r'<span class="paren">(\1)</span>', safe)
    return mark_safe(html)
