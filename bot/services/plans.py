"""Plan CRUD for the plan-based sales model."""
from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy import func
from bot.models import get_db, Plan, PlanContent
import logging

logger = logging.getLogger(__name__)


async def list_active_plans() -> List[Plan]:
    """Public catalog — active plans only, cheapest first."""
    async with get_db() as session:
        result = await session.execute(
            select(Plan).filter(Plan.is_active == True).order_by(Plan.price_inr.asc())
        )
        return list(result.scalars().all())


async def list_all_plans() -> List[Plan]:
    """Admin view — every plan, including hidden/legacy."""
    async with get_db() as session:
        result = await session.execute(select(Plan).order_by(Plan.price_inr.asc()))
        return list(result.scalars().all())


async def get_plan(plan_id: str) -> Optional[Plan]:
    async with get_db() as session:
        result = await session.execute(select(Plan).filter(Plan.id == plan_id))
        return result.scalars().first()


async def next_plan_id() -> str:
    """Generate the next sequential plan id (plan_001, plan_002, ...)."""
    async with get_db() as session:
        # Count only auto-style ids to avoid clashing with 'plan_legacy'.
        result = await session.execute(select(func.count(Plan.id)))
        n = (result.scalar() or 0) + 1
    # Ensure uniqueness even if a gap exists.
    candidate = f"plan_{n:03d}"
    async with get_db() as session:
        while (await session.execute(select(Plan.id).filter(Plan.id == candidate))).first():
            n += 1
            candidate = f"plan_{n:03d}"
    return candidate


async def create_plan(
    name: str,
    price_inr: int,
    video_count: int = 0,
    description: Optional[str] = None,
    preview_file_id: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> Plan:
    if not plan_id:
        plan_id = await next_plan_id()
    async with get_db() as session:
        plan = Plan(
            id=plan_id,
            name=name,
            description=description,
            price_inr=price_inr,
            video_count=video_count,
            preview_file_id=preview_file_id,
            is_active=True,
        )
        session.add(plan)
        logger.info(f"Created plan {plan_id}: '{name}' (₹{price_inr}, {video_count} videos).")
        return plan


async def set_plan_active(plan_id: str, active: bool) -> bool:
    async with get_db() as session:
        result = await session.execute(select(Plan).filter(Plan.id == plan_id))
        plan = result.scalars().first()
        if not plan:
            return False
        plan.is_active = active
        logger.info(f"Plan {plan_id} active={active}.")
        return True


async def update_plan_price(plan_id: str, new_price_inr: int) -> bool:
    async with get_db() as session:
        result = await session.execute(select(Plan).filter(Plan.id == plan_id))
        plan = result.scalars().first()
        if not plan:
            return False
        plan.price_inr = new_price_inr
        return True


async def update_plan_fields(
    plan_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    video_count: Optional[int] = None,
    preview_file_id: Optional[str] = None,
    preview_msg_id: Optional[int] = None,
) -> bool:
    async with get_db() as session:
        result = await session.execute(select(Plan).filter(Plan.id == plan_id))
        plan = result.scalars().first()
        if not plan:
            return False
        if name is not None:
            plan.name = name
        if description is not None:
            plan.description = description
        if video_count is not None:
            plan.video_count = video_count
        if preview_file_id is not None:
            plan.preview_file_id = preview_file_id
        if preview_msg_id is not None:
            plan.preview_msg_id = preview_msg_id
        return True


async def count_linked_content(plan_id: str) -> int:
    async with get_db() as session:
        result = await session.execute(
            select(func.count(PlanContent.id)).filter(PlanContent.plan_id == plan_id)
        )
        return result.scalar() or 0
