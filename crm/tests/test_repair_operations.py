import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.models import CRMEvent, CRMOrder, CRMOrderPartUsage, CRMWorkItem
from crm.services import install_part
from finance.models import PartCatalog, PartItem, RepairFinance, RepairPart, StockReceipt, Supplier
from .test_crm import CRMBase


class RepairOperationRegressionTests(CRMBase):
    def setUp(self):
        super().setUp()
        self.repair = self.order(status=CRMOrder.Status.REPAIR)
        CRMWorkItem.objects.create(order=self.repair, name="Замена дисплея", quantity=1, unit_price="250")
        supplier = Supplier.objects.create(name="Test supplier")
        catalog = PartCatalog.objects.create(name="Regression display")
        self.receipt = StockReceipt.objects.create(date=date(2026, 10, 1), supplier=supplier,
            part=catalog, quantity=2, unit_cost="83.17", warranty_days=90)
        self.item = self.receipt.items.first()
        self.url = reverse("crm:order_part_install", args=[self.repair.pk])

    def install(self, value=None):
        return self.client.post(self.url, {"part_item": self.item.pk if value is None else value}, HTTP_HX_REQUEST="true")

    def test_admin_install_and_duplicate_have_atomic_stock_history(self):
        admin = get_user_model().objects.create_superuser("operations-admin", "admin@example.com", "pass")
        self.client.force_login(admin)
        response = self.install()
        self.assertEqual(response.status_code, 200)
        self.assertIn("workbench:modal-close", json.loads(response["HX-Trigger"]))
        self.assertContains(response, 'id="crm-order-parts"')
        self.assertEqual(self.receipt.items.filter(status=PartItem.Status.IN_STOCK).count(), 1)
        usage = CRMOrderPartUsage.objects.get(order=self.repair)
        self.assertEqual(str(usage.unit_cost_snapshot), "83.17")
        self.assertEqual(usage.supplier_name_snapshot, "Test supplier")
        self.assertEqual(usage.warranty_days_snapshot, 90)
        self.assertEqual(RepairPart.objects.filter(part_item=self.item).count(), 1)
        self.assertEqual(self.repair.events.filter(event_type=CRMEvent.Type.PART).count(), 1)
        error = self.install()
        self.assertEqual(error.status_code, 422)
        self.assertIn("отсутствует", json.loads(error["HX-Trigger"])["workbench:toast"]["message"])
        self.assertEqual(CRMOrderPartUsage.objects.count(), 1)
        self.assertEqual(RepairPart.objects.count(), 1)
        self.assertEqual(self.receipt.items.filter(status=PartItem.Status.IN_STOCK).count(), 1)

    def test_approved_master_install_and_views_do_not_expose_finance(self):
        user = get_user_model().objects.create_user("operations-master", password="pass")
        profile = AccountProfile.objects.create(user=user, role=AccountProfile.Role.MASTER)
        approve_master(profile)
        self.client.force_login(user)
        responses = [self.client.get(reverse("crm:order_part_search", args=[self.repair.pk])), self.install(),
                     self.client.get(reverse("crm:order_detail", args=[self.repair.pk]))]
        self.repair.status = CRMOrder.Status.READY
        self.repair.save(update_fields=["status"])
        responses.append(self.client.get(reverse("crm:order_issue", args=[self.repair.pk])))
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Regression display")
            # Operational warehouse/supplier URLs live under /finance/ too.
            for secret in ("83.17", "83,17", "purchase_price", "workshop_profit", 'href="/finance/"'):
                self.assertNotContains(response, secret)
        self.assertEqual(self.client.get(reverse("finance:dashboard")).status_code, 403)

    def test_invalid_payload_and_unavailable_item_are_controlled(self):
        for value in ("", "not-an-id", "-1", "9" * 100, "999999"):
            with self.subTest(value=value):
                response = self.install(value)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(json.loads(response["HX-Trigger"])["workbench:toast"]["level"], "error")
        self.assertFalse(CRMOrderPartUsage.objects.exists())
        self.assertFalse(RepairPart.objects.exists())
        self.assertEqual(self.receipt.items.filter(status=PartItem.Status.IN_STOCK).count(), 2)

    def test_closed_orders_reject_install_and_return(self):
        usage = install_part(self.repair, self.item.pk, self.user)
        for status in (CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED):
            self.repair.status = status
            self.repair.save(update_fields=["status"])
            response = self.install(self.receipt.items.exclude(pk=self.item.pk).get().pk)
            self.assertEqual(response.status_code, 422)
            self.assertIn("выданного или отменённого", json.loads(response["HX-Trigger"])["workbench:toast"]["message"])
            returned = self.client.post(reverse("crm:order_part_return", args=[self.repair.pk, usage.pk]), HTTP_HX_REQUEST="true")
            self.assertEqual(returned.status_code, 422)
        self.assertEqual(RepairPart.objects.count(), 1)

    def test_late_failure_rolls_back_usage_stock_finance_and_event(self):
        with patch("crm.services.add_event", side_effect=ValidationError("Test rollback")):
            response = self.install()
        self.assertEqual(response.status_code, 422)
        self.item.refresh_from_db()
        self.repair.refresh_from_db()
        self.assertEqual(self.item.status, PartItem.Status.IN_STOCK)
        self.assertIsNone(self.repair.finance_repair_id)
        self.assertFalse(CRMOrderPartUsage.objects.exists())
        self.assertFalse(RepairPart.objects.exists())
        self.assertFalse(RepairFinance.objects.exists())

    def test_ready_both_endpoints_and_regular_htmx_requests(self):
        for route in ("crm:order_status", "crm:work_queue_status"):
            for htmx in (False, True):
                order = self.order(status=CRMOrder.Status.REPAIR)
                CRMWorkItem.objects.create(order=order, name="Work", quantity=1, unit_price="250")
                with patch("notify_tg.services.notify_order_status", side_effect=TimeoutError("offline")) as send:
                    with self.captureOnCommitCallbacks(execute=True):
                        response = self.client.post(reverse(route, args=[order.pk]), {"status": "ready"}, **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
                send.assert_called_once()
                self.assertEqual(response.status_code, 200 if htmx else 302)
                order.refresh_from_db()
                self.assertEqual(order.status, CRMOrder.Status.READY)
                self.assertIsNotNone(order.ready_at)

    def test_issue_with_telegram_exception_stays_committed(self):
        self.install()
        self.repair.refresh_from_db()
        self.repair.status = CRMOrder.Status.READY
        self.repair.save(update_fields=["status"])
        url = reverse("crm:order_issue", args=[self.repair.pk])
        self.assertContains(self.client.get(url), "Regression display")
        with patch("notify_tg.services.notify_order_status", side_effect=TimeoutError("offline")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"final_price": "250", "paid_amount": "250", "warranty_days": "90"})
        self.assertEqual(response.status_code, 302)
        self.repair.refresh_from_db()
        self.assertEqual(self.repair.status, CRMOrder.Status.ISSUED)
        self.assertIsNotNone(self.repair.warranty_started_at)
        self.assertEqual(str(self.repair.finance_repair.revenue), "250.00")
        self.assertEqual(RepairPart.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update_of")
    def test_postgresql_locks_only_order_not_nullable_join(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.install().status_code, 200)
        order_locks = [row["sql"] for row in queries if 'FROM "crm_crmorder"' in row["sql"] and "FOR UPDATE" in row["sql"]]
        self.assertTrue(order_locks)
        for sql in order_locks:
            self.assertIn('FOR UPDATE OF "crm_crmorder"', sql)
