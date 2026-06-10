"""Sales Bot — @muthalsamajhbot.

The storefront. Shows an eye-catching promo banner (admin-set via /setbanner,
stored in the storage channel so it's cross-bot safe) with punchy marketing copy,
then a plan catalog. Tapping a plan opens a rich detail screen with deep-link
hand-offs to the Demo bot and the Payment bot (both carrying the plan id).
"""
import logging
from html import escape as _h

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from bot.config import settings
from bot.services.sessions import register_user_and_bot, build_deep_link, is_user_blocked, safe_answer
from bot.services.plans import list_active_plans, get_plan
from bot.services.config import get_sales_banner_msg_id
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

BOT_KEY = "sales"


def _catalog_text(n_plans: int) -> str:
    return (
        "🔥 <b>VideoVault — The Premium Vault</b> 🔥\n\n"
        "✨ <b>Never-before-seen</b> exclusive videos\n"
        "🎬 Crystal-clear <b>Full HD</b> quality\n"
        "📈 <b>Trending</b> Instagram &amp; viral clips\n"
        "🆕 Fresh drops added <b>every week</b>\n"
        "🔒 Instant <b>lifetime</b> access after payment\n\n"
        "⭐ Thousands already inside the vault.\n"
        "⚡ <b>Limited-time launch pricing</b> — pick your plan below 👇"
    )


def _plan_detail_text(plan) -> str:
    name = _h(plan.name or "")
    desc = _h(plan.description or "")
    body = f"🎬 <b>{name}</b>\n"
    if desc:
        body += f"\n{desc}\n"
    body += (
        f"\n💰 <b>₹{plan.price_inr}</b> one-time · 📦 <b>{plan.video_count} videos</b> · 🔓 lifetime access\n"
        "✅ Full HD  ✅ Mobile + Desktop  ✅ Instant delivery\n\n"
        "👀 Watch a free demo, or unlock everything now 👇"
    )
    return body


def _catalog_markup(plans) -> InlineKeyboardMarkup | None:
    if not plans:
        return None
    rows = [[InlineKeyboardButton(f"🎬 {p.name} — ₹{p.price_inr}  ({p.video_count} videos)",
                                  callback_data=f"plan:{p.id}")] for p in plans]
    return InlineKeyboardMarkup(rows)


async def _edit_view(query, text: str, markup) -> None:
    """Edit the current view in place — works whether it's a photo (banner) caption
    or a plain text message."""
    msg = query.message
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"sales edit_view failed: {e}")


async def _send_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plans = await list_active_plans()
    chat_id = update.effective_chat.id

    if not plans:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎬 <b>VideoVault</b>\n\nNew premium videos are dropping very soon — check back shortly!",
            parse_mode=ParseMode.HTML,
        )
        return

    text = _catalog_text(len(plans))
    markup = _catalog_markup(plans)

    # Show the promo banner (copied from the storage channel so it works on this bot).
    banner_msg_id = get_sales_banner_msg_id()
    if banner_msg_id and settings.STORAGE_CHANNEL_ID:
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=settings.STORAGE_CHANNEL_ID,
                message_id=banner_msg_id,
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            logger.warning(f"Sales banner send failed, falling back to text: {e}")

    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    if update.effective_user:
        await register_user_and_bot(update.effective_user, BOT_KEY)
    if await is_user_blocked(update, context):
        return
    await _send_catalog(update, context)


async def _show_plan_detail(update: Update, plan_id: str) -> None:
    query = update.callback_query
    plan = await get_plan(plan_id)
    if not plan or not plan.is_active:
        await _edit_view(query, "❌ That plan isn't available right now.", None)
        return

    demo_link = build_deep_link("demo", plan.id)
    pay_link = build_deep_link("payment", plan.id)
    buttons = []
    if demo_link:
        buttons.append([InlineKeyboardButton("👀 Watch Free Demo", url=demo_link)])
    if pay_link:
        buttons.append([InlineKeyboardButton(f"💳 Unlock Now — ₹{plan.price_inr}", url=pay_link)])
    buttons.append([InlineKeyboardButton("⬅️ Back to Plans", callback_data="catalog")])

    await _edit_view(query, _plan_detail_text(plan), InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_last_message_time()
    query = update.callback_query
    await safe_answer(query)
    data = query.data or ""
    if data == "catalog":
        plans = await list_active_plans()
        await _edit_view(query, _catalog_text(len(plans)), _catalog_markup(plans))
    elif data.startswith("plan:"):
        await _show_plan_detail(update, data.split(":", 1)[1])


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("VideoVault Sales — use /start to browse plans, watch a demo, or buy.")


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plans", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^(catalog|plan:)"))
