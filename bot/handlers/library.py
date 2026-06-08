"""User-facing video library browsing: catalog list + per-video detail screen.

Callback patterns:
    library                 -> show catalog
    video:{video_id}        -> show details for a single video
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from bot.services.videos import list_active_videos, get_video
from bot.services.access import check_user_access
import logging

logger = logging.getLogger(__name__)


def _md2_escape(text: str) -> str:
    """Escape MarkdownV2 special chars in arbitrary user/admin-supplied strings."""
    if text is None:
        return ""
    for ch in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(ch, f"\\{ch}")
    return text


async def show_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists every active video; tapping one opens its detail screen."""
    videos = await list_active_videos()
    query = update.callback_query

    if not videos:
        text = "📭 No videos available yet\\. Please check back soon\\!"
        if query:
            await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    keyboard = []
    for v in videos:
        label = f"🎬 {v.title}  •  ₹{v.price_inr}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"video:{v.id}")])

    text = (
        "📚 *VideoVault Library*\n\n"
        f"Choose a video to preview or purchase \\({len(videos)} available\\):"
    )
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)


async def show_video_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, video_id: str) -> None:
    """Shows the details for one video with preview + buy (or watch) buttons."""
    query = update.callback_query
    user_id = update.effective_user.id

    video = await get_video(video_id)
    if not video or not video.is_active:
        text = "❌ That video isn't available\\."
        if query:
            await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    has_access = await check_user_access(user_id, video_id=video_id)

    title_md = _md2_escape(video.title)
    desc_md = _md2_escape(video.description) if video.description else ""
    body = f"🎬 *{title_md}*\n"
    if desc_md:
        body += f"\n{desc_md}\n"
    body += f"\n💰 Price: ₹{video.price_inr}"

    if has_access:
        body += "\n\n✅ You already own this video\\."
        keyboard = [
            [InlineKeyboardButton("📺 Watch Full Video", callback_data=f"watch_full:{video.id}")],
            [InlineKeyboardButton("📚 Back to Library", callback_data="library")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("▶️ Watch Free Preview", callback_data=f"start_preview:{video.id}")],
            [InlineKeyboardButton(f"💳 Buy Full Access — ₹{video.price_inr}", callback_data=f"buy_access:{video.id}")],
            [InlineKeyboardButton("📚 Back to Library", callback_data="library")],
        ]

    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.message.edit_text(body, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text(body, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/library command — top-level catalog."""
    await show_library(update, context)
