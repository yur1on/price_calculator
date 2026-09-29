from .models import CRMEvent, CRMOrder


CLIENT_STATUS = {
    CRMOrder.Status.ACCEPTED: ("Принят", "Устройство принято мастерской."),
    CRMOrder.Status.DIAGNOSTIC: ("Диагностика", "Мастер проводит диагностику устройства."),
    CRMOrder.Status.APPROVAL: ("Согласование", "Ожидается согласование дальнейших работ."),
    CRMOrder.Status.WAITING_PART: ("Ожидает запчасть", "Для продолжения ремонта ожидается запчасть."),
    CRMOrder.Status.REPAIR: ("В ремонте", "Устройство находится в работе у мастера."),
    CRMOrder.Status.READY: ("Готов к выдаче", "Ремонт готов. Устройство можно забрать в мастерской."),
    CRMOrder.Status.ISSUED: ("Ремонт завершён", "Устройство выдано клиенту."),
    CRMOrder.Status.CANCELED: ("Ремонт отменён", "Работы по ремонту завершены без выдачи результата ремонта."),
}

STATUS_LABEL_TO_CODE = {
    CRMOrder.Status(code).label: code for code in CLIENT_STATUS
}


def client_status(order):
    label, description = CLIENT_STATUS[order.status]
    return {"code": order.status, "label": label, "description": description}


def client_timeline(order):
    rows = []
    for event in order.events.filter(
        event_type__in=(CRMEvent.Type.CREATED, CRMEvent.Type.STATUS),
    ).only("event_type", "new_value", "created_at").order_by("created_at", "id"):
        code = CRMOrder.Status.ACCEPTED if event.event_type == CRMEvent.Type.CREATED else STATUS_LABEL_TO_CODE.get(event.new_value)
        if code in CLIENT_STATUS:
            rows.append({"created_at": event.created_at, "label": CLIENT_STATUS[code][0]})
    return rows
