from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMClient, CRMDevice, CRMOrder
from finance.models import Employee
from repairs.booking_capacity import (
    active_crm_backlog_minutes, appointment_reserved_minutes, capacity_dashboard,
    CapacitySnapshot, appointment_workload, day_capacity, project_load, slot_is_available,
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
        self.assertEqual([timezone.localtime(slot).time() for slot in slots], [time(10)])

    def test_slots_require_full_service_duration_before_working_day_end(self):
        day = self.today + timedelta(days=1)
        WorkingHour.objects.create(weekday=day.weekday(), start=time(10), end=time(18))
        slots = get_available_slots(self.phone, self.repair_type, days=1, start_date=day)
        times = [timezone.localtime(slot).time() for slot in slots]
        self.assertIn(time(15, 30), times)
        self.assertIn(time(16), times)
        self.assertNotIn(time(16, 30), times)
        self.assertNotIn(time(17), times)
        self.assertNotIn(time(17, 30), times)

        self.repair_type.default_duration_min = 30
        self.repair_type.save(update_fields=["default_duration_min"])
        short_slots = get_available_slots(self.phone, self.repair_type, days=1, start_date=day)
        self.assertIn(time(17, 30), [timezone.localtime(slot).time() for slot in short_slots])

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


@override_settings(WORKSHOP_DEFAULT_CAPACITY_MINUTES=720, WORKSHOP_DEFAULT_RESERVE_MINUTES=120,
                   BOOKING_INTAKE_SLOT_MINUTES=30, REPAIRS_MAX_PARALLEL_APPOINTMENTS=2)
class CapacitySnapshotTests(TestCase):
    setUp = WorkshopCapacityTests.setUp
    aware = WorkshopCapacityTests.aware
    appointment = WorkshopCapacityTests.appointment
    order = WorkshopCapacityTests.order

    def test_prefetched_multiple_items_and_legacy_fallback_without_queries(self):
        appointment = self.appointment(minutes=120)
        AppointmentItem.objects.create(appointment=appointment, repair_type=self.repair_type,
                                       price="10", duration_min=45)
        appointment = Appointment.objects.prefetch_related("items").get(pk=appointment.pk)
        with self.assertNumQueries(0):
            self.assertEqual(appointment_workload(appointment), 165)
        appointment.items.all().delete()
        appointment = Appointment.objects.prefetch_related("items").get(pk=appointment.pk)
        with self.assertNumQueries(0):
            self.assertEqual(appointment_workload(appointment), 30)

    @override_settings(REPAIRS_MAX_PARALLEL_APPOINTMENTS=1)
    def test_converted_appointment_still_occupies_time_and_strict_boundaries(self):
        appointment = self.appointment(minutes=120)
        order = self.order(CRMOrder.Status.ACCEPTED, 120)
        order.source_appointment = appointment
        order.save(update_fields=["source_appointment"])
        snapshot = CapacitySnapshot(self.today, self.today)
        self.assertEqual(snapshot.projection[self.today]["reserved"], 0)
        with self.assertNumQueries(0):
            self.assertFalse(snapshot.slot_is_available(appointment.start, 30))
            self.assertTrue(snapshot.slot_is_available(appointment.end, 30))
            self.assertTrue(snapshot.slot_is_available(appointment.start - timedelta(minutes=30), 30))

    def test_backlog_carries_over_closed_and_nonworking_days(self):
        WorkingHour.objects.all().delete()
        for offset in (0, 1, 3):
            WorkingHour.objects.create(weekday=(self.today + timedelta(days=offset)).weekday(), start=time(9), end=time(18))
        WorkshopDayCapacity.objects.create(date=self.today, capacity_minutes=200, reserve_minutes=50,
                                           manual_adjustment_minutes=-50)
        WorkshopDayCapacity.objects.create(date=self.today + timedelta(days=1), is_closed=True)
        self.order(CRMOrder.Status.REPAIR, 800)
        rows = project_load(self.today, 4)
        self.assertEqual(rows[self.today]["backlog"], 800)
        self.assertEqual(rows[self.today + timedelta(days=1)]["available"], 0)
        self.assertEqual(rows[self.today + timedelta(days=2)]["available"], 0)
        self.assertEqual(rows[self.today + timedelta(days=3)]["backlog"], 700)

    def test_fresh_check_sees_new_reservation(self):
        snapshot = CapacitySnapshot(self.today, self.today)
        self.assertTrue(snapshot.slot_is_available(self.aware(9), 540))
        self.appointment(minutes=120)
        self.assertFalse(slot_is_available(self.aware(9), 540))

    @override_settings(REPAIRS_MAX_PARALLEL_APPOINTMENTS=1)
    def test_timezone_and_legacy_visit_crossing_midnight(self):
        with timezone.override("Europe/Minsk"):
            day = timezone.localdate()
            WorkingHour.objects.get_or_create(weekday=day.weekday(), start=time(9), end=time(18))
            appointment = self.appointment(minutes=120)
            appointment.start = timezone.make_aware(datetime.combine(day + timedelta(days=1), time(0, 15)))
            appointment.end = appointment.start + timedelta(minutes=30)
            appointment.save(update_fields=["start", "end"])
            snapshot = CapacitySnapshot(day, day + timedelta(days=1))
            self.assertEqual(snapshot.reserved.get(day, 0), 0)
            self.assertEqual(snapshot.reserved[day + timedelta(days=1)], 120)
            slot = timezone.make_aware(datetime.combine(day, time(23, 45)))
            self.assertFalse(slot_is_available(slot, 30, appointment_duration_minutes=60))

    def test_multiple_work_intervals_and_blocks(self):
        tomorrow = self.today + timedelta(days=1)
        WorkingHour.objects.create(weekday=tomorrow.weekday(), start=time(9), end=time(11))
        WorkingHour.objects.create(weekday=tomorrow.weekday(), start=time(12), end=time(14))
        BookingBlock.objects.create(date=tomorrow, start_time=time(9, 30), end_time=time(10))
        slots = get_available_slots(self.phone, self.repair_type, days=1, start_date=tomorrow)
        self.assertEqual([timezone.localtime(slot).time() for slot in slots], [time(9), time(12)])

    def test_horizon_clamped_before_forecast_and_last_day_included(self):
        from repairs.views import MAX_BOOK_AHEAD_DAYS
        last = self.today + timedelta(days=MAX_BOOK_AHEAD_DAYS)
        WorkingHour.objects.get_or_create(weekday=last.weekday(), start=time(9), end=time(18))
        with patch("repairs.views.CapacitySnapshot", wraps=CapacitySnapshot) as build:
            slots = get_available_slots(self.phone, self.repair_type, days=100, start_date=self.today - timedelta(days=5))
        build.assert_called_once_with(self.today, last)
        self.assertTrue(any(timezone.localtime(slot).date() == last for slot in slots))
        self.assertTrue(all(self.today <= timezone.localtime(slot).date() <= last for slot in slots))
        with patch("repairs.views.CapacitySnapshot", wraps=CapacitySnapshot) as build:
            self.assertEqual(get_available_slots(self.phone, self.repair_type, days=10, start_date=last + timedelta(days=1)), [])
        build.assert_not_called()

    def test_query_count_bounded_for_full_calendar_and_many_items(self):
        for hour in range(9, 18):
            self.appointment(minutes=15, hour=hour)
        with CaptureQueriesContext(connection) as short:
            get_available_slots(self.phone, self.repair_type, days=1)
        with CaptureQueriesContext(connection) as long:
            get_available_slots(self.phone, self.repair_type, days=42)
        self.assertLessEqual(len(long), len(short) + 1)
        self.assertLessEqual(len(long), 10)

    def test_current_and_next_month_view_have_one_forecast_and_bounded_sql(self):
        url = reverse("repairs:slot_select", args=[self.brand.slug, self.phone.slug, self.repair_type.slug])
        for month in (self.today.replace(day=1), (self.today.replace(day=1) + timedelta(days=32)).replace(day=1)):
            with self.subTest(month=month), patch("repairs.booking_capacity.project_load", wraps=project_load) as forecast:
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url, {"month": month.strftime("%Y-%m")})
                self.assertEqual(response.status_code, 200)
                self.assertLess(len(queries), 40)
                forecast.assert_called_once()
                self.assertContains(response, 'content="noindex, nofollow"')

    def test_robots_only_disallows_slot_calendars(self):
        response = self.client.get(reverse("robots_txt"))
        rules = response.content.decode().splitlines()
        self.assertIn("Disallow: /repairs/*/slots/", rules)
        self.assertNotIn("Disallow: /repairs/", rules)

    def test_matches_original_query_based_reference(self):
        # Independent pre-optimization algorithm: intentionally slow, test-only.
        for weekday in range(7):
            WorkingHour.objects.get_or_create(weekday=weekday, start=time(9), end=time(18))
        self.order(CRMOrder.Status.REPAIR, 900)
        self.order(CRMOrder.Status.WAITING_PART, 900)
        self.appointment(minutes=75)
        self.appointment(status="no_show", minutes=500)
        WorkshopDayCapacity.objects.create(date=self.today + timedelta(days=1), is_closed=True)
        BookingBlock.objects.create(date=self.today + timedelta(days=2), start_time=time(10), end_time=time(11))
        snapshot = CapacitySnapshot(self.today, self.today + timedelta(days=3))
        old_slots, new_slots = [], []
        for offset in range(4):
            day = self.today + timedelta(days=offset)
            backlog = active_crm_backlog_minutes()
            for prior in range(offset + 1):
                current = self.today + timedelta(days=prior)
                cap = day_capacity(current)
                reserved = appointment_reserved_minutes(current)
                working = WorkingHour.objects.filter(weekday=current.weekday()).exists() and not cap["closed"]
                available = max(0, cap["effective"] - reserved - backlog) if working else 0
                if working:
                    backlog = max(0, backlog - max(0, cap["effective"] - reserved))
            for half_hour in range(18):
                start = timezone.make_aware(datetime.combine(day, time(9))) + timedelta(minutes=30 * half_hour)
                end = start + timedelta(minutes=30)
                blocked = any(b.start_time < timezone.localtime(end).time() and b.end_time > timezone.localtime(start).time()
                              for b in BookingBlock.objects.filter(date=day, is_active=True))
                overlaps = Appointment.objects.filter(status__in=("new", "confirmed"), start__lt=end, end__gt=start).count()
                service_end = timezone.localtime(start + timedelta(minutes=120)).time()
                if working and available >= 120 and service_end <= time(18) and not blocked and overlaps < 2:
                    old_slots.append(start)
                if snapshot.slot_is_available(start, 120):
                    new_slots.append(start)
        self.assertEqual(old_slots, new_slots)
        self.assertTrue(new_slots)
