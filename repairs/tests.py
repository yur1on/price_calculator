from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from repairs.models import Appointment, ModelRepairPrice, PhoneBrand, PhoneModel, ReferralPartner, RepairType, WorkingHour
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
        self.extra_repair_type = RepairType.objects.create(
            name="Замена аккумулятора",
            slug="battery-repair",
            default_duration_min=60,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.repair_type,
            price="100.00",
            duration_min=60,
            is_active=True,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.extra_repair_type,
            price="80.00",
            duration_min=60,
            is_active=True,
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

    def test_booking_success_does_not_show_extra_repairs_block(self):
        response = self.client.get(
            reverse("repairs:booking_success", kwargs={"appointment_id": self.appointment.id}),
            {"token": self._token()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Можно добавить к этой заявке")
        self.assertNotContains(response, "Если добавить к текущей записи")

    def test_referral_financial_error_is_logged_without_pii(self):
        partner = ReferralPartner.objects.create(
            name="Партнёр", code="LOGTEST", contact="+375291112233",
        )
        start = self.appointment.start + timedelta(hours=4)
        with patch("repairs.signals._available_credit", side_effect=RuntimeError("balance failed")):
            with self.assertLogs("repairs.signals", level="ERROR") as captured:
                appointment = Appointment.objects.create(
                    phone_model=self.model, repair_type=self.repair_type,
                    start=start, end=start + timedelta(hours=1),
                    customer_name="Лог", customer_phone="+375291112233",
                    price_original="100.00", price_final="100.00",
                )
        log = " ".join(captured.output)
        self.assertIn(f"appointment_id={appointment.pk}", log)
        self.assertIn(f"partner_id={partner.pk}", log)
        self.assertNotIn("+375291112233", log)


class BookingFormViewTests(TestCase):
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
        self.extra_repair_type = RepairType.objects.create(
            name="Замена аккумулятора",
            slug="battery-repair",
            default_duration_min=60,
        )
        self.cheap_repair_type = RepairType.objects.create(
            name="Замена микрофона",
            slug="microphone-repair",
            default_duration_min=60,
        )
        self.display_original_repair_type = RepairType.objects.create(
            name="Замена дисплея оригинал",
            slug="screen-repair-original",
            default_duration_min=60,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.repair_type,
            price="100.00",
            duration_min=60,
            is_active=True,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.extra_repair_type,
            price="80.00",
            duration_min=60,
            is_active=True,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.cheap_repair_type,
            price="70.00",
            duration_min=60,
            is_active=True,
        )
        ModelRepairPrice.objects.create(
            phone_model=self.model,
            repair_type=self.display_original_repair_type,
            price="140.00",
            duration_min=60,
            is_active=True,
        )
        WorkingHour.objects.create(weekday=0, start="10:00", end="18:00")

    def _monday_slot(self):
        local_now = timezone.localtime()
        days_until_monday = (7 - local_now.weekday()) % 7 or 7
        return (local_now + timedelta(days=days_until_monday)).replace(
            hour=10, minute=0, second=0, microsecond=0,
        ).isoformat()

    def test_booking_form_shows_extra_repairs_with_discounted_price(self):
        slot = self._monday_slot()
        response = self.client.get(
            reverse(
                "repairs:book",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                    "repair_slug": self.repair_type.slug,
                },
            ),
            {"slot": slot},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Можно добавить к этой записи")
        self.assertContains(response, "Замена аккумулятора")
        self.assertContains(response, "80,00 BYN")
        self.assertContains(response, "50,00 BYN")
        self.assertContains(response, "Замена микрофона")
        self.assertContains(response, "70,00 BYN")
        self.assertNotContains(response, "По согласованию: не для онлайн-записи")

    def test_booking_form_hides_alternative_display_repairs(self):
        slot = self._monday_slot()
        response = self.client.get(
            reverse(
                "repairs:book",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                    "repair_slug": self.repair_type.slug,
                },
            ),
            {"slot": slot},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Замена дисплея оригинал")

    def test_booking_form_uses_20_byn_discount_for_extra_repairs_cheaper_than_80(self):
        slot = self._monday_slot()
        response = self.client.get(
            reverse(
                "repairs:book",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                    "repair_slug": self.repair_type.slug,
                },
            ),
            {"slot": slot},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Замена микрофона")
        self.assertContains(response, "50,00 BYN")

    def test_booking_form_post_adds_selected_extra_repairs_to_appointment(self):
        slot = self._monday_slot()
        response = self.client.post(
            reverse(
                "repairs:book",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                    "repair_slug": self.repair_type.slug,
                },
            ) + f"?slot={slot}",
            data={
                "customer_name": "Иван",
                "customer_phone": "+375445684493",
                "referral_code": "",
                "consent": "on",
                "extra_repairs": [self.extra_repair_type.slug, self.cheap_repair_type.slug],
            },
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.get(customer_phone="+375445684493")
        self.assertEqual(appointment.end - appointment.start, timedelta(minutes=30))
        self.assertEqual(appointment.workload_minutes, 180)
        self.assertEqual(appointment.services_count, 3)
        self.assertEqual(appointment.services_display, "Замена дисплея, Замена аккумулятора, Замена микрофона")
        self.assertEqual(str(appointment.price_original), "250.00")
        self.assertEqual(str(appointment.combo_discount_amount), "50.00")
        self.assertEqual(str(appointment.price_final), "200.00")
        self.assertEqual(
            list(appointment.items.order_by("position").values_list("repair_type__slug", flat=True)),
            [self.repair_type.slug, self.extra_repair_type.slug, self.cheap_repair_type.slug],
        )

    def test_post_rejects_service_that_ends_after_closing(self):
        price = ModelRepairPrice.objects.get(phone_model=self.model, repair_type=self.repair_type)
        price.duration_min = 120
        price.save(update_fields=["duration_min"])
        slot = datetime.fromisoformat(self._monday_slot()).replace(hour=17).isoformat()
        response = self.client.post(
            reverse("repairs:book", kwargs={
                "brand_slug": self.brand.slug,
                "model_slug": self.model.slug,
                "repair_slug": self.repair_type.slug,
            }) + f"?slot={slot}",
            data={"customer_name": "Поздно", "customer_phone": "+375291234567", "consent": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Appointment.objects.filter(customer_name="Поздно").exists())

    def test_post_allows_service_ending_exactly_at_closing(self):
        price = ModelRepairPrice.objects.get(phone_model=self.model, repair_type=self.repair_type)
        price.duration_min = 120
        price.save(update_fields=["duration_min"])
        slot = datetime.fromisoformat(self._monday_slot()).replace(hour=16).isoformat()
        response = self.client.post(
            reverse("repairs:book", kwargs={
                "brand_slug": self.brand.slug,
                "model_slug": self.model.slug,
                "repair_slug": self.repair_type.slug,
            }) + f"?slot={slot}",
            data={"customer_name": "Граница", "customer_phone": "+375291234568", "consent": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Appointment.objects.filter(customer_name="Граница").exists())

    @patch("repairs.views.lock_day")
    @patch("repairs.views.slot_is_available", side_effect=[True, False])
    def test_booking_rechecks_capacity_after_day_lock(self, availability, lock_day):
        slot = self._monday_slot()
        response = self.client.post(
            reverse("repairs:book", kwargs={
                "brand_slug": self.brand.slug,
                "model_slug": self.model.slug,
                "repair_slug": self.repair_type.slug,
            }) + f"?slot={slot}",
            data={
                "customer_name": "Гонка", "customer_phone": "+375291234567",
                "referral_code": "", "consent": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        lock_day.assert_called_once()
        self.assertEqual(availability.call_count, 2)
        self.assertFalse(Appointment.objects.filter(customer_name="Гонка").exists())


class ContactsPageTests(TestCase):
    def test_contacts_page_uses_existing_gallery_images_only(self):
        response = self.client.get(reverse("repairs:contacts"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "img/contacts/05.jpg")
        self.assertNotContains(response, "img/contacts/06.jpg")


class SeoPagesTests(TestCase):
    def test_homepage_repair_status_card_uses_existing_route(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="home-status"', count=1)
        self.assertContains(response, 'Устройство уже в ремонте?')
        self.assertContains(response, 'Проверьте текущий статус по номеру из квитанции.')
        self.assertContains(response, 'Проверить статус')
        # Header, hero and standalone status block share the existing endpoint.
        self.assertContains(response, f'href="{reverse("repair_status")}"', count=3)

    def test_homepage_is_available(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "repairs/home.html")
        self.assertContains(response, "Ремонт телефонов и техники в Гомеле — Tehsfera")

    def test_home_structure_links_and_seo(self):
        brand = PhoneBrand.objects.create(name="Example", slug="example", logo="brands/example.webp")
        PhoneModel.objects.create(brand=brand, name="Phone", slug="example-phone", category="phone")
        response = self.client.get(reverse("home"))
        self.assertContains(response, '<h1', count=1)
        self.assertContains(response, '<link rel="canonical" href="http://testserver/">')
        self.assertContains(response, '"@type": "RepairService"')
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(response, 'href="/" aria-label="Tehsfera — на главную"')
        for category in ("phone", "tablet", "watch"):
            self.assertContains(response, f'href="/repairs/?cat={category}"')
        self.assertContains(response, 'href="/repairs/example/?cat=phone"')
        self.assertContains(response, 'alt="Ремонт Example в Гомеле"')
        for value in ('кабинет 50', 'tel:+375445684493', 'Пн–Сб 10:00–18:00', 'repairs/css/home.css'):
            self.assertContains(response, value)

    def test_catalog_category_metadata_and_canonical(self):
        for category, label in (("phone", "телефонов"), ("tablet", "планшетов"), ("watch", "смарт-часов")):
            with self.subTest(category=category):
                response = self.client.get(reverse("repairs:brand_list"), {"cat": category})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "repairs/brand_list.html")
                heading = 'Стоимость ремонта телефонов' if category == 'phone' else f'Ремонт {label}'
                self.assertContains(response, f'<h1>{heading} в Гомеле</h1>')
                self.assertContains(response, '<h1', count=1)
                self.assertContains(response, f'<link rel="canonical" href="http://testserver/repairs/?cat={category}">')
                self.assertContains(response, f'Ремонт {label} в Гомеле. Выберите бренд и модель:')
                self.assertNotContains(response, 'repairs/css/home.css')
                self.assertNotContains(response, 'Ремонт телефонов<br>и техники')
                if category != "phone":
                    self.assertNotContains(response, '<h1>Ремонт телефонов')

    def test_brand_category_and_model_urls_are_preserved(self):
        brand = PhoneBrand.objects.create(name="Example", slug="example")
        for category, label in (("phone", "телефонов"), ("tablet", "планшетов"), ("watch", "смарт-часов")):
            model = PhoneModel.objects.create(brand=brand, name=category, slug=category, category=category)
            response = self.client.get(reverse("repairs:model_list", args=[brand.slug]), {"cat": category})
            self.assertContains(response, f'Ремонт {label} Example в Гомеле')
            self.assertContains(response, f'href="{reverse("repairs:repair_list", args=[brand.slug, model.slug])}"')

    def test_home_query_category_keeps_root_canonical(self):
        response = self.client.get(reverse("home"), {"cat": "tablet"})
        self.assertContains(response, '<link rel="canonical" href="http://testserver/">')
        self.assertTemplateUsed(response, "repairs/home.html")

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
