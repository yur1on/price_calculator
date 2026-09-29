from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from accounts.access import is_tehsfera_admin


def can_access_crm(user):
    return user.is_authenticated and user.is_active and user.has_perm("crm.access_crm")


def crm_permission_required(permission="crm.access_crm"):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.is_active or not (is_tehsfera_admin(request.user) or request.user.has_perm(permission)):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
