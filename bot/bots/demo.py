"""Demo Bot — @lookatthedemobot.

Phase 4: serves a self-destructing demo for a plan. /start carries a plan id via
deep-link payload (from the Sales bot); the bot sends the plan's preview (or, if
none is set, the first linked content item as a teaser) with protect_content, and
schedules its deletion after DEMO_EXPIRY_SECONDS. On expiry the message is removed
and an expiry notice with a Buy deep-link to the Payment bot is sent.

With no payload, the user gets a picker of plans to demo.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from bot.config import settings
from bot.services.sessions import register_user_and_bot, build_deep_link, is_user_blocked, safe_answer
from bot.services.plans import list_active_plans, get_plan
from bot.services.content import list_content_for_plan
from bot.services.delivery import deliver_plan_preview, deliver_content_item
from bot.services.scheduler import schedule_demo_expiry
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "demo"


async def _send_demo(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str) -> None:
    """Send the demo media for a plan and schedule its expiry."""
    chat_id = update.effective_chat.id
    plan = await get_plan(plan_id)
    if not plan:
        await context.bot.send_message(chat_id=chat_id, text="❌ That plan isn't available.")
        return

    expiry = settings.DEMO_EXPIRY_SECONDS
    caption = (
        f"🎬 Demo: {plan.name}\n"
        f"This preview self-destructs in {expiry} seconds. "
        f"Buy the full plan (₹{plan.price_inr}) to keep watching."
    )

    # 1. Prefer the plan's dedicated preview clip (via storage channel, portable).
    sent_message_id = await deliver_plan_preview(context.bot, chat_id, plan, caption=caption, protect=True)

    # 2. Fallback: first deliverable linked content item as a teaser.
    if sent_message_id is None:
        items = await list_content_for_plan(plan_id, active_only=True)
        for item in items:
            sent_message_id = await deliver_content_item(context.bot, chat_id, item, caption=caption, protect=True)
            if sent_message_id is not None:
                break

    # 3. Nothing to show.
    if sent_message_id is None:
        pay_link = build_deep_link("payment", plan_id)
        buttons = [[InlineKeyboardButton(f"💳 Buy {plan.name} — ₹{plan.price_inr}", url=pay_link)]] if pay_link else None
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎬 *{plan.name}*\n\nNo demo clip is available yet, but you can unlock the full plan below.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            parse_mode="Markdown",
        )
        return

    # Immediately offer a persistent Buy button (and Back to Plans) so the user
    # doesn't have to wait for the preview to expire before they can purchase.
    pay_link = build_deep_link("payment", plan_id)
    sales_link = build_deep_link("sales", "from_demo")
    buttons = []
    if pay_link:
        buttons.append([InlineKeyboardButton(f"💳 Buy {plan.name} — ₹{plan.price_inr}", url=pay_link)])
    if sales_link:
        buttons.append([InlineKeyboardButton("🛍️ See Other Plans", url=sales_link)])
    if buttons:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(f"👆 Your *{plan.name}* preview is playing (expires in {expiry}s).\n"
                  f"Ready for the full content? Unlock it for ₹{plan.price_inr}."),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    # Schedule self-destruction of the preview clip + a follow-up notice.
    schedule_demo_expiry(context.bot, chat_id, sent_message_id, plan_id=plan_id, delay_seconds=expiry)


async def _show_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plans = await list_active_plans()
    if not plans:
        await update.effective_message.reply_text("No demos available yet. Please check back soon!")
        return
    keyboard = [[InlineKeyboardButton(f"🎬 {p.name} — ₹{p.price_inr}", callback_data=f"demo:{p.id}")] for p in plans]
    await update.effective_message.reply_text(
        "👀 Pick a plan to preview:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)
    if await is_user_blocked(update, context):
        return

    payload = context.args[0] if context.args else ""
    logger.info(f"Demo /start payload: '{payload}'")

    # Payload is a plan id (e.g. 'plan_001'); tolerate a legacy 'plan_plan_001' prefix.
    plan_id = payload
    if plan_id.startswith("plan_plan_"):
        plan_id = plan_id[len("plan_"):]

    if plan_id:
        await _send_demo(update, context, plan_id)
    else:
        await _show_picker(update, context)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    query = update.callback_query
    await safe_answer(query)
    data = query.data or ""
    if data.startswith("demo:"):
        await _send_demo(update, context, data.split(":", 1)[1])


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^demo:"))
