from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from crm.models import CRMEvent, CRMOrder, CRMWorkItem
from crm.services import install_part
from finance.models import PartCatalog, PartItem, RepairFinance, RepairPart, StockReceipt, Supplier

from .test_crm import CRMBase


class OrderIssueTests(CRMBase):
    def ready_order(self, **kwargs):
        values = {"status": CRMOrder.Status.READY, "agreed_price": "250.00", "warranty_days": 90}
        values.update(kwargs)
        order = self.order(**values)
        CRMWorkItem.objects.create(order=order, name="Замена дисплея", quantity=1, unit_price="150.00")
        return order

    def issue(self, order, **kwargs):
        data = {"final_price": "270.00", "paid_amount": "270.00", "warranty_days": "90"}
        data.update(kwargs)
        return self.client.post(reverse("crm:order_issue", args=[order.pk]), data)

    def test_issue_screen_is_available_to_authorized_employee(self):
        order = self.ready_order()
        response = self.client.get(reverse("crm:order_issue", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Выдача устройства")
        self.assertContains(response, order.number)
        self.assertContains(response, "Замена дисплея")
        self.assertEqual(response.context["form"].initial["final_price"], Decimal("250.00"))
        self.assertEqual(response.context["form"].initial["paid_amount"], Decimal("250.00"))

    def test_anonymous_client_and_pending_master_cannot_open_issue(self):
        order = self.ready_order()
        url = reverse("crm:order_issue", args=[order.pk])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        client_user = get_user_model().objects.create_user("issue-client", password="pass")
        AccountProfile.objects.create(user=client_user, role=AccountProfile.Role.CLIENT, approval_status=AccountProfile.Approval.ACTIVE)
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        pending = get_user_model().objects.create_user("issue-pending", password="pass")
        AccountProfile.objects.create(user=pending, role=AccountProfile.Role.MASTER, approval_status=AccountProfile.Approval.PENDING)
        self.client.force_login(pending)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_only_ready_order_can_use_issue_screen(self):
        order = self.order(status=CRMOrder.Status.REPAIR)
        response = self.client.get(reverse("crm:order_issue", args=[order.pk]))
        self.assertRedirects(response, reverse("crm:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.REPAIR)

    def test_paid_issue_records_final_payment_dates_finance_and_history(self):
        order = self.ready_order()
        response = self.issue(order)
        self.assertRedirects(response, reverse("crm:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.ISSUED)
        self.assertEqual(order.final_price, Decimal("270.00"))
        self.assertEqual(order.paid_amount, Decimal("270.00"))
        self.assertIsNotNone(order.issued_at)
        self.assertEqual(order.warranty_started_at, order.issued_at)
        self.assertEqual(order.finance_repair.revenue, Decimal("270.00"))
        self.assertEqual(RepairFinance.objects.count(), 1)
        self.assertTrue(order.events.filter(event_type=CRMEvent.Type.STATUS, new_value="Выдано").exists())

    def test_partial_payment_and_missing_final_price_do_not_issue(self):
        order = self.ready_order()
        response = self.issue(order, final_price="270.00", paid_amount="200.00")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "должна совпадать")
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.READY)
        self.assertEqual(RepairFinance.objects.count(), 0)
        response = self.issue(order, final_price="", paid_amount="0")
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.READY)

    def test_double_submit_does_not_duplicate_finance_or_status_event(self):
        order = self.ready_order()
        self.issue(order)
        issued_at = CRMOrder.objects.get(pk=order.pk).issued_at
        second = self.issue(order)
        self.assertRedirects(second, reverse("crm:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.issued_at, issued_at)
        self.assertEqual(RepairFinance.objects.count(), 1)
        self.assertEqual(order.events.filter(event_type=CRMEvent.Type.STATUS, new_value="Выдано").count(), 1)

    def test_existing_parts_finance_is_reused_without_stock_duplication(self):
        order = self.ready_order()
        supplier = Supplier.objects.create(name="Safe Parts")
        catalog = PartCatalog.objects.create(brand="Samsung", device_model="A55", name="OLED дисплей")
        receipt = StockReceipt.objects.create(date=date(2026, 9, 28), supplier=supplier, part=catalog, quantity=1, unit_cost="85.00")
        item = receipt.items.get()
        usage = install_part(order, item.pk, self.user)
        repair_id = usage.finance_repair_id
        self.issue(order)
        order.refresh_from_db(); item.refresh_from_db(); order.finance_repair.refresh_from_db()
        self.assertEqual(order.finance_repair_id, repair_id)
        self.assertEqual(RepairFinance.objects.count(), 1)
        self.assertEqual(RepairPart.objects.count(), 1)
        self.assertEqual(item.status, PartItem.Status.INSTALLED)
        self.assertEqual(order.finance_repair.part_cost, Decimal("85.00"))
        self.assertEqual(order.finance_repair.revenue, Decimal("270.00"))

    def test_warranty_order_is_issued_with_zero_payment(self):
        order = self.ready_order(order_type=CRMOrder.Type.WARRANTY, agreed_price="0.00")
        response = self.issue(order, final_price="999.00", paid_amount="999.00", warranty_days="30")
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.final_price, Decimal("0.00"))
        self.assertEqual(order.paid_amount, Decimal("0.00"))
        self.assertEqual(order.finance_repair.revenue, Decimal("0.00"))

    def test_quick_status_endpoints_cannot_bypass_issue_flow(self):
        order = self.ready_order()
        for name in ("crm:order_status", "crm:work_queue_status"):
            self.client.post(reverse(name, args=[order.pk]), {"status": CRMOrder.Status.ISSUED})
            order.refresh_from_db()
            self.assertEqual(order.status, CRMOrder.Status.READY)

    def test_warranty_document_contains_public_data_and_no_secrets(self):
        order = self.ready_order(internal_note="PRIVATE-NOTE")
        supplier = Supplier.objects.create(name="SECRET-SUPPLIER")
        catalog = PartCatalog.objects.create(brand="Samsung", device_model="A55", name="Дисплей клиента")
        receipt = StockReceipt.objects.create(date=date(2026, 9, 28), supplier=supplier, part=catalog, quantity=1, unit_cost="85.00")
        install_part(order, receipt.items.get().pk, self.user)
        self.issue(order)
        response = self.client.get(reverse("crm:warranty", args=[order.pk]))
        self.assertContains(response, "Дисплей клиента")
        self.assertContains(response, "270,00 BYN")
        self.assertNotContains(response, "SECRET-SUPPLIER")
        self.assertNotContains(response, "PRIVATE-NOTE")
        self.assertNotContains(response, self.device.unlock_code)
        self.assertNotContains(response, "85.00")

    def test_unknown_agreed_price_is_explained_on_issue_screen(self):
        order = self.ready_order(agreed_price="0.00")
        response = self.client.get(reverse("crm:order_issue", args=[order.pk]))
        self.assertContains(response, "Уточняется")
