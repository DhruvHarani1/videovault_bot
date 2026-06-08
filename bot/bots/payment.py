"""Payment Bot — @videovault693bot (the original/current bot token).

Phase 2: to preserve backward compatibility and avoid downtime, this bot keeps
the entire legacy handler set (video library, admin panel, contact, etc.) via
setup_handlers(). We only add a lightweight /start tracker in a separate handler
group so we also record the bot start in user_bot_state without disturbing the
existing start_handler.

The QR + payment-proof workflow replaces the legacy library here in Phase 5.
"""
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.handlers import setup_handlers
from bot.services.sessions import record_bot_start

logger = logging.getLogger(__name__)

BOT_KEY = "payment"


async def _track_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Records the payment-bot start. Runs in group -1 and does NOT stop
    propagation, so the legacy start_handler in group 0 still replies."""
    if update.effective_user:
        try:
            await record_bot_start(update.effective_user.id, BOT_KEY)
        except Exception as e:
            logger.error(f"Failed to record payment bot start: {e}")


def register(app: Application) -> None:
    # Legacy full functionality (library, admin, payments, contact, fallbacks).
    setup_handlers(app)
    # Side-channel start tracker in an earlier group so it co-exists with group 0.
    app.add_handler(CommandHandler("start", _track_start), group=-1)
