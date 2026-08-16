from datetime import timedelta
from io import BytesIO

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType
from repairs.views import BOOKING_SUCCESS_TOKEN_SALT
from news.models import NewsCategory, NewsImage, NewsPost


def make_uploaded_image(name: str = "test.jpg", color: str = "red") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (40, 40), color=color).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


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


class WebpConversionTests(TestCase):
    def test_phone_brand_logo_converts_to_webp(self):
        brand = PhoneBrand.objects.create(
            name="TestBrand",
            slug="testbrand",
            logo=make_uploaded_image("brand.jpg"),
        )
        self.assertTrue(brand.logo.name.endswith(".webp"))

    def test_phone_model_image_converts_to_webp(self):
        brand = PhoneBrand.objects.create(name="Brand", slug="brand")
        model = PhoneModel.objects.create(
            brand=brand,
            name="Model 1",
            slug="model-1",
            category="phone",
            image=make_uploaded_image("model.jpg"),
        )
        self.assertTrue(model.image.name.endswith(".webp"))

    def test_news_images_convert_to_webp(self):
        category = NewsCategory.objects.create(title="Tech", slug="tech")
        post = NewsPost.objects.create(
            category=category,
            title="Post",
            slug="post",
            cover=make_uploaded_image("cover.jpg"),
            status=NewsPost.Status.DRAFT,
        )
        image = NewsImage.objects.create(
            post=post,
            position=1,
            image=make_uploaded_image("body.jpg"),
        )
        self.assertTrue(post.cover.name.endswith(".webp"))
        self.assertTrue(image.image.name.endswith(".webp"))
