from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from finance.models import Employee, Expense, ExpenseCategory, OtherIncome, RepairFinance
from crm.models import CRMClient, CRMDevice, CRMEvent, CRMOrder


class AnalyticsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user("analytics-admin", password="pass", is_staff=True)
        AccountProfile.objects.create(user=self.admin, role=AccountProfile.Role.ADMIN)
        self.master_user = get_user_model().objects.create_user("analytics-master", password="pass", is_staff=True)
        AccountProfile.objects.create(user=self.master_user, role=AccountProfile.Role.MASTER,
                                      approval_status=AccountProfile.Approval.APPROVED)
        self.employee = Employee.objects.create(user=self.master_user, name="Сергей", default_percent="35")
        self.customer = CRMClient.objects.create(name="Иван", phone="+375291111111")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Apple", model="iPhone 13")
        self.today = timezone.localdate()

    def order(self, **kwargs):
        values = {"client": self.customer, "device": self.device, "employee": self.employee,
                  "issue_description": "Экран", "accepted_at": timezone.now() - timedelta(hours=5)}
        values.update(kwargs)
        return CRMOrder.objects.create(**values)

    def test_anonymous_is_redirected_and_master_is_forbidden(self):
        url = reverse("crm:analytics")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.master_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        client_user = get_user_model().objects.create_user("analytics-client", password="pass")
        AccountProfile.objects.create(user=client_user, role=AccountProfile.Role.CLIENT)
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_can_open_analytics_and_sees_sidebar_link(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Аналитика мастерской")
        self.assertContains(response, 'href="/crm/analytics/"')

    def test_financial_kpis_reuse_finance_formula(self):
        repair = RepairFinance.objects.create(date=self.today, description="Экран", revenue="200", part_cost="50",
                                              manual_part_cost="50", employee=self.employee, master_percent="35")
        order = self.order(status=CRMOrder.Status.ISSUED, issued_at=timezone.now(), ready_at=timezone.now(),
                           finance_repair=repair)
        category, _ = ExpenseCategory.objects.get_or_create(name="Аренда")
        Expense.objects.create(date=self.today, amount="10", category=category, description="Расход")
        OtherIncome.objects.create(date=self.today, amount="5", description="Доход")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"), {"period": "today"})
        self.assertEqual(response.context["summary"]["revenue"], Decimal("200.00"))
        self.assertEqual(response.context["summary"]["net_profit"], Decimal("92.50"))
        self.assertEqual(response.context["average_check"], Decimal("200.00"))
        self.assertEqual(response.context["issued_count"], 1)

    def test_custom_period_excludes_outside_orders(self):
        self.order()
        self.order(accepted_at=timezone.now() - timedelta(days=40))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"), {
            "period": "custom", "from": self.today.isoformat(), "to": self.today.isoformat(),
        })
        self.assertEqual(response.context["accepted_count"], 1)

    def test_chart_payload_is_json_script_not_unsafe_inline_json(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"))
        self.assertContains(response, 'id="analytics-trend-data" type="application/json"')
        self.assertNotContains(response, "trend|safe")

    def test_current_wip_is_not_limited_by_selected_period(self):
        self.order(status=CRMOrder.Status.REPAIR, accepted_at=timezone.now() - timedelta(days=90))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"), {
            "period": "custom", "from": self.today.isoformat(), "to": self.today.isoformat(),
        })
        self.assertEqual(response.context["active_count"], 1)

    def test_average_check_excludes_warranty_repairs(self):
        paid = RepairFinance.objects.create(date=self.today, description="Платный", revenue="300", employee=self.employee, master_percent="35")
        warranty = RepairFinance.objects.create(date=self.today, description="Гарантия", revenue="0", employee=self.employee, master_percent="35")
        self.order(status=CRMOrder.Status.ISSUED, issued_at=timezone.now(), ready_at=timezone.now(), finance_repair=paid)
        self.order(status=CRMOrder.Status.ISSUED, order_type=CRMOrder.Type.WARRANTY, issued_at=timezone.now(), ready_at=timezone.now(), finance_repair=warranty)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"), {"period": "today"})
        self.assertEqual(response.context["average_check"], Decimal("300.00"))

    def test_duration_uses_ready_time_and_history_only_when_present(self):
        order = self.order(status=CRMOrder.Status.READY, ready_at=timezone.now())
        event = CRMEvent.objects.create(order=order, event_type=CRMEvent.Type.STATUS, new_value=CRMOrder.Status.DIAGNOSTIC.label, description="Диагностика")
        CRMEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(hours=2))
        CRMEvent.objects.create(order=order, event_type=CRMEvent.Type.STATUS, new_value=CRMOrder.Status.READY.label, description="Готов")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics"), {"period": "today"})
        diagnostic = next(row for row in response.context["status_duration_rows"] if row["status"] == CRMOrder.Status.DIAGNOSTIC)
        self.assertEqual(diagnostic["samples"], 1)
        self.assertIn("ч", response.context["ready_duration"])

    def test_export_is_admin_only_and_contains_no_private_device_data(self):
        self.device.unlock_code = "SECRET-7391"
        self.device.save(update_fields=["unlock_code"])
        self.order()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:analytics_export"), {"period": "today"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertNotIn(b"SECRET-7391", response.content)
