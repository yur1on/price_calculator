from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def money_byn(value):
    try:
        number = Decimal(value or 0)
    except Exception:
        number = Decimal("0")
    return f"{number:,.2f}".replace(",", " ") + " BYN"
