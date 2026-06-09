"""Cross-bot session tracking and deep-link helpers.

A Telegram bot cannot message a user who hasn't pressed Start on it, so every
hand-off between our four bots is a t.me deep link, and we record which bots a
user has started in `user_bot_state`. Later phases use this to decide whether a
target bot may message a user, or whether the user must tap a deep link first.
"""
import logging
from datetime import datetime

from sqlalchemy.future import select

from bot.config import settings
from bot.models import get_db, User, UserBotState
from bot.services.users import is_suspended

logger = logging.getLogger(__name__)

VALID_BOT_KEYS = {"sales", "demo", "payment", "file"}


async def safe_answer(query, text=None, show_alert=False) -> None:
    """Acknowledge a callback query, tolerating stale/expired queries (cold starts)."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.info(f"callback answer skipped (stale/invalid query): {e}")


async def is_user_blocked(update, context) -> bool:
    """If the user is suspended, send a notice and return True (caller should bail)."""
    user = getattr(update, "effective_user", None)
    if not user:
        return False
    try:
        if await is_suspended(user.id):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🚫 Your access has been suspended. Please contact support.",
            )
            return True
    except Exception as e:
        logger.error(f"Suspension check failed for {user.id}: {e}")
    return False


def build_deep_link(bot_key: str, payload: str = "") -> str:
    """Build a t.me deep link to another bot, optionally carrying a start payload.

    Telegram start payloads are limited to 64 chars of [A-Za-z0-9_-]. Returns ""
    if the target bot's username isn't configured.
    """
    username = settings.bot_username_for(bot_key)
    if not username:
        return ""
    base = f"https://t.me/{username}"
    return f"{base}?start={payload}" if payload else base


async def record_bot_start(telegram_id: int, bot_key: str) -> None:
    """Record (idempotently) that a user has started a given bot."""
    if bot_key not in VALID_BOT_KEYS:
        return
    async with get_db() as session:
        existing = await session.execute(
            select(UserBotState).filter(
                UserBotState.telegram_id == telegram_id,
                UserBotState.bot_key == bot_key,
            )
        )
        if existing.scalars().first() is None:
            session.add(UserBotState(telegram_id=telegram_id, bot_key=bot_key))
            logger.info(f"Recorded first '{bot_key}' start for user {telegram_id}.")


async def register_user_and_bot(tg_user, bot_key: str) -> None:
    """Upsert the user profile and record the bot start in a single transaction.

    Used by the new bots (sales/demo/file) whose handlers don't otherwise create
    a users row. The legacy payment bot already upserts users in its own /start,
    so for that bot we only need record_bot_start().
    """
    async with get_db() as session:
        res = await session.execute(select(User).filter(User.telegram_id == tg_user.id))
        user = res.scalars().first()
        username = getattr(tg_user, "username", None)
        first_name = getattr(tg_user, "first_name", None)
        if user is None:
            session.add(User(
                telegram_id=tg_user.id,
                username=username,
                first_name=first_name,
                is_active=True,
            ))
        else:
            user.username = username
            user.first_name = first_name
            user.is_active = True

        if bot_key in VALID_BOT_KEYS:
            existing = await session.execute(
                select(UserBotState).filter(
                    UserBotState.telegram_id == tg_user.id,
                    UserBotState.bot_key == bot_key,
                )
            )
            if existing.scalars().first() is None:
                session.add(UserBotState(telegram_id=tg_user.id, bot_key=bot_key))
                logger.info(f"Recorded first '{bot_key}' start for user {tg_user.id}.")


async def has_started_bot(telegram_id: int, bot_key: str) -> bool:
    """Return True if the user has previously started the given bot."""
    async with get_db() as session:
        res = await session.execute(
            select(UserBotState).filter(
                UserBotState.telegram_id == telegram_id,
                UserBotState.bot_key == bot_key,
            )
        )
        return res.scalars().first() is not None
