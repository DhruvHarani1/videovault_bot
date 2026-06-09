"""Sales Bot — @muthalsamajhbot.

Phase 3: real plan catalog. /start lists active plans; tapping a plan opens a
detail screen (name / description / price / video count + preview if set) with
deep-link hand-offs to the Demo bot (carrying the plan id) and the Payment bot
(carrying the plan id, so the payment flow in Phase 5 knows what to charge for).
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from bot.services.sessions import register_user_and_bot, build_deep_link, is_user_blocked, safe_answer
from bot.services.plans import list_active_plans, get_plan
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "sales"


def _md2(text: str) -> str:
    if text is None:
        return ""
    for ch in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(ch, f"\\{ch}")
    return text


async def _render_catalog(update: Update, edit: bool = False) -> None:
    plans = await list_active_plans()
    if not plans:
        text = "🛍️ *VideoVault*\n\nNo plans available yet\\. Please check back soon\\!"
        if edit and update.callback_query:
            await update.callback_query.message.edit_text(text, parse_mode="MarkdownV2")
        else:
            await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
        return

    keyboard = [
        [InlineKeyboardButton(f"🎬 {p.name} — ₹{p.price_inr} ({p.video_count} videos)",
                              callback_data=f"plan:{p.id}")]
        for p in plans
    ]
    text = (
        "🛍️ *VideoVault Plans*\n\n"
        "Pick a plan to see details, watch a demo, or buy\\."
    )
    markup = InlineKeyboardMarkup(keyboard)
    if edit and update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="MarkdownV2")
    else:
        await update.effective_message.reply_text(text, reply_markup=markup, parse_mode="MarkdownV2")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)
    if await is_user_blocked(update, context):
        return
    await _render_catalog(update, edit=False)


async def _show_plan_detail(update: Update, plan_id: str) -> None:
    query = update.callback_query
    plan = await get_plan(plan_id)
    if not plan or not plan.is_active:
        await query.message.edit_text("❌ That plan isn't available\\.", parse_mode="MarkdownV2")
        return

    # Deep links carry the plan id so the target bots know the context.
    demo_link = build_deep_link("demo", plan.id)
    pay_link = build_deep_link("payment", plan.id)

    body = f"🎬 *{_md2(plan.name)}*\n"
    if plan.description:
        body += f"\n{_md2(plan.description)}\n"
    body += f"\n💰 Price: ₹{plan.price_inr}\n📦 Videos: {plan.video_count}"

    buttons = []
    if demo_link:
        buttons.append([InlineKeyboardButton("👀 Watch Demo", url=demo_link)])
    if pay_link:
        buttons.append([InlineKeyboardButton(f"💳 Buy — ₹{plan.price_inr}", url=pay_link)])
    buttons.append([InlineKeyboardButton("⬅️ Back to Plans", callback_data="catalog")])
    markup = InlineKeyboardMarkup(buttons)

    # If the plan has a preview image/video set, show it as a fresh message; otherwise edit text.
    await query.message.edit_text(body, reply_markup=markup, parse_mode="MarkdownV2")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    query = update.callback_query
    await safe_answer(query)
    data = query.data or ""

    if data == "catalog":
        await _render_catalog(update, edit=True)
    elif data.startswith("plan:"):
        await _show_plan_detail(update, data.split(":", 1)[1])


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "VideoVault Sales bot. Use /start to browse plans, watch a demo, or buy."
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plans", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^(catalog|plan:)"))
