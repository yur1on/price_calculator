# repairs/signals.py
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Appointment, ReferralPartner, ReferralRedemption
from .services import calc_discount_and_commission
from notify_tg.utils import notify_partner


def _short_phone(p: str) -> str:
    p = (p or "").strip()
    return p if len(p) <= 5 else f"{p[:-4]}****"


# =========================
# 1) Appointment -> создаём/синхронизируем Redemption
#    и шлём ТОЛЬКО "Новая заявка..." (при первом создании)
# =========================
@receiver(post_save, sender=Appointment)
def sync_referral_on_appointment_save(sender, instance: Appointment, created: bool, **kwargs):
    code = (instance.referral_code or "").strip()
    if not code:
        return

    try:
        partner = ReferralPartner.objects.get(code__iexact=code)
    except ReferralPartner.DoesNotExist:
        return

    # Посчитать суммы по текущей цене/процентам
    discount, commission = calc_discount_and_commission(
        instance.price_original,
        partner.client_discount_pct,
        partner.partner_commission_pct,
    )

    # Создать/обновить начисление
    redemption, was_created = ReferralRedemption.objects.get_or_create(
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
    if redemption.discount_amount != discount:
        redemption.discount_amount = discount
        changed = True
    if redemption.commission_amount != commission:
        redemption.commission_amount = commission
        changed = True

    # Переводим в accrued, если заявка завершена.
    # Уведомление об «начислении» пошлёт отдельный сигнал на ReferralRedemption (см. ниже).
    if instance.status == "done" and redemption.status not in ("accrued", "paid"):
        redemption.status = "accrued"
        changed = True
    elif instance.status == "cancelled" and redemption.status != "pending":
        redemption.status = "pending"
        redemption.paid_at = None
        changed = True

    if changed:
        redemption.save(update_fields=["discount_amount", "commission_amount", "status", "paid_at"])

    # Сообщаем только о НОВОЙ заявке (чтобы не дублировать последующие статусы)
    if was_created:
        notify_partner(
            partner,
            (
                "Новая заявка с вашим кодом\n"
                f"Заявка #{instance.id}\n"
                f"Клиент: {instance.customer_name} ({_short_phone(instance.customer_phone)})\n"
                f"Услуга: {instance.repair_type.name}\n"
                f"Устройство: {instance.phone_model}\n"
                f"Дата/время: {instance.start:%d.%m.%Y %H:%M}\n"
                f"Скидка клиенту: {redemption.discount_amount} BYN\n"
                f"Комиссия партнёру: {redemption.commission_amount} BYN\n"
                f"Статус начисления: {redemption.get_status_display()}"
            ),
        )


# =========================
# 2) ReferralRedemption: ловим ПЕРЕХОДЫ статуса
#    - pending -> accrued  => «Начисление по заявке выполнено»
#    - (что угодно) -> paid => «Выплата произведена»
# =========================
@receiver(pre_save, sender=ReferralRedemption)
def _detect_status_transitions(sender, instance: ReferralRedemption, **kwargs):
    if not instance.pk:
        # Новый объект — переходов ещё нет
        return
    try:
        prev = ReferralRedemption.objects.get(pk=instance.pk)
    except ReferralRedemption.DoesNotExist:
        return

    # Флаг начисления
    instance._notify_to_accrued = (prev.status != "accrued" and instance.status == "accrued")

    # Флаг выплаты
    instance._notify_to_paid = (prev.status != "paid" and instance.status == "paid")
    if instance._notify_to_paid and not instance.paid_at:
        instance.paid_at = timezone.now()


@receiver(post_save, sender=ReferralRedemption)
def _notify_on_redemption_change(sender, instance: ReferralRedemption, created: bool, **kwargs):
    # Сообщение о начислении (перешло в accrued)
    if getattr(instance, "_notify_to_accrued", False):
        a = instance.appointment
        notify_partner(
            instance.partner,
            (
                "Начисление по заявке выполнено\n"
                f"Заявка #{a.id} от {a.start:%d.%m.%Y}\n"
                f"Комиссия: {instance.commission_amount} BYN (статус: {instance.get_status_display()})"
            ),
        )
        instance._notify_to_accrued = False

    # Сообщение о выплате (перешло в paid)
    if getattr(instance, "_notify_to_paid", False):
        notify_partner(
            instance.partner,
            (
                "Выплата произведена\n"
                f"Заявка #{instance.appointment_id}\n"
                f"Комиссия: {instance.commission_amount} BYN\n"
                f"Дата выплаты: {instance.paid_at:%d.%m.%Y %H:%M}"
            ),
        )
        instance._notify_to_paid = False
