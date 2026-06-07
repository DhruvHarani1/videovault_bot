from datetime import datetime
from sqlalchemy.future import select
from bot.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession
import logging

logger = logging.getLogger(__name__)

async def check_user_access(telegram_id: int, db: AsyncSession = None) -> bool:
    """Check if the user has access to full videos."""
    if db is not None:
        result = await db.execute(select(User).filter(User.telegram_id == telegram_id))
        user = result.scalars().first()
        return user.has_full_access if user else False
    else:
        from bot.models import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).filter(User.telegram_id == telegram_id))
            user = result.scalars().first()
            return user.has_full_access if user else False

async def grant_user_access(telegram_id: int, db: AsyncSession = None) -> None:
    """Grant full access to a user after a successful payment."""
    now = datetime.utcnow()
    if db is not None:
        result = await db.execute(select(User).filter(User.telegram_id == telegram_id))
        user = result.scalars().first()
        if user:
            user.has_full_access = True
            user.access_granted_at = now
        else:
            user = User(
                telegram_id=telegram_id,
                has_full_access=True,
                access_granted_at=now
            )
            db.add(user)
    else:
        from bot.models import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).filter(User.telegram_id == telegram_id))
                user = result.scalars().first()
                if user:
                    user.has_full_access = True
                    user.access_granted_at = now
                else:
                    user = User(
                        telegram_id=telegram_id,
                        has_full_access=True,
                        access_granted_at=now
                    )
                    session.add(user)

async def revoke_user_access(telegram_id: int, db: AsyncSession = None) -> None:
    """Revoke full access from a user."""
    if db is not None:
        result = await db.execute(select(User).filter(User.telegram_id == telegram_id))
        user = result.scalars().first()
        if user:
            user.has_full_access = False
            user.access_granted_at = None
    else:
        from bot.models import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).filter(User.telegram_id == telegram_id))
                user = result.scalars().first()
                if user:
                    user.has_full_access = False
                    user.access_granted_at = None
