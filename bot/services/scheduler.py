from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta
from telegram.error import BadRequest
from sqlalchemy.future import select
from bot.models import get_db, PreviewSession, User
from bot.config import settings
import asyncio
import logging

logger = logging.getLogger(__name__)

# Initialize a global AsyncIOScheduler instance
scheduler = AsyncIOScheduler()

def start_scheduler(bot=None):
    """Starts the background scheduler if not already running."""
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started successfully.")
        
        # Schedule the daily reminders job to run every 24 hours
        if bot:
            scheduler.add_job(
                send_daily_reminders,
                trigger="interval",
                hours=24,
                args=[bot],
                id="daily_reminders_job",
                replace_existing=True
            )
            logger.info("Scheduled daily reminders job (every 24 hours).")

            # Schedule the Daily Report Job at 9:00 AM IST
            from bot.services.monitoring import send_daily_report
            scheduler.add_job(
                send_daily_report,
                trigger="cron",
                hour=9,
                minute=0,
                timezone="Asia/Kolkata",
                args=[bot],
                id="daily_report_job",
                replace_existing=True
            )
            logger.info("Scheduled daily report job at 9:00 AM IST.")

            # Schedule the bot inactivity check every 5 minutes
            from bot.services.monitoring import check_inactivity_alert
            scheduler.add_job(
                check_inactivity_alert,
                trigger="interval",
                minutes=5,
                args=[bot],
                id="inactivity_check_job",
                replace_existing=True
            )
            logger.info("Scheduled bot inactivity check (every 5 minutes).")

            # Schedule the database file size check every hour
            from bot.services.monitoring import check_db_size_alert
            scheduler.add_job(
                check_db_size_alert,
                trigger="interval",
                hours=1,
                args=[bot],
                id="db_size_check_job",
                replace_existing=True
            )
            logger.info("Scheduled DB size warning check (every 1 hour).")


async def delete_preview_message(bot, chat_id: int, message_id: int, session_id: int):
    """Deletes the preview video message and marks it as deleted in the database."""
    logger.info(f"Attempting to delete preview message {message_id} in chat {chat_id} for session {session_id}")
    
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id}")
    except BadRequest as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id} (MessageCantBeDeleted): {e}")
    except Exception as e:
        logger.error(f"Unexpected error when deleting message {message_id}: {e}", exc_info=True)

    # Mark the session as deleted in DB
    try:
        async with get_db() as session:
            result = await session.execute(select(PreviewSession).filter(PreviewSession.id == session_id))
            db_session = result.scalars().first()
            if db_session:
                db_session.deleted = True
                logger.info(f"Marked preview session {session_id} as deleted in DB.")
    except Exception as e:
        logger.error(f"Failed to update preview session {session_id} to deleted: {e}", exc_info=True)

    # Automatically send the post-preview purchase prompt
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from bot.services import get_video_price
        price = get_video_price()
        keyboard = [
            [
                InlineKeyboardButton(f"💳 Buy Now — ₹{price}", callback_data="buy_access:video_001")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Preview ended\\! Unlock the full video for just ₹{price}",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to send follow-up message to chat {chat_id}: {e}", exc_info=True)

def schedule_video_deletion(bot, chat_id: int, message_id: int, session_id: int, delay_seconds: int = 180):
    """Schedules the delete_preview_message job using APScheduler."""
    run_date = datetime.now() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        delete_preview_message,
        trigger=DateTrigger(run_date=run_date),
        args=[bot, chat_id, message_id, session_id],
        id=f"delete_session_{session_id}",
        replace_existing=True
    )
    logger.info(f"Scheduled deletion of message {message_id} for session {session_id} in {delay_seconds} seconds.")

async def send_daily_reminders(bot):
    """Sends a daily follow-up reminder to users who watched the preview but did not buy access."""
    logger.info("Running daily reminders background job...")
    time_threshold = datetime.utcnow() - timedelta(hours=24)

    try:
        async with get_db() as session:
            # Query users who:
            # - Do not have full access
            # - Have not opted out of reminders
            # - Have not received a reminder yet
            # - Have a preview session started >= 24 hours ago
            stmt = (
                select(User)
                .filter(User.has_full_access == False)
                .filter(User.reminders_opt_out == False)
                .filter(User.reminders_sent == 0)
                .join(PreviewSession, User.telegram_id == PreviewSession.telegram_id)
                .filter(PreviewSession.sent_at <= time_threshold)
                .distinct()
            )
            result = await session.execute(stmt)
            users_to_remind = result.scalars().all()

            if not users_to_remind:
                logger.info("No users require daily reminders today.")
                return

            logger.info(f"Found {len(users_to_remind)} user(s) eligible for daily reminders.")

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from bot.services import get_video_price
            price = get_video_price()

            for user in users_to_remind:
                try:
                    keyboard = [
                        [
                            InlineKeyboardButton(f"💳 Buy Now — ₹{price}", callback_data="buy_access:video_001")
                        ],
                        [
                            InlineKeyboardButton("🔕 Stop Reminders", callback_data="reminders_opt_out")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"⏰ Still thinking? Don't miss out on unlocking the full video for just ₹{price}!",
                        reply_markup=reply_markup
                    )
                    
                    # Update reminders sent status in DB
                    user.reminders_sent = 1
                    logger.info(f"Daily reminder sent successfully to user {user.telegram_id}.")
                except Exception as user_err:
                    logger.error(f"Failed to send daily reminder to user {user.telegram_id}: {user_err}")
                
                # Sleep briefly to avoid flooding Telegram API limits
                await asyncio.sleep(0.05)

    except Exception as e:
        logger.error(f"Error occurred during daily reminders execution: {e}", exc_info=True)
