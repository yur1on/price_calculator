"""Сервисные функции для расчёта скидок и комиссий."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def _q2(x: Decimal) -> Decimal:
    """Округление до 2 знаков (банковское)."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calc_discount_and_commission(
    price_original: Decimal,
    client_discount_pct: Decimal,
    partner_commission_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Возвращает (скидка_клиенту, комиссия_партнёру) — обе суммы считаются от цены ДО скидки.
    """
    discount = _q2(price_original * client_discount_pct / Decimal("100"))
    commission = _q2(price_original * partner_commission_pct / Decimal("100"))
    return discount, commission
