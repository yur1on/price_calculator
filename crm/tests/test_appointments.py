from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from accounts.services import approve_master
from crm.models import CRMClient, CRMDevice, CRMOrder
from finance.models import Employee
from repairs.booking_capacity import appointment_reserved_minutes
from repairs.models import Appointment, AppointmentItem, PhoneBrand, PhoneModel, RepairType


class CRMOOnlineAppointmentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("appointments-admin", "admin@example.com", "pass")
        self.brand = PhoneBrand.objects.create(name="Samsung", slug="appointments-samsung")
        self.phone = PhoneModel.objects.create(brand=self.brand, name="S24", slug="appointments-s24", category="phone")
        self.repair_type = RepairType.objects.create(name="Замена дисплея", slug="appointments-display", default_duration_min=90)

    def appointment(self, *, name="Иван", status="new", delta=timedelta(days=2)):
        start = timezone.now() + delta
        appointment = Appointment.objects.create(
            phone_model=self.phone, repair_type=self.repair_type,
            start=start, end=start + timedelta(minutes=30), customer_name=name,
            customer_phone="+375291234567", price_original="250", price_final="250", status=status,
        )
        AppointmentItem.objects.create(
            appointment=appointment, repair_type=self.repair_type,
            price="250", duration_min=90,
        )
        return appointment

    def test_admin_sees_future_online_appointment_by_default(self):
        appointment = self.appointment(name="Клиент с сайта")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:appointment_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Клиент с сайта")
        self.assertContains(response, f"appointment={appointment.pk}")
        self.assertContains(response, "Принять в ремонт")

    def test_appointment_intake_modal_keeps_existing_prefill(self):
        appointment = self.appointment(name="Клиент из записи")
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("crm:order_create"), {"appointment": appointment.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/partials/intake_wizard.html")
        self.assertContains(response, "Клиент из записи")
        self.assertContains(response, "Samsung")
        self.assertContains(response, "S24")
        self.assertContains(response, f'name="appointment" value="{appointment.pk}"')

    def test_approved_master_sees_appointment_and_sidebar_link(self):
        self.appointment(name="Запись для мастера")
        master = get_user_model().objects.create_user("appointments-master", password="pass")
        profile = AccountProfile.objects.create(user=master, role="master", approval_status="pending")
        approve_master(profile)
        self.client.force_login(master)
        response = self.client.get(reverse("crm:appointment_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Запись для мастера")
        self.assertContains(response, reverse("crm:appointment_list"))
        self.assertContains(response, "Записи")

    def test_client_and_anonymous_cannot_access_staff_appointments(self):
        self.assertEqual(self.client.get(reverse("crm:appointment_list")).status_code, 302)
        user = get_user_model().objects.create_user("appointments-client", password="pass")
        AccountProfile.objects.create(user=user, role="client", approval_status="active")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("crm:appointment_list")).status_code, 403)

    def test_nearest_appointment_is_visible_on_dashboard(self):
        self.appointment(name="Ближайший клиент", delta=timedelta(hours=4))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:dashboard"))
        self.assertContains(response, "Ближайшие записи")
        self.assertContains(response, "Ближайший клиент")

    def test_period_and_status_filters_separate_upcoming_and_history(self):
        self.appointment(name="New future", status="new")
        self.appointment(name="Confirmed future", status="confirmed", delta=timedelta(days=1))
        self.appointment(name="Done past", status="done", delta=timedelta(days=-2))
        self.appointment(name="Cancelled past", status="cancelled", delta=timedelta(days=-3))
        self.appointment(name="No show past", status="no_show", delta=timedelta(days=-4))
        self.client.force_login(self.admin)
        upcoming = self.client.get(reverse("crm:appointment_list"))
        self.assertContains(upcoming, "New future")
        self.assertContains(upcoming, "Confirmed future")
        self.assertNotContains(upcoming, "Done past")
        self.assertContains(self.client.get(reverse("crm:appointment_list"), {"period": "history"}), "Done past")
        self.assertContains(self.client.get(reverse("crm:appointment_list"), {"period": "cancelled"}), "Cancelled past")
        self.assertContains(self.client.get(reverse("crm:appointment_list"), {"period": "no_show"}), "No show past")

    def test_today_and_tomorrow_filters(self):
        # Keep fixtures inside their intended local dates even when the suite runs
        # shortly before midnight.
        now = timezone.now()
        local_noon = timezone.localtime(now).replace(hour=12, minute=0, second=0, microsecond=0)
        self.appointment(name="Today appointment", delta=local_noon - now)
        self.appointment(name="Tomorrow appointment", delta=(local_noon + timedelta(days=1)) - now)
        self.client.force_login(self.admin)
        today = self.client.get(reverse("crm:appointment_list"), {"period": "today"})
        tomorrow = self.client.get(reverse("crm:appointment_list"), {"period": "tomorrow"})
        self.assertContains(today, "Today appointment")
        self.assertNotContains(today, "Tomorrow appointment")
        self.assertContains(tomorrow, "Tomorrow appointment")

    def test_converted_appointment_links_existing_order(self):
        appointment = self.appointment(status="done")
        employee = Employee.objects.create(name="Мастер", default_percent="35")
        customer = CRMClient.objects.create(name="Иван", phone="+375291234567")
        device = CRMDevice.objects.create(client=customer, device_type="Телефон", brand="Samsung", model="S24")
        order = CRMOrder.objects.create(
            client=customer, device=device, employee=employee, source_appointment=appointment,
            issue_description="Дисплей", estimated_work_minutes_snapshot=90,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:appointment_list"), {"period": "converted"})
        self.assertContains(response, order.number)
        self.assertContains(response, reverse("crm:order_detail", args=[order.pk]))

    def test_ui_change_does_not_change_capacity_reservation(self):
        appointment = self.appointment()
        self.assertEqual(appointment_reserved_minutes(timezone.localdate(appointment.start)), 90)

    def test_intake_from_appointment_prefills_and_preserves_workload(self):
        appointment = self.appointment(name="Анна")
        employee = Employee.objects.create(name="Мастер", default_percent="35")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("crm:order_create"), {"appointment": appointment.pk})
        self.assertContains(page, "Анна")
        self.assertContains(page, "Samsung")
        self.assertContains(page, 'value="90"')
        response = self.client.post(reverse("crm:order_create"), {
            "appointment": appointment.pk, "client_name": "Анна", "phone": "+375291234567",
            "device_type": "Телефон", "brand": "Samsung", "device_model": "S24",
            "imei": "356789012345678", "serial_number": "S24-SN", "condition": "Царапины",
            "included_items": "Без комплекта", "issue_description": "Замена дисплея",
            "employee": employee.pk, "order_type": "paid", "agreed_price": "250",
            "estimated_work_minutes_snapshot": "15", "warranty_days": "90",
        })
        self.assertEqual(response.status_code, 302)
        order = CRMOrder.objects.get(source_appointment=appointment)
        self.assertEqual(order.estimated_work_minutes_snapshot, 90)
        self.assertEqual(order.condition_on_intake, "Царапины")
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "done")
