"""File Sharing Bot — @videowarehousebot.

Phase 6: delivers purchased content. Reached via a deep link from the Payment
bot's approval message (payload = plan id). Checks the user's plan_access, then
copies every linked content item from the storage channel to the user, recording
each in `deliveries` so re-taps don't resend (resume-safe).

With no payload it lists the plans the user owns so they can pick what to collect.
"""
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from bot.services.sessions import register_user_and_bot, build_deep_link, is_user_blocked
from bot.services.access import has_plan_access, list_user_plans
from bot.services.plans import get_plan
from bot.services.content import list_content_for_plan
from bot.services.delivery import deliver_content_item, already_delivered_ids, record_delivery
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "file"

# Gentle pacing between sends to stay within Telegram rate limits.
_SEND_DELAY = 0.3


def _normalize_plan_id(payload: str) -> str:
    pid = payload or ""
    if pid.startswith("plan_plan_"):
        pid = pid[len("plan_"):]
    return pid


async def deliver_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user

    if await is_user_blocked(update, context):
        return

    if not await has_plan_access(user.id, plan_id):
        pay_link = build_deep_link("payment", plan_id)
        buttons = [[InlineKeyboardButton("💳 Buy This Plan", url=pay_link)]] if pay_link else None
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔒 You don't have access to this plan yet. Purchase it to unlock the content.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        return

    plan = await get_plan(plan_id)
    plan_name = plan.name if plan else plan_id
    items = await list_content_for_plan(plan_id, active_only=True)
    if not items:
        await context.bot.send_message(chat_id=chat_id, text=f"📦 '{plan_name}' has no content yet. Please check back soon!")
        return

    delivered = await already_delivered_ids(user.id)
    new_items = [i for i in items if i.id not in delivered]

    if not new_items:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ You've already received all {len(items)} item(s) for '{plan_name}'.",
        )
        return

    await context.bot.send_message(
        chat_id=chat_id, text=f"📦 Delivering {len(new_items)} item(s) for '{plan_name}'…"
    )

    sent = 0
    for item in new_items:
        mid = await deliver_content_item(context.bot, chat_id, item, caption=item.title, protect=True)
        if mid is not None:
            await record_delivery(user.id, item.id, plan_id)
            sent += 1
        await asyncio.sleep(_SEND_DELAY)

    already = len(items) - len(new_items)
    summary = f"✅ Delivered {sent} item(s)."
    if already:
        summary += f" ({already} already sent earlier.)"
    failed = len(new_items) - sent
    if failed:
        summary += f"\n⚠️ {failed} item(s) couldn't be delivered — please contact support."
    await context.bot.send_message(chat_id=chat_id, text=summary)


async def show_owned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    plan_ids = await list_user_plans(user.id)
    if not plan_ids:
        sales_link = build_deep_link("sales")
        buttons = [[InlineKeyboardButton("🛍️ Browse Plans", url=sales_link)]] if sales_link else None
        await update.effective_message.reply_text(
            "📦 You don't have any purchases yet.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        return
    buttons = []
    for pid in plan_ids:
        p = await get_plan(pid)
        buttons.append([InlineKeyboardButton(f"📦 {p.name if p else pid}", callback_data=f"collect:{pid}")])
    await update.effective_message.reply_text(
        "Your purchases — tap to collect your content:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)
    if await is_user_blocked(update, context):
        return
    plan_id = _normalize_plan_id(context.args[0] if context.args else "")
    if plan_id:
        await deliver_plan(update, context, plan_id)
    else:
        await show_owned(update, context)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("collect:"):
        await deliver_plan(update, context, data.split(":", 1)[1])


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mycontent", start))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^collect:"))
