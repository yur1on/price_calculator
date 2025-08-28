# notify_tg/utils.py
from django.conf import settings
from typing import Optional
import httpx

def send_telegram_message(chat_id: int, text: str) -> bool:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        # без parse_mode, чтобы не споткнуться о разметку
        r = httpx.post(url, data={"chat_id": chat_id, "text": text})
        ok = r.status_code == 200 and r.json().get("ok")
        return bool(ok)
    except Exception:
        return False

def notify_partner(partner, text: str) -> bool:
    tg = getattr(partner, "telegram", None)
    if not tg or not tg.is_active:
        return False
    return send_telegram_message(tg.chat_id, text)
