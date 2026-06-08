from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from bot.config import settings
import logging

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    # Legacy single-video access flag. Kept for backwards compatibility with admin
    # tooling and stats; per-video access is canonical via UserAccess.
    has_full_access: Mapped[bool] = mapped_column(Boolean, default=False)
    access_granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    reminders_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self):
        return f"<User telegram_id={self.telegram_id} username={self.username} has_full_access={self.has_full_access}>"


class Video(Base):
    """One row per purchasable video in the library."""
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug, e.g. "video_001"
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    preview_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    full_file_id: Mapped[str] = mapped_column(String, nullable=False)
    price_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Video id={self.id} title={self.title} price={self.price_inr}>"


class UserAccess(Base):
    """Grants a specific user access to a specific video."""
    __tablename__ = "user_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("telegram_id", "video_id", name="uix_user_video"),)

    def __repr__(self):
        return f"<UserAccess telegram_id={self.telegram_id} video_id={self.video_id}>"


class PreviewSession(Base):
    __tablename__ = "preview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    video_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    delete_scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self):
        return f"<PreviewSession id={self.id} telegram_id={self.telegram_id} deleted={self.deleted}>"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    razorpay_order_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which video this payment unlocks. Backfilled to "video_001" by migration for legacy rows.
    video_id: Mapped[str] = mapped_column(String, nullable=False, default="video_001")
    status: Mapped[str] = mapped_column(String, default="pending")  # 'pending'/'paid'/'failed'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Payment order_id={self.razorpay_order_id} video_id={self.video_id} status={self.status}>"


class VideoView(Base):
    __tablename__ = "video_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    video_id: Mapped[str] = mapped_column(String, nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<VideoView id={self.id} telegram_id={self.telegram_id} video_id={self.video_id}>"


class BotConfig(Base):
    __tablename__ = "bot_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<BotConfig key={self.key} value={self.value}>"


# Create async engine and sessionmaker
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_db():
    """Async context manager for database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _migrate_payments_add_video_id(conn) -> None:
    """SQLite-safe: add payments.video_id if the column doesn't exist yet.
    Backfills existing rows with 'video_001'."""
    result = await conn.execute(text("PRAGMA table_info(payments)"))
    cols = [row[1] for row in result.fetchall()]
    if "video_id" not in cols:
        logger.info("Migrating: adding payments.video_id column with default 'video_001'.")
        await conn.execute(
            text("ALTER TABLE payments ADD COLUMN video_id VARCHAR NOT NULL DEFAULT 'video_001'")
        )


async def init_db():
    """Initializes the database, creates all tables, and applies in-place migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_payments_add_video_id(conn)


async def seed_legacy_video_if_needed() -> None:
    """If the videos table is empty but the legacy bot_config / settings have a video
    configured, create a 'video_001' row so existing single-video deploys keep working
    after the multi-video refactor. Also backfills UserAccess rows for users who had
    has_full_access=True."""
    from sqlalchemy.future import select

    async with get_db() as session:
        # Already migrated?
        existing = await session.execute(select(Video))
        if existing.scalars().first():
            return

        # Pull legacy values from bot_config (set via /uploadvideo) with env-var fallback.
        cfg_rows = await session.execute(select(BotConfig))
        cfg = {row.key: row.value for row in cfg_rows.scalars().all()}

        full_file_id = cfg.get("FULL_VIDEO_FILE_ID") or settings.FULL_VIDEO_FILE_ID
        preview_file_id = cfg.get("PREVIEW_VIDEO_FILE_ID") or full_file_id
        try:
            price = int(cfg.get("FULL_VIDEO_PRICE_INR", str(settings.FULL_VIDEO_PRICE_INR)))
        except (ValueError, TypeError):
            price = settings.FULL_VIDEO_PRICE_INR

        if not full_file_id:
            logger.info("No legacy video config found; skipping seed. Use /addvideo to add the first video.")
            return

        session.add(Video(
            id="video_001",
            title="Featured Video",
            description=None,
            preview_file_id=preview_file_id,
            full_file_id=full_file_id,
            price_inr=price,
            is_active=True,
        ))
        logger.info("Seeded videos table with legacy 'video_001' row.")

        # Backfill UserAccess from User.has_full_access
        legacy_users = await session.execute(select(User).filter(User.has_full_access == True))
        migrated = 0
        for u in legacy_users.scalars().all():
            session.add(UserAccess(
                telegram_id=u.telegram_id,
                video_id="video_001",
                granted_at=u.access_granted_at or datetime.utcnow(),
            ))
            migrated += 1
        if migrated:
            logger.info(f"Migrated {migrated} legacy paying user(s) into user_access for video_001.")
