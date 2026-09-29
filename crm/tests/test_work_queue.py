from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.models import CRMClient, CRMDevice, CRMEvent, CRMOrder, CRMWorkItem
from finance.models import Employee


class WorkQueueTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("queue-admin", "queue-admin@example.com", "pass")
        self.master_user = get_user_model().objects.create_user("queue-master@example.com", password="pass")
        self.master_profile = AccountProfile.objects.create(
            user=self.master_user, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.PENDING,
        )
        self.master = approve_master(self.master_profile)
        self.other_user = get_user_model().objects.create_user("queue-other@example.com", password="pass")
        other_profile = AccountProfile.objects.create(
            user=self.other_user, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.PENDING,
        )
        self.other = approve_master(other_profile)
        self.customer = CRMClient.objects.create(name="Иван Очередин", phone="+375291112233")
        self.device = CRMDevice.objects.create(
            client=self.customer, device_type="Телефон", brand="Samsung", model="Galaxy S24",
            imei="356789012345678", serial_number="QUEUE-SERIAL", unlock_code="PIN-SECRET-7391",
        )

    def order(self, status=CRMOrder.Status.ACCEPTED, employee=None, minutes=60, issue="Не заряжается"):
        return CRMOrder.objects.create(
            client=self.customer, device=self.device, employee=employee, status=status,
            issue_description=issue, estimated_work_minutes_snapshot=minutes,
            condition_on_intake="Царапины на рамке", accessories_on_intake="Чехол",
            agreed_price="999.00",
        )

    def test_permissions_for_admin_master_client_anonymous_and_pending(self):
        url = reverse("crm:work_queue")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.master_user)
        self.assertEqual(self.client.get(url).status_code, 200)
        client_user = get_user_model().objects.create_user("queue-client", password="pass")
        AccountProfile.objects.create(user=client_user, role=AccountProfile.Role.CLIENT, approval_status=AccountProfile.Approval.ACTIVE)
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        pending_user = get_user_model().objects.create_user("queue-pending", password="pass")
        AccountProfile.objects.create(user=pending_user, role=AccountProfile.Role.MASTER, approval_status=AccountProfile.Approval.PENDING)
        self.client.force_login(pending_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_all_active_statuses_are_grouped_and_closed_are_absent(self):
        labels = {
            CRMOrder.Status.ACCEPTED: "Нужно начать", CRMOrder.Status.DIAGNOSTIC: "Диагностика",
            CRMOrder.Status.REPAIR: "В ремонте", CRMOrder.Status.APPROVAL: "Ожидают согласования",
            CRMOrder.Status.WAITING_PART: "Ждут запчасть", CRMOrder.Status.READY: "Готовы к выдаче",
        }
        active = {status: self.order(status=status, employee=self.master, issue=f"ISSUE-{status}") for status in labels}
        issued = self.order(status=CRMOrder.Status.ISSUED, employee=self.master, issue="ISSUE-issued")
        canceled = self.order(status=CRMOrder.Status.CANCELED, employee=self.master, issue="ISSUE-canceled")
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("crm:work_queue"))
        for status, label in labels.items():
            self.assertContains(response, label)
            self.assertContains(response, active[status].number)
        self.assertNotContains(response, issued.number)
        self.assertNotContains(response, canceled.number)

    def test_master_defaults_to_own_and_can_view_unassigned_and_all(self):
        own = self.order(employee=self.master)
        foreign = self.order(employee=self.other)
        unassigned = self.order(employee=None)
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("crm:work_queue"))
        self.assertContains(response, own.number)
        self.assertNotContains(response, foreign.number)
        self.assertNotContains(response, unassigned.number)
        response = self.client.get(reverse("crm:work_queue"), {"mode": "unassigned"})
        self.assertContains(response, unassigned.number)
        response = self.client.get(reverse("crm:work_queue"), {"mode": "all"})
        self.assertContains(response, foreign.number)

    def test_take_ownership_is_atomic_and_does_not_steal(self):
        order = self.order(employee=None)
        url = reverse("crm:work_queue_take", args=[order.pk])
        self.client.force_login(self.master_user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"mode": "unassigned"}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.employee, self.master)
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(url, {"mode": "unassigned"}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.employee, self.master)
        self.assertEqual(order.events.filter(event_type=CRMEvent.Type.EMPLOYEE).count(), 1)

    def test_status_post_uses_service_and_htmx_moves_card(self):
        order = self.order(employee=self.master)
        url = reverse("crm:work_queue_status", args=[order.pk])
        self.client.force_login(self.master_user)
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"status": CRMOrder.Status.DIAGNOSTIC, "mode": "mine"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="work-queue-content"')
        self.assertIn("workbench:toast", response.headers["HX-Trigger"])
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.DIAGNOSTIC)

    def test_ready_and_issued_keep_existing_side_effects(self):
        order = self.order(status=CRMOrder.Status.REPAIR, employee=self.master)
        CRMWorkItem.objects.create(order=order, name="Ремонт", quantity=1, unit_price=100)
        self.client.force_login(self.master_user)
        self.client.post(reverse("crm:work_queue_status", args=[order.pk]), {"status": CRMOrder.Status.READY, "mode": "mine"})
        order.refresh_from_db()
        self.assertIsNotNone(order.ready_at)
        blocked = self.client.post(reverse("crm:work_queue_status", args=[order.pk]), {"status": CRMOrder.Status.ISSUED, "mode": "mine"})
        self.assertEqual(blocked.status_code, 302)
        order.refresh_from_db()
        self.assertIsNone(order.issued_at)
        self.client.post(reverse("crm:order_issue", args=[order.pk]), {
            "final_price": "100.00", "paid_amount": "100.00", "warranty_days": "90",
        })
        order.refresh_from_db()
        self.assertIsNotNone(order.issued_at)

    def test_productive_workload_and_physical_wip(self):
        for status in (CRMOrder.Status.ACCEPTED, CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.REPAIR):
            self.order(status=status, employee=self.master, minutes=60)
        for status in (CRMOrder.Status.APPROVAL, CRMOrder.Status.WAITING_PART, CRMOrder.Status.READY):
            self.order(status=status, employee=self.master, minutes=90)
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("crm:work_queue"))
        self.assertEqual(response.context["productive_minutes"], 180)
        self.assertEqual(response.context["work_total"], 6)
        self.assertEqual(response.context["physical_wip"], 6)

    def test_searches_operational_fields_and_never_exposes_access_code_or_price(self):
        order = self.order(employee=self.master)
        self.client.force_login(self.master_user)
        url = reverse("crm:work_queue")
        for query in (order.number, "Иван", "291112233", "Galaxy", "356789012345678", "QUEUE-SERIAL"):
            self.assertContains(self.client.get(url, {"q": query}), order.number)
        response = self.client.get(url, {"mode": "all"})
        self.assertNotContains(response, self.device.unlock_code)
        self.assertNotContains(response, "999.00")

    def test_htmx_get_returns_only_queue_content(self):
        self.order(employee=self.master)
        self.client.force_login(self.master_user)
        response = self.client.get(reverse("crm:work_queue"), HTTP_HX_REQUEST="true")
        self.assertContains(response, 'id="work-queue-content"')
        self.assertNotContains(response, 'class="wb-sidebar"')
