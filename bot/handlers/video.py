from datetime import datetime, timedelta
from collections import defaultdict
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from sqlalchemy.future import select
from bot.models import get_db, User, PreviewSession, VideoView
from bot.config import settings
from bot.services import schedule_video_deletion, check_user_access
from bot.services.videos import get_video, list_active_videos
from bot.handlers.payment import handle_buy_access, handle_check_payment
import logging

logger = logging.getLogger(__name__)


def _extract_video_id(query, default: str = "video_001") -> str:
    """Pull the video_id off a callback like 'start_preview:video_007'."""
    if query and query.data and ":" in query.data:
        return query.data.split(":", 1)[1]
    return default


async def send_typing_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Sends a typing chat action for UX polish."""
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


async def handle_start_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delivers a video preview, registers the session, and schedules its auto-deletion."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    video_id = _extract_video_id(query)

    # Resolve the video so we can use its preview_file_id and price for buttons
    video = await get_video(video_id)
    if not video or not video.is_active:
        msg = "❌ That video isn't available."
        if query:
            await query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    # If the user already paid for this specific video, jump straight to watch
    if await check_user_access(user_id, video_id=video_id):
        await handle_watch_full(update, context)
        return

    # Throttle: one preview per video per 24h
    time_threshold = datetime.utcnow() - timedelta(hours=24)
    async with get_db() as session:
        result = await session.execute(
            select(PreviewSession)
            .filter(PreviewSession.telegram_id == user_id)
            .filter(PreviewSession.video_id == video_id)
            .filter(PreviewSession.sent_at > time_threshold)
        )
        existing_session = result.scalars().first()

    if existing_session:
        logger.info(f"User {user_id} requested preview for {video_id} but already watched in last 24h.")
        keyboard = [[InlineKeyboardButton(
            f"💳 Buy Full Access — ₹{video.price_inr}",
            callback_data=f"buy_access:{video_id}",
        )]]
        msg = "You already watched your free preview for this video. Upgrade to get full access!"
        if query:
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await send_typing_action(context, chat_id)

    notice_text = f"🎬 Your 3-minute preview of '{video.title}' is starting! It will disappear after 3 minutes."
    if query:
        await query.message.reply_text(notice_text)
    else:
        await update.message.reply_text(notice_text)

    try:
        keyboard = [[InlineKeyboardButton(
            f"💳 Unlock Full Access — ₹{video.price_inr}",
            callback_data=f"buy_access:{video_id}",
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        preview_src = video.preview_file_id or video.full_file_id
        video_msg = await context.bot.send_video(
            chat_id=chat_id,
            video=preview_src,
            caption=f"⏱ Preview of '{video.title}' ends in 3 minutes. Tap below to unlock full access!",
            reply_markup=reply_markup,
            protect_content=True,
            supports_streaming=True,
        )

        delete_time = datetime.utcnow() + timedelta(seconds=settings.VIDEO_PREVIEW_SECONDS)
        async with get_db() as session:
            new_session = PreviewSession(
                telegram_id=user_id,
                video_id=video_id,
                message_id=video_msg.message_id,
                deleted=False,
                delete_scheduled_at=delete_time,
            )
            session.add(new_session)
            await session.commit()
            session_id = new_session.id

        schedule_video_deletion(
            context.bot,
            chat_id,
            video_msg.message_id,
            session_id,
            delay_seconds=settings.VIDEO_PREVIEW_SECONDS,
            video_id=video_id,
        )

    except Exception as e:
        logger.error(f"Failed to send video preview: {e}", exc_info=True)
        error_msg = "Sorry, we could not send the preview video right now. Please try again later."
        if query:
            await query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)


async def handle_watch_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the full video to paid users, or prompts unpaid users to purchase access."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    video_id = _extract_video_id(query)

    video = await get_video(video_id)
    if not video:
        msg = "❌ That video isn't available."
        if query:
            await query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    await send_typing_action(context, chat_id)

    if not await check_user_access(user_id, video_id=video_id):
        keyboard = [[InlineKeyboardButton("💳 Buy Access", callback_data=f"buy_access:{video_id}")]]
        text = f"Access denied for '{video.title}'. Please complete payment first."
        if query:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    welcome_text = f"🔓 Full access unlocked for '{video.title}'! Enjoy 🎉"
    if query:
        await query.message.reply_text(welcome_text)
    else:
        await update.message.reply_text(welcome_text)

    try:
        await context.bot.send_video(
            chat_id=chat_id,
            video=video.full_file_id,
            caption=f"✅ '{video.title}' — enjoy! Thank you for your purchase.",
            protect_content=True,
            supports_streaming=True,
        )

        async with get_db() as session:
            session.add(VideoView(
                telegram_id=user_id,
                video_id=video_id,
                viewed_at=datetime.utcnow(),
            ))
            logger.info(f"Logged full video view for user {user_id} on video {video_id}")

    except Exception as e:
        logger.error(f"Failed to send full video: {e}", exc_info=True)
        error_text = "Failed to retrieve the full video. Please contact support."
        if query:
            await query.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def preview_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/preview command — if no arg given, opens the library so the user can pick."""
    from bot.handlers.library import show_library
    args = context.args if hasattr(context, "args") else []
    if args:
        # Allow `/preview video_002`
        update.callback_query  # not present
        # Build a synthetic query.data via a thin wrapper: easiest is to just open detail.
        from bot.handlers.library import show_video_detail
        await show_video_detail(update, context, args[0])
        return
    await show_library(update, context)


async def watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watch command — if no arg, ask user to pick from library."""
    from bot.handlers.library import show_library
    args = context.args if hasattr(context, "args") else []
    if args:
        # Synthesize a video_id from the arg and call handle_watch_full with a fake query
        class _FakeQuery:
            def __init__(self, data, message):
                self.data = data
                self.message = message
        fake = _FakeQuery(f"watch_full:{args[0]}", update.message)
        update.callback_query = fake  # type: ignore[assignment]
        await handle_watch_full(update, context)
        return
    await show_library(update, context)


from bot.services.monitoring import update_last_message_time
_rate_limit_tracker = defaultdict(list)


class CallbackRouter:
    async def __call__(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        update_last_message_time()
        query = update.callback_query
        user = query.from_user
        if not user:
            logger.warning("Callback query has no effective user.")
            await query.answer()
            return

        # Rate Limiting: Max 10 callbacks per user per minute
        now = time.time()
        user_calls = _rate_limit_tracker[user.id]
        user_calls = [t for t in user_calls if now - t < 60]
        _rate_limit_tracker[user.id] = user_calls

        if len(user_calls) >= 10:
            await query.answer("⏱ Too many requests. Please wait.", show_alert=True)
            return

        user_calls.append(now)
        await query.answer()

        data = query.data
        if not data:
            return

        action, param = (data.split(":", 1) + [""])[:2]
        logger.info(f"Routing callback action '{action}' with param '{param}' for user {user.id}")

        # Bounce unknown users back to /start to register
        async with get_db() as session:
            result = await session.execute(select(User).filter(User.telegram_id == user.id))
            db_user = result.scalars().first()
            if not db_user:
                logger.info(f"Callback query from unknown user {user.id}. Redirecting to /start.")
                from bot.handlers.start import start_handler
                await start_handler(update, context)
                return

        try:
            if action == "library":
                from bot.handlers.library import show_library
                await show_library(update, context)
            elif action == "video":
                from bot.handlers.library import show_video_detail
                await show_video_detail(update, context, param)
            elif action == "start_preview":
                await handle_start_preview(update, context)
            elif action == "buy_access":
                await handle_buy_access(update, context)
            elif action == "check_payment":
                await handle_check_payment(update, context)
            elif action == "watch_full":
                await handle_watch_full(update, context)
            elif action == "main_menu":
                from bot.handlers.start import show_onboarding_menu
                await show_onboarding_menu(update, context)
            elif action == "reminders_opt_out":
                async with get_db() as session:
                    result = await session.execute(select(User).filter(User.telegram_id == user.id))
                    db_user = result.scalars().first()
                    if db_user:
                        db_user.reminders_opt_out = True
                await query.message.reply_text("🔕 You have unsubscribed from daily reminders.")
            else:
                logger.warning(f"Unknown action prefix received: {action}")
        except Exception as e:
            logger.error(f"Error handling action '{action}' for user {user.id}: {e}", exc_info=True)
            await query.message.reply_text("An error occurred while processing your request.")
