from datetime import datetime, timedelta
from collections import defaultdict
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from sqlalchemy.future import select
from bot.models import get_db, User, PreviewSession, VideoView
from bot.config import settings
from bot.services import (
    schedule_video_deletion, 
    check_user_access, 
    get_preview_video_id, 
    get_full_video_id, 
    get_video_price
)
from bot.handlers.payment import handle_buy_access, handle_check_payment
import logging

logger = logging.getLogger(__name__)

# Development sample video URL
SAMPLE_VIDEO_URL = "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/one-by-one-person-detection.mp4"

async def send_typing_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Sends a typing chat action for UX polish."""
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

async def handle_start_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delivers a video preview, registers the session, and schedules its auto-deletion."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # 1. Extract video_id from callback_data (format: "action:param")
    video_id = "video_001"
    if query and query.data and ":" in query.data:
        video_id = query.data.split(":", 1)[1]

    # 2. Check if user already paid -> watch full instead
    has_access = await check_user_access(user_id)
    if has_access:
        await handle_watch_full(update, context)
        return

    # 3. Check if user already watched a preview in the last 24h
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
        keyboard = [
            [
                InlineKeyboardButton(
                    f"💳 Buy Full Access — ₹{get_video_price()}", 
                    callback_data=f"buy_access:{video_id}"
                )
            ]
        ]
        msg = "You already watched your free preview. Upgrade to get full access!"
        if query:
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 4. Eligible for preview
    await send_typing_action(context, chat_id)
    
    notice_text = "🎬 Your 3-minute preview is starting! It will disappear after 3 minutes."
    if query:
        await query.message.reply_text(notice_text)
    else:
        await update.message.reply_text(notice_text)

    try:
        # Send preview video
        # protect_content=True prevents forwarding or saving of the preview
        keyboard = [
            [
                InlineKeyboardButton(
                    f"💳 Unlock Full Access — ₹{get_video_price()}", 
                    callback_data=f"buy_access:{video_id}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        video_msg = await context.bot.send_video(
            chat_id=chat_id,
            video=get_preview_video_id(),
            caption="⏱ Preview ends in 3 minutes. Tap below to unlock full access!",
            reply_markup=reply_markup,
            protect_content=True,
            supports_streaming=True
        )

        # Save session to DB
        delete_time = datetime.utcnow() + timedelta(seconds=settings.VIDEO_PREVIEW_SECONDS)
        async with get_db() as session:
            new_session = PreviewSession(
                telegram_id=user_id,
                video_id=video_id,
                message_id=video_msg.message_id,
                deleted=False,
                delete_scheduled_at=delete_time
            )
            session.add(new_session)
            await session.commit()
            session_id = new_session.id

        # Schedule deletion
        schedule_video_deletion(
            context.bot, 
            chat_id, 
            video_msg.message_id, 
            session_id, 
            delay_seconds=settings.VIDEO_PREVIEW_SECONDS
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

    # Extract video_id from callback_data if available
    video_id = "video_001"
    if query and query.data and ":" in query.data:
        video_id = query.data.split(":", 1)[1]

    await send_typing_action(context, chat_id)

    # 1. Double-check if user already paid in database
    has_access = await check_user_access(user_id)
    
    # 2. If not authorized: send Access denied and a Buy button
    if not has_access:
        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Buy Access", 
                    callback_data=f"buy_access:{video_id}"
                )
            ]
        ]
        text = "Access denied. Please complete payment first."
        if query:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 3. If authorized:
    # a. Send unlock confirmation
    welcome_text = "🔓 Full access unlocked! Enjoy your video 🎉"
    if query:
        await query.message.reply_text(welcome_text)
    else:
        await update.message.reply_text(welcome_text)

    try:
        # b. Send the full video
        await context.bot.send_video(
            chat_id=chat_id,
            video=get_full_video_id(),
            caption="✅ Full video — enjoy! Thank you for your purchase.",
            protect_content=True,
            supports_streaming=True
        )

        # c. Log the access in video_views table
        async with get_db() as session:
            session.add(VideoView(
                telegram_id=user_id,
                video_id=video_id,
                viewed_at=datetime.utcnow()
            ))
            logger.info(f"Logged full video view for user {user_id} on video {video_id}")

    except Exception as e:
        logger.error(f"Failed to send full video: {e}", exc_info=True)
        error_text = "Failed to retrieve the full video. Please contact support."
        if query:
            await query.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)

# Core payment handlers are imported from bot.handlers.payment

async def preview_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command fallback handler /preview."""
    await handle_start_preview(update, context)

async def watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command fallback handler /watch."""
    await handle_watch_full(update, context)


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
        # Filter out timestamps older than 60 seconds
        user_calls = [t for t in user_calls if now - t < 60]
        _rate_limit_tracker[user.id] = user_calls

        if len(user_calls) >= 10:
            await query.answer("⏱ Too many requests. Please wait.", show_alert=True)
            return

        user_calls.append(now)

        # Always call query.answer() first in every callback handler (for allowed requests)
        await query.answer()

        data = query.data
        if not data:
            return

        if ":" in data:
            action, param = data.split(":", 1)
        else:
            action, param = data, ""

        logger.info(f"Routing callback action '{action}' with param '{param}' for user {user.id}")

        # 1. Unknown user redirection to /start
        async with get_db() as session:
            result = await session.execute(select(User).filter(User.telegram_id == user.id))
            db_user = result.scalars().first()
            if not db_user:
                logger.info(f"Callback query from unknown user {user.id}. Redirecting to /start.")
                from bot.handlers.start import start_handler
                await start_handler(update, context)
                return

        # 2. Routing to actions
        try:
            if action == "start_preview":
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
