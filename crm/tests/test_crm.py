from decimal import Decimal
from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from finance.models import Employee, PartCatalog, PartItem, RepairFinance, StockReceipt, Supplier
from crm.models import CRMClient, CRMDevice, CRMEvent, CRMOrder, CRMOrderPartUsage, CRMWorkItem, normalize_phone
from crm.services import change_status, install_part, return_part
from core.storage import private_media_storage


class CRMBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("crm-user", password="pass", is_staff=True)
        self.user.user_permissions.set(Permission.objects.filter(codename__in=[
            "access_crm", "view_crmorder", "add_crmorder", "change_crmorder",
            "change_order_status", "manage_documents",
        ]))
        self.client.force_login(self.user)
        self.employee = Employee.objects.create(name="Сергей", default_percent="35")
        self.customer = CRMClient.objects.create(name="Иван", phone="80291234567")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Samsung", model="A55", serial_number="123456789", unlock_code="7391")

    def order(self, **kwargs):
        values = dict(client=self.customer, device=self.device, employee=self.employee, issue_description="Не включается", agreed_price="250")
        values.update(kwargs)
        return CRMOrder.objects.create(**values)


class ModelTests(CRMBase):
    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("80 (29) 123-45-67"), "+375291234567")
        self.assertEqual(self.customer.normalized_phone, "+375291234567")

    def test_device_is_linked_and_searchable_by_serial(self):
        response = self.client.get(reverse("crm:client_list"), {"q": "123456789"})
        self.assertContains(response, "Иван")

    def test_order_has_unique_public_number_and_employee(self):
        first, second = self.order(), self.order()
        self.assertRegex(first.number, r"^R[A-Z0-9]{8}$")
        self.assertNotEqual(first.number, second.number)
        self.assertEqual(first.employee, self.employee)
        self.assertIsNone(first.source_appointment)

    def test_multiple_work_items_use_decimal(self):
        order = self.order()
        CRMWorkItem.objects.create(order=order, name="Работа 1", quantity="1.50", unit_price="40.00")
        CRMWorkItem.objects.create(order=order, name="Работа 2", quantity="1", unit_price="20.00")
        self.assertEqual(order.works_total, Decimal("80.00"))


class WorkflowTests(CRMBase):
    def test_allowed_waiting_part_workflow(self):
        order = self.order(status=CRMOrder.Status.DIAGNOSTIC)
        change_status(order, CRMOrder.Status.WAITING_PART, self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.WAITING_PART)
        change_status(order, CRMOrder.Status.REPAIR, self.user)

    def test_invalid_transition_is_rejected(self):
        order = self.order()
        with self.assertRaisesMessage(Exception, "переход"):
            change_status(order, CRMOrder.Status.ISSUED, self.user)

    def test_ready_requires_work(self):
        order = self.order(status=CRMOrder.Status.REPAIR)
        with self.assertRaisesMessage(Exception, "работу"):
            change_status(order, CRMOrder.Status.READY, self.user)

    def test_issued_sets_dates_and_starts_warranty(self):
        order = self.order(status=CRMOrder.Status.READY, warranty_days=90)
        change_status(order, CRMOrder.Status.ISSUED, self.user)
        order.refresh_from_db()
        self.assertIsNotNone(order.issued_at)
        self.assertEqual(order.warranty_started_at, order.issued_at)
        self.assertIsNotNone(order.warranty_until)
        self.assertFalse(order.allowed_next_statuses)

    def test_event_contains_old_and_new_status_without_unlock_code(self):
        order = self.order()
        change_status(order, CRMOrder.Status.DIAGNOSTIC, self.user)
        event = order.events.get(event_type=CRMEvent.Type.STATUS)
        self.assertEqual(event.old_value, "Принято")
        self.assertEqual(event.new_value, "Диагностика")
        self.assertNotIn(self.device.unlock_code, event.description)


class ViewAndPermissionTests(CRMBase):
    def test_order_create_uses_full_screen_continuous_form(self):
        response = self.client.get(reverse("crm:order_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="intake-modal-host"')
        self.assertContains(response, 'class="intake-overlay"')
        self.assertContains(response, 'class="intake-dialog crm-intake-modal"')
        self.assertNotContains(response, 'wb-modal--intake')
        self.assertContains(response, 'data-intake-section="1"')
        self.assertContains(response, 'data-intake-section="5"')
        self.assertEqual(response.content.count(b'data-intake-form'), 1)

    def test_order_create_htmx_returns_only_reusable_wizard(self):
        response = self.client.get(reverse("crm:order_create"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/partials/intake_wizard.html")
        self.assertContains(response, 'data-intake-wizard')
        self.assertContains(response, 'class="intake-overlay"')
        self.assertContains(response, 'hx-target="#intake-modal-host"')
        self.assertNotContains(response, 'data-modal-variant')
        self.assertNotContains(response, '<html')

    def test_invalid_order_displays_errors_in_continuous_form(self):
        response = self.client.post(reverse("crm:order_create"), {
            "client_name": "Иван", "phone": "", "device_type": "Телефон",
            "issue_description": "Не включается", "order_type": "paid",
            "agreed_price": "100", "warranty_days": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проверьте выделенные поля.')
        self.assertContains(response, "Выберите клиента или заполните имя и телефон")

    def test_invalid_htmx_order_replaces_dedicated_intake_host(self):
        response = self.client.post(reverse("crm:order_create"), {
            "client_name": "Иван", "phone": "", "device_type": "Телефон",
            "issue_description": "Не включается", "order_type": "paid",
            "agreed_price": "100", "warranty_days": "0",
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'class="intake-overlay"', status_code=422)
        self.assertContains(response, 'hx-target="#intake-modal-host"', status_code=422)
        self.assertContains(response, 'Проверьте выделенные поля.', status_code=422)
        self.assertNotContains(response, 'id="app-modal-content"', status_code=422)

    def test_anonymous_and_unprivileged_users_cannot_access(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 302)
        ordinary = get_user_model().objects.create_user("ordinary", password="pass", is_staff=True)
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 403)

    def test_authorized_user_can_search_and_view_client(self):
        self.assertEqual(self.client.get(reverse("crm:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("crm:client_search"), {"q": "8029"}).json()["results"][0]["name"], "Иван")
        self.assertContains(self.client.get(reverse("crm:client_detail", args=[self.customer.pk])), "Samsung A55")

    def test_create_order_with_new_client_and_device(self):
        response = self.client.post(reverse("crm:order_create"), {
            "client_name": "Пётр", "phone": "+375441112233", "device_type": "Телефон",
            "brand": "Apple", "device_model": "iPhone 14", "imei": "356789012345678", "serial_number": "SN-14",
            "condition": "Царапины на рамке", "included_items": "Чехол", "issue_description": "Разбит экран",
            "employee": self.employee.pk, "order_type": "paid", "agreed_price": "300", "warranty_days": "90",
        })
        self.assertEqual(response.status_code, 302)
        order = CRMOrder.objects.get(client__name="Пётр", device__model="iPhone 14")
        self.assertEqual(order.device.imei, "356789012345678")
        self.assertEqual(order.device.serial_number, "SN-14")
        self.assertEqual(order.condition_on_intake, "Царапины на рамке")
        self.assertEqual(order.accessories_on_intake, "Чехол")

    def test_warranty_order_copies_client_and_device(self):
        source = self.order(status=CRMOrder.Status.ISSUED)
        response = self.client.post(reverse("crm:order_create") + f"?warranty_source={source.pk}", {
            "warranty_source": source.pk, "issue_description": "Повторный дефект", "order_type": "warranty",
            "agreed_price": "0", "warranty_days": "0",
        })
        self.assertEqual(response.status_code, 302)
        followup = CRMOrder.objects.exclude(pk=source.pk).get()
        self.assertEqual(followup.client, source.client)
        self.assertEqual(followup.device, source.device)
        self.assertEqual(followup.warranty_source_order, source)

    def test_documents_open_only_with_permission(self):
        order = self.order(status=CRMOrder.Status.ISSUED, warranty_days=90)
        for name in ("receipt", "act", "warranty"):
            self.assertEqual(self.client.get(reverse(f"crm:{name}", args=[order.pk])).status_code, 200)
        self.user.user_permissions.remove(Permission.objects.get(codename="manage_documents"))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("crm:receipt", args=[order.pk])).status_code, 403)

    def test_receipt_has_two_safe_copies_and_intake_snapshot(self):
        self.device.imei = "356789012345678"
        self.device.serial_number = "SAFE-SERIAL"
        self.device.condition = "Текущее состояние"
        self.device.save()
        order = self.order(
            agreed_price="0", condition_on_intake="Скол при приёме",
            accessories_on_intake="Чехол", internal_note="Секретная заметка",
        )
        response = self.client.get(reverse("crm:receipt", args=[order.pk]))
        self.assertContains(response, "Экземпляр клиента")
        self.assertContains(response, "Экземпляр сервиса")
        self.assertContains(response, "Скол при приёме", count=2)
        self.assertContains(response, "Уточняется", count=2)
        self.assertNotContains(response, "Секретная заметка")
        self.assertNotContains(response, self.device.unlock_code)

    def test_receipt_anonymous_redirects_and_client_role_is_denied(self):
        order = self.order()
        self.client.logout()
        url = reverse("crm:receipt", args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        client_user = get_user_model().objects.create_user("receipt-client", password="pass")
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_attachment_uses_protected_view(self):
        with TemporaryDirectory() as private_dir:
            old_location = private_media_storage._location
            private_media_storage._location = private_dir
            private_media_storage.__dict__.pop("base_location", None)
            private_media_storage.__dict__.pop("location", None)
            try:
                order = self.order()
                response = self.client.post(reverse("crm:attachment_add", args=[order.pk]), {
                    "file": SimpleUploadedFile("photo.jpg", b"test", content_type="image/jpeg"), "description": "До ремонта",
                })
                self.assertEqual(response.status_code, 302)
                attachment = order.attachments.get()
                self.assertEqual(self.client.get(reverse("crm:attachment_open", args=[attachment.pk])).status_code, 200)
            finally:
                private_media_storage._location = old_location
                private_media_storage.__dict__.pop("base_location", None)
                private_media_storage.__dict__.pop("location", None)

    def test_htmx_dashboard_returns_only_order_results(self):
        order = self.order()
        response = self.client.get(reverse("crm:dashboard"), {"q": order.number}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="crm-order-results"')
        self.assertNotContains(response, 'class="wb-sidebar"')

    def test_invalid_per_page_uses_safe_default(self):
        for value in ("abc", "-100", "0", "999999999"):
            response = self.client.get(reverse("crm:dashboard"), {"per_page": value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].paginator.per_page, 25)

    def test_valid_per_page_is_accepted(self):
        for value in ("25", "50", "100"):
            response = self.client.get(reverse("crm:dashboard"), {"per_page": value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].paginator.per_page, int(value))

    def test_quick_status_accepts_valid_post_and_returns_partial(self):
        order = self.order()
        response = self.client.post(
            reverse("crm:order_status", args=[order.pk]), {"status": CRMOrder.Status.DIAGNOSTIC},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("workbench:toast", response.headers["HX-Trigger"])
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.DIAGNOSTIC)

    def test_quick_status_rejects_invalid_transition(self):
        order = self.order()
        response = self.client.post(
            reverse("crm:order_status", args=[order.pk]), {"status": CRMOrder.Status.ISSUED},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 422)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.ACCEPTED)

    def test_quick_status_is_post_only_and_permission_protected(self):
        order = self.order()
        url = reverse("crm:order_status", args=[order.pk])
        self.assertEqual(self.client.get(url, HTTP_HX_REQUEST="true").status_code, 405)
        self.user.user_permissions.remove(Permission.objects.get(codename="change_order_status"))
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.post(url, {"status": CRMOrder.Status.DIAGNOSTIC}, HTTP_HX_REQUEST="true").status_code, 403)

    def test_quick_assignee_uses_active_employee_and_permission(self):
        order = self.order(employee=None)
        other = Employee.objects.create(name="Александр", default_percent="30")
        url = reverse("crm:order_assignee", args=[order.pk])
        response = self.client.post(url, {"employee": other.pk}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.employee, other)
        self.assertTrue(order.events.filter(event_type=CRMEvent.Type.EMPLOYEE).exists())

    def test_global_search_is_limited_and_uses_existing_data(self):
        order = self.order()
        response = self.client.get(reverse("crm:global_search"), {"q": order.number[:4]}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.number)
        self.assertContains(response, self.customer.name)


class CRMOrderPartTests(CRMBase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="PartsMarket")
        self.catalog = PartCatalog.objects.create(brand="Apple", device_model="iPhone 13", name="OLED дисплей")
        self.receipt = StockReceipt.objects.create(
            date=date(2026, 9, 1), supplier=self.supplier, part=self.catalog,
            quantity=3, unit_cost="85.00", warranty_days=90,
        )
        self.repair = self.order(agreed_price="250.00")

    def test_install_part_creates_snapshot_finance_and_history(self):
        item = self.receipt.items.first()
        usage = install_part(self.repair, item.pk, self.user)
        item.refresh_from_db()
        usage.finance_repair.refresh_from_db()
        self.assertEqual(item.status, PartItem.Status.INSTALLED)
        self.assertEqual(usage.unit_cost_snapshot, Decimal("85.00"))
        self.assertEqual(usage.supplier_name_snapshot, "PartsMarket")
        self.assertEqual(usage.warranty_days_snapshot, 90)
        self.assertEqual(usage.installed_by, self.user)
        self.assertEqual(usage.finance_repair.part_cost, Decimal("85.00"))
        self.assertEqual(usage.finance_repair.repair_margin, Decimal("165.00"))
        self.assertTrue(self.repair.events.filter(event_type=CRMEvent.Type.PART).exists())

    def test_multiple_parts_and_double_install_are_safe(self):
        first, second = list(self.receipt.items.all()[:2])
        usage = install_part(self.repair, first.pk, self.user)
        install_part(self.repair, second.pk, self.user)
        usage.finance_repair.refresh_from_db()
        self.assertEqual(usage.finance_repair.part_cost, Decimal("170.00"))
        with self.assertRaisesMessage(Exception, "отсутствует"):
            install_part(self.repair, first.pk, self.user)
        self.assertEqual(PartItem.objects.filter(status=PartItem.Status.INSTALLED).count(), 2)

    def test_return_is_audited_and_cannot_restore_twice(self):
        item = self.receipt.items.first()
        usage = install_part(self.repair, item.pk, self.user)
        return_part(usage, self.user)
        item.refresh_from_db(); usage.refresh_from_db(); usage.finance_repair.refresh_from_db()
        self.assertEqual(item.status, PartItem.Status.IN_STOCK)
        self.assertEqual(usage.status, CRMOrderPartUsage.Status.REMOVED)
        self.assertEqual(usage.finance_repair.part_cost, Decimal("0.00"))
        with self.assertRaisesMessage(Exception, "уже была снята"):
            return_part(usage, self.user)
        self.assertEqual(PartItem.objects.filter(pk=item.pk, status=PartItem.Status.IN_STOCK).count(), 1)

    def test_http_install_return_are_post_only_and_htmx_updates_block(self):
        item = self.receipt.items.first()
        install_url = reverse("crm:order_part_install", args=[self.repair.pk])
        self.assertEqual(self.client.get(install_url).status_code, 405)
        response = self.client.post(install_url, {"part_item": item.pk}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="crm-order-parts"')
        self.assertIn("workbench:modal-close", response.headers["HX-Trigger"])
        usage = CRMOrderPartUsage.objects.get()
        return_url = reverse("crm:order_part_return", args=[self.repair.pk, usage.pk])
        self.assertEqual(self.client.get(return_url).status_code, 405)
        self.assertEqual(self.client.post(return_url, HTTP_HX_REQUEST="true").status_code, 200)

    def test_unprivileged_user_cannot_install(self):
        item = self.receipt.items.first()
        ordinary = get_user_model().objects.create_user("no-stock", password="pass")
        self.client.force_login(ordinary)
        response = self.client.post(reverse("crm:order_part_install", args=[self.repair.pk]), {"part_item": item.pk})
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, PartItem.Status.IN_STOCK)
