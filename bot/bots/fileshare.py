"""File Sharing Bot — @videowarehousebot.

Phase 2: skeleton that registers the user and records the bot start so that,
once a payment is approved, this bot is allowed to message and deliver content
to the user. Automatic plan delivery arrives in Phase 6.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.services.sessions import register_user_and_bot, build_deep_link
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "file"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)

    payload = context.args[0] if context.args else ""
    logger.info(f"File /start payload from user {update.effective_user.id if update.effective_user else '?'}: '{payload}'")

    sales_link = build_deep_link("sales", "from_file")
    buttons = []
    if sales_link:
        buttons.append([InlineKeyboardButton("🛍️ Browse Plans", url=sales_link)])

    text = (
        "📦 *File Warehouse*\n\n"
        "Your purchased content will be delivered here after your payment is approved.\n"
        "_Automatic delivery arrives in the next update._"
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode="Markdown",
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
