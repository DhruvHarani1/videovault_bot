from datetime import datetime, time
from functools import wraps
import asyncio
import logging
import csv
import io
import psutil

# Track startup time when module is loaded
bot_start_time = datetime.utcnow()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, Application
from telegram.constants import ParseMode
from sqlalchemy import func
from sqlalchemy.future import select
from bot.config import settings
from bot.models import get_db, User, Payment, PreviewSession
from bot.services import (
    grant_user_access,
    revoke_user_access,
    set_config,
    get_preview_video_id,
    get_full_video_id,
    get_video_price,
)
from bot.services.videos import (
    list_all_videos,
    create_video,
    set_video_active,
    update_video_price,
    update_video_files,
    get_video,
)
from bot.services.plans import (
    list_all_plans,
    get_plan,
    create_plan,
    set_plan_active,
    update_plan_price,
    update_plan_fields,
    count_linked_content,
)
from bot.services.content import (
    create_content,
    list_content,
    get_content,
    set_content_active,
    link_content_to_plan,
    unlink_content_from_plan,
    list_content_for_plan,
)
from bot.services.delivery import store_media_in_channel
from bot.services.demos import add_plan_demo, list_plan_demos, count_plan_demos, clear_plan_demos
from bot.services.users import (
    list_paid_users,
    list_free_users,
    list_suspended_users,
    set_suspended,
    find_user,
    user_overview,
)
from bot.services.access import revoke_plan_access, list_user_plans
from bot.services.backup import (
    schedule_backup_soon, upload_and_pin, send_to_admin, restore_now, backup_status, is_backup_enabled,
)

logger = logging.getLogger(__name__)


def _md(text) -> str:
    """Escape legacy-Markdown special chars in dynamic text (titles, names, ids
    shown outside backticks). Prevents 'Can't parse entities' errors from stray
    underscores/asterisks in user- or auto-generated strings."""
    if text is None:
        return ""
    text = str(text)
    for ch in ("\\", "`", "*", "_", "["):
        text = text.replace(ch, "\\" + ch)
    return text


# ──────────────────────────────────────────────────────────────────────────────
# Telegram "/" command menus (setMyCommands). Public commands show to everyone;
# the admin command list is scoped to each admin's chat on the Payment bot, so
# only admins see admin commands when they type "/".
# ──────────────────────────────────────────────────────────────────────────────

PUBLIC_COMMAND_MENUS = {
    "sales":   [("start", "Browse plans & buy"), ("plans", "View all plans"), ("help", "How it works")],
    "demo":    [("start", "Watch a free demo")],
    "payment": [("start", "Buy a plan"), ("help", "How it works")],
    "file":    [("start", "Collect your content"), ("mycontent", "Your purchases")],
}

# (command, description) — shown to admins on the Payment bot when they type "/".
ADMIN_COMMAND_MENU = [
    ("admin", "Open the admin panel"),
    ("stats", "Bot statistics"),
    # Users
    ("paidusers", "List paying users"),
    ("freeusers", "List free users"),
    ("suspendedusers", "List suspended users"),
    ("userinfo", "Look up a user"),
    ("removeaccess", "Revoke a user's plan"),
    ("suspend", "Suspend a user"),
    ("unsuspend", "Unsuspend a user"),
    # Plans
    ("listplans", "List plans"),
    ("addplan", "Add a plan"),
    ("setplanprice", "Set a plan's price"),
    ("setplanpreview", "Set a plan's demo video"),
    ("adddemo", "Add multiple demos to a plan"),
    ("listdemos", "List a plan's demos"),
    ("cleardemos", "Clear a plan's demos"),
    ("removeplan", "Hide a plan"),
    ("restoreplan", "Restore a plan"),
    # Content
    ("listcontent", "List content"),
    ("addcontent", "Upload content"),
    ("linkcontent", "Link content to a plan"),
    ("unlinkcontent", "Unlink content"),
    ("plancontents", "Show a plan's content"),
    # Payments & config
    ("setqr", "Set the payment QR image"),
    ("setbanner", "Set the Sales promo banner"),
    ("broadcast", "Broadcast a message"),
    ("health", "System health"),
    ("canceladmin", "Cancel the current flow"),
    ("setcommands", "Refresh the / menu"),
    # Backups
    ("backup", "Back up the database now"),
    ("getbackup", "Download a DB snapshot"),
    ("backupstatus", "Backup status"),
    ("restore", "Restore from latest snapshot"),
]


async def apply_command_menus(bot, bot_key: str, admin_ids) -> None:
    """Register the "/" command suggestions for a bot: public commands for everyone,
    plus the admin command list scoped to each admin's chat (Payment bot only)."""
    from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

    public = PUBLIC_COMMAND_MENUS.get(bot_key, [("start", "Start")])
    try:
        await bot.set_my_commands([BotCommand(c, d) for c, d in public], scope=BotCommandScopeDefault())
    except Exception as e:
        logger.error(f"Failed to set public commands for '{bot_key}': {e}")

    if bot_key == "payment":
        admin_cmds = [BotCommand(c, d) for c, d in ADMIN_COMMAND_MENU]
        for aid in admin_ids:
            try:
                await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=aid))
            except Exception as e:
                # Admin may not have started the bot yet — harmless, will apply later.
                logger.info(f"Could not set admin command menu for {aid} (not started yet?): {e}")


def admin_only(func_to_decorate):
    """Decorator to restrict handler access to admin users only."""
    @wraps(func_to_decorate)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id not in settings.ADMIN_USER_IDS:
            logger.warning(f"Unauthorized admin access attempt by user {user.id if user else 'Unknown'}")
            return  # Silently ignore unauthorized users
        return await func_to_decorate(update, context, *args, **kwargs)
    return wrapper

# Grouped, tappable command reference shown by /admin. Telegram makes every
# /command token tappable, so the admin can run anything without memorizing it.
ADMIN_PANEL_TEXT = (
    "🛠 *VideoVault Admin Panel*\n"
    "Tap any command below to run it. Type / anytime to see the full menu.\n\n"
    "👥 *Users*\n"
    "/paidusers — paying users\n"
    "/freeusers — free users\n"
    "/suspendedusers — suspended users\n"
    "/userinfo — look up a user\n"
    "/removeaccess — revoke a user's plan\n"
    "/suspend · /unsuspend — block / unblock a user\n\n"
    "📦 *Plans*\n"
    "/listplans — list plans\n"
    "/addplan — add a plan\n"
    "/setplanprice — change price\n"
    "/setplanpreview — set a single demo\n"
    "/adddemo — add multiple demos (video/photo)\n"
    "/listdemos · /cleardemos — manage demos\n"
    "/removeplan · /restoreplan — hide / restore\n\n"
    "🎞 *Content*\n"
    "/listcontent — list content\n"
    "/addcontent — upload content\n"
    "/linkcontent · /unlinkcontent — attach / detach\n"
    "/plancontents — a plan's content\n\n"
    "💳 *Payments & Config*\n"
    "/setqr — set payment QR\n"
    "/setbanner — set Sales promo banner\n"
    "/broadcast — message users\n"
    "/stats · /health — metrics\n"
    "/canceladmin — cancel a flow\n"
    "/setcommands — refresh the / menu\n\n"
    "🗄 *Backups*\n"
    "/backup — back up the DB now\n"
    "/getbackup — download a snapshot\n"
    "/backupstatus — last backup & health\n"
    "/restore — restore latest snapshot"
)


@admin_only
async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the admin command reference + quick-action buttons."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            InlineKeyboardButton("👥 Paid Users", callback_data="admin_list_paid:1"),
        ],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_init")],
        [InlineKeyboardButton("❌ Close", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(ADMIN_PANEL_TEXT, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.message.edit_text(ADMIN_PANEL_TEXT, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Queries and displays bot statistics."""
    today_start = datetime.combine(datetime.utcnow().date(), time.min)

    async with get_db() as session:
        # Total users count
        res_users = await session.execute(select(func.count(User.id)))
        total_users = res_users.scalar() or 0

        # Total paid users
        res_paid = await session.execute(select(func.count(User.id)).filter(User.has_full_access == True))
        total_paid = res_paid.scalar() or 0

        # Total revenue
        res_rev = await session.execute(select(func.sum(Payment.amount_inr)).filter(Payment.status == "paid"))
        total_revenue = res_rev.scalar() or 0

        # Previews sent today
        res_previews = await session.execute(select(func.count(PreviewSession.id)).filter(PreviewSession.sent_at >= today_start))
        previews_today = res_previews.scalar() or 0

        # New users registered today
        res_new_users = await session.execute(select(func.count(User.id)).filter(User.joined_at >= today_start))
        new_users_today = res_new_users.scalar() or 0

    stats_text = (
        "📊 *Bot Statistics*\n\n"
        f"👥 Total users: {total_users}\n"
        f"💰 Total paid users: {total_paid}\n"
        f"💵 Total revenue: ₹{total_revenue}\n"
        f"🎬 Previews sent today: {previews_today}\n"
        f"📅 New users today: {new_users_today}"
    )

    keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="admin_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(stats_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.message.edit_text(stats_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def grant_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually grants lifetime access to a Telegram User ID."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/grantaccess {telegram_id}`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return

    async with get_db() as session:
        await grant_user_access(target_id, session)
        
    await update.message.reply_text(f"✅ Manually granted full video access to user `{target_id}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
    logger.info(f"Admin {update.effective_user.id} manually granted access to user {target_id}")

@admin_only
async def revoke_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually revokes access from a Telegram User ID."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/revokeaccess {telegram_id}`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return

    async with get_db() as session:
        await revoke_user_access(target_id, session)
        
    await update.message.reply_text(f"✅ Manually revoked video access from user `{target_id}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
    logger.info(f"Admin {update.effective_user.id} manually revoked access from user {target_id}")

@admin_only
async def list_paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command fallback for paid list."""
    await render_paid_list(update, page=1)

async def render_paid_list(update: Update, page: int) -> None:
    """Renders a paginated view of paid users."""
    limit = 10
    offset = (page - 1) * limit

    async with get_db() as session:
        # Count total paid users
        count_res = await session.execute(select(func.count(User.id)).filter(User.has_full_access == True))
        total_count = count_res.scalar() or 0

        # Query users
        users_res = await session.execute(
            select(User)
            .filter(User.has_full_access == True)
            .order_by(User.joined_at.desc())
            .offset(offset)
            .limit(limit)
        )
        users = users_res.scalars().all()

    text = f"👥 *Paid Users \\(Page {page}\\)*\n"
    text += f"Total paid: {total_count}\n\n"

    if not users:
        text += "No paid users found\\."
    else:
        for idx, u in enumerate(users, start=1 + offset):
            first_name = (u.first_name or "N/A").replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
            username = f"@{u.username}".replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`") if u.username else "N/A"
            text += f"{idx}\\. `{u.telegram_id}` \\- {username} \\({first_name}\\)\n"

    # Navigation buttons
    keyboard = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"admin_list_paid:{page-1}"))
    if total_count > page * limit:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_list_paid:{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("Back to Menu", callback_data="admin_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

@admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initiates the broadcast conversation flow."""
    context.user_data["admin_state"] = "waiting_for_broadcast_msg"
    await update.message.reply_text(
        "📢 *Broadcast Mode Initiated*\n\n"
        "Send the message (Text, Photo, or Video) you want to broadcast to users.",
        parse_mode=ParseMode.MARKDOWN
    )

# Legacy single-video upload flow. With the multi-video library this is now a
# shortcut: it replaces the preview + full file_ids of video_001 in place.
# For brand-new videos, prefer /addvideo.
@admin_only
async def upload_video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Replace the preview/full files on video_001. New videos: use /addvideo instead."""
    context.user_data["admin_state"] = "waiting_for_preview_video"
    context.user_data["upload_target_video_id"] = "video_001"
    await update.message.reply_text(
        "🎥 *Replace video_001 files*\n\n"
        "Reply to this message with the **PREVIEW** video clip.\n"
        "_For new videos use /addvideo instead._",
        parse_mode=ParseMode.MARKDOWN,
    )


# Multi-video guided add flow: title → price → preview → full
@admin_only
async def add_video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Walks the admin through adding a new video to the library."""
    context.user_data.pop("new_video", None)
    context.user_data["new_video"] = {}
    context.user_data["admin_state"] = "addvideo_waiting_title"
    await update.message.reply_text(
        "➕ *Add Video — Step 1 of 4*\n\n"
        "Send the **title** for this video.\n"
        "Type /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def cancel_admin_flow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels any guided admin flow."""
    cancelled = bool(context.user_data.get("admin_state"))
    context.user_data.pop("admin_state", None)
    context.user_data.pop("new_video", None)
    context.user_data.pop("upload_target_video_id", None)
    if cancelled:
        await update.message.reply_text("❌ Admin flow cancelled.")
    else:
        await update.message.reply_text("No admin flow in progress.")


@admin_only
async def list_videos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists every video in the library (active and hidden)."""
    videos = await list_all_videos()
    if not videos:
        await update.message.reply_text("📭 No videos in the library yet. Use /addvideo to add one.")
        return

    lines = ["📚 *Video Library*\n"]
    for v in videos:
        flag = "🟢" if v.is_active else "⚪"
        lines.append(f"{flag} `{v.id}` — {v.title} — ₹{v.price_inr}")
    lines.append("\n🟢 active, ⚪ hidden")
    lines.append("Use `/removevideo <id>` to hide, `/restorevideo <id>` to restore.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def remove_video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Soft-deletes a video (hides from user library, keeps DB rows)."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/removevideo <video_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    video_id = args[0]
    ok = await set_video_active(video_id, False)
    if ok:
        await update.message.reply_text(f"✅ Video `{video_id}` is now hidden from the library.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ No video found with id `{video_id}`.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def restore_video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restores a previously hidden video."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/restorevideo <video_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    video_id = args[0]
    ok = await set_video_active(video_id, True)
    if ok:
        await update.message.reply_text(f"✅ Video `{video_id}` is visible again.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ No video found with id `{video_id}`.", parse_mode=ParseMode.MARKDOWN)


# Per-video price editor. Usage:
#   /setprice                — show all video prices
#   /setprice 250            — legacy, updates video_001 only
#   /setprice video_002 250  — updates a specific video
@admin_only
async def set_price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates the price of a specific video. Defaults to video_001 if only an amount is given."""
    args = context.args

    if not args:
        videos = await list_all_videos()
        if not videos:
            await update.message.reply_text("No videos in the library. Use /addvideo to add one.")
            return
        lines = ["💰 *Current prices*\n"]
        for v in videos:
            lines.append(f"`{v.id}` — {v.title} — ₹{v.price_inr}")
        lines.append("\nUpdate with `/setprice <video_id> <amount>`.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if len(args) == 1:
        target_id = "video_001"
        amount_raw = args[0]
    else:
        target_id = args[0]
        amount_raw = args[1]

    try:
        new_price = int(amount_raw)
        if new_price <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Price must be a positive integer.")
        return

    ok = await update_video_price(target_id, new_price)
    if ok:
        await update.message.reply_text(f"✅ Price for `{target_id}` set to ₹{new_price}.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ No video found with id `{target_id}`.", parse_mode=ParseMode.MARKDOWN)

# Prompt 12: Test Preview Video
@admin_only
async def test_preview_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends current preview video to admin for testing."""
    preview_id = get_preview_video_id()
    await update.message.reply_text("Sending preview video... Please wait.")
    try:
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=preview_id,
            caption="⏱ Test Preview Video Clip",
            supports_streaming=True
        )
    except Exception as e:
        logger.error(f"Test preview failed: {e}")
        await update.message.reply_text(f"❌ Failed to send preview: {e}")

# Prompt 12: Test Full Video
@admin_only
async def test_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends current full video to admin for testing."""
    full_id = get_full_video_id()
    await update.message.reply_text("Sending full video... Please wait.")
    try:
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=full_id,
            caption="✅ Test Full Video Clip",
            supports_streaming=True
        )
    except Exception as e:
        logger.error(f"Test full video failed: {e}")
        await update.message.reply_text(f"❌ Failed to send full video: {e}")

# Prompt 13: Look up user profile details
@admin_only
async def user_lookup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Looks up user details by telegram_id or username."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/user {telegram_id_or_username}`", parse_mode=ParseMode.MARKDOWN)
        return

    query_str = args[0].strip()
    db_user = None

    async with get_db() as session:
        if query_str.isdigit():
            # Lookup by telegram_id
            tg_id = int(query_str)
            res = await session.execute(select(User).filter(User.telegram_id == tg_id))
            db_user = res.scalars().first()
        else:
            # Lookup by username (case-insensitive)
            clean_username = query_str.lstrip("@").lower()
            res = await session.execute(select(User).filter(func.lower(User.username) == clean_username))
            db_user = res.scalars().first()

        if not db_user:
            await update.message.reply_text("❌ User not found.")
            return

        # Fetch preview sessions count
        p_count_res = await session.execute(
            select(func.count(PreviewSession.id))
            .filter(PreviewSession.telegram_id == db_user.telegram_id)
        )
        previews_count = p_count_res.scalar() or 0

        # Fetch latest payment status
        pay_res = await session.execute(
            select(Payment)
            .filter(Payment.telegram_id == db_user.telegram_id)
            .order_by(Payment.created_at.desc())
        )
        latest_payment = pay_res.scalars().first()
        payment_status = latest_payment.status if latest_payment else "No payments initiated"

    user_info = (
        "👤 *User Info*\n\n"
        f"ID: `{db_user.telegram_id}`\n"
        f"Name: {db_user.first_name or 'N/A'} @{db_user.username or 'N/A'}\n"
        f"Joined: {db_user.joined_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Access: {'✅ Full Access' if db_user.has_full_access else '🔒 Preview only'}\n"
        f"Previews watched: {previews_count}\n"
        f"Payment status: {payment_status.upper()}"
    )

    # Inline options
    keyboard = [
        [
            InlineKeyboardButton("Grant Access", callback_data=f"admin_user_op:grant:{db_user.telegram_id}"),
            InlineKeyboardButton("Revoke Access", callback_data=f"admin_user_op:revoke:{db_user.telegram_id}")
        ],
        [
            InlineKeyboardButton("💬 Send Message", callback_data=f"admin_user_op:msg:{db_user.telegram_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(user_info, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# Prompt 13: Manual Payment Recording
@admin_only
async def record_payment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually records an offline payment and grants access."""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/recordpayment {telegram_id} {amount} [note]`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        target_id = int(args[0])
        amount = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID and Amount must be integers.")
        return

    note = " ".join(args[2:]) if len(args) > 2 else "Offline Payment (Manual)"
    order_id = f"MANUAL_{int(datetime.utcnow().timestamp())}"

    try:
        async with get_db() as session:
            # Check user exists
            user_res = await session.execute(select(User).filter(User.telegram_id == target_id))
            db_user = user_res.scalars().first()
            if not db_user:
                await update.message.reply_text("❌ User not found in database. User must /start the bot first.")
                return

            # Insert payment
            new_payment = Payment(
                telegram_id=target_id,
                razorpay_order_id=order_id,
                razorpay_payment_id=f"PAY_{order_id}",
                amount_inr=amount,
                status="paid",
                paid_at=datetime.utcnow()
            )
            session.add(new_payment)
            
            # Grant access
            await grant_user_access(target_id, session)

        await update.message.reply_text(f"✅ Recorded payment of ₹{amount} for user `{target_id}`. Full access granted.")
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"✅ Your payment of ₹{amount} has been recorded. Full access granted!"
            )
        except Exception as tg_err:
            logger.error(f"Failed to notify user {target_id} of manual payment: {tg_err}")

    except Exception as e:
        logger.error(f"Manual payment recording failed: {e}", exc_info=True)
        await update.message.reply_text("❌ Failed to record payment.")

# Prompt 13: Manual Refund / Revocation
@admin_only
async def refund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revokes user access and updates latest payment to 'refunded'."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/refund {telegram_id}`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return

    try:
        async with get_db() as session:
            # Revoke access
            await revoke_user_access(target_id, session)

            # Update latest payment
            pay_res = await session.execute(
                select(Payment)
                .filter(Payment.telegram_id == target_id)
                .order_by(Payment.created_at.desc())
            )
            latest_pay = pay_res.scalars().first()
            if latest_pay:
                latest_pay.status = "refunded"
                logger.info(f"Marked payment {latest_pay.razorpay_order_id} as refunded.")

        await update.message.reply_text(f"✅ Revoked access and marked last payment as refunded for user `{target_id}`.")

        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="Your access has been revoked. Please contact support for refund processing."
            )
        except Exception as tg_err:
            logger.error(f"Failed to notify user {target_id} of refund/revocation: {tg_err}")

    except Exception as e:
        logger.error(f"Refund command execution failed: {e}", exc_info=True)
        await update.message.reply_text("❌ Failed to execute refund operations.")

# Prompt 13: CSV Users and Payments Export
@admin_only
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generates a CSV of all users and payments and sends it as a document."""
    await update.message.reply_text("⏳ Generating CSV export... Please wait.")
    
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Telegram ID", "Username", "First Name", "Joined At", 
            "Access Status", "Access Granted At", "Latest Order ID", 
            "Latest Payment Status", "Total Paid (INR)"
        ])

        async with get_db() as session:
            res_users = await session.execute(select(User).order_by(User.joined_at.desc()))
            users = res_users.scalars().all()

            for u in users:
                # Fetch payments for user
                res_pay = await session.execute(
                    select(Payment)
                    .filter(Payment.telegram_id == u.telegram_id)
                    .order_by(Payment.created_at.desc())
                )
                payments = res_pay.scalars().all()
                latest_pay = payments[0] if payments else None
                total_paid = sum(p.amount_inr for p in payments if p.status == "paid")

                writer.writerow([
                    u.telegram_id,
                    u.username or "N/A",
                    u.first_name or "N/A",
                    u.joined_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Full Access" if u.has_full_access else "Preview Only",
                    u.access_granted_at.strftime("%Y-%m-%d %H:%M:%S") if u.access_granted_at else "N/A",
                    latest_pay.razorpay_order_id if latest_pay else "N/A",
                    latest_pay.status if latest_pay else "N/A",
                    total_paid
                ])

        # Send file document
        csv_data = bytes(output.getvalue(), "utf-8")
        bio = io.BytesIO(csv_data)
        bio.name = f"videovault_users_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=bio,
            caption="📊 Here is the complete database export containing users and their payment history."
        )

    except Exception as e:
        logger.error(f"CSV export failed: {e}", exc_info=True)
        await update.message.reply_text("❌ Failed to generate CSV export.")

# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Plan & Content Management (CMS)
# ══════════════════════════════════════════════════════════════════════════════

@admin_only
async def add_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Guided flow: name → description → price → video count."""
    context.user_data.pop("new_plan", None)
    context.user_data["new_plan"] = {}
    context.user_data["admin_state"] = "addplan_name"
    await update.message.reply_text(
        "➕ *Add Plan — Step 1 of 4*\n\nSend the plan **name** (e.g. `Standard`).\n"
        "Type /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def list_plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plans = await list_all_plans()
    if not plans:
        await update.message.reply_text("📭 No plans yet. Use /addplan to create one.")
        return
    lines = ["📋 *Plans*\n"]
    for p in plans:
        linked = await count_linked_content(p.id)
        flag = "🟢" if p.is_active else "⚪"
        demo = "🎞 demo set" if p.preview_file_id else "no demo"
        lines.append(
            f"{flag} `{p.id}` — {_md(p.name)} — ₹{p.price_inr}\n"
            f"     advertised: {p.video_count} • linked: {linked} • {demo}"
        )
    lines.append("\n🟢 active ⚪ hidden")
    lines.append("`/setplanprice <id> <amt>` · `/removeplan <id>` · `/plancontents <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def set_plan_price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/setplanprice <plan_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        price = int(args[1])
        if price <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Amount must be a positive integer.")
        return
    ok = await update_plan_price(args[0], price)
    await update.message.reply_text(
        f"✅ Plan `{args[0]}` price set to ₹{price}." if ok else f"❌ Plan `{args[0]}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def remove_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/removeplan <plan_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    ok = await set_plan_active(args[0], False)
    await update.message.reply_text(
        f"✅ Plan `{args[0]}` hidden from the catalog." if ok else f"❌ Plan `{args[0]}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def restore_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/restoreplan <plan_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    ok = await set_plan_active(args[0], True)
    await update.message.reply_text(
        f"✅ Plan `{args[0]}` is visible again." if ok else f"❌ Plan `{args[0]}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def set_banner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the Sales bot promo banner image. Send a photo after this command."""
    context.user_data["admin_state"] = "setbanner_waiting_image"
    await update.message.reply_text(
        "🖼 Send the **promo banner image** now (as a photo).\n"
        "It'll appear at the top of the Sales catalog. /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def set_qr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the payment QR/UPI image shown by the Payment bot. Send a photo after."""
    context.user_data["admin_state"] = "setqr_waiting_image"
    await update.message.reply_text(
        "💳 Send the **payment QR / UPI image** now (as a photo).\n"
        "It will be shown to users on the payment screen. /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def set_plan_preview_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a plan's demo preview clip. Usage: /setplanpreview <plan_id>, then send a video."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/setplanpreview <plan_id>` then send the demo video.", parse_mode=ParseMode.MARKDOWN)
        return
    plan = await get_plan(args[0])
    if not plan:
        await update.message.reply_text(f"❌ Plan `{args[0]}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["admin_state"] = "setplanpreview_waiting_video"
    context.user_data["setplanpreview_plan_id"] = args[0]
    await update.message.reply_text(
        f"🎬 Send the **demo/preview video** for plan `{args[0]}` ({plan.name}).\n"
        "It will be shown (and auto-deleted) by the Demo bot. /canceladmin to abort.\n"
        "_Tip: use /adddemo to attach MULTIPLE demo videos/photos._",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def add_demo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bulk-add demo media (videos AND/OR photos) to a plan. Usage: /adddemo <plan_id>."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/adddemo <plan_id>` then send videos/photos.", parse_mode=ParseMode.MARKDOWN)
        return
    plan = await get_plan(args[0])
    if not plan:
        await update.message.reply_text(f"❌ Plan `{args[0]}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["admin_state"] = "adddemo_bulk"
    context.user_data["adddemo_plan_id"] = args[0]
    context.user_data["adddemo_count"] = 0
    await update.message.reply_text(
        f"🎬 *Add demos to `{args[0]}` ({_md(plan.name)})*\n\n"
        "Send videos and/or photos one after another — each becomes a demo item shown by the Demo bot.\n"
        "Send /donedemo when finished, or /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def done_demo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    n = context.user_data.pop("adddemo_count", 0)
    plan_id = context.user_data.pop("adddemo_plan_id", None)
    context.user_data.pop("admin_state", None)
    await update.message.reply_text(f"✅ Done. Added {n} demo item(s) to `{plan_id}`.", parse_mode=ParseMode.MARKDOWN)
    if n:
        schedule_backup_soon(context.bot)


@admin_only
async def list_demos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/listdemos <plan_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    demos = await list_plan_demos(args[0])
    if not demos:
        await update.message.reply_text(f"Plan `{args[0]}` has no demo items. Add some with `/adddemo {args[0]}`.", parse_mode=ParseMode.MARKDOWN)
        return
    lines = [f"🎬 *Demos for `{args[0]}`* — {len(demos)} item(s)\n"]
    for d in demos:
        lines.append(f"{d.position + 1}. {d.media_type} (id {d.id})")
    lines.append(f"\nClear all with `/cleardemos {args[0]}`.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def clear_demos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/cleardemos <plan_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    n = await clear_plan_demos(args[0])
    await update.message.reply_text(f"✅ Removed {n} demo item(s) from `{args[0]}`.", parse_mode=ParseMode.MARKDOWN)
    if n:
        schedule_backup_soon(context.bot)


@admin_only
async def add_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bulk content upload mode: every video/photo/document sent becomes a content
    item (title = caption or auto). Exit with /donecontent or /canceladmin."""
    context.user_data["admin_state"] = "addcontent_bulk"
    context.user_data["addcontent_count"] = 0
    await update.message.reply_text(
        "📥 *Bulk Content Upload*\n\n"
        "Send me videos (or photos/documents) one after another. Each becomes a content "
        "item; the **caption** is used as its title if provided.\n\n"
        "Send /donecontent when finished, or /canceladmin to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def done_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    n = context.user_data.pop("addcontent_count", 0)
    context.user_data.pop("admin_state", None)
    await update.message.reply_text(f"✅ Done. Added {n} content item(s). Use /linkcontent to attach them to plans.")
    if n:
        schedule_backup_soon(context.bot)


@admin_only
async def list_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await list_content(active_only=False, limit=50)
    if not items:
        await update.message.reply_text("📭 No content yet. Use /addcontent to upload.")
        return
    lines = ["🗂 *Content Library* (latest 50)\n"]
    for it in items:
        flag = "🟢" if it.is_active else "⚪"
        lines.append(f"{flag} `{it.id}` — {_md(it.title)} ({it.media_type})")
    lines.append("\n`/linkcontent <plan_id> <content_id...>` · `/removecontent <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def remove_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/removecontent <content_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    ok = await set_content_active(args[0], False)
    await update.message.reply_text(
        f"✅ Content `{args[0]}` hidden." if ok else f"❌ Content `{args[0]}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def link_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/linkcontent <plan_id> <content_id> [content_id ...]`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    plan_id = args[0]
    plan = await get_plan(plan_id)
    if not plan:
        await update.message.reply_text(f"❌ Plan `{plan_id}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    status_disp = {"linked": "linked", "exists": "already linked", "not_found": "not found"}
    results = []
    for cid in args[1:]:
        status = await link_content_to_plan(plan_id, cid)
        emoji = {"linked": "✅", "exists": "↔️", "not_found": "❌"}.get(status, "❓")
        results.append(f"{emoji} `{cid}` — {status_disp.get(status, status)}")
    await update.message.reply_text(
        f"Linking to `{plan_id}`:\n" + "\n".join(results), parse_mode=ParseMode.MARKDOWN
    )
    schedule_backup_soon(context.bot)


@admin_only
async def unlink_content_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/unlinkcontent <plan_id> <content_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    ok = await unlink_content_from_plan(args[0], args[1])
    await update.message.reply_text(
        f"✅ Unlinked `{args[1]}` from `{args[0]}`." if ok else "❌ That link doesn't exist.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def plan_contents_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/plancontents <plan_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    plan = await get_plan(args[0])
    if not plan:
        await update.message.reply_text(f"❌ Plan `{args[0]}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    items = await list_content_for_plan(args[0], active_only=False)
    if not items:
        await update.message.reply_text(f"Plan `{args[0]}` ({plan.name}) has no linked content yet.", parse_mode=ParseMode.MARKDOWN)
        return
    lines = [f"📦 *{_md(plan.name)}* (`{plan.id}`) — {len(items)} item(s)\n"]
    for it in items:
        flag = "🟢" if it.is_active else "⚪"
        lines.append(f"{flag} `{it.id}` — {_md(it.title)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5+ — Plan-based user management (admin)
# ══════════════════════════════════════════════════════════════════════════════

def _page_arg(context) -> int:
    try:
        return max(1, int(context.args[0])) if context.args else 1
    except (ValueError, IndexError):
        return 1


@admin_only
async def paid_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = _page_arg(context)
    data = await list_paid_users(page)
    if data["total"] == 0:
        await update.message.reply_text("No paid users yet.")
        return
    lines = [f"💰 *Paid users* — {data['total']} total (page {data['page']}/{data['pages']})\n"]
    for u, plans in data["items"]:
        uname = f"@{u.username}" if u.username else "—"
        susp = " 🚫" if u.suspended else ""
        plans_str = ", ".join(f"`{p}`" for p in plans)
        lines.append(f"`{u.telegram_id}` {_md(uname)}{susp}\n   plans: {plans_str}")
    lines.append(f"\nNext page: `/paidusers {data['page']+1}`" if data['page'] < data['pages'] else "")
    await update.message.reply_text("\n".join(l for l in lines if l), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def free_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = _page_arg(context)
    data = await list_free_users(page)
    if data["total"] == 0:
        await update.message.reply_text("No free (non-paying) users.")
        return
    lines = [f"🆓 *Free users* — {data['total']} total (page {data['page']}/{data['pages']})\n"]
    for u in data["items"]:
        uname = f"@{u.username}" if u.username else "—"
        lines.append(f"`{u.telegram_id}` {_md(uname)} {_md(u.first_name or '')}")
    if data['page'] < data['pages']:
        lines.append(f"\nNext page: `/freeusers {data['page']+1}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def suspended_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = _page_arg(context)
    data = await list_suspended_users(page)
    if data["total"] == 0:
        await update.message.reply_text("No suspended users.")
        return
    lines = [f"🚫 *Suspended users* — {data['total']} total (page {data['page']}/{data['pages']})\n"]
    for u in data["items"]:
        uname = f"@{u.username}" if u.username else "—"
        lines.append(f"`{u.telegram_id}` {_md(uname)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/userinfo <telegram_id | @username>`", parse_mode=ParseMode.MARKDOWN)
        return
    user = await find_user(context.args[0])
    if not user:
        await update.message.reply_text("❌ User not found.")
        return
    ov = await user_overview(user.telegram_id)
    plans = ov["plans"]
    plans_str = ", ".join(f"`{p}`" for p in plans) if plans else "(none)"
    tc = ov["ticket_counts"]
    tickets = ", ".join(f"{k}: {v}" for k, v in tc.items()) or "none"
    text = (
        f"👤 *User* `{user.telegram_id}`\n"
        f"Name: {_md(user.first_name or '—')}  Username: {_md('@'+user.username if user.username else '—')}\n"
        f"Joined: {user.joined_at.strftime('%Y-%m-%d')}\n"
        f"Status: {'🚫 SUSPENDED' if user.suspended else ('🔕 inactive' if not user.is_active else '✅ active')}\n"
        f"Plans: {plans_str}\n"
        f"Tickets: {tickets}\n\n"
        f"`/removeaccess {user.telegram_id} all` · "
        f"`/{'unsuspend' if user.suspended else 'suspend'} {user.telegram_id}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@admin_only
async def remove_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revoke a user's plan access. Usage: /removeaccess <id> <plan_id|all>"""
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/removeaccess <telegram_id> <plan_id|all>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return
    which = context.args[1]
    if which == "all":
        plans = await list_user_plans(target)
        if not plans:
            await update.message.reply_text("That user has no plan access.")
            return
        for pid in plans:
            await revoke_plan_access(target, pid)
        await update.message.reply_text(f"✅ Revoked all access ({len(plans)} plan(s)) from `{target}`.", parse_mode=ParseMode.MARKDOWN)
    else:
        await revoke_plan_access(target, which)
        await update.message.reply_text(f"✅ Revoked `{which}` from `{target}`.", parse_mode=ParseMode.MARKDOWN)
    schedule_backup_soon(context.bot)
    # Notify the user.
    try:
        await context.bot.send_message(chat_id=target, text="ℹ️ Your access to a plan has been removed. Contact support if this is unexpected.")
    except Exception:
        pass


@admin_only
async def suspend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/suspend <telegram_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return
    ok = await set_suspended(target, True)
    await update.message.reply_text(
        f"🚫 User `{target}` suspended. They're now blocked from all bots." if ok else f"❌ User `{target}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )
    if ok:
        schedule_backup_soon(context.bot)
        try:
            await context.bot.send_message(chat_id=target, text="🚫 Your access has been suspended. Please contact support.")
        except Exception:
            pass


@admin_only
async def unsuspend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/unsuspend <telegram_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be an integer.")
        return
    ok = await set_suspended(target, False)
    await update.message.reply_text(
        f"✅ User `{target}` un-suspended." if ok else f"❌ User `{target}` not found.",
        parse_mode=ParseMode.MARKDOWN,
    )
    if ok:
        schedule_backup_soon(context.bot)
        try:
            await context.bot.send_message(chat_id=target, text="✅ Your access has been restored. Welcome back!")
        except Exception:
            pass


async def admin_message_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercepts messages when the admin is in broadcast, Direct Message, or Video guided upload flows."""
    user = update.effective_user
    if not user or user.id not in settings.ADMIN_USER_IDS:
        return

    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return

    msg = update.message

    if admin_state == "waiting_for_broadcast_msg":
        if msg.text:
            context.user_data["broadcast_msg"] = {"type": "text", "text": msg.text}
        elif msg.photo:
            context.user_data["broadcast_msg"] = {
                "type": "photo", 
                "photo": msg.photo[-1].file_id, 
                "caption": msg.caption
            }
        elif msg.video:
            context.user_data["broadcast_msg"] = {
                "type": "video", 
                "video": msg.video.file_id, 
                "caption": msg.caption
            }
        else:
            await update.message.reply_text("❌ Unsupported message type. Please send text, photo, or video.")
            return

        context.user_data["admin_state"] = "waiting_for_broadcast_target"
        
        keyboard = [
            [
                InlineKeyboardButton("All Users", callback_data="admin_bc_target:all"),
                InlineKeyboardButton("Paid Users Only", callback_data="admin_bc_target:paid")
            ],
            [
                InlineKeyboardButton("❌ Cancel Broadcast", callback_data="admin_bc_target:cancel")
            ]
        ]
        await update.message.reply_text(
            "Target Audience:\nWho should receive this broadcast?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # ────────── Legacy /uploadvideo flow — replaces files on an existing video ──────────
    elif admin_state == "waiting_for_preview_video":
        if msg.video:
            file_id = msg.video.file_id
            context.user_data["pending_preview_file_id"] = file_id
            # Also keep the legacy bot_config keys updated so the env-var fallback path keeps working
            await set_config("PREVIEW_VIDEO_FILE_ID", file_id)
            context.user_data["admin_state"] = "waiting_for_full_video"
            await update.message.reply_text("✅ Preview video saved!\n\nNow, send the **FULL** premium video.")
        else:
            await update.message.reply_text("❌ Please reply with a valid video file.")

    elif admin_state == "waiting_for_full_video":
        if msg.video:
            file_id = msg.video.file_id
            await set_config("FULL_VIDEO_FILE_ID", file_id)
            target_id = context.user_data.get("upload_target_video_id", "video_001")
            preview_id = context.user_data.get("pending_preview_file_id")
            # If the video row exists, update its file_ids; otherwise create it with a default title/price.
            updated = await update_video_files(target_id, preview_file_id=preview_id, full_file_id=file_id)
            if not updated:
                existing = await list_all_videos()
                default_title = "Featured Video"
                default_price = get_video_price()
                await create_video(
                    title=default_title,
                    full_file_id=file_id,
                    price_inr=default_price,
                    preview_file_id=preview_id,
                    video_id=target_id,
                )
            context.user_data.pop("admin_state", None)
            context.user_data.pop("pending_preview_file_id", None)
            context.user_data.pop("upload_target_video_id", None)
            await update.message.reply_text(f"✅ Video `{target_id}` updated!", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ Please reply with a valid video file.")

    # ────────── /addvideo guided flow: title → price → preview → full ──────────
    elif admin_state == "addvideo_waiting_title":
        if msg.text:
            context.user_data["new_video"]["title"] = msg.text.strip()
            context.user_data["admin_state"] = "addvideo_waiting_price"
            await update.message.reply_text(
                "➕ *Add Video — Step 2 of 4*\n\nSend the **price in INR** (integer, e.g. `299`).",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("❌ Please reply with text — the video title.")

    elif admin_state == "addvideo_waiting_price":
        if msg.text:
            try:
                price = int(msg.text.strip())
                if price <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("❌ Price must be a positive integer.")
                return
            context.user_data["new_video"]["price"] = price
            context.user_data["admin_state"] = "addvideo_waiting_preview"
            await update.message.reply_text(
                "➕ *Add Video — Step 3 of 4*\n\nSend the **PREVIEW** video clip (3-min teaser).",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("❌ Please send a number for the price.")

    elif admin_state == "addvideo_waiting_preview":
        if msg.video:
            context.user_data["new_video"]["preview_file_id"] = msg.video.file_id
            context.user_data["admin_state"] = "addvideo_waiting_full"
            await update.message.reply_text(
                "➕ *Add Video — Step 4 of 4*\n\nSend the **FULL** premium video.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("❌ Please reply with a valid video file.")

    elif admin_state == "addvideo_waiting_full":
        if msg.video:
            data = context.user_data.get("new_video", {})
            data["full_file_id"] = msg.video.file_id
            try:
                video = await create_video(
                    title=data["title"],
                    full_file_id=data["full_file_id"],
                    price_inr=data["price"],
                    preview_file_id=data.get("preview_file_id"),
                )
                await update.message.reply_text(
                    f"✅ Created `{video.id}` — *{data['title']}* — ₹{data['price']}.\n"
                    f"It's now visible to users in /library.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Failed to create video: {e}", exc_info=True)
                await update.message.reply_text(f"❌ Failed to create video: {e}")
            finally:
                context.user_data.pop("admin_state", None)
                context.user_data.pop("new_video", None)
        else:
            await update.message.reply_text("❌ Please reply with a valid video file.")

    # ────────── Phase 3: /addplan guided flow ──────────
    elif admin_state == "addplan_name":
        if msg.text:
            context.user_data["new_plan"]["name"] = msg.text.strip()
            context.user_data["admin_state"] = "addplan_desc"
            await update.message.reply_text(
                "➕ *Add Plan — Step 2 of 4*\n\nSend a short **description** (or `-` to skip).",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("❌ Please send the plan name as text.")

    elif admin_state == "addplan_desc":
        if msg.text:
            desc = msg.text.strip()
            context.user_data["new_plan"]["description"] = None if desc == "-" else desc
            context.user_data["admin_state"] = "addplan_price"
            await update.message.reply_text(
                "➕ *Add Plan — Step 3 of 4*\n\nSend the **price in INR** (integer).",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("❌ Please send a description (or `-`).")

    elif admin_state == "addplan_price":
        try:
            price = int(msg.text.strip())
            if price <= 0:
                raise ValueError()
        except (ValueError, AttributeError):
            await update.message.reply_text("❌ Price must be a positive integer.")
            return
        context.user_data["new_plan"]["price"] = price
        context.user_data["admin_state"] = "addplan_count"
        await update.message.reply_text(
            "➕ *Add Plan — Step 4 of 4*\n\nSend the **advertised video count** (integer, e.g. `65`).",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif admin_state == "addplan_count":
        try:
            count = int(msg.text.strip())
            if count < 0:
                raise ValueError()
        except (ValueError, AttributeError):
            await update.message.reply_text("❌ Count must be a non-negative integer.")
            return
        data = context.user_data.get("new_plan", {})
        try:
            plan = await create_plan(
                name=data["name"],
                price_inr=data["price"],
                video_count=count,
                description=data.get("description"),
            )
            await update.message.reply_text(
                f"✅ Created `{plan.id}` — *{_md(data['name'])}* — ₹{data['price']} ({count} videos).\n"
                f"Now link content with `/linkcontent {plan.id} <content_id...>`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            schedule_backup_soon(context.bot)
        except Exception as e:
            logger.error(f"Failed to create plan: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Failed to create plan: {e}")
        finally:
            context.user_data.pop("admin_state", None)
            context.user_data.pop("new_plan", None)

    # ────────── Sales promo banner capture ──────────
    elif admin_state == "setbanner_waiting_image":
        file_id = None
        if msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_id = msg.document.file_id
        if not file_id:
            await update.message.reply_text("❌ Please send an image (photo) for the banner.")
            return
        # Copy into the storage channel so the Sales bot (different token) can show it.
        storage_msg_id = await store_media_in_channel(
            context.bot, from_chat_id=update.effective_chat.id, message_id=msg.message_id
        )
        context.user_data.pop("admin_state", None)
        if storage_msg_id:
            await set_config("SALES_BANNER_MSG_ID", str(storage_msg_id))
            await update.message.reply_text("✅ Sales banner saved. It'll show at the top of the catalog.")
            schedule_backup_soon(context.bot)
        else:
            await update.message.reply_text(
                "⚠️ Couldn't copy the banner to the storage channel — make sure this bot is an admin there, then retry."
            )

    # ────────── Phase 5: /setqr capture ──────────
    elif admin_state == "setqr_waiting_image":
        file_id = None
        if msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_id = msg.document.file_id
        if not file_id:
            await update.message.reply_text("❌ Please send an image (photo) of the QR.")
            return
        await set_config("PAYMENT_QR_FILE_ID", file_id)
        context.user_data.pop("admin_state", None)
        await update.message.reply_text("✅ Payment QR image saved. Users will now see it on the payment screen.")
        schedule_backup_soon(context.bot)

    # ────────── Multi-demo bulk capture (/adddemo) ──────────
    elif admin_state == "adddemo_bulk":
        file_id = None
        media_type = "video"
        if msg.video:
            file_id, media_type = msg.video.file_id, "video"
        elif msg.photo:
            file_id, media_type = msg.photo[-1].file_id, "photo"
        elif msg.document:
            file_id, media_type = msg.document.file_id, "document"
        if not file_id:
            await update.message.reply_text("❌ Send a video or photo — or /donedemo to finish.")
            return
        plan_id = context.user_data.get("adddemo_plan_id")
        storage_msg_id = await store_media_in_channel(
            context.bot, from_chat_id=update.effective_chat.id, message_id=msg.message_id
        )
        try:
            await add_plan_demo(plan_id, storage_msg_id=storage_msg_id, file_id=file_id, media_type=media_type)
            context.user_data["adddemo_count"] = context.user_data.get("adddemo_count", 0) + 1
            warn = "" if storage_msg_id else " ⚠️ (not copied to storage — check bot is channel admin)"
            await update.message.reply_text(f"✅ Demo {media_type} added{warn}. Send more, or /donedemo.")
        except Exception as e:
            logger.error(f"Failed to add demo: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Failed to add demo: {e}")

    # ────────── Phase 4: /setplanpreview capture ──────────
    elif admin_state == "setplanpreview_waiting_video":
        if msg.video:
            plan_id = context.user_data.get("setplanpreview_plan_id")
            storage_msg_id = await store_media_in_channel(
                context.bot, from_chat_id=update.effective_chat.id, message_id=msg.message_id
            )
            ok = await update_plan_fields(
                plan_id, preview_file_id=msg.video.file_id, preview_msg_id=storage_msg_id,
            )
            context.user_data.pop("admin_state", None)
            context.user_data.pop("setplanpreview_plan_id", None)
            if not ok:
                txt = f"❌ Plan `{plan_id}` not found."
            elif storage_msg_id:
                txt = f"✅ Demo preview set for plan `{plan_id}` (stored in library channel)."
            else:
                txt = (f"⚠️ Preview saved for `{plan_id}`, but it could NOT be copied to the "
                       f"library channel — the Demo bot won't be able to show it. "
                       f"Make sure this bot is an admin of the storage channel, then retry.")
            await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
            if ok:
                schedule_backup_soon(context.bot)
        else:
            await update.message.reply_text("❌ Please send a video file for the demo preview.")

    # ────────── Phase 3: /addcontent bulk capture ──────────
    elif admin_state == "addcontent_bulk":
        file_id = None
        media_type = "video"
        if msg.video:
            file_id, media_type = msg.video.file_id, "video"
        elif msg.photo:
            file_id, media_type = msg.photo[-1].file_id, "photo"
        elif msg.document:
            file_id, media_type = msg.document.file_id, "document"

        if not file_id:
            await update.message.reply_text("❌ Send a video, photo, or document — or /donecontent to finish.")
            return

        title = (msg.caption or "").strip() or f"Untitled {media_type}"
        try:
            # Copy into the shared storage channel so any bot can deliver it later.
            storage_msg_id = await store_media_in_channel(
                context.bot, from_chat_id=update.effective_chat.id, message_id=msg.message_id
            )
            item = await create_content(
                title=title, file_id=file_id, media_type=media_type, storage_msg_id=storage_msg_id,
            )
            context.user_data["addcontent_count"] = context.user_data.get("addcontent_count", 0) + 1
            warn = "" if storage_msg_id else "\n⚠️ Not copied to the library channel — check the bot is an admin there."
            await update.message.reply_text(
                f"✅ Saved `{item.id}` — {_md(title)}.{warn} Send more, or /donecontent.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Failed to save content: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Failed to save content: {e}")

    # Prompt 13: DM message sending state
    elif admin_state.startswith("waiting_for_admin_msg:"):
        target_id = int(admin_state.split(":")[1])
        if msg.text:
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"💬 *Message from Admin:*\n\n{msg.text}",
                    parse_mode=ParseMode.MARKDOWN
                )
                await update.message.reply_text(f"✅ Message sent successfully to user `{target_id}`.")
            except Exception as e:
                logger.error(f"Failed to send direct message to {target_id}: {e}")
                await update.message.reply_text(f"❌ Failed to send message to user: {e}")
        else:
            await update.message.reply_text("❌ Directly sent messages must contain text only.")
        
        context.user_data.pop("admin_state", None)

# ══════════════════════════════════════════════════════════════════════════════
# Database backup / restore (free persistence across Render redeploys)
# ══════════════════════════════════════════════════════════════════════════════

@admin_only
async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a snapshot and upload+pin it in the backup channel."""
    if not is_backup_enabled():
        await update.message.reply_text("❌ Backups are disabled (need SQLite + BACKUP_CHANNEL_ID).")
        return
    await update.message.reply_text("🗄 Creating backup…")
    res = await upload_and_pin(context.bot)
    if res.get("ok"):
        c = res["counts"]
        pin = "📌 pinned" if res.get("pinned") else "⚠️ NOT pinned (check Pin permission)"
        await update.message.reply_text(
            f"✅ Backup saved — {res['ts']}\n{', '.join(f'{k}={v}' for k, v in c.items())}\n{pin}"
        )
    else:
        await update.message.reply_text(f"❌ Backup failed: {res.get('reason')}")


@admin_only
async def getbackup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a fresh snapshot .db file to the admin's chat."""
    await update.message.reply_text("🗄 Preparing snapshot…")
    res = await send_to_admin(context.bot, update.effective_chat.id)
    if not res.get("ok"):
        await update.message.reply_text(f"❌ Could not export: {res.get('reason')}")


@admin_only
async def backupstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = await backup_status(context.bot)
    counts = info.get("local_counts") or {}
    lines = [
        "🗄 *Backup status*",
        f"Enabled: {'yes' if info['enabled'] else 'no'}",
        f"Channel: `{info['channel']}`",
        f"Pinned snapshot present: {'yes ✅' if info['has_pinned'] else 'no ❌'}",
        f"Last backup: {info['last_at']}",
        f"Interval: every {info['interval_hours']}h · keep {info['retention']}",
        f"Local rows: {', '.join(f'{k}={v}' for k, v in counts.items()) or 'n/a'}",
    ]
    if info.get("channel_error"):
        lines.append(f"⚠️ Channel error: {info['channel_error']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before a destructive restore from the pinned snapshot."""
    if not is_backup_enabled():
        await update.message.reply_text("❌ Backups are disabled.")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Yes, restore now", callback_data="backup_restore_confirm"),
        InlineKeyboardButton("Cancel", callback_data="backup_restore_cancel"),
    ]])
    await update.message.reply_text(
        "⚠️ This will *overwrite the current database* with the latest pinned snapshot. "
        "Any changes since that snapshot will be lost.\n\nProceed?",
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def restorefrom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore from a specific .db file the admin replies to / sends."""
    msg = update.message
    target = msg.reply_to_message or msg
    doc = target.document if target else None
    if not doc:
        await update.message.reply_text(
            "Reply to a snapshot `.db` document with /restorefrom (or send the file with that caption).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text("♻️ Restoring from the provided file…")
    res = await restore_now(context.bot, file_id=doc.file_id)
    if res.get("ok"):
        c = res["counts"]
        await update.message.reply_text(f"✅ Restored. Rows: {', '.join(f'{k}={v}' for k, v in c.items())}")
    else:
        await update.message.reply_text(f"❌ Restore failed: {res.get('reason')}")


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles admin panel callback queries (menus, stats, broadcasts, user operations)."""
    query = update.callback_query
    from bot.services.sessions import safe_answer
    await safe_answer(query)

    user = query.from_user
    if not user or user.id not in settings.ADMIN_USER_IDS:
        logger.warning(f"Unauthorized callback query attempt from user {user.id if user else 'Unknown'}")
        return

    data = query.data

    if data == "admin_menu":
        await admin_menu_handler(update, context)
    elif data == "admin_stats":
        await stats_handler(update, context)
    elif data.startswith("admin_list_paid:"):
        page = int(data.split(":")[1])
        await render_paid_list(update, page)
    elif data == "admin_broadcast_init":
        context.user_data["admin_state"] = "waiting_for_broadcast_msg"
        await query.message.edit_text(
            "📢 Send the message (Text, Photo, or Video) you want to broadcast to users. "
            "Type /start or any command to exit admin flow."
        )
    elif data.startswith("admin_bc_target:"):
        target = data.split(":")[1]
        
        if target == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("broadcast_msg", None)
            await query.message.edit_text("❌ Broadcast cancelled.")
            return
            
        broadcast_msg = context.user_data.get("broadcast_msg")
        if not broadcast_msg:
            await query.message.edit_text("❌ Error: Broadcast payload missing.")
            return
            
        await query.message.edit_text("📢 Starting broadcast... Please wait.")
        
        # Query target users
        async with get_db() as session:
            if target == "paid":
                result = await session.execute(select(User.telegram_id).filter(User.has_full_access == True))
            else:
                result = await session.execute(select(User.telegram_id))
            user_ids = result.scalars().all()

        success_count = 0
        fail_count = 0
        
        for uid in user_ids:
            try:
                if broadcast_msg["type"] == "text":
                    await context.bot.send_message(chat_id=uid, text=broadcast_msg["text"])
                elif broadcast_msg["type"] == "photo":
                    await context.bot.send_photo(chat_id=uid, photo=broadcast_msg["photo"], caption=broadcast_msg["caption"])
                elif broadcast_msg["type"] == "video":
                    await context.bot.send_video(chat_id=uid, video=broadcast_msg["video"], caption=broadcast_msg["caption"])
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to {uid}: {e}")
                fail_count += 1
            await asyncio.sleep(0.05)

        context.user_data.pop("admin_state", None)
        context.user_data.pop("broadcast_msg", None)

        await query.message.reply_text(
            f"✅ *Broadcast Complete*\n\n"
            f"👥 Target: {target.upper()}\n"
            f"📤 Successfully sent: {success_count}\n"
            f"❌ Failed: {fail_count}",
            parse_mode=ParseMode.MARKDOWN
        )

    # Prompt 13: Admin actions from /user lookup
    elif data.startswith("admin_user_op:"):
        _, op, target_id_str = data.split(":")
        target_id = int(target_id_str)
        
        async with get_db() as session:
            if op == "grant":
                await grant_user_access(target_id, session)
                await query.message.reply_text(f"✅ Manually granted access to user `{target_id}`.")
                logger.info(f"Admin {user.id} manually granted access to {target_id} via User Lookup menu.")
            elif op == "revoke":
                await revoke_user_access(target_id, session)
                await query.message.reply_text(f"✅ Manually revoked access from user `{target_id}`.")
                logger.info(f"Admin {user.id} manually revoked access from {target_id} via User Lookup menu.")
            elif op == "msg":
                context.user_data["admin_state"] = f"waiting_for_admin_msg:{target_id}"
                await query.message.reply_text(f"💬 Send the text message you want to deliver to user `{target_id}`.")

    elif data == "admin_close":
        await query.message.delete()

    elif data == "backup_restore_cancel":
        await query.message.edit_text("Restore cancelled.")

    elif data == "backup_restore_confirm":
        await query.message.edit_text("♻️ Restoring from the latest pinned snapshot…")
        res = await restore_now(context.bot, file_id=None)
        if res.get("ok"):
            c = res["counts"]
            await query.message.reply_text(
                f"✅ Restored from pinned snapshot. Rows: {', '.join(f'{k}={v}' for k, v in c.items())}"
            )
        else:
            await query.message.reply_text(f"❌ Restore failed: {res.get('reason')}")

@admin_only
async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows system health status including uptime, DB connection, scheduler, and memory."""
    uptime = datetime.utcnow() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    db_status = "Disconnected ❌"
    try:
        async with get_db() as session:
            await session.execute(select(1))
            db_status = "Connected ✅"
    except Exception as e:
        db_status = f"Error: {str(e)} ❌"

    from bot.services.scheduler import scheduler
    scheduler_status = "Running ✅" if scheduler.running else "Stopped ❌"

    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / (1024 * 1024)
        
        sys_mem = psutil.virtual_memory()
        mem_str = f"Process RSS: {rss_mb:.2f} MB\nSystem Memory: {sys_mem.percent}% used ({sys_mem.used / (1024**3):.2f} GB / {sys_mem.total / (1024**3):.2f} GB)"
    except Exception as e:
        mem_str = f"Error retrieving memory usage: {str(e)}"

    health_text = (
        "⚙️ *VideoVault Bot Health Status*\n\n"
        f"⏱ *Uptime:* {uptime_str}\n"
        f"🗄 *DB Connection:* {db_status}\n"
        f"⏰ *Scheduler:* {scheduler_status}\n\n"
        f"📊 *Memory Usage:*\n`{mem_str}`"
    )

    await update.message.reply_text(health_text, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def setcommands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-register the Payment bot's command menus (public + admin-scoped)."""
    try:
        await apply_command_menus(context.bot, "payment", settings.ADMIN_USER_IDS)
        await update.message.reply_text(
            "✅ Command menus refreshed. Type / to see them.\n"
            "(Admin commands are shown only to admins.)"
        )
        logger.info(f"Admin {update.effective_user.id} refreshed command menus.")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")
        await update.message.reply_text(f"❌ Failed to register bot commands: {e}")

def setup_admin_handlers(app: Application) -> None:
    """Registers all admin-panel command handlers and callback query handlers."""
    app.add_handler(CommandHandler("admin", admin_menu_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("grantaccess", grant_access_cmd))
    app.add_handler(CommandHandler("revokeaccess", revoke_access_cmd))
    app.add_handler(CommandHandler("listpaid", list_paid_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("setcommands", setcommands_cmd))

    # Database backup / restore
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("createsnapshot", backup_cmd))
    app.add_handler(CommandHandler("getbackup", getbackup_cmd))
    app.add_handler(CommandHandler("getsnapshot", getbackup_cmd))
    app.add_handler(CommandHandler("backupstatus", backupstatus_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("restorefrom", restorefrom_cmd))
    
    # Prompt 12 commands
    app.add_handler(CommandHandler("uploadvideo", upload_video_cmd))
    app.add_handler(CommandHandler("setprice", set_price_cmd))
    app.add_handler(CommandHandler("testpreview", test_preview_cmd))
    app.add_handler(CommandHandler("testfull", test_full_cmd))

    # Multi-video library commands
    app.add_handler(CommandHandler("addvideo", add_video_cmd))
    app.add_handler(CommandHandler("listvideos", list_videos_cmd))
    app.add_handler(CommandHandler("removevideo", remove_video_cmd))
    app.add_handler(CommandHandler("restorevideo", restore_video_cmd))

    # Phase 3 — Plan & Content CMS commands
    app.add_handler(CommandHandler("addplan", add_plan_cmd))
    app.add_handler(CommandHandler("listplans", list_plans_cmd))
    app.add_handler(CommandHandler("setplanprice", set_plan_price_cmd))
    app.add_handler(CommandHandler("setplanpreview", set_plan_preview_cmd))
    app.add_handler(CommandHandler("removeplan", remove_plan_cmd))
    app.add_handler(CommandHandler("restoreplan", restore_plan_cmd))
    app.add_handler(CommandHandler("setqr", set_qr_cmd))
    app.add_handler(CommandHandler("setbanner", set_banner_cmd))
    app.add_handler(CommandHandler("adddemo", add_demo_cmd))
    app.add_handler(CommandHandler("donedemo", done_demo_cmd))
    app.add_handler(CommandHandler("listdemos", list_demos_cmd))
    app.add_handler(CommandHandler("cleardemos", clear_demos_cmd))
    app.add_handler(CommandHandler("addcontent", add_content_cmd))
    app.add_handler(CommandHandler("donecontent", done_content_cmd))
    app.add_handler(CommandHandler("listcontent", list_content_cmd))
    app.add_handler(CommandHandler("removecontent", remove_content_cmd))
    app.add_handler(CommandHandler("linkcontent", link_content_cmd))
    app.add_handler(CommandHandler("unlinkcontent", unlink_content_cmd))
    app.add_handler(CommandHandler("plancontents", plan_contents_cmd))

    # Plan-based user management
    app.add_handler(CommandHandler("paidusers", paid_users_cmd))
    app.add_handler(CommandHandler("freeusers", free_users_cmd))
    app.add_handler(CommandHandler("suspendedusers", suspended_users_cmd))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("removeaccess", remove_access_cmd))
    app.add_handler(CommandHandler("suspend", suspend_cmd))
    app.add_handler(CommandHandler("unsuspend", unsuspend_cmd))
    # /canceladmin to avoid colliding with /cancel from the user-facing /contact conversation
    app.add_handler(CommandHandler("canceladmin", cancel_admin_flow_cmd))

    # Prompt 13 commands
    app.add_handler(CommandHandler("user", user_lookup_cmd))
    app.add_handler(CommandHandler("recordpayment", record_payment_cmd))
    app.add_handler(CommandHandler("refund", refund_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    
    # Callback query router for admin panel buttons
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_menu|admin_stats|admin_list_paid:|admin_broadcast_init|admin_bc_target:|admin_user_op:|admin_close|backup_restore_)"))
    
    # Message receiver for broadcast, guided upload and DMs payload capturing
    app.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND) | filters.PHOTO | filters.VIDEO | filters.Document.ALL,
        admin_message_receiver,
    ))


