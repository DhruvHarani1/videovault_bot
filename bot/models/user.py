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


# ──────────────────────────────────────────────────────────────────────────────
# Phase 0 — Plan-based, multi-bot schema (ADDITIVE).
# These tables are created now but are NOT yet read by any runtime handler; later
# phases migrate the user flows onto them. Legacy tables above remain authoritative
# until then, so this phase causes zero behavior change.
# ──────────────────────────────────────────────────────────────────────────────

class Plan(Base):
    """A purchasable plan, e.g. '₹50 → 65 videos'."""
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # 'plan_001'
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, default=0)  # denormalized for display
    preview_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Plan id={self.id} name={self.name} price={self.price_inr}>"


class ContentItem(Base):
    """A single deliverable piece of content stored as a Telegram file_id."""
    __tablename__ = "content_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # 'content_0001'
    title: Mapped[str] = mapped_column(String, nullable=False)
    file_id: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(String, default="video")  # video/photo/document
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # comma-separated
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<ContentItem id={self.id} title={self.title} type={self.media_type}>"


class PlanContent(Base):
    """Many-to-many mapping of plans to the content they include."""
    __tablename__ = "plan_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("plans.id"), nullable=False)
    content_id: Mapped[str] = mapped_column(String, ForeignKey("content_items.id"), nullable=False)

    __table_args__ = (UniqueConstraint("plan_id", "content_id", name="uix_plan_content"),)

    def __repr__(self):
        return f"<PlanContent plan={self.plan_id} content={self.content_id}>"


class PaymentTicket(Base):
    """A manual-proof payment awaiting admin approval (replaces Razorpay orders)."""
    __tablename__ = "payment_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("plans.id"), nullable=False)
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # screenshot
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/approved/rejected
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # admin telegram_id
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<PaymentTicket id={self.id} user={self.telegram_id} plan={self.plan_id} status={self.status}>"


class PlanAccess(Base):
    """Grants a user access to all content in a plan (replaces per-video user_access)."""
    __tablename__ = "plan_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("plans.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("telegram_id", "plan_id", name="uix_user_plan"),)

    def __repr__(self):
        return f"<PlanAccess user={self.telegram_id} plan={self.plan_id}>"


class Delivery(Base):
    """Tracks which content has been delivered to which user (for resume/audit)."""
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_id: Mapped[str] = mapped_column(String, nullable=False)
    plan_id: Mapped[str] = mapped_column(String, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("telegram_id", "content_id", name="uix_user_delivery"),)

    def __repr__(self):
        return f"<Delivery user={self.telegram_id} content={self.content_id}>"


class UserBotState(Base):
    """Records which bots a user has started — required for cross-bot delivery routing,
    since a Telegram bot cannot message a user who hasn't pressed Start on it."""
    __tablename__ = "user_bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_key: Mapped[str] = mapped_column(String, nullable=False)  # sales/demo/payment/file
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("telegram_id", "bot_key", name="uix_user_bot"),)

    def __repr__(self):
        return f"<UserBotState user={self.telegram_id} bot={self.bot_key}>"


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


# Phase 0 product plan catalog (denormalized counts shown to users).
PHASE0_PLANS = [
    {"id": "plan_001", "name": "Starter",  "price_inr": 20,  "video_count": 30},
    {"id": "plan_002", "name": "Standard", "price_inr": 50,  "video_count": 65},
    {"id": "plan_003", "name": "Premium",  "price_inr": 100, "video_count": 150},
]
# A synthetic plan that holds all pre-existing content and preserves access for
# anyone who already paid under the old single-video model.
LEGACY_PLAN_ID = "plan_legacy"


async def backfill_plans_phase0() -> None:
    """Phase 0 backfill — additive and idempotent.

    Seeds the 3 product plans, migrates every existing `videos` row into
    `content_items`, bundles them under a legacy plan, and converts existing
    access (user_access rows + User.has_full_access) into plan_access for the
    legacy plan. Old tables are left untouched, so the live bot is unaffected.
    Safe to run on every boot — each step no-ops if already applied.
    """
    from sqlalchemy.future import select

    async with get_db() as session:
        # 1. Seed the 3 product plans (skip any already present).
        plan_rows = (await session.execute(select(Plan))).scalars().all()
        existing_plan_ids = {p.id for p in plan_rows}

        seeded_plans = 0
        for spec in PHASE0_PLANS:
            if spec["id"] not in existing_plan_ids:
                session.add(Plan(
                    id=spec["id"],
                    name=spec["name"],
                    description=f"{spec['video_count']} videos for ₹{spec['price_inr']}",
                    price_inr=spec["price_inr"],
                    video_count=spec["video_count"],
                    is_active=True,
                ))
                seeded_plans += 1
        if seeded_plans:
            logger.info(f"Phase 0: seeded {seeded_plans} product plan(s).")

        # 2. Migrate existing `videos` rows → `content_items` (preserve file_ids).
        existing_content_ids = {
            c.id for c in (await session.execute(select(ContentItem.id))).scalars().all()
        }
        videos = (await session.execute(select(Video))).scalars().all()
        migrated_content = 0
        legacy_content_ids = []
        for v in videos:
            content_id = f"content_{v.id}"  # e.g. content_video_001 (stable, idempotent)
            legacy_content_ids.append(content_id)
            if content_id not in existing_content_ids:
                session.add(ContentItem(
                    id=content_id,
                    title=v.title,
                    file_id=v.full_file_id,
                    media_type="video",
                    category="legacy",
                    is_active=True,
                ))
                migrated_content += 1
        if migrated_content:
            logger.info(f"Phase 0: migrated {migrated_content} video(s) into content_items.")

        # 3. Create the legacy plan holding all migrated content (if there is any).
        if legacy_content_ids:
            legacy_plan = (await session.execute(
                select(Plan).filter(Plan.id == LEGACY_PLAN_ID)
            )).scalars().first()
            if not legacy_plan:
                session.add(Plan(
                    id=LEGACY_PLAN_ID,
                    name="Founding Access",
                    description="Legacy access to all original content.",
                    price_inr=0,
                    video_count=len(legacy_content_ids),
                    is_active=False,  # hidden from the public catalog
                ))
                logger.info("Phase 0: created legacy plan 'plan_legacy'.")

            # Link content to the legacy plan (skip existing links).
            existing_links = {
                (pc.plan_id, pc.content_id)
                for pc in (await session.execute(
                    select(PlanContent).filter(PlanContent.plan_id == LEGACY_PLAN_ID)
                )).scalars().all()
            }
            linked = 0
            for cid in legacy_content_ids:
                if (LEGACY_PLAN_ID, cid) not in existing_links:
                    session.add(PlanContent(plan_id=LEGACY_PLAN_ID, content_id=cid))
                    linked += 1
            if linked:
                logger.info(f"Phase 0: linked {linked} content item(s) to the legacy plan.")

        # 4. Convert existing access → plan_access for the legacy plan.
        #    Sources: user_access rows (per-video) + User.has_full_access flag.
        granted_users = set()
        ua_rows = (await session.execute(select(UserAccess))).scalars().all()
        for ua in ua_rows:
            granted_users.add(ua.telegram_id)
        flag_users = (await session.execute(
            select(User).filter(User.has_full_access == True)
        )).scalars().all()
        for u in flag_users:
            granted_users.add(u.telegram_id)

        if granted_users and legacy_content_ids:
            existing_access = {
                pa.telegram_id
                for pa in (await session.execute(
                    select(PlanAccess).filter(PlanAccess.plan_id == LEGACY_PLAN_ID)
                )).scalars().all()
            }
            migrated_access = 0
            for tid in granted_users:
                if tid not in existing_access:
                    session.add(PlanAccess(telegram_id=tid, plan_id=LEGACY_PLAN_ID))
                    migrated_access += 1
            if migrated_access:
                logger.info(f"Phase 0: migrated {migrated_access} user(s) into legacy plan_access.")
