from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from crm.models import CRMClient, CRMDevice, CRMEvent, CRMOrder, CRMWorkItem
from crm.services import change_status, issue_order
from crm.stale import decorate_stale_orders, with_status_changed_at
from finance.models import Employee
from notify_tg.models import PartnerTelegram
from notify_tg.services import order_status_message
from repairs.models import Appointment, PhoneBrand, PhoneModel, ReferralPartner, RepairType


@override_settings(SITE_URL="https://tehsfera.by")
class ClientNotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("notify-staff", password="pass", is_staff=True)
        self.user.user_permissions.set(Permission.objects.filter(codename__in=[
            "access_crm", "view_crmorder", "change_crmorder", "change_order_status",
        ]))
        self.employee = Employee.objects.create(name="Сергей", default_percent="35")
        self.customer = CRMClient.objects.create(name="Иван", phone="+375291234567")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Samsung", model="A55", serial_number="PRIVATE-SERIAL", unlock_code="PRIVATE-PIN")
        self.partner = ReferralPartner.objects.create(name="Иван TG", contact="+375 (29) 123-45-67", code="NOTIFY14")
        self.binding = PartnerTelegram.objects.create(partner=self.partner, chat_id=140014)

    def order(self, status=CRMOrder.Status.DIAGNOSTIC, **kwargs):
        values = dict(client=self.customer, device=self.device, employee=self.employee, status=status, issue_description="Экран", internal_note="PRIVATE-NOTE supplier 85 salary profit", agreed_price="250.00", warranty_days=90)
        values.update(kwargs)
        return CRMOrder.objects.create(**values)

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_appointment_created_is_sent_after_commit(self, send):
        brand = PhoneBrand.objects.create(name="Samsung", slug="notify-samsung")
        model = PhoneModel.objects.create(brand=brand, name="S24", slug="notify-s24")
        repair = RepairType.objects.create(name="Замена дисплея", slug="notify-display")
        start = timezone.now() + timedelta(days=1)
        with self.captureOnCommitCallbacks(execute=True):
            Appointment.objects.create(phone_model=model, repair_type=repair, start=start, end=start + timedelta(hours=1), customer_name="Иван", customer_phone=self.customer.phone, price_original="200", price_final="200")
        texts = [call.args[1] for call in send.call_args_list]
        self.assertTrue(any("Запись подтверждена" in text and "Замена дисплея" in text for text in texts))

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_approval_waiting_ready_and_issued_messages(self, send):
        cases = [(CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.APPROVAL, "ожидает вашего согласования"), (CRMOrder.Status.DIAGNOSTIC, CRMOrder.Status.WAITING_PART, "ожидается запчасть"), (CRMOrder.Status.REPAIR, CRMOrder.Status.READY, "готово к выдаче")]
        for old, new, expected in cases:
            order = self.order(status=old)
            if new == CRMOrder.Status.READY:
                CRMWorkItem.objects.create(order=order, name="Работа", quantity=1, unit_price="250")
            with self.captureOnCommitCallbacks(execute=True):
                change_status(order, new, self.user)
            self.assertIn(expected, send.call_args.args[1])
        ready = self.order(status=CRMOrder.Status.READY)
        CRMWorkItem.objects.create(order=ready, name="Работа", quantity=1, unit_price="250")
        with self.captureOnCommitCallbacks(execute=True):
            issue_order(ready.pk, final_price="250.00", paid_amount="250.00", warranty_days=90, author=self.user)
        self.assertIn("Устройство выдано", send.call_args.args[1])
        self.assertIn("Гарантия действует до", send.call_args.args[1])

    def test_message_privacy_and_unknown_price(self):
        order = self.order(status=CRMOrder.Status.READY, agreed_price="0.00", final_price=None)
        text = order_status_message(order)
        self.assertNotIn("0.00 BYN", text)
        for secret in ("PRIVATE-PIN", "PRIVATE-SERIAL", "PRIVATE-NOTE", "supplier", "salary", "profit", "/crm/"):
            self.assertNotIn(secret, text)
        self.assertIn("https://tehsfera.by/repair-status/", text)

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_device_accepted_message_uses_existing_binding(self, send):
        from notify_tg.services import notify_order_status
        order = self.order(status=CRMOrder.Status.ACCEPTED)
        self.assertTrue(notify_order_status(order.pk, CRMOrder.Status.ACCEPTED))
        self.assertIn("Устройство принято в ремонт", send.call_args.args[1])
        self.assertIn(order.number, send.call_args.args[1])

    @patch("notify_tg.services.notify_partner_by_chat", side_effect=RuntimeError("offline"))
    def test_telegram_failure_does_not_block_status(self, send):
        order = self.order()
        with self.captureOnCommitCallbacks(execute=True):
            change_status(order, CRMOrder.Status.APPROVAL, self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.APPROVAL)

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_no_telegram_is_normal_and_status_changes(self, send):
        self.binding.delete()
        order = self.order()
        with self.captureOnCommitCallbacks(execute=True):
            change_status(order, CRMOrder.Status.APPROVAL, self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.APPROVAL)
        send.assert_not_called()

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_notification_waits_for_commit_and_rollback_discards_it(self, send):
        order = self.order()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            change_status(order, CRMOrder.Status.APPROVAL, self.user)
        send.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        second = self.order()
        try:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    change_status(second, CRMOrder.Status.APPROVAL, self.user)
                    raise RuntimeError("rollback")
        except RuntimeError:
            pass
        send.assert_not_called()

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_same_status_save_does_not_send_duplicate(self, send):
        order = self.order(status=CRMOrder.Status.APPROVAL)
        order.internal_note = "changed"
        with self.captureOnCommitCallbacks(execute=True):
            order.save(update_fields=["internal_note", "updated_at"])
        send.assert_not_called()

    @patch("notify_tg.services.notify_partner_by_chat", return_value=True)
    def test_manual_resend_is_post_only_and_permission_protected(self, send):
        order = self.order(status=CRMOrder.Status.READY)
        url = reverse("crm:order_notification_resend", args=[order.pk])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        send.assert_called_once()
        ordinary = get_user_model().objects.create_user("notify-client", password="pass")
        AccountProfile.objects.create(user=ordinary, role="client", approval_status="active")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.post(url).status_code, 403)


class StaleRepairTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("stale-admin", "stale@example.com", "pass")
        self.employee = Employee.objects.create(name="Мастер", default_percent="35")
        self.customer = CRMClient.objects.create(name="Клиент", phone="+375291111111")
        self.device = CRMDevice.objects.create(client=self.customer, device_type="Телефон", brand="Apple", model="iPhone")

    def test_status_age_uses_latest_event_and_dashboard_groups_stale(self):
        order = CRMOrder.objects.create(client=self.customer, device=self.device, employee=self.employee, status=CRMOrder.Status.DIAGNOSTIC, issue_description="Диагностика", accepted_at=timezone.now() - timedelta(days=5))
        event = CRMEvent.objects.create(order=order, event_type=CRMEvent.Type.STATUS, description="Статус", old_value="Принято", new_value="Диагностика")
        CRMEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(hours=30))
        decorated = list(with_status_changed_at(CRMOrder.objects.filter(pk=order.pk)))
        attention = decorate_stale_orders(decorated)
        self.assertEqual(attention[0].pk, order.pk)
        self.assertTrue(attention[0].needs_attention)
        self.assertIn("дн.", attention[0].workshop_age_label)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("crm:dashboard"))
        self.assertContains(response, "Требуют внимания")
        self.assertContains(response, order.number)

    def test_stale_words_never_appear_on_public_or_client_pages(self):
        order = CRMOrder.objects.create(client=self.customer, device=self.device, employee=self.employee, status=CRMOrder.Status.READY, issue_description="Ремонт", accepted_at=timezone.now() - timedelta(days=10))
        public = self.client.post(reverse("repair_status"), {"order_number": order.number})
        self.assertNotContains(public, "Без изменений")
        self.assertNotContains(public, "Требуют внимания")
        user = get_user_model().objects.create_user("stale-client", password="pass")
        AccountProfile.objects.create(
            user=user, role=AccountProfile.Role.CLIENT,
            approval_status=AccountProfile.Approval.ACTIVE, crm_client=self.customer,
            phone=self.customer.phone, phone_verified_at=timezone.now(),
        )
        self.client.force_login(user)
        cabinet = self.client.get(reverse("accounts:client_dashboard"))
        self.assertNotContains(cabinet, "Без изменений")
        self.assertNotContains(cabinet, "Требуют внимания")
