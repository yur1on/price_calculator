"""Phone-proof gate shared by the Telegram referral commands."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from repairs.models import ReferralPartner
from repairs.referrals import full_phone, partners_for_phone, create_partner, gen_ref_code
from .models import PartnerTelegram


def verified_partner(chat_id):
    binding = PartnerTelegram.objects.select_related("partner").filter(
        chat_id=chat_id, is_active=True, phone_verified_at__isnull=False,
    ).first()
    if binding and binding.verified_phone and binding.verified_phone == full_phone(binding.partner.contact):
        return binding.partner
    return None


@transaction.atomic
def confirm_owner(*, chat_id, user_id, contact_user_id, phone, name):
    # Telegram request_contact is meaningful only in a private chat, for that user.
    if not user_id or chat_id != user_id or contact_user_id != user_id:
        raise ValidationError("Отправьте свой номер кнопкой в личном чате с ботом.")
    phone = full_phone(phone)
    if not phone:
        raise ValidationError("Нужен полный международный номер телефона.")
    matches = partners_for_phone(phone)
    if len(matches) > 1:
        raise ValidationError("Номер связан с несколькими участниками. Обратитесь в мастерскую.")
    current = PartnerTelegram.objects.select_for_update().filter(chat_id=chat_id).first()
    if matches:
        partner = ReferralPartner.objects.select_for_update().get(pk=matches[0].pk)
        if current and current.partner_id != partner.pk:
            raise ValidationError("Этот Telegram уже связан с другим участником. Обратитесь в мастерскую.")
    elif current:
        partner = ReferralPartner.objects.select_for_update().get(pk=current.partner_id)
        # Legacy placeholders may complete enrollment; never overwrite a real owner's phone.
        if not partner.code.startswith("PEND"):
            raise ValidationError("Номер не совпадает с владельцем. Обратитесь в мастерскую.")
        partner.contact = phone
        partner.save(update_fields=["contact"])
    else:
        partner = create_partner(name=name or "Участник Telegram", contact=phone)
    if partner.code.startswith("PEND"):
        from django.db import IntegrityError
        for _ in range(50):
            code = gen_ref_code()
            if code.startswith("PEND"):
                continue
            try:
                with transaction.atomic():
                    partner.code = code
                    partner.save(update_fields=["code"])
                break
            except IntegrityError:
                continue
        else:
            raise ValidationError("Не удалось выдать код. Попробуйте позже.")
    PartnerTelegram.objects.update_or_create(partner=partner, defaults={
        "chat_id": chat_id, "is_active": True, "verified_phone": phone,
        "phone_verified_at": timezone.now(),
    })
    return partner
