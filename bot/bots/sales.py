"""Sales Bot — @muthalsamajhbot.

Phase 2: minimal skeleton that registers the user, records the bot start, and
offers deep-link hand-offs to the Demo and Payment bots. The full plan catalog
arrives in Phase 3.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.services.sessions import register_user_and_bot, build_deep_link
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "sales"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)

    demo_link = build_deep_link("demo", "from_sales")
    pay_link = build_deep_link("payment", "from_sales")

    buttons = []
    if demo_link:
        buttons.append([InlineKeyboardButton("👀 See a Demo", url=demo_link)])
    if pay_link:
        buttons.append([InlineKeyboardButton("💳 Buy / Payment", url=pay_link)])

    text = (
        "🛍️ *Welcome to VideoVault*\n\n"
        "Pick a plan and unlock a full library of videos.\n"
        "_The plan catalog lands in the next update._\n\n"
        "Tap below to watch a quick demo or head to payment."
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "This is the VideoVault Sales bot. Use /start to see plans and options."
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
