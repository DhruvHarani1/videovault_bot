"""Content repository CMS: content_items + plan_contents linking."""
from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy import func
from bot.models import get_db, ContentItem, PlanContent
import logging

logger = logging.getLogger(__name__)


async def next_content_id() -> str:
    """Generate the next sequential content id (content_0001, ...)."""
    async with get_db() as session:
        result = await session.execute(select(func.count(ContentItem.id)))
        n = (result.scalar() or 0) + 1
    candidate = f"content_{n:04d}"
    async with get_db() as session:
        while (await session.execute(select(ContentItem.id).filter(ContentItem.id == candidate))).first():
            n += 1
            candidate = f"content_{n:04d}"
    return candidate


async def create_content(
    title: str,
    file_id: str,
    media_type: str = "video",
    category: Optional[str] = None,
    tags: Optional[str] = None,
    content_id: Optional[str] = None,
    storage_msg_id: Optional[int] = None,
) -> ContentItem:
    if not content_id:
        content_id = await next_content_id()
    async with get_db() as session:
        item = ContentItem(
            id=content_id,
            title=title,
            file_id=file_id,
            storage_msg_id=storage_msg_id,
            media_type=media_type,
            category=category,
            tags=tags,
            is_active=True,
        )
        session.add(item)
        logger.info(f"Created content {content_id}: '{title}' ({media_type}, storage_msg={storage_msg_id}).")
        return item


async def list_content(active_only: bool = False, limit: Optional[int] = None) -> List[ContentItem]:
    async with get_db() as session:
        stmt = select(ContentItem).order_by(ContentItem.created_at.asc())
        if active_only:
            stmt = stmt.filter(ContentItem.is_active == True)
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_content(content_id: str) -> Optional[ContentItem]:
    async with get_db() as session:
        result = await session.execute(select(ContentItem).filter(ContentItem.id == content_id))
        return result.scalars().first()


async def set_content_active(content_id: str, active: bool) -> bool:
    async with get_db() as session:
        result = await session.execute(select(ContentItem).filter(ContentItem.id == content_id))
        item = result.scalars().first()
        if not item:
            return False
        item.is_active = active
        return True


async def link_content_to_plan(plan_id: str, content_id: str) -> str:
    """Link a content item to a plan. Returns 'linked', 'exists', or 'not_found'."""
    async with get_db() as session:
        # Validate both exist.
        plan_ok = (await session.execute(select(ContentItem.id).filter(ContentItem.id == content_id))).first()
        if not plan_ok:
            return "not_found"
        existing = await session.execute(
            select(PlanContent).filter(
                PlanContent.plan_id == plan_id,
                PlanContent.content_id == content_id,
            )
        )
        if existing.scalars().first():
            return "exists"
        session.add(PlanContent(plan_id=plan_id, content_id=content_id))
        logger.info(f"Linked content {content_id} to plan {plan_id}.")
        return "linked"


async def unlink_content_from_plan(plan_id: str, content_id: str) -> bool:
    async with get_db() as session:
        result = await session.execute(
            select(PlanContent).filter(
                PlanContent.plan_id == plan_id,
                PlanContent.content_id == content_id,
            )
        )
        row = result.scalars().first()
        if not row:
            return False
        await session.delete(row)
        logger.info(f"Unlinked content {content_id} from plan {plan_id}.")
        return True


async def list_content_for_plan(plan_id: str, active_only: bool = True) -> List[ContentItem]:
    """Return the ContentItems linked to a plan (in link order)."""
    async with get_db() as session:
        stmt = (
            select(ContentItem)
            .join(PlanContent, PlanContent.content_id == ContentItem.id)
            .filter(PlanContent.plan_id == plan_id)
            .order_by(PlanContent.id.asc())
        )
        if active_only:
            stmt = stmt.filter(ContentItem.is_active == True)
        result = await session.execute(stmt)
        return list(result.scalars().all())
