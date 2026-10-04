from __future__ import annotations
import logging
import asyncio
from decimal import Decimal
from html import escape

from django.conf import settings
from django.utils import timezone
from asgiref.sync import async_to_sync
from telegram import Bot
from telegram.request import HTTPXRequest

from .models import PartnerTelegram

logger = logging.getLogger(__name__)
SEND_TIMEOUT_SECONDS = 5


class _TelegramURLFilter(logging.Filter):
    def filter(self, record):
        # HTTPX INFO logs contain the full Telegram URL, including the token.
        return "api.telegram.org/bot" not in record.getMessage()


logging.getLogger("httpx").addFilter(_TelegramURLFilter())


def format_local_datetime(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y %H:%M")

def get_bot(*, request=None, get_updates_request=None) -> Bot | None:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    if not token:
        return None
    return Bot(token=token, request=request, get_updates_request=get_updates_request)


async def _send_by_chat(chat_id: int, text: str) -> bool:
    # Create, initialize and close the owned client in the same event loop.
    if not getattr(settings, "TELEGRAM_BOT_TOKEN", ""):
        return False
    timeout = dict(connect_timeout=2, read_timeout=3, write_timeout=3, pool_timeout=1)
    # Own both transports explicitly: Bot.shutdown() is a no-op when its
    # initialization failed (including a cancelled getMe request).
    async with HTTPXRequest(**timeout) as request, HTTPXRequest(**timeout) as updates:
        bot = get_bot(request=request, get_updates_request=updates)
        async with bot:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    return True


async def _send_with_timeout(chat_id: int, text: str) -> bool:
    return await asyncio.wait_for(_send_by_chat(chat_id, text), timeout=SEND_TIMEOUT_SECONDS)

def notify_partner_by_chat(chat_id: int, text: str) -> bool:
    """Отправка сообщения по chat_id. Возвращает True при успехе."""
    if not chat_id:
        return False
    try:
        return async_to_sync(_send_with_timeout)(chat_id, text)
    except Exception as exc:
        # API exception text/tracebacks may contain token URLs or customer data.
        logger.warning("Telegram delivery failed (%s)", type(exc).__name__)
        return False

def notify_partner(partner, text: str) -> bool:
    """Отправка, зная объект партнёра repairs.ReferralPartner."""
    tg = getattr(partner, "telegram", None)
    if not tg or not tg.is_active or not tg.chat_id:
        return False
    return notify_partner_by_chat(tg.chat_id, text)


def _phone_key(value: str) -> str:
    digits = "".join(char for char in (value or "") if char.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def client_telegram_for_phone(phone: str):
    """Return an active, phone-confirmed Telegram binding. Names are never used."""
    target = _phone_key(phone)
    if not target:
        return None
    bindings = PartnerTelegram.objects.filter(is_active=True).select_related("partner").only(
        "chat_id", "is_active", "partner__contact",
    )
    return next((binding for binding in bindings if _phone_key(binding.partner.contact) == target), None)


def client_has_telegram(phone: str) -> bool:
    return client_telegram_for_phone(phone) is not None


def _public_status_url() -> str:
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}/repair-status/" if base else "/repair-status/"


def _safe_send_to_phone(phone: str, text: str) -> bool:
    binding = client_telegram_for_phone(phone)
    if not binding:
        return False
    try:
        return notify_partner_by_chat(binding.chat_id, text)
    except Exception as exc:
        logger.warning("Telegram client notification failed (%s)", type(exc).__name__)
        return False


def appointment_created_message(appointment) -> str:
    settings_obj = None
    try:
        from crm.models import CRMDocumentSettings
        settings_obj = CRMDocumentSettings.load()
    except Exception:
        pass
    lines = [
        "Запись подтверждена",
        f"Дата и время: {format_local_datetime(appointment.start)}",
        f"Устройство: {escape(str(appointment.phone_model))}",
        f"Услуга: {escape(appointment.services_display)}",
    ]
    if settings_obj and settings_obj.address:
        lines.append(f"Адрес: {escape(settings_obj.address)}")
    if settings_obj and settings_obj.phone:
        lines.append(f"Контакт: {escape(settings_obj.phone)}")
    return "\n".join(lines)


def notify_appointment_created(appointment_id: int) -> bool:
    from repairs.models import Appointment
    try:
        appointment = Appointment.objects.select_related("phone_model__brand", "repair_type").prefetch_related("items__repair_type").get(pk=appointment_id)
        return _safe_send_to_phone(appointment.customer_phone, appointment_created_message(appointment))
    except Exception as exc:
        logger.warning("Appointment Telegram notification failed (%s)", type(exc).__name__)
        return False


def order_status_message(order, status: str | None = None) -> str | None:
    from crm.models import CRMOrder
    status = status or order.status
    head = {
        CRMOrder.Status.ACCEPTED: "Устройство принято в ремонт.",
        CRMOrder.Status.APPROVAL: "Ремонт ожидает вашего согласования.",
        CRMOrder.Status.WAITING_PART: "Для ремонта ожидается запчасть.",
        CRMOrder.Status.READY: "Ваше устройство готово к выдаче.",
        CRMOrder.Status.ISSUED: "Устройство выдано. Спасибо.",
    }.get(status)
    if not head:
        return None
    lines = [head, f"Номер ремонта: {escape(order.number)}", f"Устройство: {escape(order.device.display_name)}"]
    if status == CRMOrder.Status.APPROVAL and Decimal(order.agreed_price or 0) > 0:
        lines.append(f"Согласованная сумма: {order.agreed_price} BYN")
    if status == CRMOrder.Status.READY:
        total = order.final_price if order.final_price is not None else order.agreed_price
        if total is not None and Decimal(total) > 0:
            lines.append(f"Сумма: {total} BYN")
        lines.append("Устройство можно забрать в мастерской.")
    if status == CRMOrder.Status.ISSUED and order.warranty_until:
        lines.append(f"Гарантия действует до {order.warranty_until:%d.%m.%Y}.")
    lines.append(f"Проверить статус: {_public_status_url()}")
    return "\n".join(lines)


def notify_order_status(order_id: int, status: str | None = None) -> bool:
    from crm.models import CRMOrder
    try:
        order = CRMOrder.objects.select_related("client", "device").get(pk=order_id)
        text = order_status_message(order, status)
        return bool(text and _safe_send_to_phone(order.client.phone, text))
    except Exception as exc:
        logger.warning("Order Telegram notification failed (%s)", type(exc).__name__)
        return False
