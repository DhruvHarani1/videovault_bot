import os
import re
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.future import select
from telegram import Bot
from telegram.constants import ParseMode
from bot.config import settings
from bot.models import get_db, User, PreviewSession, Payment

logger = logging.getLogger(__name__)

# Track the last time the bot received any message/update
last_message_time = datetime.utcnow()
# Flag to prevent spamming inactivity emails
inactivity_alert_sent = False

def update_last_message_time():
    """Updates the last message received timestamp and resets alert status."""
    global last_message_time, inactivity_alert_sent
    last_message_time = datetime.utcnow()
    inactivity_alert_sent = False

def send_email_alert(subject: str, body: str):
    """Sends an email alert using Python's smtplib with Gmail SMTP."""
    if not settings.ALERT_EMAIL or not settings.GMAIL_APP_PASSWORD:
        logger.warning("Email alert skipped: ALERT_EMAIL or GMAIL_APP_PASSWORD not configured in .env.")
        return

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.ALERT_EMAIL
        msg["To"] = settings.ALERT_EMAIL  # Send to the configured alert destination email

        # Connect to Gmail SMTP server
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(settings.ALERT_EMAIL, settings.GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logger.info(f"Email alert sent successfully: '{subject}'")
    except Exception as e:
        logger.error(f"Failed to send email alert: {e}", exc_info=True)

async def check_inactivity_alert(bot: Bot):
    """Checks if the bot hasn't received any updates in 30 minutes, and triggers email if needed."""
    global inactivity_alert_sent
    elapsed = datetime.utcnow() - last_message_time
    if elapsed > timedelta(minutes=30):
        if not inactivity_alert_sent:
            inactivity_alert_sent = True
            subject = "🚨 Bot Inactivity Alert!"
            body = (
                f"Warning: The VideoVault Bot has not received any updates/messages for "
                f"{int(elapsed.total_seconds() / 60)} minutes (since {last_message_time.strftime('%Y-%m-%d %H:%M:%S')} UTC).\n\n"
                f"Please check if the container is running and if webhook registration is valid."
            )
            send_email_alert(subject, body)
    else:
        # Reset if bot is active again
        inactivity_alert_sent = False

async def check_db_size_alert(bot: Bot):
    """Alerts admins if the SQLite database file size exceeds 500MB."""
    # SQLite file path from DATABASE_URL
    parts = settings.DATABASE_URL.split("///")
    if len(parts) <= 1:
        return
    db_path = parts[-1]
    
    if os.path.exists(db_path):
        size_bytes = os.path.getsize(db_path)
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > 500:
            alert_text = (
                f"⚠️ *Database Size Warning*\n\n"
                f"The SQLite database size has exceeded 500MB\\.\n"
                f"Current Size: `{size_mb:.2f} MB`"
            )
            for admin_id in settings.ADMIN_USER_IDS:
                try:
                    await bot.send_message(chat_id=admin_id, text=alert_text, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e:
                    logger.error(f"Failed to send DB size warning to admin {admin_id}: {e}")

async def check_failed_payments_alert(bot: Bot):
    """Alerts admins if 3+ failed payments occur in the last hour."""
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    try:
        async with get_db() as session:
            result = await session.execute(
                select(func.count(Payment.id))
                .filter(Payment.status == "failed")
                .filter(Payment.created_at >= one_hour_ago)
            )
            failed_count = result.scalar() or 0

            if failed_count >= 3:
                alert_text = (
                    f"⚠️ *Payment Alert*\n\n"
                    f"Notice: `{failed_count}` payments have failed in the last hour\\."
                )
                for admin_id in settings.ADMIN_USER_IDS:
                    try:
                        await bot.send_message(chat_id=admin_id, text=alert_text, parse_mode=ParseMode.MARKDOWN_V2)
                    except Exception as e:
                        logger.error(f"Failed to send failed payment warning to admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error checking failed payments count: {e}", exc_info=True)

async def send_daily_report(bot: Bot):
    """Fetches yesterday's stats and sends a daily report to admin users."""
    logger.info("Generating daily report...")
    
    # Yesterday range in UTC
    now_utc = datetime.utcnow()
    yesterday = now_utc - timedelta(days=1)
    start_time = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0)
    end_time = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59)
    
    try:
        async with get_db() as session:
            # 1. New users
            res = await session.execute(
                select(func.count(User.id)).filter(User.joined_at.between(start_time, end_time))
            )
            new_users = res.scalar() or 0

            # 2. Previews sent
            res = await session.execute(
                select(func.count(PreviewSession.id)).filter(PreviewSession.sent_at.between(start_time, end_time))
            )
            previews_sent = res.scalar() or 0

            # 3. Payments initiated
            res = await session.execute(
                select(func.count(Payment.id)).filter(Payment.created_at.between(start_time, end_time))
            )
            payments_initiated = res.scalar() or 0

            # 4. Payments completed
            res = await session.execute(
                select(func.count(Payment.id))
                .filter(Payment.status == "paid")
                .filter(Payment.paid_at.between(start_time, end_time))
            )
            payments_completed = res.scalar() or 0

            # 5. Revenue
            res = await session.execute(
                select(func.sum(Payment.amount_inr))
                .filter(Payment.status == "paid")
                .filter(Payment.paid_at.between(start_time, end_time))
            )
            revenue = res.scalar() or 0

        # Calculate conversion rate: completed payments / previews sent
        conversion_rate = 0.0
        if previews_sent > 0:
            conversion_rate = (payments_completed / previews_sent) * 100

        # Format report
        report_date = yesterday.strftime("%Y-%m-%d")
        report_text = (
            f"📈 *Daily Report — {report_date}*\n\n"
            f"New users: {new_users}\n"
            f"Previews sent: {previews_sent}\n"
            f"Payments initiated: {payments_initiated}\n"
            f"Payments completed: {payments_completed}\n"
            f"Revenue: ₹{revenue}\n"
            f"Conversion rate: {conversion_rate:.1f}%"
        )

        for admin_id in settings.ADMIN_USER_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=report_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed to send daily report to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Failed to generate daily report: {e}", exc_info=True)
