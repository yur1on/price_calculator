from __future__ import annotations
from django.conf import settings
from telegram import Bot

def get_bot() -> Bot | None:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    if not token:
        return None
    return Bot(token=token)

def notify_partner_by_chat(chat_id: int, text: str) -> bool:
    """Отправка сообщения по chat_id. Возвращает True при успехе."""
    bot = get_bot()
    if not bot or not chat_id:
        return False
    try:
        bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
        return True
    except Exception:
        return False

def notify_partner(partner, text: str) -> bool:
    """Отправка, зная объект партнёра repairs.ReferralPartner."""
    tg = getattr(partner, "telegram", None)
    if not tg or not tg.is_active or not tg.chat_id:
        return False
    return notify_partner_by_chat(tg.chat_id, text)
