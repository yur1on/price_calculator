# repairs/signals.py
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
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


def _norm_phone(s: str) -> str:
    """Нормализация: только цифры, сравниваем по последним 9 цифрам."""
    digits = "".join(ch for ch in (s or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def _partner_phone_norm(partner: ReferralPartner) -> str:
    # partner.contact у продавцов может быть "@username", поэтому нормализация может дать пусто — это ок
    return _norm_phone(partner.contact or "")


def _find_partner_by_customer_phone(customer_phone: str) -> ReferralPartner | None:
    """
    Без изменения моделей у нас нет phone_norm поля, поэтому ищем в Python.
    Обычно партнёров не тысячи — это нормально.
    """
    target = _norm_phone(customer_phone)
    if not target:
        return None

    for p in ReferralPartner.objects.exclude(contact="").only("id", "contact", "name", "code"):
        if _partner_phone_norm(p) == target:
            return p
    return None


def _available_credit(partner: ReferralPartner) -> Decimal:
    """
    Доступные накопления партнёра:
      accrued (commission > 0)  -  abs(списания: commission < 0)
    Списания мы храним как отрицательные commission_amount в ReferralRedemption.
    """
    earned_accrued = (
        ReferralRedemption.objects
        .filter(partner=partner, status="accrued", commission_amount__gt=0)
        .aggregate(s=Sum("commission_amount"))["s"]
        or Decimal("0.00")
    )

    spent = (
        ReferralRedemption.objects
        .filter(partner=partner, commission_amount__lt=0)  # статус можно не проверять, но обычно будет paid
        .aggregate(s=Sum("commission_amount"))["s"]
        or Decimal("0.00")
    )  # spent отрицательное

    available = (Decimal(earned_accrued) + Decimal(spent)).quantize(Decimal("0.01"))
    return available


@receiver(pre_save, sender=Appointment)
def _track_prev_status(sender, instance: Appointment, **kwargs):
    if not instance.pk:
        instance._prev_status = None
        return
    try:
        prev = Appointment.objects.get(pk=instance.pk)
        instance._prev_status = prev.status
    except Appointment.DoesNotExist:
        instance._prev_status = None


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

    # ==========================================================
    # 1) РЕФЕРАЛКИ (как у вас) + анти-самореферал (комиссия 0)
    # ==========================================================
    code = (instance.referral_code or "").strip()
    if code:
        try:
            partner = ReferralPartner.objects.get(code__iexact=code)
        except ReferralPartner.DoesNotExist:
            partner = None

        if partner:
            discount, commission = calc_discount_and_commission(
                instance.price_original,
                partner.client_discount_pct,
                partner.partner_commission_pct,
            )

            # анти-самореферал:
            # если владелец кода = клиент по телефону -> комиссию не начисляем
            is_self = False
            pnorm = _partner_phone_norm(partner)
            if pnorm and pnorm == _norm_phone(instance.customer_phone):
                is_self = True
                commission = Decimal("0.00")

            # ВАЖНО: если self-referral — мы НЕ создаём redemption,
            # чтобы не занять пару (partner, appointment) и не мешать списанию накоплений на этот же ремонт.
            # Скидку клиенту вы уже применили через apply_referral() в модели/форме.
            if not is_self:
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
                                f"Накопления владельцу кода: {redemption.commission_amount} BYN\n"
                                f"Статус: {redemption.get_status_display()}"
                            ),
                        )
                    except Exception:
                        pass

    # ==========================================================
    # 2) ОТКАТ СПИСАНИЯ, если заявку отменили
    # ==========================================================
    prev_status = getattr(instance, "_prev_status", None)
    if prev_status != "cancelled" and instance.status == "cancelled":
        spend_row = (
            ReferralRedemption.objects
            .filter(appointment=instance, commission_amount__lt=0)
            .select_related("partner")
            .first()
        )
        if spend_row:
            spend_amount = (-spend_row.commission_amount).quantize(Decimal("0.01"))

            # возвращаем цену: снимаем только списание накоплений
            new_discount = (Decimal(instance.discount_amount or 0) - spend_amount).quantize(Decimal("0.01"))
            if new_discount < 0:
                new_discount = Decimal("0.00")
            new_price_final = (Decimal(instance.price_final or 0) + spend_amount).quantize(Decimal("0.01"))

            # удаляем строку списания
            spend_row.delete()

            # обновляем заявку (без рекурсии по instance.save)
            Appointment.objects.filter(pk=instance.pk).update(
                discount_amount=new_discount,
                price_final=new_price_final,
            )
        return

    # ==========================================================
    # 3) АВТОСПИСАНИЕ НАКОПЛЕНИЙ на ремонт владельца (ТОЛЬКО при создании)
    # ==========================================================
    if not created:
        return

    owner = _find_partner_by_customer_phone(instance.customer_phone)
    if not owner:
        return

    # если уже есть строка списания на эту заявку — ничего не делаем
    if ReferralRedemption.objects.filter(partner=owner, appointment=instance, commission_amount__lt=0).exists():
        return

    # доступные накопления
    try:
        with transaction.atomic():
            # блокируем строки по партнёру, чтобы два параллельных заказа не потратили один и тот же баланс
            ReferralRedemption.objects.select_for_update().filter(partner=owner)

            available = _available_credit(owner)
            if available <= 0:
                return

            to_spend = min(available, Decimal(instance.price_final or 0)).quantize(Decimal("0.01"))
            if to_spend <= 0:
                return

            # создаём строку "списания" (commission_amount отрицательное)
            ReferralRedemption.objects.create(
                partner=owner,
                appointment=instance,
                phone=instance.customer_phone,
                discount_amount=Decimal("0.00"),
                commission_amount=-to_spend,
                status="paid",  # трактуем как "использовано/закрыто"
                paid_at=timezone.now(),
            )

            new_discount = (Decimal(instance.discount_amount or 0) + to_spend).quantize(Decimal("0.01"))
            new_price_final = (Decimal(instance.price_final or 0) - to_spend).quantize(Decimal("0.01"))

            Appointment.objects.filter(pk=instance.pk).update(
                discount_amount=new_discount,
                price_final=new_price_final,
            )

        try:
            notify_partner(
                owner,
                (
                    "✅ Накопления применены к вашему ремонту\n"
                    f"Заявка #{instance.id}\n"
                    f"Списано накоплений: {to_spend} BYN\n"
                    f"Итог к оплате: {new_price_final} BYN"
                ),
            )
        except Exception:
            pass

    except Exception:
        # Не ломаем процесс записи, даже если что-то пошло не так
        return


# ==========================================================
# 4) ReferralRedemption: уведомления при смене статуса (как у вас)
# ==========================================================
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
    # начисление (earned)
    if getattr(instance, "_notify_to_accrued", False):
        a = instance.appointment
        try:
            notify_partner(
                instance.partner,
                (
                    "Начисление выполнено (добавлено в накопления)\n"
                    f"Заявка #{a.id} от {a.start:%d.%m.%Y}\n"
                    f"Сумма в накопления: {instance.commission_amount} BYN"
                ),
            )
        except Exception:
            pass
        instance._notify_to_accrued = False

    # "paid" теперь может быть и "списание" (commission < 0), и старое "выплачено"
    if getattr(instance, "_notify_to_paid", False):
        if instance.commission_amount < 0:
            # списание
            try:
                notify_partner(
                    instance.partner,
                    (
                        "Списание накоплений\n"
                        f"Заявка #{instance.appointment_id}\n"
                        f"Списано: {(-instance.commission_amount).quantize(Decimal('0.01'))} BYN\n"
                        f"Дата: {instance.paid_at:%d.%m.%Y %H:%M}"
                    ),
                )
            except Exception:
                pass
        else:
            # если где-то ещё используется "paid" как выплата — оставим нейтральный текст
            try:
                notify_partner(
                    instance.partner,
                    (
                        "Статус начисления изменён\n"
                        f"Заявка #{instance.appointment_id}\n"
                        f"Сумма: {instance.commission_amount} BYN\n"
                        f"Дата: {instance.paid_at:%d.%m.%Y %H:%M}"
                    ),
                )
            except Exception:
                pass
        instance._notify_to_paid = False
