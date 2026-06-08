"""Multi-bot factory.

build_all_applications() constructs one python-telegram-bot Application per bot
whose token is configured, registers that bot's role-specific handlers, and
attaches the shared global error handler.
"""
import logging

from telegram.ext import Application

from bot.config import settings
from bot.error_handler import global_error_handler
from bot.bots import sales, demo, payment, fileshare

logger = logging.getLogger(__name__)

# Ordered so the scheduler / "representative" bot preference is deterministic.
BOT_KEYS = ["sales", "demo", "payment", "file"]

_REGISTRARS = {
    "sales": sales.register,
    "demo": demo.register,
    "payment": payment.register,
    "file": fileshare.register,
}


def build_all_applications() -> dict:
    """Return {bot_key: Application} for every bot with a configured token.

    A failure building one bot is logged and skipped — the others still come up.
    """
    apps = {}
    for key in BOT_KEYS:
        token = settings.bot_token_for(key)
        if not token:
            logger.info(f"Bot '{key}' has no token configured — skipping.")
            continue
        try:
            application = Application.builder().token(token).build()
            _REGISTRARS[key](application)
            application.add_error_handler(global_error_handler)
            apps[key] = application
            logger.info(f"Built '{key}' bot application.")
        except Exception as e:
            logger.error(f"Failed to build '{key}' bot application: {e}", exc_info=True)
    if not apps:
        logger.error("No bot applications were built! Check your *_BOT_TOKEN settings.")
    return apps
