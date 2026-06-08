"""Global PTB error handler, shared by every bot Application.

Lives in its own module (rather than main.py) so the per-bot factories in
bot/bots can attach it without importing main — which would create a circular
import (main imports bot.bots, bot.bots would import main).
"""
import logging
import traceback

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import NetworkError, TimedOut, Forbidden
from sqlalchemy.future import select

from bot.config import settings
from bot.models import get_db, User

logger = logging.getLogger(__name__)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs full traceback, marks users inactive if they blocked the bot, and alerts admins."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    user_id = None
    if isinstance(update, Update) and update.effective_user:
        user_id = update.effective_user.id

    # Respond to the user depending on the error type.
    if isinstance(update, Update) and update.effective_message:
        if isinstance(context.error, (NetworkError, TimedOut)):
            await update.effective_message.reply_text("⚠️ Network issue. Please try again.")
        elif isinstance(context.error, Forbidden):
            if user_id:
                logger.info(f"User {user_id} blocked the bot. Marking as inactive in DB.")
                try:
                    async with get_db() as session:
                        result = await session.execute(select(User).filter(User.telegram_id == user_id))
                        user = result.scalars().first()
                        if user:
                            user.is_active = False
                except Exception as db_err:
                    logger.error(f"Failed to mark user {user_id} as inactive: {db_err}")
        else:
            await update.effective_message.reply_text("Something went wrong. Our team has been notified.")

    # Alert admins via Telegram (truncate traceback to fit Telegram's message limit).
    admin_text = (
        f"🚨 *Bot Error Alert*\n\n"
        f"👤 User: `{user_id or 'Unknown'}`\n"
        f"🔍 Error: `{str(context.error)}`\n\n"
        f"📋 *Traceback:*\n```python\n{tb_string[:3000]}\n```"
    )

    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="Markdown")
        except Exception as alert_err:
            logger.error(f"Failed to send error alert to admin {admin_id}: {alert_err}")
