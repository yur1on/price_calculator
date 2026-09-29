from .access import account_landing_url, is_approved_master, is_client, is_tehsfera_admin, profile_for


def account_access(request):
    if not request.user.is_authenticated:
        return {}
    profile = profile_for(request.user)
    context = {
        "account_profile": profile,
        "account_is_admin": is_tehsfera_admin(request.user),
        "account_is_master": is_approved_master(request.user),
        "account_is_client": is_client(request.user),
        "account_home_url": account_landing_url(request.user),
        "account_is_pending_master": bool(
            profile
            and profile.role == profile.Role.MASTER
            and profile.approval_status == profile.Approval.PENDING
        ),
        "account_is_rejected_master": bool(
            profile
            and profile.role == profile.Role.MASTER
            and profile.approval_status == profile.Approval.REJECTED
        ),
        "account_role_label": profile.get_role_display() if profile else ("Администратор" if request.user.is_superuser else "Сотрудник"),
    }
    if context["account_is_admin"] or request.user.has_perm("crm.access_crm"):
        from django.utils import timezone
        from repairs.models import Appointment
        context["upcoming_appointments_badge"] = Appointment.objects.filter(
            status__in=["new", "confirmed"], start__gte=timezone.now(), crm_order__isnull=True,
        ).count()
    return context
