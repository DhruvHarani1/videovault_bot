"""Per-video access management.

Backwards-compat note: the old call sites passed only telegram_id. Those still
work and now mean "does the user have access to ANY video" (rarely useful in
the multi-video world). New code should pass video_id explicitly.
"""
from datetime import datetime
from contextlib import asynccontextmanager
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.models.user import User, UserAccess, AsyncSessionLocal
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _maybe_session(db: AsyncSession = None):
    """Yield the passed session if given, else open a fresh one and commit on exit."""
    if db is not None:
        yield db
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_user_access(telegram_id: int, video_id: str = None, db: AsyncSession = None) -> bool:
    """Returns True if the user has access to a specific video. If video_id is None,
    returns True if the user has access to ANY video (legacy behaviour)."""
    async with _maybe_session(db) as session:
        if video_id is not None:
            result = await session.execute(
                select(UserAccess).filter(
                    UserAccess.telegram_id == telegram_id,
                    UserAccess.video_id == video_id,
                )
            )
            return result.scalars().first() is not None

        # Legacy "any access?" check
        result = await session.execute(
            select(UserAccess).filter(UserAccess.telegram_id == telegram_id)
        )
        if result.scalars().first() is not None:
            return True
        # Fall back to the deprecated User.has_full_access flag
        user_res = await session.execute(select(User).filter(User.telegram_id == telegram_id))
        user = user_res.scalars().first()
        return bool(user and user.has_full_access)


async def grant_user_access(telegram_id: int, video_id: str = "video_001", db: AsyncSession = None) -> None:
    """Grants the user access to a specific video. Idempotent."""
    now = datetime.utcnow()
    async with _maybe_session(db) as session:
        # Make sure a User row exists so the FK is satisfied.
        user_res = await session.execute(select(User).filter(User.telegram_id == telegram_id))
        user = user_res.scalars().first()
        if not user:
            user = User(telegram_id=telegram_id, has_full_access=True, access_granted_at=now)
            session.add(user)
        else:
            user.has_full_access = True  # keep legacy flag in sync for stats/listpaid
            user.access_granted_at = now

        existing = await session.execute(
            select(UserAccess).filter(
                UserAccess.telegram_id == telegram_id,
                UserAccess.video_id == video_id,
            )
        )
        if existing.scalars().first() is None:
            session.add(UserAccess(telegram_id=telegram_id, video_id=video_id, granted_at=now))
            logger.info(f"Granted user {telegram_id} access to video {video_id}.")


async def revoke_user_access(telegram_id: int, video_id: str = None, db: AsyncSession = None) -> None:
    """Revokes access. If video_id is None, revokes access to ALL videos."""
    async with _maybe_session(db) as session:
        if video_id is None:
            rows = await session.execute(select(UserAccess).filter(UserAccess.telegram_id == telegram_id))
            for row in rows.scalars().all():
                await session.delete(row)
            user_res = await session.execute(select(User).filter(User.telegram_id == telegram_id))
            user = user_res.scalars().first()
            if user:
                user.has_full_access = False
                user.access_granted_at = None
            logger.info(f"Revoked ALL access from user {telegram_id}.")
            return

        row_res = await session.execute(
            select(UserAccess).filter(
                UserAccess.telegram_id == telegram_id,
                UserAccess.video_id == video_id,
            )
        )
        row = row_res.scalars().first()
        if row:
            await session.delete(row)
            logger.info(f"Revoked access to {video_id} from user {telegram_id}.")

        # Update legacy flag based on whether ANY access remains
        any_left = await session.execute(select(UserAccess).filter(UserAccess.telegram_id == telegram_id))
        if any_left.scalars().first() is None:
            user_res = await session.execute(select(User).filter(User.telegram_id == telegram_id))
            user = user_res.scalars().first()
            if user:
                user.has_full_access = False
                user.access_granted_at = None
