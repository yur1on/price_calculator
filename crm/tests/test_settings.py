from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from crm.forms import IntakeForm
from crm.models import (
    CRMBrand,
    CRMClient,
    CRMDevice,
    CRMDeviceModel,
    CRMDeviceType,
    CRMDocumentSettings,
    CRMIssueTemplate,
    CRMOrder,
    CRMWarrantyTerm,
    CRMWorkItem,
    CRMWorkTemplate,
)
from finance.models import Employee


class CRMSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "settings-user", password="pass", is_staff=True
        )
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="crm",
                codename__in=[
                    "access_crm", "add_crmorder", "manage_crm_settings", "manage_documents",
                ],
            )
        )
        self.client.force_login(self.user)

    def post_setting(self, section, **values):
        values.update({"section": section, "action": "save"})
        return self.client.post(reverse("crm:settings"), values)

    def test_settings_require_specific_permission(self):
        self.assertEqual(self.client.get(reverse("crm:settings")).status_code, 200)
        self.user.user_permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="manage_crm_settings")
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("crm:settings")).status_code, 403)

    def test_superuser_can_open_settings(self):
        admin = get_user_model().objects.create_superuser("root", "root@example.com", "pass")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("crm:settings")).status_code, 200)

    def test_issue_template_create_edit_toggle_and_intake_filter(self):
        response = self.post_setting("issues", **{
            "issues-name": "Экран", "issues-text": "Разбит экран",
            "issues-sort_order": "10", "issues-is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        template = CRMIssueTemplate.objects.get(name="Экран")
        self.assertTrue(IntakeForm().fields["issue_template"].queryset.filter(pk=template.pk).exists())
        self.post_setting("issues", **{
            "pk": template.pk, "issues-name": "Дисплей",
            "issues-text": "Нет изображения", "issues-sort_order": "5",
            "issues-is_active": "on",
        })
        template.refresh_from_db()
        self.assertEqual(template.text, "Нет изображения")
        self.client.post(reverse("crm:settings"), {
            "section": "issues", "action": "toggle", "pk": template.pk,
        })
        self.assertFalse(IntakeForm().fields["issue_template"].queryset.filter(pk=template.pk).exists())

    def test_work_template_create_edit_and_toggle(self):
        response = self.post_setting("works", **{
            "works-name": "Диагностика", "works-default_price": "25.50",
            "works-description": "Проверка", "works-sort_order": "10",
            "works-is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        template = CRMWorkTemplate.objects.get(name="Диагностика")
        self.assertEqual(template.default_price, Decimal("25.50"))
        self.post_setting("works", **{
            "pk": template.pk, "works-name": "Полная диагностика",
            "works-default_price": "30", "works-description": "Полная проверка",
            "works-sort_order": "5", "works-is_active": "on",
        })
        template.refresh_from_db()
        self.assertEqual(template.name, "Полная диагностика")
        self.client.post(reverse("crm:settings"), {
            "section": "works", "action": "toggle", "pk": template.pk,
        })
        template.refresh_from_db()
        self.assertFalse(template.is_active)

    def test_work_template_is_copied_to_work_item_snapshot(self):
        template = CRMWorkTemplate.objects.create(name="Замена дисплея", default_price="120")
        client = CRMClient.objects.create(name="Иван", phone="80291234567")
        device = CRMDevice.objects.create(client=client, device_type="Телефон")
        order = CRMOrder.objects.create(client=client, device=device, issue_description="Экран")
        form_data = {"template": template.pk, "name": "", "quantity": "1", "unit_price": "0", "comment": ""}
        from crm.forms import WorkItemForm
        form = WorkItemForm(form_data)
        self.assertTrue(form.is_valid(), form.errors)
        item = form.save(commit=False)
        item.order = order
        item.save()
        template.name = "Новый заголовок"
        template.default_price = Decimal("200")
        template.save()
        item.refresh_from_db()
        self.assertEqual(item.name, "Замена дисплея")
        self.assertEqual(item.unit_price, Decimal("120"))

    def test_device_type_brand_model_and_json_filtering(self):
        CRMDeviceType.objects.create(name="Игровая приставка", sort_order=1)
        apple = CRMBrand.objects.create(name="Apple")
        samsung = CRMBrand.objects.create(name="Samsung")
        CRMDeviceModel.objects.create(brand=apple, name="iPhone 15")
        CRMDeviceModel.objects.create(brand=samsung, name="A55")
        CRMDeviceModel.objects.create(brand=apple, name="Скрытая", is_active=False)
        response = self.client.get(reverse("crm:device_models"), {"brand": "Apple"})
        names = [item["name"] for item in response.json()["results"]]
        self.assertEqual(names, ["iPhone 15"])
        intake = self.client.get(reverse("crm:order_create"))
        self.assertContains(intake, "Игровая приставка")
        self.assertContains(intake, "iPhone 15")
        self.assertNotContains(intake, "Скрытая")

    def test_device_type_and_brand_can_be_managed_from_settings(self):
        self.post_setting("device_types", **{
            "device_types-name": "Фотоаппарат", "device_types-sort_order": "4",
            "device_types-is_active": "on",
        })
        device_type = CRMDeviceType.objects.get(name="Фотоаппарат")
        self.client.post(reverse("crm:settings"), {
            "section": "device_types", "action": "toggle", "pk": device_type.pk,
        })
        device_type.refresh_from_db()
        self.assertFalse(device_type.is_active)

        self.post_setting("brands", **{
            "brands-name": "Honor", "brands-sort_order": "2", "brands-is_active": "on",
        })
        brand = CRMBrand.objects.get(name="Honor")
        self.post_setting("brands", **{
            "pk": brand.pk, "brands-name": "HONOR", "brands-sort_order": "1",
            "brands-is_active": "on",
        })
        brand.refresh_from_db()
        self.assertEqual(brand.name, "HONOR")
        self.client.post(reverse("crm:settings"), {
            "section": "brands", "action": "toggle", "pk": brand.pk,
        })
        brand.refresh_from_db()
        self.assertFalse(brand.is_active)

    def test_warranty_default_and_order_snapshot(self):
        CRMWarrantyTerm.objects.update(is_default=False)
        term = CRMWarrantyTerm.objects.create(days=45, label="Полтора месяца", is_default=True)
        self.assertEqual(IntakeForm().initial["warranty_days"], 45)
        client = CRMClient.objects.create(name="Анна", phone="80291110000")
        device = CRMDevice.objects.create(client=client, device_type="Телефон")
        order = CRMOrder.objects.create(
            client=client, device=device, issue_description="Не включается", warranty_days=term.days
        )
        term.days = 50
        term.save()
        order.refresh_from_db()
        self.assertEqual(order.warranty_days, 45)

    def test_all_settings_sections_render(self):
        for section in (
            "general", "device_types", "brands", "models", "issues", "works",
            "warranty", "documents", "masters", "security",
        ):
            with self.subTest(section=section):
                self.assertEqual(
                    self.client.get(reverse("crm:settings"), {"section": section}).status_code,
                    200,
                )

    def test_document_settings_are_used_by_documents(self):
        settings = CRMDocumentSettings.load()
        response = self.client.post(reverse("crm:settings"), {
            "section": "documents", "action": "save_documents",
            "documents-workshop_name": "Мастерская Тест",
            "documents-phone": "+375 29 000-00-00",
            "documents-email": "test@example.com",
            "documents-address": "Минск",
            "documents-receipt_terms": "Условия квитанции",
            "documents-act_terms": "Условия акта",
            "documents-warranty_terms": "Условия гарантии",
        })
        self.assertEqual(response.status_code, 302)
        settings.refresh_from_db()
        self.assertEqual(settings.workshop_name, "Мастерская Тест")
        customer = CRMClient.objects.create(name="Клиент", phone="80290000000")
        device = CRMDevice.objects.create(client=customer, device_type="Телефон")
        order = CRMOrder.objects.create(client=customer, device=device, issue_description="Тест")
        self.assertContains(self.client.get(reverse("crm:receipt", args=[order.pk])), "Условия квитанции")
        self.assertContains(self.client.get(reverse("crm:act", args=[order.pk])), "Условия акта")
        self.assertContains(self.client.get(reverse("crm:warranty", args=[order.pk])), "Условия гарантии")

    def test_no_physical_delete_route_is_exposed(self):
        response = self.client.get(reverse("crm:settings"), {"section": "security"})
        self.assertContains(response, "Физическое удаление CRM-заказов")
