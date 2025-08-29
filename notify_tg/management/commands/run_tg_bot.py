# notify_tg/management/commands/run_tg_bot.py
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count, Q
from asgiref.sync import sync_to_async

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

from repairs.models import ReferralPartner, ReferralRedemption
from notify_tg.models import PartnerTelegram

logger = logging.getLogger(__name__)

# =========================
# DB helpers (ORM в потоках)
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
    obj, created = PartnerTelegram.objects.update_or_create(
        partner=partner,
        defaults={"chat_id": chat_id, "is_active": True},
    )
    return created, obj

@sync_to_async
def db_calc_balance_all_time(partner_id: int) -> dict:
    qs = ReferralRedemption.objects.filter(partner_id=partner_id)
    agg = qs.aggregate(
        uses=Count("id"),
        total_discount=Sum("discount_amount"),
        total_commission=Sum("commission_amount"),
        pending_commission=Sum("commission_amount", filter=Q(status="pending")),
        accrued_commission=Sum("commission_amount", filter=Q(status="accrued")),
        paid_commission=Sum("commission_amount", filter=Q(status="paid")),
    )
    def _d(x): return Decimal(x or 0).quantize(Decimal("0.01"))
    return {
        "uses": agg["uses"] or 0,
        "total_discount": _d(agg["total_discount"]),
        "total_commission": _d(agg["total_commission"]),
        "pending_commission": _d(agg["pending_commission"]),
        "accrued_commission": _d(agg["accrued_commission"]),
        "paid_commission": _d(agg["paid_commission"]),
    }

@sync_to_async
def db_last_ops_all_time(partner_id: int, limit: int) -> list[dict]:
    qs = (ReferralRedemption.objects
          .select_related("appointment", "appointment__phone_model", "appointment__repair_type")
          .filter(partner_id=partner_id)
          .order_by("-created_at")[:limit])
    data = []
    for r in qs:
        data.append({
            "created_at": r.created_at,
            "appointment_id": r.appointment_id,
            "model": str(r.appointment.phone_model),
            "repair": r.appointment.repair_type.name,
            "commission": r.commission_amount,
            "status": r.get_status_display(),
        })
    return data

# =========================
# UI helpers
# =========================
BTN_BALANCE = "Баланс"
BTN_REPORT  = "Отчёт"
BTN_HELP    = "Помощь"

def reply_kb() -> ReplyKeyboardMarkup:
    # три кнопки в один ряд; можно сделать 2+1 — по вкусу
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_BALANCE), KeyboardButton(BTN_REPORT), KeyboardButton(BTN_HELP)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,  # оставляет клавиатуру
    )

def fmt_money(x: Decimal | int | None) -> str:
    try:
        return f"{Decimal(x or 0):.2f}"
    except Exception:
        return "0.00"

# =========================
# Ответы
# =========================
async def send_balance_all_time(update_or_ctx, partner: ReferralPartner):
    b = await db_calc_balance_all_time(partner.id)
    text = (
        f"Баланс за всё время — {partner.name} ({partner.code})\n"
        f"Использований: {b['uses']}\n"
        f"Комиссия всего: {fmt_money(b['total_commission'])} BYN\n"
        f"— Ожидает: {fmt_money(b['pending_commission'])} BYN\n"
        f"— Начислено: {fmt_money(b['accrued_commission'])} BYN\n"
        f"— Выплачено: {fmt_money(b['paid_commission'])} BYN\n"
        f"Скидок клиентам: {fmt_money(b['total_discount'])} BYN"
    )
    await _reply(update_or_ctx, text)

async def send_last_ops_all_time(update_or_ctx, partner: ReferralPartner, limit: int = 10):
    ops = await db_last_ops_all_time(partner.id, limit=limit)
    if not ops:
        await _reply(update_or_ctx, "Операций пока нет.")
        return
    lines = [f"Последние {limit} операций — {partner.name} ({partner.code})"]
    for o in ops:
        lines.append(
            f"{o['created_at']:%d.%m %H:%M} • #{o['appointment_id']} • "
            f"{fmt_money(o['commission'])} BYN • {o['status']}"
        )
    await _reply(update_or_ctx, "\n".join(lines))

async def _reply(update_or_ctx, text: str):
    # Универсальная отправка с постоянной клавиатурой
    if isinstance(update_or_ctx, Update):
        if update_or_ctx.message:
            await update_or_ctx.message.reply_text(text, reply_markup=reply_kb())
        elif update_or_ctx.effective_message:
            await update_or_ctx.effective_message.reply_text(text, reply_markup=reply_kb())
    else:
        # fallback: контекст без апдейта
        await update_or_ctx.bot.send_message(
            chat_id=update_or_ctx.effective_chat.id,
            text=text,
            reply_markup=reply_kb(),
        )

# =========================
# Команды
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот уведомлений для партнёров.\n\n"
        "Чтобы привязать чат к партнёрскому коду, отправьте:\n"
        "/link КОД\n\n"
        "После привязки используйте кнопки внизу: «Баланс», «Отчёт», «Помощь».",
        reply_markup=reply_kb(),
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды и кнопки:\n"
        "• «Баланс» — сводка за всё время\n"
        "• «Отчёт» — последние 10 операций\n"
        "• «Помощь» — это сообщение\n\n"
        "/link КОД — привязать чат к партнёру\n"
        "/balance — то же, что и «Баланс»",
        reply_markup=reply_kb(),
    )

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    partner = await db_get_partner_by_chat(chat_id)
    if not partner:
        await update.message.reply_text("Чат не привязан. Используйте: /link КОД", reply_markup=reply_kb())
        return
    await send_balance_all_time(update, partner)

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # код может прийти аргументом: /link ABC123, либо слитно '/linkABC123'
    text = (update.message.text or "").strip()
    code = None
    if context.args:
        code = context.args[0].strip()
    elif text.startswith("/link") and len(text) > len("/link"):
        code = text[len("/link"):].strip()

    if not code:
        await update.message.reply_text("Не указан код. Пример: /link ABC123", reply_markup=reply_kb())
        return

    partner = await db_get_partner_by_code(code)
    if not partner:
        await update.message.reply_text("Код не найден. Проверьте и попробуйте снова.", reply_markup=reply_kb())
        return

    chat_id = update.effective_chat.id
    created, _ = await db_link_partner_chat(partner.id, chat_id)
    if created:
        await update.message.reply_text(
            f"Готово! Этот чат привязан к партнёру: {partner.name} ({partner.code}).",
            reply_markup=reply_kb(),
        )
    else:
        await update.message.reply_text(
            f"Обновлено. Этот чат уже привязан к партнёру: {partner.name} ({partner.code}).",
            reply_markup=reply_kb(),
        )

# =========================
# Обработка текстовых кнопок
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()

    # Чтобы кнопки работали без слэшей
    if text == BTN_BALANCE.lower():
        partner = await db_get_partner_by_chat(update.effective_chat.id)
        if not partner:
            await update.message.reply_text("Чат не привязан. Используйте: /link КОД", reply_markup=reply_kb())
            return
        await send_balance_all_time(update, partner)
        return

    if text == BTN_REPORT.lower():
        partner = await db_get_partner_by_chat(update.effective_chat.id)
        if not partner:
            await update.message.reply_text("Чат не привязан. Используйте: /link КОД", reply_markup=reply_kb())
            return
        await send_last_ops_all_time(update, partner, limit=10)
        return

    if text == BTN_HELP.lower():
        await cmd_help(update, context)
        return

    # необязательный ответ на незнакомый текст
    await update.message.reply_text(
        "Не понял. Используйте кнопки снизу или /help.",
        reply_markup=reply_kb(),
    )

# =========================
# Ошибки
# =========================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка в боте", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Произошла ошибка. Попробуйте ещё раз позже.", reply_markup=reply_kb())
    except Exception:
        pass

# =========================
# Запуск
# =========================
class Command(BaseCommand):
    help = "Запускает Telegram-бота уведомлений партнёров (с постоянными кнопками)."

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

        # команды
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("balance", cmd_balance))
        app.add_handler(CommandHandler("link", cmd_link))

        # текстовые нажатия кнопок (и любой обычный текст)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

        # ошибки
        app.add_error_handler(on_error)

        self.stdout.write(self.style.SUCCESS("Бот запущен. Нажмите Ctrl+C для остановки."))
        try:
            app.run_polling(close_loop=False)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Остановка бота..."))
