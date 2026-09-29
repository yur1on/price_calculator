from django.contrib.auth.models import Group, Permission
from django.conf import settings
from django.db import transaction
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
import logging, secrets

from finance.models import Employee

from .models import AccountProfile, PhoneVerification
from crm.models import CRMClient, normalize_phone

logger = logging.getLogger(__name__)


MASTER_GROUP = "Tehsfera Master"
CLIENT_GROUP = "Tehsfera Client"
ADMIN_GROUP = "Tehsfera Admin"


def configure_groups():
    admin_group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    master_group, _ = Group.objects.get_or_create(name=MASTER_GROUP)
    Group.objects.get_or_create(name=CLIENT_GROUP)
    master_group.permissions.set(Permission.objects.filter(
        content_type__app_label="crm",
        codename__in=[
            "access_crm", "view_all_orders", "view_crmorder", "add_crmorder", "change_crmorder",
            "view_crmclient", "add_crmclient", "change_crmclient",
            "view_crmdevice", "add_crmdevice", "change_crmdevice",
            "change_order_status", "manage_documents",
        ],
    ))
    admin_group.permissions.set(Permission.objects.filter(content_type__app_label__in=["crm", "finance"]))
    return admin_group, master_group


@transaction.atomic
def approve_master(profile):
    if profile.role != AccountProfile.Role.MASTER:
        raise ValueError("Одобрить можно только профиль мастера.")
    employee, _ = Employee.objects.get_or_create(
        user=profile.user,
        defaults={"name": profile.user.get_full_name() or profile.user.username, "default_percent": "35.00", "is_active": True},
    )
    if not employee.is_active:
        employee.is_active = True
        employee.save(update_fields=["is_active"])
    _, group = configure_groups()
    profile.user.groups.add(group)
    profile.approval_status = AccountProfile.Approval.APPROVED
    profile.save(update_fields=["approval_status", "updated_at"])
    return employee


@transaction.atomic
def reject_master(profile):
    profile.approval_status = AccountProfile.Approval.REJECTED
    profile.save(update_fields=["approval_status", "updated_at"])
    group = Group.objects.filter(name=MASTER_GROUP).first()
    if group:
        profile.user.groups.remove(group)
    try:
        employee = profile.user.employee_profile
    except Employee.DoesNotExist:
        employee = None
    if employee and employee.is_active:
        employee.is_active = False
        employee.save(update_fields=["is_active"])


def link_verified_client(profile):
    if not profile.phone_verified_at:
        return "unverified"
    matches = CRMClient.objects.filter(normalized_phone=normalize_phone(profile.phone))
    if matches.count() == 1:
        profile.crm_client = matches.first()
        profile.save(update_fields=["crm_client", "updated_at"])
        return "linked"
    return "multiple" if matches.exists() else "missing"


def request_phone_verification(profile):
    phone = normalize_phone(profile.phone)
    now = timezone.now()
    latest = PhoneVerification.objects.filter(user=profile.user).first()
    if latest and latest.created_at > now - timedelta(seconds=60):
        raise ValidationError("Новый код можно запросить через минуту.")
    if PhoneVerification.objects.filter(user=profile.user, created_at__gte=now-timedelta(hours=1)).count() >= 5:
        raise ValidationError("Слишком много запросов. Попробуйте позже.")
    code = f"{secrets.randbelow(1000000):06d}"
    verification = PhoneVerification.objects.create(user=profile.user, phone=phone, code_hash=make_password(code), expires_at=now+timedelta(minutes=10))
    if settings.DEBUG:
        logger.info("Development phone verification code for user %s: %s", profile.user_id, code)
    return verification, code


@transaction.atomic
def verify_phone_code(profile, code):
    verification = PhoneVerification.objects.select_for_update().filter(user=profile.user, phone=normalize_phone(profile.phone), verified_at__isnull=True).first()
    if not verification or not verification.is_active:
        raise ValidationError("Код истёк или недействителен.")
    verification.attempts += 1
    if not check_password(code, verification.code_hash):
        verification.save(update_fields=["attempts"])
        raise ValidationError("Неверный код подтверждения.")
    now = timezone.now(); verification.verified_at = now
    verification.save(update_fields=["attempts", "verified_at"])
    profile.phone_verified_at = now
    profile.save(update_fields=["phone_verified_at", "updated_at"])
    return link_verified_client(profile)
