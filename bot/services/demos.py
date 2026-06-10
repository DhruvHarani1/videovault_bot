"""Per-plan demo media (multiple videos/photos per plan)."""
from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy import func
from bot.models import get_db, PlanDemo
import logging

logger = logging.getLogger(__name__)


async def add_plan_demo(plan_id: str, storage_msg_id: Optional[int], file_id: Optional[str], media_type: str = "video") -> PlanDemo:
    async with get_db() as session:
        pos_res = await session.execute(
            select(func.coalesce(func.max(PlanDemo.position), -1)).filter(PlanDemo.plan_id == plan_id)
        )
        next_pos = (pos_res.scalar() or -1) + 1
        demo = PlanDemo(
            plan_id=plan_id,
            storage_msg_id=storage_msg_id,
            file_id=file_id,
            media_type=media_type,
            position=next_pos,
        )
        session.add(demo)
        logger.info(f"Added demo to plan {plan_id} (type={media_type}, pos={next_pos}).")
        return demo


async def list_plan_demos(plan_id: str) -> List[PlanDemo]:
    async with get_db() as session:
        result = await session.execute(
            select(PlanDemo).filter(PlanDemo.plan_id == plan_id).order_by(PlanDemo.position.asc(), PlanDemo.id.asc())
        )
        return list(result.scalars().all())


async def count_plan_demos(plan_id: str) -> int:
    async with get_db() as session:
        result = await session.execute(select(func.count(PlanDemo.id)).filter(PlanDemo.plan_id == plan_id))
        return result.scalar() or 0


async def clear_plan_demos(plan_id: str) -> int:
    async with get_db() as session:
        result = await session.execute(select(PlanDemo).filter(PlanDemo.plan_id == plan_id))
        rows = result.scalars().all()
        for r in rows:
            await session.delete(r)
        logger.info(f"Cleared {len(rows)} demo(s) from plan {plan_id}.")
        return len(rows)
