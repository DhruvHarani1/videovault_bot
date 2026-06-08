"""Demo Bot — @lookatthedemobot.

Phase 2: skeleton that registers the user, records the bot start, parses any
deep-link payload (e.g. 'from_sales' or a plan id), and points back to Sales.
The 200-second self-destructing demo media arrives in Phase 4.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.services.sessions import register_user_and_bot, build_deep_link
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "demo"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)

    payload = context.args[0] if context.args else ""
    logger.info(f"Demo /start payload from user {update.effective_user.id if update.effective_user else '?'}: '{payload}'")

    sales_link = build_deep_link("sales", "from_demo")
    buttons = []
    if sales_link:
        buttons.append([InlineKeyboardButton("🛍️ Back to Plans", url=sales_link)])

    text = (
        "👀 *Demo Bot*\n\n"
        "Here you'll get a quick self-destructing preview of the content.\n"
        "_Demo media (auto-deletes after a few minutes) arrives in the next update._"
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode="Markdown",
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
