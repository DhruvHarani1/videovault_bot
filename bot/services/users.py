"""Admin user-management queries: paid/free/suspended lists, suspension, overview."""
from typing import Optional, List, Dict, Any
from sqlalchemy.future import select
from sqlalchemy import func
from bot.models import get_db, User, PlanAccess, PaymentTicket
import logging

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


async def _plans_by_user() -> Dict[int, List[str]]:
    """Map telegram_id -> [plan_id, ...] for everyone with plan access."""
    async with get_db() as session:
        rows = await session.execute(select(PlanAccess.telegram_id, PlanAccess.plan_id))
        mapping: Dict[int, List[str]] = {}
        for tid, pid in rows.all():
            mapping.setdefault(tid, []).append(pid)
        return mapping


async def list_paid_users(page: int = 1) -> Dict[str, Any]:
    """Users with at least one plan. Returns {items:[(User,[plans])], total, page, pages}."""
    plans_map = await _plans_by_user()
    paid_ids = list(plans_map.keys())
    total = len(paid_ids)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    page_ids = paid_ids[start:start + PAGE_SIZE]

    items = []
    if page_ids:
        async with get_db() as session:
            res = await session.execute(select(User).filter(User.telegram_id.in_(page_ids)))
            users = {u.telegram_id: u for u in res.scalars().all()}
        for tid in page_ids:
            u = users.get(tid)
            if u:
                items.append((u, plans_map.get(tid, [])))
    return {"items": items, "total": total, "page": page, "pages": pages}


async def list_free_users(page: int = 1) -> Dict[str, Any]:
    """Users with NO plan access and not suspended."""
    plans_map = await _plans_by_user()
    paid_ids = set(plans_map.keys())
    async with get_db() as session:
        res = await session.execute(select(User).filter(User.suspended == False).order_by(User.joined_at.desc()))
        all_users = res.scalars().all()
    free = [u for u in all_users if u.telegram_id not in paid_ids]
    total = len(free)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    return {"items": free[start:start + PAGE_SIZE], "total": total, "page": page, "pages": pages}


async def list_suspended_users(page: int = 1) -> Dict[str, Any]:
    async with get_db() as session:
        res = await session.execute(select(User).filter(User.suspended == True).order_by(User.joined_at.desc()))
        users = res.scalars().all()
    total = len(users)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    return {"items": users[start:start + PAGE_SIZE], "total": total, "page": page, "pages": pages}


async def set_suspended(telegram_id: int, suspended: bool) -> bool:
    async with get_db() as session:
        res = await session.execute(select(User).filter(User.telegram_id == telegram_id))
        user = res.scalars().first()
        if not user:
            return False
        user.suspended = suspended
        logger.info(f"User {telegram_id} suspended={suspended}.")
        return True


async def is_suspended(telegram_id: int) -> bool:
    async with get_db() as session:
        res = await session.execute(select(User.suspended).filter(User.telegram_id == telegram_id))
        row = res.first()
        return bool(row[0]) if row else False


async def find_user(query: str) -> Optional[User]:
    """Look up by telegram_id (digits) or @username."""
    async with get_db() as session:
        if query.lstrip("-").isdigit():
            res = await session.execute(select(User).filter(User.telegram_id == int(query)))
        else:
            uname = query.lstrip("@").lower()
            res = await session.execute(select(User).filter(func.lower(User.username) == uname))
        return res.scalars().first()


async def user_overview(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Full admin overview of one user: profile, plans, suspension, ticket counts."""
    async with get_db() as session:
        ures = await session.execute(select(User).filter(User.telegram_id == telegram_id))
        user = ures.scalars().first()
        if not user:
            return None
        pres = await session.execute(select(PlanAccess.plan_id).filter(PlanAccess.telegram_id == telegram_id))
        plans = list(pres.scalars().all())
        tres = await session.execute(
            select(PaymentTicket.status, func.count(PaymentTicket.id))
            .filter(PaymentTicket.telegram_id == telegram_id)
            .group_by(PaymentTicket.status)
        )
        ticket_counts = {status: cnt for status, cnt in tres.all()}
    return {"user": user, "plans": plans, "ticket_counts": ticket_counts}
