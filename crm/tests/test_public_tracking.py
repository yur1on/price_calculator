from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.client_status import CLIENT_STATUS
from crm.models import CRMAttachment, CRMClient, CRMDevice, CRMEvent, CRMOrder, CRMWorkItem
from finance.models import Employee


class PublicOrderNumberModelTests(TestCase):
    def setUp(self):
        client = CRMClient.objects.create(name="Клиент", phone="80291112233")
        device = CRMDevice.objects.create(client=client, device_type="Телефон", brand="Samsung", model="A55")
        self.values = {"client": client, "device": device, "issue_description": "Не включается"}

    def test_new_orders_receive_distinct_random_numbers(self):
        first = CRMOrder.objects.create(**self.values)
        second = CRMOrder.objects.create(**self.values)
        self.assertRegex(first.number, r"^R[A-Z0-9]{8}$")
        self.assertNotEqual(first.number, second.number)
        # A random identifier may legitimately contain the same digit as its PK;
        # guard against the predictable sequential format instead.
        self.assertNotEqual(first.number, f"R{first.pk:08d}")

    def test_number_survives_status_change(self):
        order = CRMOrder.objects.create(**self.values)
        number = order.number
        order.status = CRMOrder.Status.DIAGNOSTIC
        order.save(update_fields=["status", "updated_at"])
        order.refresh_from_db()
        self.assertEqual(order.number, number)

    def test_generated_collision_is_retried(self):
        existing = CRMOrder.objects.create(**self.values)
        with patch.object(CRMOrder, "generate_number", side_effect=[existing.number, "RABCDEFGH"]):
            created = CRMOrder.objects.create(**self.values)
        self.assertEqual(created.number, "RABCDEFGH")


class PublicRepairStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.employee = Employee.objects.create(name="Секретный мастер", default_percent="35")
        self.customer = CRMClient.objects.create(
            name="Иван Секретный", phone="+375291234567", email="private@example.com",
        )
        self.device = CRMDevice.objects.create(
            client=self.customer, device_type="Телефон", brand="Samsung", model="Galaxy S24",
            serial_number="359999999999999",
        )
        self.order = CRMOrder.objects.create(
            client=self.customer, device=self.device, employee=self.employee,
            issue_description="Замена дисплея", internal_note="INTERNAL-SECRET",
            diagnostic_result="INTERNAL-DIAGNOSTIC", agreed_price="250.00",
        )
        CRMEvent.objects.create(order=self.order, event_type=CRMEvent.Type.CREATED, description="Заказ принят")
        CRMEvent.objects.create(
            order=self.order, event_type=CRMEvent.Type.COMMENT,
            description="PRIVATE-EVENT", new_value="PRIVATE-EVENT",
        )
        self.url = reverse("repair_status")

    def tearDown(self):
        cache.clear()

    def post(self, number=None):
        return self.client.post(self.url, {
            "order_number": number if number is not None else self.order.number,
        })

    def test_form_is_public_and_uses_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверить статус ремонта")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'name="robots" content="noindex, nofollow"')
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(self.url, {"order_number": self.order.number}).status_code, 403)

    def test_page_links_public_tracking_stylesheet(self):
        response = self.client.get(self.url)
        self.assertContains(
            response,
            'href="/static/crm/css/public-repair-status.css?v=20260927"',
        )
        self.assertContains(response, 'class="repair-tracking-page"')
        self.assertContains(response, 'class="repair-tracking-search"')

    def test_correct_number_shows_safe_result_and_normalizes_case_and_spaces(self):
        response = self.post(number=f"  {self.order.number.lower()}  ")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Samsung Galaxy S24")
        self.assertContains(response, "Замена дисплея")
        self.assertContains(response, "250,00 BYN")

    def test_zero_unagreed_price_is_presented_as_pending(self):
        self.order.agreed_price = "0.00"
        self.order.save(update_fields=["agreed_price", "updated_at"])
        response = self.post()
        self.assertContains(response, "Уточняется")
        self.assertNotContains(response, "0,00 BYN")

    def test_wrong_number_uses_one_generic_error(self):
        other = CRMOrder.objects.create(
            client=self.customer, device=self.device, issue_description="Другой ремонт",
        )
        response = self.post("RNOTFOUND")
        self.assertContains(response, "Ремонт с указанным номером не найден")
        self.assertNotContains(response, "Samsung Galaxy S24")

        response = self.post(other.number)
        self.assertContains(response, "Другой ремонт")
        self.assertNotContains(response, self.order.issue_description)

    def test_public_result_does_not_leak_private_data(self):
        CRMWorkItem.objects.create(order=self.order, name="Замена модуля", quantity=1, unit_price="100", comment="PRIVATE-WORK-COMMENT")
        response = self.post()
        for secret in (
            self.customer.name, self.customer.phone, self.customer.email,
            self.device.serial_number, self.employee.name, "INTERNAL-SECRET",
            "INTERNAL-DIAGNOSTIC", "PRIVATE-EVENT", "PRIVATE-WORK-COMMENT",
        ):
            self.assertNotContains(response, secret)
        self.assertContains(response, "Замена модуля")

    def test_every_status_has_centralized_public_mapping(self):
        for status in CRMOrder.Status.values:
            self.order.status = status
            self.order.save(update_fields=["status", "updated_at"])
            response = self.post()
            self.assertContains(response, CLIENT_STATUS[status][0])
            self.assertContains(response, CLIENT_STATUS[status][1])
        self.order.status = CRMOrder.Status.READY
        self.order.save(update_fields=["status", "updated_at"])
        self.assertContains(self.post(), "Ремонт готов к выдаче")

    def test_timeline_uses_only_safe_status_events(self):
        CRMEvent.objects.create(
            order=self.order, event_type=CRMEvent.Type.STATUS,
            description="internal description", new_value=CRMOrder.Status(CRMOrder.Status.DIAGNOSTIC).label,
        )
        CRMEvent.objects.create(
            order=self.order, event_type=CRMEvent.Type.STATUS,
            description="malformed", new_value="PRIVATE-STATUS",
        )
        response = self.post()
        self.assertContains(response, "Принят")
        self.assertContains(response, "Диагностика")
        self.assertNotContains(response, "internal description")
        self.assertNotContains(response, "PRIVATE-STATUS")

    def test_customer_warranty_only_appears_after_issue(self):
        self.order.status = CRMOrder.Status.ISSUED
        self.order.warranty_days = 90
        self.order.warranty_started_at = timezone.now()
        self.order.issued_at = timezone.now()
        self.order.save(update_fields=["status", "warranty_days", "warranty_started_at", "issued_at", "updated_at"])
        response = self.post()
        self.assertContains(response, "Гарантия Tehsfera")
        self.assertContains(response, (timezone.now() + timedelta(days=90)).strftime("%d.%m.%Y"))
        self.assertNotContains(response, "Гарантия поставщика")

    def test_failed_attempts_are_rate_limited_without_revealing_order(self):
        for _ in range(12):
            response = self.post(number="RNOTFOUND")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Ремонт с указанным номером не найден")
        self.assertNotContains(response, self.order.device.display_name)


class PublicOrderNumberStaffTests(TestCase):
    def setUp(self):
        customer = CRMClient.objects.create(name="Клиент", phone="80293334455")
        device = CRMDevice.objects.create(client=customer, device_type="Телефон", model="A55")
        self.order = CRMOrder.objects.create(client=customer, device=device, issue_description="Тест")

    def test_admin_sees_number_in_crm_and_receipt(self):
        admin = get_user_model().objects.create_superuser("tracking-admin", "a@example.com", "pass")
        self.client.force_login(admin)
        detail = self.client.get(reverse("crm:order_detail", args=[self.order.pk]))
        receipt = self.client.get(reverse("crm:receipt", args=[self.order.pk]))
        self.assertContains(detail, self.order.number)
        self.assertContains(receipt, self.order.number)
        self.assertContains(receipt, reverse("repair_status"))
        self.assertNotContains(receipt, "Код проверки")

    def test_approved_master_sees_number_but_client_has_no_crm_access(self):
        master = get_user_model().objects.create_user("tracking-master", password="pass")
        profile = AccountProfile.objects.create(
            user=master, role=AccountProfile.Role.MASTER,
            approval_status=AccountProfile.Approval.PENDING,
        )
        approve_master(profile)
        self.client.force_login(master)
        self.assertContains(self.client.get(reverse("crm:order_detail", args=[self.order.pk])), self.order.number)

        client_user = get_user_model().objects.create_user("tracking-client", password="pass")
        AccountProfile.objects.create(user=client_user, role=AccountProfile.Role.CLIENT)
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(reverse("crm:order_detail", args=[self.order.pk])).status_code, 403)
