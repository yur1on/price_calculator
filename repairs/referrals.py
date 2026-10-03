"""Shared referral reads and enrollment. No second wallet or code namespace."""
import re
import random
import string
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum

from .models import ReferralPartner, ReferralRedemption


def gen_ref_code(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def full_phone(value):
    """Only complete international numbers; never match a nine-digit suffix."""
    value = (value or "").strip()
    if not re.fullmatch(r"\+?[0-9 ()-]+", value):
        return ""
    digits = re.sub(r"\D", "", value)
    return "+" + digits if 10 <= len(digits) <= 15 and not digits.startswith("0") else ""


def partners_for_phone(phone):
    return [p for p in ReferralPartner.objects.exclude(contact="") if full_phone(p.contact) == phone]


def create_partner(**values):
    values["name"] = values.get("name", "")[:ReferralPartner._meta.get_field("name").max_length]
    for _ in range(50):
        code = gen_ref_code()
        if code.startswith("PEND"):
            continue
        try:
            with transaction.atomic():
                return ReferralPartner.objects.create(code=code, **values)
        except IntegrityError:
            continue
    raise ValidationError("Не удалось создать уникальный код. Попробуйте ещё раз.")


def referral_balance(partner_id):
    qs = ReferralRedemption.objects.filter(partner_id=partner_id)
    totals = qs.aggregate(
        pending=Sum("commission_amount", filter=Q(status="pending", commission_amount__gt=0)),
        accrued=Sum("commission_amount", filter=Q(status="accrued", commission_amount__gt=0)),
        spent=Sum("commission_amount", filter=Q(commission_amount__lt=0)),
        uses=Count("pk", filter=Q(commission_amount__gt=0)),
        discount=Sum("discount_amount", filter=Q(commission_amount__gt=0)),
    )
    def money(key):
        return (totals[key] or Decimal("0.00")).quantize(Decimal("0.01"))
    pending, accrued, used = money("pending"), money("accrued"), -money("spent")
    return dict(available_balance=accrued-used, pending_balance=pending,
                used_balance=used, accrued_balance=accrued,
                uses=totals["uses"], total_discount=money("discount"),
                potential=accrued+pending-used)


def referral_operations(partner_id, limit=12):
    # Deliberately do not include appointment IDs or invited customers' data.
    return [dict(created_at=r.created_at, kind="🔻 Списание" if r.commission_amount < 0 else "➕ Начисление",
                 amount=abs(r.commission_amount), status=r.get_status_display())
            for r in ReferralRedemption.objects.filter(partner_id=partner_id).order_by("-created_at", "-pk")[:limit]]


def referral_operations_page(partner_id, page_number=1):
    """Paginated, presentation-only history; never return an ORM object to the client template."""
    rows = ReferralRedemption.objects.filter(partner_id=partner_id).only(
        "created_at", "commission_amount", "status",
    ).order_by("-created_at", "-pk")
    page = Paginator(rows, 12).get_page(page_number)
    statuses = {"pending": "Ожидает", "accrued": "Начислено", "paid": "Выплачено", "cancelled": "Отменено"}
    page.object_list = [dict(
        date=row.created_at, kind="Использование накоплений" if row.commission_amount < 0 else "Начисление",
        status="Использовано" if row.status == "paid" and row.commission_amount < 0 else statuses.get(row.status, "Обработано"),
        amount=abs(row.commission_amount), sign="−" if row.commission_amount < 0 else "+",
    ) for row in page.object_list]
    return page


def _client_profile(user):
    from accounts.models import AccountProfile
    if not user.is_authenticated or not user.is_active:
        raise ValidationError("Требуется активный аккаунт клиента.")
    profile = AccountProfile.objects.select_for_update().filter(user=user, role=AccountProfile.Role.CLIENT).first()
    if not profile or not profile.phone_verified_at or not full_phone(profile.phone):
        raise ValidationError("Подтвердите полный международный номер телефона клиента.")
    return profile


@transaction.atomic
def link_existing_participant(user):
    from accounts.models import AccountProfile
    from notify_tg.models import PartnerTelegram
    profile = _client_profile(user)
    if profile.referral_partner_id:
        return profile.referral_partner
    phone = full_phone(profile.phone)
    matches = partners_for_phone(phone)
    if len(matches) != 1:
        return None
    partner = ReferralPartner.objects.select_for_update().get(pk=matches[0].pk)
    # A free-text contact alone is not proof. Require an independently verified binding.
    if not PartnerTelegram.objects.filter(partner=partner, is_active=True,
            verified_phone=phone, phone_verified_at__isnull=False).exists():
        return None
    if AccountProfile.objects.filter(referral_partner=partner).exists():
        return None
    profile.referral_partner = partner
    profile.save(update_fields=["referral_partner", "updated_at"])
    return partner


@transaction.atomic
def enroll_client(user):
    from accounts.models import AccountProfile
    profile = _client_profile(user)
    if profile.referral_partner_id:
        return profile.referral_partner
    phone = full_phone(profile.phone)
    # Duplicate verified accounts must not mint independent wallets for the same owner.
    if any(full_phone(p.phone) == phone for p in AccountProfile.objects.exclude(pk=profile.pk)
           .filter(phone_verified_at__isnull=False)):
        raise ValidationError("Этот телефон используется другим аккаунтом. Обратитесь в мастерскую.")
    if partners_for_phone(phone):
        partner = link_existing_participant(user)
        if partner:
            return partner
        raise ValidationError("Найден существующий участник. Требуется проверка владения в мастерской.")
    partner = create_partner(name=user.get_full_name() or user.username, contact=phone)
    profile.referral_partner = partner
    profile.save(update_fields=["referral_partner", "updated_at"])
    return partner
