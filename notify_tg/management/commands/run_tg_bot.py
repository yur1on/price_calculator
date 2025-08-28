# notify_tg/management/commands/run_tg_bot.py
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from asgiref.sync import sync_to_async

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from repairs.models import ReferralPartner
from notify_tg.models import PartnerTelegram

logger = logging.getLogger(__name__)

# ---------- БД-обёртки (ORM в потоках) ----------
@sync_to_async
def db_get_partner_by_code(code: str):
    return ReferralPartner.objects.filter(code__iexact=code).first()

@sync_to_async
def db_link_partner_chat(partner_id: int, chat_id: int):
    partner = ReferralPartner.objects.get(id=partner_id)
    obj, created = PartnerTelegram.objects.update_or_create(
        partner=partner,
        defaults={"chat_id": chat_id, "is_active": True},
    )
    return created, obj

# ---------- Команды ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот уведомлений для партнёров.\n\n"
        "Чтобы привязать ваш Telegram к партнёрскому коду, отправьте команду:\n"
        "/link КОД\n\n"
        "Где КОД — ваш код партнёра (например, Q1W2E3)."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — инструкция\n"
        "/help — помощь\n"
        "/link КОД — привязать Telegram к партнёрскому коду"
    )

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # код может прийти как аргумент: /link ABC123, либо как склеенный текст: '/linkABC123'
    text = update.message.text or ""
    code = None
    if context.args:
        code = context.args[0].strip()
    elif text.startswith("/link") and len(text) > len("/link"):
        code = text[len("/link"):].strip()

    if not code:
        await update.message.reply_text("Не указан код. Пример: /link ABC123")
        return

    partner = await db_get_partner_by_code(code)
    if not partner:
        await update.message.reply_text("Код не найден. Проверьте правильность и попробуйте снова.")
        return

    chat_id = update.effective_chat.id
    created, obj = await db_link_partner_chat(partner.id, chat_id)

    if created:
        await update.message.reply_text(
            f"Готово! Этот чат привязан к партнёру: {partner.name} ({partner.code})."
        )
    else:
        await update.message.reply_text(
            f"Обновлено. Этот чат уже привязан к партнёру: {partner.name} ({partner.code})."
        )

# ---------- Ошибки ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка в боте", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Произошла ошибка. Попробуйте ещё раз позже.")
    except Exception:
        pass

# ---------- Запуск ----------
class Command(BaseCommand):
    help = "Запускает Telegram-бота уведомлений партнёров."

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
        app.add_error_handler(on_error)

        self.stdout.write(self.style.SUCCESS("Бот запущен. Нажмите Ctrl+C для остановки."))
        try:
            app.run_polling(close_loop=False)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Остановка бота..."))
