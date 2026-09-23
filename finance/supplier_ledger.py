from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.urls import reverse

from .models import StockReceipt, SupplierPayment, SupplierReturn, WarrantyClaim, money


ZERO = Decimal("0.00")


@dataclass
class LedgerRow:
    date: date
    operation_type: str
    operation_label: str
    document: str = ""
    description: str = ""
    device_model: str = ""
    quantity: int | None = None
    unit_price: Decimal | None = None
    debit: Decimal = ZERO
    return_amount: Decimal = ZERO
    payment: Decimal = ZERO
    adjustment: Decimal = ZERO
    credit_category: str = ""
    comment: str = ""
    source_url: str = ""
    stable_key: tuple = ()
    balance: Decimal = ZERO

    @property
    def search_text(self):
        return " ".join((self.document, self.description, self.device_model, self.comment)).lower()

    @property
    def financial_delta(self):
        return money(self.debit - self.return_amount - self.payment + self.adjustment)


def _all_rows(supplier):
    rows = []
    receipts = StockReceipt.objects.filter(supplier=supplier).select_related("part")
    for item in receipts:
        rows.append(LedgerRow(
            date=item.date, operation_type="receipt", operation_label="Поставка",
            document=item.order_number, description=item.part.name,
            device_model=" ".join(filter(None, [item.part.brand, item.part.device_model])),
            quantity=item.quantity, unit_price=item.unit_cost, debit=item.total_cost,
            comment=item.comment, source_url=reverse("finance:receipt_edit", args=[item.pk]),
            stable_key=(10, item.pk),
        ))
    for item in SupplierPayment.objects.filter(supplier=supplier).select_related("receipt"):
        method = item.get_payment_method_display()
        rows.append(LedgerRow(
            date=item.date, operation_type="payment", operation_label="Оплата",
            document=item.document_number or (item.receipt.order_number if item.receipt else ""),
            description=method, payment=item.amount, comment=item.comment,
            source_url=reverse("finance:supplier_payment_edit", args=[item.pk]), stable_key=(20, item.pk),
        ))
    for item in SupplierReturn.objects.filter(supplier=supplier).select_related("part_item__receipt__part", "replacement_part_item"):
        part = item.part_item.receipt.part
        rows.append(LedgerRow(
            date=item.date, operation_type="return", operation_label="Возврат поставщику",
            document=item.document_number or item.part_item.receipt.order_number,
            description=f"{part.name} · #{item.part_item.inventory_code}",
            device_model=" ".join(filter(None, [part.brand, part.device_model])), quantity=1,
            unit_price=item.part_item.unit_cost, comment=f"{item.reason}. {item.get_status_display()}. {item.comment}".strip(),
            source_url=reverse("finance:supplier_return_edit", args=[item.pk]), stable_key=(30, item.pk),
        ))
        if item.financial_amount > 0 and item.status in {SupplierReturn.Status.REFUNDED, SupplierReturn.Status.CREDITED}:
            rows.append(LedgerRow(
                date=item.financial_date or item.date, operation_type="adjustment", operation_label=item.get_status_display(),
                document=item.document_number, description=f"{part.name} · #{item.part_item.inventory_code}",
                device_model=" ".join(filter(None, [part.brand, part.device_model])), return_amount=item.financial_amount,
                credit_category="return",
                comment=item.comment, source_url=reverse("finance:supplier_return_edit", args=[item.pk]), stable_key=(40, item.pk),
            ))
        if item.status == SupplierReturn.Status.REPLACED:
            rows.append(LedgerRow(
                date=item.financial_date or item.date, operation_type="replacement", operation_label="Замена детали",
                document=item.document_number, description=f"{part.name} · #{item.part_item.inventory_code}",
                device_model=" ".join(filter(None, [part.brand, part.device_model])),
                comment=f"Без денежного движения. Новая деталь: #{item.replacement_part_item.inventory_code}" if item.replacement_part_item else "Без денежного движения",
                source_url=reverse("finance:supplier_return_edit", args=[item.pk]), stable_key=(50, item.pk),
            ))
    claims = WarrantyClaim.objects.filter(part_item__receipt__supplier=supplier).select_related("part_item__receipt__part", "replacement_part_item")
    for item in claims:
        part = item.part_item.receipt.part
        common = dict(document=item.part_item.receipt.order_number, description=f"{part.name} · #{item.part_item.inventory_code}", device_model=" ".join(filter(None, [part.brand, part.device_model])), source_url=reverse("finance:warranty_edit", args=[item.pk]))
        rows.append(LedgerRow(date=item.opened_at, operation_type="warranty", operation_label="Гарантийный случай", comment=item.reason, stable_key=(60, item.pk), **common))
        if item.sent_to_supplier_at:
            rows.append(LedgerRow(date=item.sent_to_supplier_at, operation_type="warranty", operation_label="Отправлено по гарантии", comment=item.defect_description, stable_key=(61, item.pk), **common))
        decision_date = item.financial_resolution_date or item.closed_at
        if item.status == WarrantyClaim.Status.REPLACED and decision_date:
            replacement = f"#{item.replacement_part_item.inventory_code}" if item.replacement_part_item else "не указана"
            rows.append(LedgerRow(date=decision_date, operation_type="replacement", operation_label="Заменено по гарантии", comment=f"Новая деталь: {replacement}. Без денежного движения", stable_key=(62, item.pk), **common))
        if item.status == WarrantyClaim.Status.REFUNDED and item.financial_resolution_amount > 0 and decision_date:
            rows.append(LedgerRow(date=decision_date, operation_type="adjustment", operation_label="Гарантийный возврат денег", return_amount=item.financial_resolution_amount, credit_category="warranty", comment=item.supplier_decision, stable_key=(63, item.pk), **common))
    return sorted(rows, key=lambda row: (row.date, row.stable_key))


def supplier_ledger(supplier, start=None, end=None, query="", operation_type="all"):
    all_rows = _all_rows(supplier)
    balance = ZERO
    for row in all_rows:
        balance = money(balance + row.financial_delta)
        row.balance = balance
    start = start or date.min
    end = end or date.max
    opening = money(sum((row.financial_delta for row in all_rows if row.date < start), ZERO))
    period_rows = [row for row in all_rows if start <= row.date <= end]
    financial_rows = period_rows
    summary = {
        "opening": opening,
        "received": money(sum((row.debit for row in financial_rows), ZERO)),
        "quantity": sum((row.quantity or 0 for row in financial_rows if row.operation_type == "receipt")),
        "paid": money(sum((row.payment for row in financial_rows), ZERO)),
        "returns": money(sum((row.return_amount for row in financial_rows if row.credit_category == "return"), ZERO)),
        "warranty_adjustments": money(sum((row.return_amount for row in financial_rows if row.credit_category == "warranty"), ZERO)),
        "adjustments": money(sum((row.adjustment for row in financial_rows), ZERO)),
    }
    summary["credits_total"] = money(summary["returns"] + summary["warranty_adjustments"])
    summary["closing"] = money(opening + summary["received"] - summary["paid"] - summary["credits_total"] + summary["adjustments"])
    summary["current"] = money(sum((row.financial_delta for row in all_rows), ZERO))
    if query:
        needle = query.lower().strip()
        period_rows = [row for row in period_rows if needle in row.search_text]
    if operation_type and operation_type != "all":
        period_rows = [row for row in period_rows if row.operation_type == operation_type]
    return {"rows": period_rows, "summary": summary}
