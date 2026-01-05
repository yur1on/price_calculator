# notify_tg/management/commands/run_tg_bot.py
from __future__ import annotations

import logging
import random
import string
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models import Sum

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

from repairs.models import ReferralPartner, ReferralRedemption
from notify_tg.models import PartnerTelegram

logger = logging.getLogger(__name__)

# =========================
# Utils
# =========================
def gen_ref_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def gen_pending_code() -> str:
    """
    Временный код (никогда не показываем пользователю).

    ВАЖНО: в проде Postgres строго валидирует длину поля code.
    Судя по ошибке у тебя code = varchar(16), поэтому делаем <= 16 символов.
    """
    alphabet = string.ascii_uppercase + string.digits
    # 4 ("PEND") + 12 = 16
    return "PEND" + "".join(random.choice(alphabet) for _ in range(12))


def norm_phone(s: str) -> str:
    digits = "".join(ch for ch in (s or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def partner_has_phone(partner: ReferralPartner) -> bool:
    return len(norm_phone(partner.contact or "")) >= 9


def partner_has_real_code(partner: ReferralPartner) -> bool:
    # временные коды начинаются с PEND
    return bool(partner.code) and not partner.code.startswith("PEND")


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
def rules_text(with_code: str | None = None) -> str:
    code_line = f"\n\n🎟 Ваш код: <b>{with_code}</b>" if with_code else ""
    return (
        "📌 <b>Реферальная программа</b>\n\n"
        "Как это работает:\n"
        "1) Подтвердите номер телефона в боте\n"
        "2) Получите личный реферальный код\n"
        "3) Делитесь кодом с друзьями/знакомыми\n\n"
        "🛠 <b>Где оформляют ремонт</b>\n"
        "• На сайте <code>tehsfera.by</code>\n"
        "• При оформлении заявки на ремонт клиент вводит ваш код в поле «Промокод / Реферальный код»\n\n"
        "✅ <b>Что получает клиент</b>\n"
        "• <b>-5%</b> скидка от суммы ремонта при вводе кода\n\n"
        "✅ <b>Что получаете вы</b>\n"
        "• <b>+5%</b> в накопления после выполненного ремонта (статус <b>done</b>)\n"
        "• Накопления можно использовать на ваш будущий ремонт — хоть до <b>0 BYN</b>"
        f"{code_line}"
    )


# =========================
# DB helpers
# =========================
@sync_to_async
def db_get_partner_by_code(code: str) -> ReferralPartner | None:
    return ReferralPartner.objects.filter(code__iexact=code).first()


@sync_to_async
def db_get_partner_by_chat(chat_id: int) -> ReferralPartner | None:
    pt = PartnerTelegram.objects.select_related("partner").filter(
        chat_id=chat_id, is_active=True
    ).first()
    return pt.partner if pt else None


@sync_to_async
def db_link_partner_chat(partner_id: int, chat_id: int):
    partner = ReferralPartner.objects.get(id=partner_id)
    PartnerTelegram.objects.filter(chat_id=chat_id).exclude(partner_id=partner_id).delete()
    obj, created = PartnerTelegram.objects.update_or_create(
        partner=partner,
        defaults={"chat_id": chat_id, "is_active": True},
    )
    return created, obj


@sync_to_async
def db_get_or_create_partner_for_chat(
    chat_id: int,
    tg_username: str | None,
    full_name: str | None,
) -> tuple[ReferralPartner, bool]:
    """
    Создаём партнёра и привязку Telegram.
    ВАЖНО: создаём временный code=PEND..., настоящий код выдаём только после подтверждения телефона.
    """
    pt = (PartnerTelegram.objects
          .select_related("partner")
          .filter(chat_id=chat_id, is_active=True)
          .first())
    if pt:
        return pt.partner, False

    name = (full_name or "").strip() or f"TG user {chat_id}"
    contact = f"@{tg_username}" if tg_username else ""

    for _ in range(50):
        pending_code = gen_pending_code()
        try:
            with transaction.atomic():
                partner = ReferralPartner.objects.create(
                    name=name,
                    contact=contact,
                    code=pending_code,  # временный, до подтверждения телефона
                )
                PartnerTelegram.objects.create(
                    partner=partner,
                    chat_id=chat_id,
                    is_active=True,
                )
            return partner, True
        except IntegrityError:
            continue

    raise RuntimeError("Не удалось создать временный код")


@sync_to_async
def db_set_partner_phone(partner_id: int, phone: str):
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    ReferralPartner.objects.filter(id=partner_id).update(contact=digits)


@sync_to_async
def db_assign_real_code_if_needed(partner_id: int) -> str:
    """
    Если у партнёра временный PEND-код — генерируем настоящий реф-код.
    """
    partner = ReferralPartner.objects.get(id=partner_id)
    if partner_has_real_code(partner):
        return partner.code

    for _ in range(50):
        code = gen_ref_code(8)
        try:
            with transaction.atomic():
                p = ReferralPartner.objects.select_for_update().get(id=partner_id)
                if partner_has_real_code(p):
                    return p.code
                p.code = code
                p.save(update_fields=["code"])
            return code
        except IntegrityError:
            continue

    raise RuntimeError("Не удалось сгенерировать уникальный реферальный код")


@sync_to_async
def db_calc_balance(partner_id: int) -> dict:
    qs = ReferralRedemption.objects.filter(partner_id=partner_id)

    earned_pending = qs.filter(status="pending", commission_amount__gt=0).aggregate(s=Sum("commission_amount"))["s"] or Decimal("0.00")
    earned_accrued = qs.filter(status="accrued", commission_amount__gt=0).aggregate(s=Sum("commission_amount"))["s"] or Decimal("0.00")

    spent = qs.filter(commission_amount__lt=0).aggregate(s=Sum("commission_amount"))["s"] or Decimal("0.00")  # отрицательное
    spent_abs = -Decimal(spent)

    uses = qs.filter(commission_amount__gt=0).count()
    total_discount = qs.filter(commission_amount__gt=0).aggregate(s=Sum("discount_amount"))["s"] or Decimal("0.00")

    earned_pending = Decimal(earned_pending).quantize(Decimal("0.01"))
    earned_accrued = Decimal(earned_accrued).quantize(Decimal("0.01"))
    spent_abs = Decimal(spent_abs).quantize(Decimal("0.01"))
    available = (earned_accrued - spent_abs).quantize(Decimal("0.01"))
    total_discount = Decimal(total_discount).quantize(Decimal("0.01"))

    potential = (earned_accrued + earned_pending - spent_abs).quantize(Decimal("0.01"))

    return {
        "uses": uses,
        "earned_pending": earned_pending,
        "earned_accrued": earned_accrued,
        "spent": spent_abs,
        "available": available,
        "potential": potential,
        "total_discount": total_discount,
    }


@sync_to_async
def db_last_ops(partner_id: int, limit: int = 12) -> list[dict]:
    qs = (ReferralRedemption.objects
          .select_related("appointment")
          .filter(partner_id=partner_id)
          .order_by("-created_at")[:limit])
    res = []
    for r in qs:
        is_spend = r.commission_amount < 0
        res.append({
            "created_at": r.created_at,
            "appointment_id": r.appointment_id,
            "kind": "🔻 Списание" if is_spend else "➕ Начисление",
            "amount": (-r.commission_amount if is_spend else r.commission_amount),
            "status": r.get_status_display(),
        })
    return res


# =========================
# Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    tg_username = (user.username or "").strip() if user else ""
    full_name = " ".join([x for x in [(user.first_name if user else ""), (user.last_name if user else "")] if x]).strip()

    code_arg = (context.args[0].strip() if context.args else "")
    if code_arg:
        partner = await db_get_partner_by_code(code_arg)
        if partner:
            await db_link_partner_chat(partner.id, chat_id)
            if not partner_has_phone(partner):
                await _reply(
                    update,
                    "✅ Вам будет присвоен реферальный код, но сначала подтвердите номер телефона.\n"
                    "Нажмите кнопку «Подтвердить номер».",
                    full_keyboard=False,
                )
                return

            await _reply(
                update,
                "✅ Кабинет активен.\n"
                f"🎟 Ваш код: <b>{partner.code}</b>\n\n"
                "Ремонт оформляют на сайте <code>tehsfera.by</code> — при оформлении заявки вводят код.",
                full_keyboard=True,
                parse_mode="HTML",
            )
            return

    partner, _ = await db_get_or_create_partner_for_chat(
        chat_id=chat_id,
        tg_username=tg_username or None,
        full_name=full_name or None,
    )

    if not partner_has_phone(partner):
        await _reply(
            update,
            "✅ Вам будет присвоен реферальный код, но сначала подтвердите номер телефона.\n"
            "Нажмите кнопку «Подтвердить номер».",
            full_keyboard=False,
        )
        return

    code = await db_assign_real_code_if_needed(partner.id) if not partner_has_real_code(partner) else partner.code

    await _reply(
        update,
        "✅ Готово!\n"
        f"🎟 Ваш реферальный код: <b>{code}</b>\n\n"
        "Ремонт оформляют на сайте <code>tehsfera.by</code> — при оформлении заявки вводят код.\n"
        "Нажмите «Как работает?», чтобы посмотреть правила.",
        full_keyboard=True,
        parse_mode="HTML",
    )


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

    code = await db_assign_real_code_if_needed(partner.id) if not partner_has_real_code(partner) else partner.code
    await _reply(update, rules_text(code), full_keyboard=True, parse_mode="HTML")


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    code = context.args[0].strip() if context.args else (text[len("/link"):].strip() if text.startswith("/link") else "")

    if not code:
        await _reply(update, "Не указан код. Пример: /link ABC123", full_keyboard=False)
        return

    partner = await db_get_partner_by_code(code)
    if not partner:
        await _reply(update, "Код не найден. Проверьте и попробуйте снова.", full_keyboard=False)
        return

    await db_link_partner_chat(partner.id, update.effective_chat.id)

    if not partner_has_phone(partner):
        await _reply(
            update,
            "✅ Вам будет присвоен реферальный код, но сначала подтвердите номер телефона.\n"
            "Нажмите кнопку «Подтвердить номер».",
            full_keyboard=False,
        )
        return

    await _reply(update, f"✅ Чат привязан. Ваш код: <b>{partner.code}</b>", full_keyboard=True, parse_mode="HTML")


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    c = update.message.contact

    if c.user_id and user and c.user_id != user.id:
        await _reply(update, "Можно подтвердить только свой номер (через кнопку).", full_keyboard=False)
        return

    phone = (c.phone_number or "").strip()
    if not phone:
        await _reply(update, "Не удалось прочитать номер. Попробуйте ещё раз.", full_keyboard=False)
        return

    partner = await db_get_partner_by_chat(chat_id)
    if not partner:
        tg_username = (user.username or "").strip() if user else ""
        full_name = " ".join([x for x in [(user.first_name if user else ""), (user.last_name if user else "")] if x]).strip()
        partner, _ = await db_get_or_create_partner_for_chat(chat_id, tg_username or None, full_name or None)

    await db_set_partner_phone(partner.id, phone)
    real_code = await db_assign_real_code_if_needed(partner.id)

    await _reply(
        update,
        "✅ Номер подтверждён!\n\n"
        f"🎟 Ваш реферальный код: <b>{real_code}</b>\n\n"
        "Ремонт оформляют на сайте <code>tehsfera.by</code> — при оформлении заявки вводят код.\n"
        "Нажмите «Как работает?», чтобы посмотреть правила.",
        full_keyboard=True,
        parse_mode="HTML",
    )


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

    if not partner_has_real_code(partner):
        await db_assign_real_code_if_needed(partner.id)
        partner = await db_get_partner_by_chat(chat_id)

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
                f"• <b>#{o['appointment_id']}</b>  {o['kind']}  <b>{fmt_money(o['amount'])}</b> BYN\n"
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
