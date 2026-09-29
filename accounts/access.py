from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

from .models import AccountProfile


def profile_for(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.account_profile
    except AccountProfile.DoesNotExist:
        return None


def is_tehsfera_admin(user):
    profile = profile_for(user)
    return bool(user.is_authenticated and user.is_active and (
        user.is_superuser or (profile and profile.role == AccountProfile.Role.ADMIN)
    ))


def master_employee(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    try:
        employee = user.employee_profile
    except Exception:
        return None
    profile = profile_for(user)
    approved = not profile or (
        profile.role == AccountProfile.Role.MASTER and profile.approval_status == AccountProfile.Approval.APPROVED
    )
    return employee if approved and employee.is_active and not employee.is_owner else None


def is_approved_master(user):
    return master_employee(user) is not None


def is_client(user):
    profile = profile_for(user)
    return bool(profile and profile.role == AccountProfile.Role.CLIENT)


def account_landing_url(user):
    """Return the safe first page for an authenticated account."""
    if is_tehsfera_admin(user):
        return reverse("workbench")
    if is_approved_master(user):
        return reverse("crm:work_queue")
    if is_client(user):
        return reverse("accounts:client_dashboard")
    profile = profile_for(user)
    if profile and profile.role == AccountProfile.Role.MASTER:
        return reverse("accounts:registration_pending")
    return reverse("accounts:workspace")


def workspace_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not request.user.is_active:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def approved_master_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        employee = master_employee(request.user)
        if employee is None:
            raise PermissionDenied
        request.master_employee = employee
        return view(request, *args, **kwargs)
    return wrapped
