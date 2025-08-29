# notify_tg/utils.py
from django.conf import settings
from typing import Optional, List
import httpx
from django.urls import reverse

def send_telegram_message(chat_id: int, text: str) -> bool:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
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

# === НОВОЕ НИЖЕ ===
def _parse_admin_ids() -> List[int]:
    raw = (getattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "") or "").replace(";", ",")
    ids: List[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            pass
    return ids

def notify_admins(text: str) -> int:
    """Шлёт сообщение всем chat_id из TELEGRAM_ADMIN_CHAT_IDS. Возвращает число удачных отправок."""
    ok_count = 0
    for cid in _parse_admin_ids():
        if send_telegram_message(cid, text):
            ok_count += 1
    return ok_count

def admin_appointment_link(appointment_id: int) -> str:
    """Возвращает относительную/абсолютную ссылку на изменение заявки в админке."""
    try:
        path = reverse("admin:repairs_appointment_change", args=[appointment_id])
    except Exception:
        path = f"/admin/repairs/appointment/{appointment_id}/change/"
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path
