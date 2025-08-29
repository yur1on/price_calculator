# repairs/signals.py
from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Appointment, ReferralPartner, ReferralRedemption
from .services import calc_discount_and_commission
from notify_tg.utils import notify_partner

# Пытаемся импортировать функции для уведомлений админам (могут отсутствовать).
try:
    from notify_tg.utils import notify_admins, admin_appointment_link  # type: ignore
except Exception:  # pragma: no cover
    def notify_admins(text: str) -> int:  # type: ignore
        return 0
    def admin_appointment_link(appointment_id: int) -> str:  # type: ignore
        return f"/admin/repairs/appointment/{appointment_id}/change/"


def _short_phone(p: str) -> str:
    p = (p or "").strip()
    return p if len(p) <= 5 else f"{p[:-4]}****"


# =========================
# 1) Appointment:
#    - ВСЕГДА шлём админам уведомление при создании;
#    - создаём/синхронизируем Redemption (если указан referral_code);
#    - партнёру шлём «Новая заявка...» только при первом создании Redemption.
# =========================
@receiver(post_save, sender=Appointment)
def sync_referral_on_appointment_save(sender, instance: Appointment, created: bool, **kwargs):
    # --- уведомление админам о ЛЮБОЙ новой заявке ---
    if created:
        a = instance
        admin_msg = (
            "Новая заявка\n"
            f"ID: #{a.id}\n"
            f"Клиент: {a.customer_name} ({_short_phone(a.customer_phone)})\n"
            f"Устройство: {a.phone_model}\n"
            f"Услуга: {a.repair_type.name}\n"
            f"Дата/время: {a.start:%d.%m.%Y %H:%M}\n"
            f"Итоговая цена: {a.price_final} BYN"
            + (f"\nПартнёрский код: {a.referral_code}" if a.referral_code else "")
            + f"\nАдминка: {admin_appointment_link(a.id)}"
        )
        try:
            notify_admins(admin_msg)
        except Exception:
            pass

    # --- рефералки ---
    code = (instance.referral_code or "").strip()
    if not code:
        return

    try:
        partner = ReferralPartner.objects.get(code__iexact=code)
    except ReferralPartner.DoesNotExist:
        return

    discount, commission = calc_discount_and_commission(
        instance.price_original,
        partner.client_discount_pct,
        partner.partner_commission_pct,
    )

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

    if instance.status == "done" and redemption.status not in ("accrued", "paid"):
        redemption.status = "accrued"
        changed = True
    elif instance.status == "cancelled" and redemption.status != "pending":
        redemption.status = "pending"
        redemption.paid_at = None
        changed = True

    if changed:
        redemption.save(update_fields=["discount_amount", "commission_amount", "status", "paid_at"])

    if was_created:
        try:
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
        except Exception:
            pass


# =========================
# 2) ReferralRedemption: ловим ПЕРЕХОДЫ статуса
#    - pending -> accrued  => «Начисление по заявке выполнено»
#    - * -> paid           => «Выплата произведена»
# =========================
@receiver(pre_save, sender=ReferralRedemption)
def _detect_status_transitions(sender, instance: ReferralRedemption, **kwargs):
    if not instance.pk:
        return
    try:
        prev = ReferralRedemption.objects.get(pk=instance.pk)
    except ReferralRedemption.DoesNotExist:
        return

    instance._notify_to_accrued = (prev.status != "accrued" and instance.status == "accrued")
    instance._notify_to_paid = (prev.status != "paid" and instance.status == "paid")
    if instance._notify_to_paid and not instance.paid_at:
        instance.paid_at = timezone.now()


@receiver(post_save, sender=ReferralRedemption)
def _notify_on_redemption_change(sender, instance: ReferralRedemption, created: bool, **kwargs):
    if getattr(instance, "_notify_to_accrued", False):
        a = instance.appointment
        try:
            notify_partner(
                instance.partner,
                (
                    "Начисление по заявке выполнено\n"
                    f"Заявка #{a.id} от {a.start:%d.%m.%Y}\n"
                    f"Комиссия: {instance.commission_amount} BYN (статус: {instance.get_status_display()})"
                ),
            )
        except Exception:
            pass
        instance._notify_to_accrued = False

    if getattr(instance, "_notify_to_paid", False):
        try:
            notify_partner(
                instance.partner,
                (
                    "Выплата произведена\n"
                    f"Заявка #{instance.appointment_id}\n"
                    f"Комиссия: {instance.commission_amount} BYN\n"
                    f"Дата выплаты: {instance.paid_at:%d.%m.%Y %H:%M}"
                ),
            )
        except Exception:
            pass
        instance._notify_to_paid = False
