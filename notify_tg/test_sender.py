import asyncio
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch

from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.request import HTTPXRequest

from crm.models import CRMOrder, CRMWorkItem
from crm.services import change_status, issue_order
from notify_tg.services import (
    _TelegramURLFilter, appointment_created_message, notify_order_status,
    notify_partner_by_chat,
)
from notify_tg import tests as existing_tests
from repairs.models import Appointment, PhoneBrand, PhoneModel, RepairType


@override_settings(TELEGRAM_BOT_TOKEN="123456:test-token", SITE_URL="https://tehsfera.by")
class SenderRegressionTests(TestCase):
    order = existing_tests.ClientNotificationTests.order

    def setUp(self):
        existing_tests.ClientNotificationTests.setUp(self)
        self.bot = MagicMock()
        self.bot.__aenter__ = AsyncMock(return_value=self.bot)
        self.bot.__aexit__ = AsyncMock(return_value=None)
        self.bot.send_message = AsyncMock(return_value=True)
        self.patch = patch("notify_tg.services.get_bot", return_value=self.bot)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_send_is_awaited_with_safe_text_and_session_closed(self):
        order = self.order(status=CRMOrder.Status.READY)
        self.assertTrue(notify_order_status(order.pk))
        self.bot.send_message.assert_awaited_once()
        kwargs = self.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], self.binding.chat_id)
        self.assertIn("готово к выдаче", kwargs["text"])
        self.assertIn("https://tehsfera.by/repair-status/", kwargs["text"])
        for secret in ("PRIVATE-PIN", "PRIVATE-SERIAL", "PRIVATE-NOTE", "supplier", "salary", "profit", "/crm/"):
            self.assertNotIn(secret, kwargs["text"])
        self.bot.__aexit__.assert_awaited_once()

    def test_commit_sends_once_rollback_does_not_send(self):
        order = self.order()
        with self.captureOnCommitCallbacks(execute=True):
            change_status(order, CRMOrder.Status.APPROVAL, self.user)
            self.bot.send_message.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.bot.send_message.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    change_status(order, CRMOrder.Status.REPAIR, self.user)
                    change_status(order, CRMOrder.Status.WAITING_PART, self.user)
                    raise ValueError("rollback")
            except ValueError:
                pass
        self.bot.send_message.assert_not_awaited()

    @override_settings(TELEGRAM_ADMIN_CHAT_IDS="")
    def test_appointment_commit_runs_async_sender_once(self):
        brand = PhoneBrand.objects.create(name="Test", slug="sender-test")
        model = PhoneModel.objects.create(brand=brand, name="Phone", slug="sender-phone")
        repair = RepairType.objects.create(name="Display", slug="sender-display")
        start = timezone.now() + timedelta(days=1)
        with self.captureOnCommitCallbacks(execute=True):
            appointment = Appointment.objects.create(phone_model=model, repair_type=repair,
                start=start, end=start + timedelta(minutes=30), customer_name="Test",
                customer_phone=self.customer.phone, price_original="100", price_final="100")
            self.bot.send_message.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Запись подтверждена", self.bot.send_message.await_args.kwargs["text"])
        with self.captureOnCommitCallbacks(execute=True):
            appointment.save()
        self.bot.send_message.assert_awaited_once()

    @override_settings(TIME_ZONE="Europe/Minsk", USE_TZ=True)
    def test_appointment_message_formats_database_utc_as_local_time(self):
        brand = PhoneBrand.objects.create(name="Timezone", slug="timezone-test")
        model = PhoneModel.objects.create(brand=brand, name="Phone", slug="timezone-phone")
        repair = RepairType.objects.create(name="Display", slug="timezone-display")
        start = datetime(2026, 1, 15, 14, 0, tzinfo=dt_timezone.utc)
        appointment = Appointment.objects.create(
            phone_model=model, repair_type=repair, start=start,
            end=start + timedelta(minutes=30), customer_name="Test",
            customer_phone=self.customer.phone, price_original="100", price_final="100",
        )
        appointment.refresh_from_db()
        self.assertIn("15.01.2026 17:00", appointment_created_message(appointment))
        self.assertNotIn("15.01.2026 14:00", appointment_created_message(appointment))

    def test_transport_exceptions_preserve_ready_and_do_not_log_secrets(self):
        for error in (TimeoutError, TimedOut, NetworkError, BadRequest):
            order = self.order(status=CRMOrder.Status.REPAIR)
            CRMWorkItem.objects.create(order=order, name="Work", quantity=1, unit_price="250")
            self.bot.send_message.side_effect = error("SECRET-token-and-phone")
            with self.assertLogs("notify_tg.services", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    change_status(order, CRMOrder.Status.READY, self.user)
            order.refresh_from_db()
            self.assertEqual(order.status, CRMOrder.Status.READY)
            self.assertNotIn("SECRET", " ".join(logs.output))
        self.assertEqual(self.bot.__aexit__.await_count, 4)

    def test_issue_finance_warranty_survive_api_error(self):
        order = self.order(status=CRMOrder.Status.READY)
        self.bot.send_message.side_effect = BadRequest("unavailable")
        with self.captureOnCommitCallbacks(execute=True):
            issue_order(order.pk, final_price="250", paid_amount="250", warranty_days=90, author=self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, CRMOrder.Status.ISSUED)
        self.assertIsNotNone(order.warranty_started_at)
        self.assertEqual(str(order.finance_repair.revenue), "250.00")

    def test_save_get_and_no_link_do_not_send_and_resend_is_post(self):
        order = self.order(status=CRMOrder.Status.READY)
        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            order.save()
            self.client.get(reverse("crm:order_detail", args=[order.pk]))
        url = reverse("crm:order_notification_resend", args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.bot.send_message.assert_awaited_once()
        self.binding.delete()
        self.bot.send_message.reset_mock()
        self.assertFalse(notify_order_status(order.pk))
        self.bot.send_message.assert_not_awaited()

    def test_total_timeout_cancels_send_and_closes_context(self):
        async def stalled(**kwargs):
            await asyncio.sleep(60)
        self.bot.send_message.side_effect = stalled
        with patch("notify_tg.services.SEND_TIMEOUT_SECONDS", 0.2):
            self.assertFalse(notify_partner_by_chat(123, "test"))
        self.bot.send_message.assert_awaited_once()
        self.bot.__aexit__.assert_awaited_once()

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_missing_token_does_not_create_bot(self):
        self.assertFalse(notify_partner_by_chat(123, "test"))
        self.bot.__aenter__.assert_not_awaited()


@override_settings(TELEGRAM_BOT_TOKEN="123456:test-token")
class RealBotLifecycleTests(TestCase):
    def test_httpx_telegram_url_is_not_logged(self):
        import logging
        filter_ = _TelegramURLFilter()
        record = logging.LogRecord("httpx", logging.INFO, "", 0,
            "HTTP Request: POST %s", ("https://api.telegram.org/botSECRET/sendMessage",), None)
        self.assertFalse(filter_.filter(record))
        record.args = ("https://example.com/",)
        self.assertTrue(filter_.filter(record))

    def test_owned_transports_close_on_success_api_error_and_initialize_failure(self):
        me = (200, json.dumps({"ok": True, "result": {"id": 123456, "is_bot": True, "first_name": "Test", "username": "test_bot"}}).encode())
        message = (200, json.dumps({"ok": True, "result": {"message_id": 1, "date": 0, "chat": {"id": 123, "type": "private"}, "text": "test"}}).encode())
        for replies, expected in (([me, message], True), ([me, BadRequest("failed")], False), ([TimedOut("init failed")], False)):
            transports = []
            def transport(**kwargs):
                obj = HTTPXRequest(**kwargs)
                transports.append(obj)
                return obj
            with patch("notify_tg.services.HTTPXRequest", side_effect=transport), patch.object(HTTPXRequest, "do_request", new_callable=AsyncMock, side_effect=replies) as request:
                self.assertEqual(notify_partner_by_chat(123, "test"), expected)
                self.assertGreater(request.await_count, 0)
            self.assertEqual(len(transports), 2)
            self.assertTrue(all(obj._client.is_closed for obj in transports))
