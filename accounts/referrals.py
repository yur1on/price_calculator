"""Client presentation assembled from the existing referral services."""
from decimal import Decimal
from django.conf import settings
from repairs.referrals import full_phone, referral_balance, referral_operations_page
from repairs.services import calc_discount_and_commission


def client_referral_context(profile, page=1):
    partner = profile.referral_partner
    context = {"profile": profile, "partner": partner,
               "can_enroll": bool(profile.phone_verified_at and full_phone(profile.phone)),
               "phone_verification_available": settings.DEBUG}
    if not partner:
        return context
    base = Decimal("200.00")
    discount, commission = calc_discount_and_commission(base, partner.client_discount_pct, partner.partner_commission_pct)
    context.update(
        balance=referral_balance(partner.pk), history=referral_operations_page(partner.pk, page),
        example={"base": base, "discount": discount, "payable": base-discount, "commission": commission},
        share_text=f"Ремонт в Tehsfera со скидкой {partner.client_discount_pct.normalize():f}%. "
                   f"При оформлении укажи мой код: {partner.code}",
        code_active=partner.is_active(),
    )
    return context
