import logging

from django.core.cache import cache
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from .client_status import client_status, client_timeline
from .forms import PublicRepairStatusForm
from .models import CRMOrder

logger = logging.getLogger(__name__)
MAX_FAILED_ATTEMPTS = 10
RATE_LIMIT_SECONDS = 600


@never_cache
def repair_status(request):
    form = PublicRepairStatusForm(request.POST or None)
    order = None
    not_found = False
    remote_addr = request.META.get("REMOTE_ADDR", "unknown")
    cache_key = f"repair-status-failures:{remote_addr}"
    blocked = cache.get(cache_key, 0) >= MAX_FAILED_ATTEMPTS

    if request.method == "POST":
        if blocked:
            not_found = True
        elif form.is_valid():
            order = (
                CRMOrder.objects
                .select_related("device")
                .prefetch_related("work_items")
                .filter(number=form.cleaned_data["order_number"])
                .first()
            )
            if order is None:
                not_found = True
                try:
                    failures = cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, 1, RATE_LIMIT_SECONDS)
                    failures = 1
                if failures == MAX_FAILED_ATTEMPTS:
                    logger.warning("Public repair status rate limit reached for remote_addr=%s", remote_addr)
            else:
                cache.delete(cache_key)

        # Never echo the public identifier back into the form after a lookup.
        form = PublicRepairStatusForm()

    context = {"form": form, "order": order, "not_found": not_found}
    if order:
        context.update({"public_status": client_status(order), "timeline": client_timeline(order)})
    return render(request, "crm/public_repair_status.html", context)
