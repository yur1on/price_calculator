from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Appointment, ReferralPartner, ReferralRedemption
from .services import calc_discount_and_commission


@receiver(post_save, sender=Appointment)
def sync_referral_on_appointment_save(sender, instance: Appointment, created: bool, **kwargs):
    """
    Держим реферальное начисление в актуальном состоянии:
    - если есть referral_code и партнёр активен — создаём/обновляем Redemption;
    - статус Redemption:
        pending  — запись создана, но не выполнена;
        accrued  — запись выполнена (status == 'done'), деньги «начислены»;
        paid     — админом отмечено как выплачено (paid_at не трогаем).
    """
    code = (instance.referral_code or "").strip()
    if not code:
        return

    partner = ReferralPartner.objects.filter(code__iexact=code).first()
    if not partner:
        return

    # Пересчёт сумм от цены ДО скидки
    discount, commission = calc_discount_and_commission(
        instance.price_original,
        partner.client_discount_pct,
        partner.partner_commission_pct,
    )

    # Гарантируем уникальность (partner + appointment)
    redemption, _ = ReferralRedemption.objects.get_or_create(
        partner=partner,
        appointment=instance,
        defaults={
            "phone": instance.customer_phone,
            "discount_amount": discount,
            "commission_amount": commission,
            "status": "pending",
        },
    )

    changed = False

    # Синхронизация полей (на случай правок)
    if redemption.phone != instance.customer_phone:
        redemption.phone = instance.customer_phone
        changed = True
    if redemption.discount_amount != discount:
        redemption.discount_amount = discount
        changed = True
    if redemption.commission_amount != commission:
        redemption.commission_amount = commission
        changed = True

    # Логика статусов в зависимости от статуса записи
    if redemption.status != "paid":
        # paid — «терминальный» статус, не трогаем его сигналами
        if instance.status == "done" and redemption.status != "accrued":
            redemption.status = "accrued"
            changed = True
        elif instance.status == "cancelled" and redemption.status != "pending":
            # можно было бы удалять, но оставим как pending
            redemption.status = "pending"
            # paid_at не трогаем, т.к. paid сюда не должен попадать
            changed = True

    if changed:
        redemption.save()
