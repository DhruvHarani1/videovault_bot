"""Cross-bot media delivery.

The portable way to send media from any bot is copy_message() out of the shared
storage channel (every bot is an admin of it). We fall back to a raw file_id/URL
for legacy items that predate the storage channel (e.g. the sample-video URL).
"""
import logging
from typing import Optional

from bot.config import settings

logger = logging.getLogger(__name__)


async def copy_from_storage(
    bot,
    chat_id: int,
    storage_msg_id: int,
    caption: Optional[str] = None,
    protect: bool = True,
) -> Optional[int]:
    """Copy a message from the storage channel to a user. Returns the new message id."""
    if not settings.STORAGE_CHANNEL_ID or not storage_msg_id:
        return None
    try:
        res = await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=settings.STORAGE_CHANNEL_ID,
            message_id=storage_msg_id,
            caption=caption,
            protect_content=protect,
        )
        return res.message_id
    except Exception as e:
        logger.warning(f"copy_from_storage failed (msg {storage_msg_id}): {e}")
        return None


async def send_by_file_id(
    bot,
    chat_id: int,
    file_id: str,
    media_type: str = "video",
    caption: Optional[str] = None,
    protect: bool = True,
) -> Optional[int]:
    """Send media by raw file_id or URL (works cross-bot only for URLs)."""
    if not file_id:
        return None
    try:
        if media_type == "photo":
            msg = await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, protect_content=protect)
        elif media_type == "document":
            msg = await bot.send_document(chat_id=chat_id, document=file_id, caption=caption, protect_content=protect)
        else:
            msg = await bot.send_video(chat_id=chat_id, video=file_id, caption=caption, protect_content=protect, supports_streaming=True)
        return msg.message_id
    except Exception as e:
        logger.warning(f"send_by_file_id failed: {e}")
        return None


async def deliver_content_item(bot, chat_id: int, item, caption: Optional[str] = None, protect: bool = True) -> Optional[int]:
    """Deliver a ContentItem: prefer the storage channel, fall back to file_id/URL."""
    mid = await copy_from_storage(bot, chat_id, item.storage_msg_id, caption=caption, protect=protect)
    if mid is not None:
        return mid
    return await send_by_file_id(bot, chat_id, item.file_id, media_type=item.media_type, caption=caption, protect=protect)


async def deliver_plan_preview(bot, chat_id: int, plan, caption: Optional[str] = None, protect: bool = True) -> Optional[int]:
    """Deliver a plan's demo preview: prefer the storage channel, fall back to file_id."""
    mid = await copy_from_storage(bot, chat_id, getattr(plan, "preview_msg_id", None), caption=caption, protect=protect)
    if mid is not None:
        return mid
    if getattr(plan, "preview_file_id", None):
        return await send_by_file_id(bot, chat_id, plan.preview_file_id, media_type="video", caption=caption, protect=protect)
    return None


async def store_media_in_channel(bot, from_chat_id: int, message_id: int) -> Optional[int]:
    """Copy a just-uploaded admin message into the storage channel; return its channel message id.

    The calling bot must be an admin of STORAGE_CHANNEL_ID. Returns None if the
    channel isn't configured or the copy fails (caller can fall back to file_id)."""
    if not settings.STORAGE_CHANNEL_ID:
        return None
    try:
        res = await bot.copy_message(
            chat_id=settings.STORAGE_CHANNEL_ID,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
        return res.message_id
    except Exception as e:
        logger.error(f"store_media_in_channel failed: {e}", exc_info=True)
        return None
