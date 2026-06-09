"""Payment Bot — @videovault693bot.

Phase 5: manual payment-proof workflow (replaces Razorpay and the legacy library).
  /start <plan_id>  → show the plan's QR + price, with an "I've Paid" button
  "I've Paid"       → bot waits for a screenshot
  screenshot        → create a PaymentTicket, forward proof to admins (with
                      Approve/Reject buttons), and email admins the proof
  Approve           → grant plan access, notify the user with a File-bot deep
                      link, email admins
  Reject            → notify the user, email admins

The admin panel (CMS, stats, broadcast, etc.) still lives on this bot via
setup_admin_handlers. The legacy per-video library is intentionally dropped.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters,
)

from bot.config import settings
from bot.services.sessions import register_user_and_bot, build_deep_link, is_user_blocked
from bot.services.plans import list_active_plans, get_plan
from bot.services.config import get_payment_qr
from bot.services.access import has_plan_access, grant_plan_access
from bot.services.tickets import create_ticket, get_ticket, set_ticket_status
from bot.services.email import send_payment_proof_email, send_approval_email, send_rejection_email
from bot.services.monitoring import update_last_message_time
from admin.panel import setup_admin_handlers

logger = logging.getLogger(__name__)

BOT_KEY = "payment"


def _normalize_plan_id(payload: str) -> str:
    pid = payload or ""
    if pid.startswith("plan_plan_"):
        pid = pid[len("plan_"):]
    return pid


async def _safe_answer(query, text=None, show_alert=False) -> None:
    """Acknowledge a callback query, tolerating stale queries.

    On Render's free tier a cold start can delay processing past Telegram's ~15s
    callback validity window, making query.answer() raise 'Query is too old'. That
    must never abort the actual work (approve/reject still proceed via send_message),
    so we swallow the error here."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.info(f"callback answer skipped (stale/invalid query): {e}")


async def _download_proof(bot, file_id: str):
    """Download the proof screenshot bytes for emailing. Returns bytes or None."""
    try:
        f = await bot.get_file(file_id)
        ba = await f.download_as_bytearray()
        return bytes(ba)
    except Exception as e:
        logger.warning(f"Could not download proof {file_id}: {e}")
        return None


async def show_payment_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    plan = await get_plan(plan_id)
    if not plan or not plan.is_active:
        await context.bot.send_message(chat_id=chat_id, text="❌ That plan isn't available.")
        return

    if await has_plan_access(user.id, plan_id):
        file_link = build_deep_link("file", plan_id)
        buttons = [[InlineKeyboardButton("📦 Collect Your Content", url=file_link)]] if file_link else None
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ You already own '{plan.name}'. Tap below to collect your content.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        return

    caption = (
        f"💳 *{plan.name}* — ₹{plan.price_inr}\n\n"
        f"1️⃣ Pay ₹{plan.price_inr} using the QR / UPI below.\n"
        f"2️⃣ Tap *I've Paid* and send a screenshot of your payment.\n"
        f"3️⃣ We'll verify and unlock your content."
    )
    keyboard = [[InlineKeyboardButton("✅ I've Paid — Upload Proof", callback_data=f"payproof:{plan_id}")]]
    markup = InlineKeyboardMarkup(keyboard)

    qr = get_payment_qr()
    if qr:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=qr, caption=caption, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception as e:
            logger.warning(f"Failed to send QR photo: {e}")
    # No QR (or send failed): text-only instructions.
    await context.bot.send_message(
        chat_id=chat_id,
        text=caption + "\n\n_(Payment QR not configured — please contact support for payment details.)_",
        reply_markup=markup,
        parse_mode="Markdown",
    )


async def show_plan_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plans = await list_active_plans()
    if not plans:
        await update.effective_message.reply_text("No plans available right now. Please check back soon!")
        return
    keyboard = [[InlineKeyboardButton(f"💳 {p.name} — ₹{p.price_inr}", callback_data=f"payplan:{p.id}")] for p in plans]
    await update.effective_message.reply_text(
        "Choose a plan to purchase:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)
    if await is_user_blocked(update, context):
        return
    plan_id = _normalize_plan_id(context.args[0] if context.args else "")
    if plan_id:
        await show_payment_screen(update, context, plan_id)
    else:
        await show_plan_picker(update, context)


async def on_workflow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles payplan / payproof / approve / reject callbacks."""
    update_last_message_time()
    query = update.callback_query
    data = query.data or ""
    action, _, param = data.partition(":")

    if action == "payplan":
        await _safe_answer(query)
        await show_payment_screen(update, context, param)
        return

    if action == "payproof":
        await _safe_answer(query)
        context.user_data["awaiting_proof_plan"] = param
        await query.message.reply_text(
            "📸 Please send a *screenshot* of your completed payment now (as a photo).",
            parse_mode="Markdown",
        )
        return

    if action in ("approve", "reject"):
        await _handle_review(update, context, action, param)
        return


async def on_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo while awaiting proof becomes a payment ticket. Runs in group 1 so it
    coexists with the admin panel's message handler (group 0)."""
    plan_id = context.user_data.get("awaiting_proof_plan")
    if not plan_id:
        return  # not in a proof flow — ignore (admin uploads etc. handled elsewhere)

    if await is_user_blocked(update, context):
        context.user_data.pop("awaiting_proof_plan", None)
        return

    update_last_message_time()
    user = update.effective_user
    plan = await get_plan(plan_id)
    if not plan:
        context.user_data.pop("awaiting_proof_plan", None)
        await update.message.reply_text("❌ That plan is no longer available.")
        return

    proof_file_id = update.message.photo[-1].file_id
    ticket = await create_ticket(user.id, plan_id, plan.price_inr, proof_file_id)
    context.user_data.pop("awaiting_proof_plan", None)

    await update.message.reply_text(
        f"✅ Proof received — ticket #{ticket.id}.\n"
        f"Plan: {plan.name} (₹{plan.price_inr}). We'll review and unlock your content shortly."
    )

    # Forward proof to each admin with Approve/Reject buttons.
    admin_caption = (
        f"🧾 Ticket #{ticket.id}\n"
        f"User: @{user.username or 'N/A'} ({user.id})\n"
        f"Plan: {plan.name} — ₹{plan.price_inr}\n"
        f"Status: PENDING"
    )
    review_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{ticket.id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{ticket.id}"),
    ]])
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id, caption=admin_caption, reply_markup=review_kb)
        except Exception as e:
            logger.error(f"Failed to forward proof to admin {admin_id}: {e}")

    # Email admins with the proof attached.
    proof_bytes = await _download_proof(context.bot, proof_file_id)
    try:
        await send_payment_proof_email(
            ticket_id=ticket.id, telegram_id=user.id, username=user.username,
            plan_name=plan.name, amount_inr=plan.price_inr,
            proof_bytes=proof_bytes, proof_filename=f"proof_{ticket.id}.jpg",
        )
    except Exception as e:
        logger.error(f"Failed to email payment proof: {e}")


async def _handle_review(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, param: str) -> None:
    query = update.callback_query
    admin = query.from_user
    if not admin or admin.id not in settings.ADMIN_USER_IDS:
        await _safe_answer(query, "Not authorized.", show_alert=True)
        return

    try:
        ticket_id = int(param)
    except ValueError:
        await _safe_answer(query, "Bad ticket id.", show_alert=True)
        return

    ticket = await get_ticket(ticket_id)
    if not ticket:
        await _safe_answer(query, "Ticket not found.", show_alert=True)
        return
    if ticket.status != "pending":
        await _safe_answer(query, f"Already {ticket.status}.", show_alert=True)
        return

    plan = await get_plan(ticket.plan_id)
    plan_name = plan.name if plan else ticket.plan_id
    await _safe_answer(query)

    if action == "approve":
        await set_ticket_status(ticket_id, "approved", reviewed_by=admin.id)
        await grant_plan_access(ticket.telegram_id, ticket.plan_id)

        # Notify the buyer with a File-bot deep link to collect content.
        file_link = build_deep_link("file", ticket.plan_id)
        buttons = [[InlineKeyboardButton("📦 Collect Your Content", url=file_link)]] if file_link else None
        try:
            await context.bot.send_message(
                chat_id=ticket.telegram_id,
                text=f"✅ Payment approved! You now have access to *{plan_name}*.\nTap below to collect your content.",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {ticket.telegram_id} of approval: {e}")

        try:
            await query.message.edit_caption(caption=f"✅ Ticket #{ticket_id} — APPROVED by {admin.id}\nPlan: {plan_name}")
        except Exception:
            pass

        try:
            await send_approval_email(
                ticket_id=ticket_id, telegram_id=ticket.telegram_id, username=None,
                plan_name=plan_name, amount_inr=ticket.amount_inr, reviewed_by=admin.id,
            )
        except Exception as e:
            logger.error(f"Approval email failed: {e}")

    else:  # reject
        await set_ticket_status(ticket_id, "rejected", reviewed_by=admin.id, reason="Rejected by admin")
        # Give the user a one-tap way to retry payment right here (re-opens the
        # payment screen for the same plan) — no need to go back to the Sales bot.
        retry_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Try Payment Again", callback_data=f"payplan:{ticket.plan_id}")
        ]])
        try:
            await context.bot.send_message(
                chat_id=ticket.telegram_id,
                text=(f"❌ Your payment proof for *{plan_name}* was not approved.\n\n"
                      f"If you believe this is a mistake or want to retry, tap below to pay again "
                      f"and upload a clear screenshot."),
                reply_markup=retry_kb,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {ticket.telegram_id} of rejection: {e}")

        try:
            await query.message.edit_caption(caption=f"❌ Ticket #{ticket_id} — REJECTED by {admin.id}\nPlan: {plan_name}")
        except Exception:
            pass

        try:
            await send_rejection_email(
                ticket_id=ticket_id, telegram_id=ticket.telegram_id, username=None,
                plan_name=plan_name, amount_inr=ticket.amount_inr, reviewed_by=admin.id,
            )
        except Exception as e:
            logger.error(f"Rejection email failed: {e}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Use /start to pick a plan and complete payment.")


def register(app: Application) -> None:
    # Admin panel (CMS, stats, broadcast, approve via buttons handled below) in group 0.
    setup_admin_handlers(app)

    # Payment workflow.
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_workflow_callback, pattern="^(payplan|payproof|approve|reject):"))
    # Proof photos in a later group so they don't clash with the admin message handler.
    app.add_handler(MessageHandler(filters.PHOTO, on_proof_photo), group=1)
