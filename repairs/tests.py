from datetime import timedelta

from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType
from repairs.views import BOOKING_SUCCESS_TOKEN_SALT


class BookingSuccessViewTests(TestCase):
    def setUp(self):
        self.brand = PhoneBrand.objects.create(name="Apple", slug="apple")
        self.model = PhoneModel.objects.create(
            brand=self.brand,
            name="iPhone 14",
            slug="iphone-14",
            category="phone",
        )
        self.repair_type = RepairType.objects.create(
            name="Замена дисплея",
            slug="screen-repair",
            default_duration_min=60,
        )
        start = timezone.now() + timedelta(days=1)
        self.appointment = Appointment.objects.create(
            phone_model=self.model,
            repair_type=self.repair_type,
            start=start,
            end=start + timedelta(hours=1),
            customer_name="Иван",
            customer_phone="+375445684493",
            price_original="100.00",
            price_final="100.00",
            discount_amount="0.00",
        )

    def _token(self, appointment_id: int | None = None) -> str:
        return signing.dumps(
            {"appointment_id": appointment_id or self.appointment.id},
            salt=BOOKING_SUCCESS_TOKEN_SALT,
        )

    def test_booking_success_requires_token(self):
        response = self.client.get(
            reverse("repairs:booking_success", kwargs={"appointment_id": self.appointment.id})
        )
        self.assertEqual(response.status_code, 404)

    def test_booking_success_rejects_token_for_another_appointment(self):
        other = Appointment.objects.create(
            phone_model=self.model,
            repair_type=self.repair_type,
            start=self.appointment.start + timedelta(hours=2),
            end=self.appointment.end + timedelta(hours=2),
            customer_name="Петр",
            customer_phone="+375445684494",
            price_original="120.00",
            price_final="120.00",
            discount_amount="0.00",
        )
        response = self.client.get(
            reverse("repairs:booking_success", kwargs={"appointment_id": self.appointment.id}),
            {"token": self._token(other.id)},
        )
        self.assertEqual(response.status_code, 404)

    def test_booking_success_allows_valid_token(self):
        response = self.client.get(
            reverse("repairs:booking_success", kwargs={"appointment_id": self.appointment.id}),
            {"token": self._token()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Иван")


class ContactsPageTests(TestCase):
    def test_contacts_page_uses_existing_gallery_images_only(self):
        response = self.client.get(reverse("repairs:contacts"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "img/contacts/05.jpg")
        self.assertNotContains(response, "img/contacts/06.jpg")


class SeoPagesTests(TestCase):
    def test_homepage_is_available(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ремонт iPhone, Google Pixel и других телефонов в Гомеле")

    def test_robots_txt_exposes_sitemap(self):
        response = self.client.get(reverse("robots_txt"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User-agent: *")
        self.assertContains(response, "Sitemap:")

    def test_sitemap_is_available(self):
        response = self.client.get(reverse("django.contrib.sitemaps.views.sitemap"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<urlset", html=False)
