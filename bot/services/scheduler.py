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


async def delete_preview_message(bot, chat_id: int, message_id: int, session_id: int, video_id: str = "video_001"):
    """Deletes the preview video message and marks it as deleted in the database."""
    logger.info(f"Attempting to delete preview message {message_id} in chat {chat_id} for session {session_id}")

    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id}")
    except BadRequest as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id} (MessageCantBeDeleted): {e}")
    except Exception as e:
        logger.error(f"Unexpected error when deleting message {message_id}: {e}", exc_info=True)

    try:
        async with get_db() as session:
            result = await session.execute(select(PreviewSession).filter(PreviewSession.id == session_id))
            db_session = result.scalars().first()
            if db_session:
                db_session.deleted = True
                logger.info(f"Marked preview session {session_id} as deleted in DB.")
    except Exception as e:
        logger.error(f"Failed to update preview session {session_id} to deleted: {e}", exc_info=True)

    # Send post-preview purchase prompt with the video-specific price
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from bot.services.videos import get_video
        video = await get_video(video_id)
        price = video.price_inr if video else 0
        title = video.title if video else "the video"

        keyboard = [
            [InlineKeyboardButton(f"💳 Buy Now — ₹{price}", callback_data=f"buy_access:{video_id}")],
            [InlineKeyboardButton("📚 Back to Library", callback_data="library")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Title is escaped via send_message without parse_mode to avoid MD parse errors
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Preview ended! Unlock '{title}' for just ₹{price}.",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Failed to send follow-up message to chat {chat_id}: {e}", exc_info=True)


def schedule_video_deletion(bot, chat_id: int, message_id: int, session_id: int, delay_seconds: int = 180, video_id: str = "video_001"):
    """Schedules the delete_preview_message job using APScheduler."""
    run_date = datetime.now() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        delete_preview_message,
        trigger=DateTrigger(run_date=run_date),
        args=[bot, chat_id, message_id, session_id, video_id],
        id=f"delete_session_{session_id}",
        replace_existing=True
    )
    logger.info(f"Scheduled deletion of message {message_id} for session {session_id} (video {video_id}) in {delay_seconds} seconds.")


# ──────────────────────────────────────────────────────────────────────────────
# Demo Bot — self-destructing demo media (Phase 4).
# Note: jobs live in an in-memory store, so a process restart cancels pending
# deletions (same behaviour as the legacy preview deletion). For a 200s demo
# this is an acceptable edge case.
# ──────────────────────────────────────────────────────────────────────────────

async def delete_demo_message(bot, chat_id: int, message_ids, plan_id: str = ""):
    """Deletes the demo message(s) and sends an expiry notice with a Buy link."""
    if isinstance(message_ids, int):
        message_ids = [message_ids]

    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except BadRequest as e:
            logger.warning(f"Could not delete demo message {mid} in chat {chat_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error deleting demo message {mid}: {e}", exc_info=True)

    # Send the expiry notice with a deep link to the Payment bot.
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from bot.services.sessions import build_deep_link
        from bot.services.plans import get_plan

        plan = await get_plan(plan_id) if plan_id else None
        pay_link = build_deep_link("payment", plan_id) if plan_id else build_deep_link("payment")
        title = plan.name if plan else "the full plan"
        price_txt = f" — ₹{plan.price_inr}" if plan else ""

        buttons = []
        if pay_link:
            buttons.append([InlineKeyboardButton(f"💳 Buy {title}{price_txt}", url=pay_link)])

        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Your demo has expired.\n\nUnlock '{title}' to watch the full content.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    except Exception as e:
        logger.error(f"Failed to send demo expiry notice to chat {chat_id}: {e}", exc_info=True)


def schedule_demo_expiry(bot, chat_id: int, message_ids, plan_id: str = "", delay_seconds: int = 200):
    """Schedule deletion of demo media after delay_seconds, then an expiry notice."""
    run_date = datetime.now() + timedelta(seconds=delay_seconds)
    # Unique job id per chat+first message so repeated demos don't clash.
    first_mid = message_ids[0] if isinstance(message_ids, (list, tuple)) else message_ids
    scheduler.add_job(
        delete_demo_message,
        trigger=DateTrigger(run_date=run_date),
        args=[bot, chat_id, message_ids, plan_id],
        id=f"demo_expire_{chat_id}_{first_mid}",
        replace_existing=True,
    )
    logger.info(f"Scheduled demo expiry for chat {chat_id} (plan {plan_id}) in {delay_seconds}s.")

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

            for user in users_to_remind:
                try:
                    keyboard = [
                        [InlineKeyboardButton("📚 Browse Library", callback_data="library")],
                        [InlineKeyboardButton("🔕 Stop Reminders", callback_data="reminders_opt_out")],
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text="⏰ Still thinking? Browse the library and unlock the videos you loved!",
                        reply_markup=reply_markup,
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
