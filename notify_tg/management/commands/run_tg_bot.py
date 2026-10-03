# notify_tg/management/commands/run_tg_bot.py
from __future__ import annotations

import logging
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.exceptions import ValidationError
from repairs.referrals import full_phone, referral_balance, referral_operations
from notify_tg.referrals import verified_partner, confirm_owner

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

from repairs.models import ReferralPartner

logger = logging.getLogger(__name__)

# =========================
# Utils
# =========================
def partner_has_phone(partner: ReferralPartner) -> bool:
    return bool(full_phone(partner.contact))


def fmt_money(x: Decimal | int | None) -> str:
    try:
        return f"{Decimal(x or 0):.2f}"
    except Exception:
        return "0.00"


def fmt_date(dt) -> str:
    try:
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def shorten_status_ru(status_display: str) -> str:
    s = (status_display or "").strip().lower()
    if "ожида" in s or "pending" in s:
        return "⏳ ожидает"
    if "начис" in s or "accru" in s:
        return "✅ начислено"
    if "выплач" in s or "paid" in s:
        return "🔻 использовано"
    return status_display or ""


# =========================
# UI
# =========================
BTN_MY_CODE = "Мой код"
BTN_BALANCE = "Баланс"
BTN_REPORT = "Отчёт"
BTN_HELP = "Помощь"
BTN_RULES = "Как работает?"
BTN_SEND_PHONE = "Подтвердить номер"


def reply_kb(full: bool) -> ReplyKeyboardMarkup:
    if not full:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton(BTN_SEND_PHONE, request_contact=True)],
                [KeyboardButton(BTN_RULES), KeyboardButton(BTN_HELP)],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True,
        )

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_MY_CODE), KeyboardButton(BTN_BALANCE)],
            [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_RULES)],
            [KeyboardButton(BTN_HELP), KeyboardButton(BTN_SEND_PHONE, request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


async def _reply(update: Update, text: str, full_keyboard: bool, parse_mode: str | None = None):
    msg = update.message or update.effective_message
    if not msg:
        return
    try:
        await msg.reply_text(text, reply_markup=reply_kb(full_keyboard), parse_mode=parse_mode)
    except Exception:
        # чтобы бот не "молчал" при ошибках Telegram API
        logger.exception("TG send failed (len=%s, parse_mode=%s)", len(text or ""), parse_mode)


# =========================
# Text blocks
# =========================
def rules_text(with_code=None, partner=None):
    discount = partner.client_discount_pct if partner else ReferralPartner._meta.get_field("client_discount_pct").get_default()
    commission = partner.partner_commission_pct if partner else ReferralPartner._meta.get_field("partner_commission_pct").get_default()
    code_line = f"\n\n🎟 Ваш код: <b>{with_code}</b>" if with_code else ""
    return (
        "📌 <b>Реферальная программа</b>\n\n"
        "Подтвердите свой номер, получите код и поделитесь им с друзьями.\n"
        f"Друг получает скидку {discount}%, вам начисляется {commission}% "
        "от стоимости услуг после скидки за несколько услуг.\n"
        "Начисление доступно после выдачи отремонтированного устройства.\n"
        "При создании вашей онлайн-записи накопления применяются автоматически — вплоть до 0 BYN."
        f"{code_line}"
    )


# =========================
# DB helpers
# =========================
@sync_to_async
def db_get_partner_by_chat(chat_id):
    return verified_partner(chat_id)


@sync_to_async
def db_confirm_owner(**kwargs):
    return confirm_owner(**kwargs)


@sync_to_async
def db_calc_balance(partner_id):
    b = referral_balance(partner_id)
    return dict(b, available=b["available_balance"], earned_pending=b["pending_balance"],
                earned_accrued=b["accrued_balance"], spent=b["used_balance"])


@sync_to_async
def db_last_ops(partner_id, limit=12):
    return referral_operations(partner_id, limit)


# =========================
# Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Public recommendation codes are never ownership credentials.
    partner = await db_get_partner_by_chat(update.effective_chat.id)
    if not partner:
        await _reply(update, "Подтвердите свой номер кнопкой «Подтвердить номер» в личном чате. Знание реферального кода не даёт доступ к его владельцу.", full_keyboard=False)
        return
    await _reply(update, f"🎟 Ваш код: <b>{partner.code}</b>", full_keyboard=True, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    partner = await db_get_partner_by_chat(update.effective_chat.id)
    full = bool(partner and partner_has_phone(partner))

    if not full:
        await _reply(update, "ℹ️ Сначала подтвердите номер телефона кнопкой «Подтвердить номер».", full_keyboard=False)
        return

    await _reply(
        update,
        "📍 Разделы:\n"
        f"• «{BTN_MY_CODE}» — ваш код\n"
        f"• «{BTN_BALANCE}» — накопления и сколько доступно\n"
        f"• «{BTN_REPORT}» — операции (начисления/списания)\n"
        f"• «{BTN_RULES}» — подробные правила\n",
        full_keyboard=True,
    )


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    partner = await db_get_partner_by_chat(update.effective_chat.id)
    full = bool(partner and partner_has_phone(partner))

    if not full:
        await _reply(update, rules_text(None), full_keyboard=False, parse_mode="HTML")
        return

    code = partner.code
    await _reply(update, rules_text(code, partner), full_keyboard=True, parse_mode="HTML")


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Для доступа к своим накоплениям подтвердите свой номер кнопкой «Подтвердить номер». Публичный код не используется для входа.", full_keyboard=False)


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    if not contact or not user:
        return
    try:
        partner = await db_confirm_owner(
            chat_id=update.effective_chat.id, user_id=user.id, contact_user_id=contact.user_id,
            phone=contact.phone_number, name=user.full_name,
        )
    except ValidationError as exc:
        await _reply(update, exc.messages[0], full_keyboard=False)
        return
    await _reply(update, f"✅ Номер подтверждён!\n🎟 Ваш код: <b>{partner.code}</b>", full_keyboard=True, parse_mode="HTML")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    text_l = text.lower()
    chat_id = update.effective_chat.id

    partner = await db_get_partner_by_chat(chat_id)
    if not partner:
        await _reply(update, "Нажмите /start для начала.", full_keyboard=False)
        return

    if not partner_has_phone(partner):
        if text_l == BTN_HELP.lower():
            await cmd_help(update, context)
            return
        if text_l == BTN_RULES.lower():
            await cmd_rules(update, context)
            return
        await _reply(update, "Сначала подтвердите номер кнопкой «Подтвердить номер».", full_keyboard=False)
        return


    if text_l == BTN_MY_CODE.lower():
        await _reply(
            update,
            (
                "🎟 <b>Ваш реферальный код</b>\n"
                f"<code>{partner.code}</code>\n\n"
                "Ремонт оформляют на сайте <code>tehsfera.by</code> — при оформлении заявки вводят код."
            ),
            full_keyboard=True,
            parse_mode="HTML",
        )
        return

    if text_l == BTN_BALANCE.lower():
        b = await db_calc_balance(partner.id)
        text_out = (
            "💰 <b>Баланс накоплений</b>\n"
            f"👤 {partner.name}\n"
            f"🎟 Код: <code>{partner.code}</code>\n\n"
            "📌 Сводка:\n"
            f"• Использований кода: <b>{b['uses']}</b>\n"
            f"• Начислено (выполнено): <b>{fmt_money(b['earned_accrued'])}</b> BYN\n"
            f"• Ожидает: <b>{fmt_money(b['earned_pending'])}</b> BYN\n"
            f"• Использовано: <b>{fmt_money(b['spent'])}</b> BYN\n\n"
            f"✅ <b>Доступно сейчас:</b> <b>{fmt_money(b['available'])}</b> BYN\n"
            f"🔮 <b>Потенциал:</b> {fmt_money(b['potential'])} BYN\n\n"
            f"🎁 Скидок клиентам: {fmt_money(b['total_discount'])} BYN"
        )
        await _reply(update, text_out, full_keyboard=True, parse_mode="HTML")
        return

    if text_l == BTN_REPORT.lower():
        ops = await db_last_ops(partner.id, limit=12)
        if not ops:
            await _reply(update, "📭 Операций пока нет.", full_keyboard=True)
            return

        lines = [
            "📊 <b>Отчёт (последние операции)</b>",
            f"🎟 Код: <code>{partner.code}</code>",
            "",
        ]

        for o in ops:
            status_short = shorten_status_ru(o["status"])
            lines.append(
                f"• {o['kind']}  <b>{fmt_money(o['amount'])}</b> BYN\n"
                f"  {fmt_date(o['created_at'])} • {status_short}"
            )

        await _reply(update, "\n".join(lines), full_keyboard=True, parse_mode="HTML")
        return

    if text_l == BTN_RULES.lower():
        await cmd_rules(update, context)
        return

    if text_l == BTN_HELP.lower():
        await cmd_help(update, context)
        return

    await _reply(update, "Используйте кнопки снизу или /help.", full_keyboard=True)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка в боте", exc_info=context.error)


# =========================
# Run
# =========================
class Command(BaseCommand):
    help = "TG бот: подтверждение телефона -> выдача кода -> кабинет. + правила и красивый баланс/отчёт."

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            self.stdout.write(self.style.ERROR("TELEGRAM_BOT_TOKEN не задан"))
            return

        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO,
        )

        app = ApplicationBuilder().token(token).build()

        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("link", cmd_link))
        app.add_handler(CommandHandler("rules", cmd_rules))

        # ВАЖНО: contact handler выше TEXT handler
        app.add_handler(MessageHandler(filters.CONTACT, on_contact))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

        app.add_error_handler(on_error)

        self.stdout.write(self.style.SUCCESS("Бот запущен. Ctrl+C для остановки."))
        app.run_polling(close_loop=False)
