"""CRUD helpers for the multi-video library."""
from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy import func
from bot.models import get_db, Video
import logging

logger = logging.getLogger(__name__)


async def list_active_videos() -> List[Video]:
    """Returns all videos marked active, ordered by creation time."""
    async with get_db() as session:
        result = await session.execute(
            select(Video).filter(Video.is_active == True).order_by(Video.created_at.asc())
        )
        return list(result.scalars().all())


async def list_all_videos() -> List[Video]:
    """Returns every video including hidden ones — admin use only."""
    async with get_db() as session:
        result = await session.execute(select(Video).order_by(Video.created_at.asc()))
        return list(result.scalars().all())


async def get_video(video_id: str) -> Optional[Video]:
    async with get_db() as session:
        result = await session.execute(select(Video).filter(Video.id == video_id))
        return result.scalars().first()


async def next_video_id() -> str:
    """Generates the next sequential video_id (video_001, video_002, ...)."""
    async with get_db() as session:
        result = await session.execute(select(func.count(Video.id)))
        n = (result.scalar() or 0) + 1
    return f"video_{n:03d}"


async def create_video(
    title: str,
    full_file_id: str,
    price_inr: int,
    preview_file_id: Optional[str] = None,
    description: Optional[str] = None,
    video_id: Optional[str] = None,
) -> Video:
    """Creates a new Video row. Auto-assigns video_id if not provided."""
    if not video_id:
        video_id = await next_video_id()
    async with get_db() as session:
        video = Video(
            id=video_id,
            title=title,
            description=description,
            preview_file_id=preview_file_id or full_file_id,
            full_file_id=full_file_id,
            price_inr=price_inr,
            is_active=True,
        )
        session.add(video)
        logger.info(f"Created video {video_id}: '{title}' (₹{price_inr}).")
        return video


async def set_video_active(video_id: str, active: bool) -> bool:
    """Soft-delete (active=False) or restore (active=True). Returns True if found."""
    async with get_db() as session:
        result = await session.execute(select(Video).filter(Video.id == video_id))
        v = result.scalars().first()
        if not v:
            return False
        v.is_active = active
        logger.info(f"Video {video_id} active={active}.")
        return True


async def update_video_price(video_id: str, new_price_inr: int) -> bool:
    async with get_db() as session:
        result = await session.execute(select(Video).filter(Video.id == video_id))
        v = result.scalars().first()
        if not v:
            return False
        v.price_inr = new_price_inr
        return True


async def update_video_files(video_id: str, preview_file_id: Optional[str], full_file_id: Optional[str]) -> bool:
    """Updates the preview/full file_id of an existing video. Pass None to leave unchanged."""
    async with get_db() as session:
        result = await session.execute(select(Video).filter(Video.id == video_id))
        v = result.scalars().first()
        if not v:
            return False
        if preview_file_id is not None:
            v.preview_file_id = preview_file_id
        if full_file_id is not None:
            v.full_file_id = full_file_id
        return True
