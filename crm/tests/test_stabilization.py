from datetime import date
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from crm.admin import CRMOrderAdmin
from crm.models import CRMClient, CRMDevice, CRMOrder
from crm.services import install_part
from finance.models import Employee, PartCatalog, StockReceipt, Supplier


class WorkflowAdminSafetyTests(TestCase):
    def test_admin_cannot_edit_service_owned_workflow_fields(self):
        model_admin = CRMOrderAdmin(CRMOrder, admin.site)
        readonly = set(model_admin.get_readonly_fields(None))
        self.assertTrue({"status", "ready_at", "issued_at", "warranty_started_at", "finance_repair"} <= readonly)


class MasterFinancialPrivacyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("privacy-master", password="pass", is_staff=True)
        AccountProfile.objects.create(
            user=self.user, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.APPROVED,
        )
        self.user.user_permissions.set(Permission.objects.filter(codename__in=[
            "access_crm", "view_crmorder", "change_crmorder", "change_order_status",
        ]))
        self.employee = Employee.objects.create(user=self.user, name="Мастер", default_percent="35")
        self.supplier = Supplier.objects.create(name="Поставщик")
        self.catalog = PartCatalog.objects.create(brand="Apple", device_model="iPhone", name="Дисплей")
        self.receipt = StockReceipt.objects.create(
            date=date(2026, 9, 28), supplier=self.supplier, part=self.catalog,
            quantity=1, unit_cost="9187.43", order_number="PRIVATE-COST",
        )
        customer = CRMClient.objects.create(name="Клиент", phone="+375291234567")
        device = CRMDevice.objects.create(client=customer, device_type="Телефон", brand="Apple", model="iPhone")
        self.order = CRMOrder.objects.create(
            client=customer, device=device, employee=self.employee, issue_description="Экран", agreed_price="200",
        )
        self.client.force_login(self.user)

    def test_master_stock_pages_do_not_render_purchase_cost(self):
        stock = self.client.get(reverse("finance:stock_list"))
        detail = self.client.get(reverse("finance:part_item_detail", args=[self.receipt.items.first().pk]))
        self.assertEqual(stock.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(stock, "9187")
        self.assertNotContains(detail, "9187")

    def test_master_cannot_open_receipt_price_forms(self):
        self.assertEqual(self.client.get(reverse("finance:receipt_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:receipt_edit", args=[self.receipt.pk])).status_code, 403)

    def test_master_crm_part_views_do_not_render_purchase_cost(self):
        search = self.client.get(reverse("crm:order_part_search", args=[self.order.pk]))
        self.assertEqual(search.status_code, 200)
        self.assertNotContains(search, "9187")
        install_part(self.order, self.receipt.items.first().pk, self.user)
        detail = self.client.get(reverse("crm:order_detail", args=[self.order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "9187")


class LegacyWebhookPrivacyTests(TestCase):
    @patch("builtins.print")
    def test_yoomoney_webhook_does_not_print_payload(self, mocked_print):
        response = self.client.post(reverse("yoomoney_webhook"), {"secret": "PRIVATE-PAYMENT-DATA"})
        self.assertEqual(response.status_code, 200)
        mocked_print.assert_not_called()


class ClientLookupScriptSafetyTests(SimpleTestCase):
    def test_client_payload_is_encoded_before_inserting_into_html_attributes(self):
        script = (settings.BASE_DIR / "static/crm/js/crm.js").read_text(encoding="utf-8")
        self.assertIn("encodeURIComponent(JSON.stringify(client))", script)
        self.assertIn("JSON.parse(decodeURIComponent(clientButton.dataset.client))", script)
        self.assertNotIn("data-client='${JSON.stringify(client)}'", script)
