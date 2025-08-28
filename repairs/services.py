"""Сервисные функции для расчёта скидок и комиссий."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def _q2(x: Decimal) -> Decimal:
    """Округление до 2 знаков (банковское)."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



# repairs/services.py
from decimal import Decimal

def quantize_money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(Decimal("0.01"))

def calc_discount_and_commission(price: Decimal,
                                 client_discount_pct: Decimal,
                                 partner_commission_pct: Decimal) -> tuple[Decimal, Decimal]:
    price = Decimal(price or 0)
    d_pct = Decimal(client_discount_pct or 0) / Decimal("100")
    c_pct = Decimal(partner_commission_pct or 0) / Decimal("100")

    discount = quantize_money(price * d_pct)
    commission = quantize_money(price * c_pct)
    return discount, commission
