from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from finance.models import PartItem, RepairFinance, RepairPart

from .models import CRMEvent, CRMOrder, CRMOrderPartUsage


def add_event(order, event_type, description, author=None, old_value="", new_value=""):
    return CRMEvent.objects.create(
        order=order, author=author, event_type=event_type, description=description,
        old_value=str(old_value or "")[:255], new_value=str(new_value or "")[:255],
    )


@transaction.atomic
def change_status(order, new_status, author):
    if new_status not in order.allowed_next_statuses:
        raise ValidationError("Этот переход статуса недоступен.")
    if new_status == CRMOrder.Status.READY and not order.work_items.exists():
        raise ValidationError("Перед готовностью добавьте хотя бы одну выполненную работу.")
    old_status = order.status
    now = timezone.now()
    order.status = new_status
    fields = ["status", "updated_at"]
    if new_status == CRMOrder.Status.READY and not order.ready_at:
        order.ready_at = now
        fields.append("ready_at")
    if new_status == CRMOrder.Status.ISSUED and not order.issued_at:
        order.issued_at = now
        fields.extend(["issued_at", "warranty_started_at"])
        if order.warranty_days and not order.warranty_started_at:
            order.warranty_started_at = now
    order.save(update_fields=fields)
    add_event(
        order, CRMEvent.Type.STATUS, "Статус заказа изменён.", author,
        CRMOrder.Status(old_status).label, CRMOrder.Status(new_status).label,
    )
    if new_status in {
        CRMOrder.Status.APPROVAL, CRMOrder.Status.WAITING_PART,
        CRMOrder.Status.READY, CRMOrder.Status.ISSUED,
    }:
        order_id = order.pk
        transaction.on_commit(lambda: _notify_order_status_safely(order_id, new_status))
    return order


def _notify_order_status_safely(order_id, status):
    try:
        from notify_tg.services import notify_order_status
        notify_order_status(order_id, status)
    except Exception:
        # Telegram must never roll back or break the repair workflow.
        import logging
        logging.getLogger(__name__).exception("CRM Telegram notification failed for order_id=%s", order_id)


def _finance_repair_for_order(order):
    if order.finance_repair_id:
        return order.finance_repair
    existing = RepairFinance.objects.filter(crm_part_usages__order=order).distinct().first()
    if existing:
        order.finance_repair = existing
        order.save(update_fields=["finance_repair", "updated_at"])
        return existing
    if not order.employee_id:
        raise ValidationError("Сначала назначьте мастера на ремонт.")
    repair = RepairFinance.objects.create(
        date=timezone.localdate(), description=f"{order.number} · {order.device.display_name}",
        revenue=order.agreed_price, part_cost=0, manual_part_cost=0,
        employee=order.employee, master_percent=order.employee.default_percent,
        comment=f"Создано автоматически из CRM-заказа {order.number}.",
    )
    order.finance_repair = repair
    order.save(update_fields=["finance_repair", "updated_at"])
    return repair


def sync_finance_repair(order):
    repair = order.finance_repair if order.finance_repair_id else RepairFinance.objects.filter(crm_part_usages__order=order).distinct().first()
    if not repair or not order.employee_id:
        return repair
    if not order.finance_repair_id:
        order.finance_repair = repair
        order.save(update_fields=["finance_repair", "updated_at"])
    repair.revenue = order.paid_amount if order.status == CRMOrder.Status.ISSUED else order.agreed_price
    repair.employee = order.employee
    repair.master_percent = 0 if order.employee.is_owner else order.employee.default_percent
    repair.description = f"{order.number} · {order.device.display_name}"
    repair.save()
    repair.recalculate_part_cost()
    return repair


@transaction.atomic
def issue_order(order_id, *, final_price, paid_amount, warranty_days, author):
    order = CRMOrder.objects.select_for_update().select_related("client", "device", "employee", "finance_repair").get(pk=order_id)
    if order.status != CRMOrder.Status.READY:
        raise ValidationError("Выдача доступна только для ремонта со статусом «Готово к выдаче».")
    if not order.employee_id:
        raise ValidationError("Перед выдачей назначьте исполнителя.")
    order.final_price = final_price
    order.paid_amount = paid_amount
    order.warranty_days = warranty_days
    order.save(update_fields=["final_price", "paid_amount", "warranty_days", "updated_at"])

    repair = _finance_repair_for_order(order)
    repair = RepairFinance.objects.select_for_update().get(pk=repair.pk)
    repair.revenue = paid_amount
    repair.employee = order.employee
    repair.master_percent = 0 if order.employee.is_owner else order.employee.default_percent
    repair.description = f"{order.number} · {order.device.display_name}"
    repair.save()
    repair.recalculate_part_cost()
    change_status(order, CRMOrder.Status.ISSUED, author)
    return order


@transaction.atomic
def install_part(order, part_item_id, author):
    order = CRMOrder.objects.select_for_update().select_related("employee", "device").get(pk=order.pk)
    item = PartItem.objects.select_for_update().select_related("receipt__part", "receipt__supplier").get(pk=part_item_id)
    if item.status != PartItem.Status.IN_STOCK:
        raise ValidationError("Деталь уже отсутствует на складе.")
    repair = _finance_repair_for_order(order)
    usage = CRMOrderPartUsage.objects.create(
        order=order, part_item=item, finance_repair=repair, unit_cost_snapshot=item.unit_cost,
        supplier_name_snapshot=item.receipt.supplier.name,
        warranty_days_snapshot=item.receipt.warranty_days, installed_by=author,
    )
    RepairPart.objects.create(repair=repair, part_item=item, cost_used=usage.unit_cost_snapshot, installed_at=timezone.localdate())
    add_event(order, CRMEvent.Type.PART, f"Добавлена деталь: {item.receipt.part} · #{item.inventory_code}. Склад: −1.", author)
    return usage


@transaction.atomic
def return_part(usage, author):
    usage = CRMOrderPartUsage.objects.select_for_update().select_related("order", "part_item", "finance_repair", "part_item__receipt__part").get(pk=usage.pk)
    if usage.status != CRMOrderPartUsage.Status.INSTALLED:
        raise ValidationError("Деталь уже была снята с ремонта.")
    item = PartItem.objects.select_for_update().get(pk=usage.part_item_id)
    repair_part = RepairPart.objects.select_for_update().filter(repair=usage.finance_repair, part_item=item).first()
    if repair_part:
        repair_part.delete()
    else:
        item.status = PartItem.Status.IN_STOCK
        item.save(update_fields=["status"])
    usage.status = CRMOrderPartUsage.Status.REMOVED
    usage.removed_at = timezone.now()
    usage.removed_by = author
    usage.save(update_fields=["status", "removed_at", "removed_by"])
    add_event(usage.order, CRMEvent.Type.PART, f"Деталь снята: {usage.part_item.receipt.part} · #{usage.part_item.inventory_code}. Возвращена на склад: +1.", author)
    return usage
