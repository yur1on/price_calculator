from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMClient, CRMDevice, CRMOrder
from finance.models import Employee
from repairs.booking_capacity import (
    active_crm_backlog_minutes, appointment_reserved_minutes, capacity_dashboard,
    day_capacity, slot_is_available,
)
from repairs.models import (
    Appointment, AppointmentItem, BookingBlock, PhoneBrand, PhoneModel,
    RepairType, WorkingHour, WorkshopDayCapacity,
)
from repairs.views import get_available_slots


@override_settings(
    WORKSHOP_DEFAULT_CAPACITY_MINUTES=720,
    WORKSHOP_DEFAULT_RESERVE_MINUTES=120,
    BOOKING_INTAKE_SLOT_MINUTES=30,
    REPAIRS_MAX_PARALLEL_APPOINTMENTS=2,
)
class WorkshopCapacityTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        WorkingHour.objects.create(weekday=self.today.weekday(), start=time(9), end=time(18))
        self.brand = PhoneBrand.objects.create(name="Capacity", slug="capacity")
        self.phone = PhoneModel.objects.create(brand=self.brand, name="Phone", slug="phone", category="phone")
        self.repair_type = RepairType.objects.create(name="Display", slug="display", default_duration_min=120)
        self.employee = Employee.objects.create(name="Мастер", default_percent="35")
        self.customer = CRMClient.objects.create(name="Клиент", phone="+375291111111")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Capacity", model="Phone")

    def aware(self, hour=10):
        return timezone.make_aware(datetime.combine(self.today, time(hour)))

    def appointment(self, *, status="new", minutes=120, hour=10):
        item = Appointment.objects.create(
            phone_model=self.phone, repair_type=self.repair_type,
            start=self.aware(hour), end=self.aware(hour) + timedelta(minutes=30),
            customer_name="Клиент", customer_phone="+375291111111",
            price_original="100", price_final="100", status=status,
        )
        AppointmentItem.objects.create(appointment=item, repair_type=self.repair_type, price="100", duration_min=minutes)
        return item

    def order(self, status, minutes):
        return CRMOrder.objects.create(
            client=self.customer, device=self.device, employee=self.employee,
            issue_description="Не включается", status=status,
            estimated_work_minutes_snapshot=minutes,
        )

    def test_effective_capacity_uses_reserve_and_manual_adjustment(self):
        self.assertEqual(day_capacity(self.today)["effective"], 600)
        day = WorkshopDayCapacity.objects.create(
            date=self.today, capacity_minutes=720, reserve_minutes=120,
            manual_adjustment_minutes=30,
        )
        self.assertEqual(day.effective_capacity, 630)

    def test_only_unconverted_new_and_confirmed_appointments_reserve_workload(self):
        self.appointment(status="new", minutes=120, hour=10)
        self.appointment(status="confirmed", minutes=90, hour=11)
        self.appointment(status="done", minutes=300, hour=12)
        self.appointment(status="cancelled", minutes=300, hour=13)
        self.appointment(status="no_show", minutes=300, hour=14)
        self.assertEqual(appointment_reserved_minutes(self.today), 210)

    def test_all_active_order_statuses_count_but_paused_and_completed_do_not(self):
        for status in [CRMOrder.Status.ACCEPTED, CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.REPAIR]:
            self.order(status, 60)
        for status in [CRMOrder.Status.APPROVAL, CRMOrder.Status.WAITING_PART, CRMOrder.Status.READY, CRMOrder.Status.ISSUED, CRMOrder.Status.CANCELED]:
            self.order(status, 90)
        self.assertEqual(active_crm_backlog_minutes(), 180)

    def test_active_backlog_counts_and_paused_orders_are_separate(self):
        self.order(CRMOrder.Status.ACCEPTED, 240)
        self.order(CRMOrder.Status.APPROVAL, 300)
        self.order(CRMOrder.Status.READY, None)
        result = capacity_dashboard()
        self.assertEqual(result["today"]["backlog"], 240)
        self.assertEqual(result["paused_count"], 1)
        self.assertEqual(result["physical_count"], 3)

    def test_closed_day_and_partial_block_reject_slots(self):
        slot = self.aware(10)
        BookingBlock.objects.create(date=self.today, start_time=time(9, 30), end_time=time(10, 30))
        self.assertFalse(slot_is_available(slot, 60))
        WorkshopDayCapacity.objects.create(date=self.today, is_closed=True)
        self.assertFalse(slot_is_available(self.aware(11), 60))

    def test_two_parallel_intakes_are_allowed_and_third_is_rejected(self):
        slot = self.aware(10)
        first = self.appointment(minutes=30, hour=10)
        self.assertTrue(slot_is_available(slot, 30))
        second = self.appointment(minutes=30, hour=10)
        self.assertFalse(slot_is_available(slot, 30))
        second.status = "cancelled"; second.save(update_fields=["status"])
        self.assertTrue(slot_is_available(slot, 30))
        first.status = "no_show"; first.save(update_fields=["status"])
        self.assertTrue(slot_is_available(slot, 30))

    def test_generated_intake_slots_use_thirty_minute_grid(self):
        tomorrow = self.today + timedelta(days=1)
        WorkingHour.objects.create(weekday=tomorrow.weekday(), start=time(10), end=time(12))
        slots = get_available_slots(self.phone, self.repair_type, days=1, start_date=tomorrow)
        self.assertGreaterEqual(len(slots), 4)
        self.assertEqual(slots[1] - slots[0], timedelta(minutes=30))

    @override_settings(REPAIRS_MAX_PARALLEL_APPOINTMENTS=1)
    def test_historical_long_appointment_still_blocks_overlapping_intake(self):
        old = self.appointment(minutes=180, hour=10)
        old.end = old.start + timedelta(hours=3)
        old.save(update_fields=["end"])
        self.assertFalse(slot_is_available(self.aware(11), 30))

    def test_capacity_allows_short_work_but_rejects_long_work(self):
        WorkshopDayCapacity.objects.create(date=self.today, capacity_minutes=180, reserve_minutes=0)
        self.order(CRMOrder.Status.ACCEPTED, 120)
        self.assertTrue(slot_is_available(self.aware(10), 60))
        self.assertFalse(slot_is_available(self.aware(11), 90))

    def test_conversion_is_counted_once_in_crm_not_twice(self):
        appointment = self.appointment(minutes=60)
        self.assertEqual(appointment_reserved_minutes(self.today), 60)
        appointment.status = "done"; appointment.save(update_fields=["status"])
        order = self.order(CRMOrder.Status.ACCEPTED, 60)
        order.source_appointment = appointment; order.save(update_fields=["source_appointment"])
        self.assertEqual(appointment_reserved_minutes(self.today), 0)
        self.assertEqual(active_crm_backlog_minutes(), 60)

    def test_walk_in_and_warranty_orders_both_add_workload(self):
        self.order(CRMOrder.Status.ACCEPTED, 60)
        warranty = self.order(CRMOrder.Status.DIAGNOSTIC, 90)
        warranty.order_type = CRMOrder.Type.WARRANTY
        warranty.save(update_fields=["order_type"])
        self.assertEqual(active_crm_backlog_minutes(), 150)

    def test_overload_is_allowed_and_reported(self):
        WorkshopDayCapacity.objects.create(date=self.today, capacity_minutes=100, reserve_minutes=0)
        self.order(CRMOrder.Status.REPAIR, 180)
        self.assertEqual(capacity_dashboard()["today"]["overload"], 80)

    def test_no_show_does_not_reserve_capacity(self):
        appointment = self.appointment(status="new", minutes=120)
        user = get_user_model().objects.create_user("worker", password="pass")
        user.user_permissions.add(Permission.objects.get(codename="access_crm"))
        self.client.force_login(user)
        response = self.client.post(reverse("crm:appointment_no_show", args=[appointment.pk]))
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "no_show")
        self.assertEqual(appointment_reserved_minutes(self.today), 0)

    def test_capacity_page_is_read_only_for_worker_and_editable_for_admin(self):
        worker = get_user_model().objects.create_user("worker2", password="pass")
        worker.user_permissions.add(Permission.objects.get(codename="access_crm"))
        self.client.force_login(worker)
        self.assertEqual(self.client.get(reverse("crm:capacity")).status_code, 200)
        self.assertEqual(self.client.post(reverse("crm:capacity"), {"date": self.today}).status_code, 403)
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pass")
        self.client.force_login(admin)
        response = self.client.post(reverse("crm:capacity"), {
            "date": self.today, "capacity_minutes": 800, "reserve_minutes": 100,
            "manual_adjustment_minutes": -20, "note": "Сокращённый день",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WorkshopDayCapacity.objects.get(date=self.today).effective_capacity, 680)

    def test_capacity_page_denies_anonymous_and_client(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("crm:capacity")).status_code, 302)
        client_user = get_user_model().objects.create_user("client", password="pass")
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(reverse("crm:capacity")).status_code, 403)
