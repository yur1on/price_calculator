from django.db.models import Prefetch
from crm.models import CRMEvent, CRMOrder
from repairs.models import Appointment

CLIENT_EVENT_TYPES = (CRMEvent.Type.CREATED, CRMEvent.Type.STATUS)

def orders_for_client_account(user):
    profile = getattr(user, "account_profile", None)
    if not profile or not profile.crm_client_id:
        return CRMOrder.objects.none()
    safe_events = CRMEvent.objects.filter(event_type__in=CLIENT_EVENT_TYPES).only("order_id", "event_type", "new_value", "created_at")
    return (CRMOrder.objects.filter(client_id=profile.crm_client_id)
            .select_related("device")
            .prefetch_related("work_items", Prefetch("events", queryset=safe_events, to_attr="client_events")))

def appointments_for_client_account(user):
    if not getattr(user, "is_authenticated", False):
        return Appointment.objects.none()
    return (Appointment.objects.filter(account=user)
            .select_related("phone_model__brand", "repair_type", "crm_order__device")
            .prefetch_related("items__repair_type"))
