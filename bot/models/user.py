from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from bot.config import settings

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    has_full_access: Mapped[bool] = mapped_column(Boolean, default=False)
    access_granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    reminders_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self):
        return f"<User telegram_id={self.telegram_id} username={self.username} has_full_access={self.has_full_access}>"

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
    status: Mapped[str] = mapped_column(String, default="pending")  # 'pending'/'paid'/'failed'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Payment order_id={self.razorpay_order_id} status={self.status}>"

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
AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False
)

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

async def init_db():
    """Initializes the database and creates all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
