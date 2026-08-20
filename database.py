# ================================================================
# ECHO — database.py
# DATABASE, MODELS, REDIS & PERSISTENT STATE (Data Layer / Foundation)
# ================================================================
#
# نقش این فایل:
#   - Data Layer پروژه ECHO
#   - مدیریت PostgreSQL (Async, SQLAlchemy 2.x, asyncpg)
#   - مدیریت Redis (State موقت، Session، Lock، Cooldown)
#   - تعریف تمام Modelهای Foundation
#
# این فایل:
#   - Gameplay ندارد
#   - Intent Detection ندارد
#   - Telegram Bot / Dispatcher / Router ندارد
#   - UI Button نمی‌سازد
#
# Configuration فقط از config.py گرفته می‌شود.
# ================================================================

from __future__ import annotations

import contextlib
import enum
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    import redis.asyncio as aioredis
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "پکیج redis (redis.asyncio) نصب نیست. با: pip install redis>=4.2.0 نصب کنید."
    ) from exc

from config import settings

logger = logging.getLogger("echo.database")


# ================================================================
# 1. Helpers عمومی
# ================================================================

def utcnow() -> datetime:
    """زمان فعلی UTC با timezone-awareness."""
    return datetime.now(timezone.utc)


def _normalize_postgres_url(raw_url: str) -> str:
    """
    Normalize کردن Database URL برای استفاده Async با asyncpg.

    postgresql://...   -> postgresql+asyncpg://...
    postgres://...      -> postgresql+asyncpg://...
    postgresql+asyncpg://... -> بدون تغییر
    """
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw_url


DATABASE_URL: str = _normalize_postgres_url(settings.database_url)
REDIS_URL: str = settings.redis_url


# ================================================================
# 2. Declarative Base
# ================================================================

class Base(DeclarativeBase):
    """Base مشترک تمام Modelهای ECHO."""
    pass


# ================================================================
# 3. Async Engine & Session
# ================================================================

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@contextlib.asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """
    Context Manager استاندارد برای دریافت Session.

    استفاده:
        async with get_session() as session:
            session.add(obj)
            # commit خودکار در پایان بلوک موفق
    """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextlib.asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """
    Transaction Helper صریح برای عملیات حساس (Wallet, Reward, ...).

    استفاده:
        async with get_session() as session:
            async with transaction(session):
                # عملیات اتمیک
                ...

    از session.begin_nested (SAVEPOINT) استفاده می‌کند تا در صورت وجود
    Transaction بیرونی هم قابل استفاده باشد.
    """
    if session.in_transaction():
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            yield session


async def init_models() -> None:
    """
    ایجاد Jدولها در صورت عدم وجود (فقط برای Development/Bootstrap).
    در Production باید از Migration استفاده شود (بخش Migration Strategy).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def database_health() -> bool:
    """بررسی سلامت اتصال PostgreSQL. هیچ Secretی Log نمی‌شود."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("database_health failed: %s", type(exc).__name__)
        return False


# ================================================================
# 4. Redis Client
# ================================================================

redis_client: aioredis.Redis = aioredis.from_url(
    REDIS_URL,
    decode_responses=True,
    max_connections=20,
)


async def redis_health() -> bool:
    """بررسی سلامت اتصال Redis. هیچ Secretی Log نمی‌شود."""
    try:
        pong = await redis_client.ping()
        return bool(pong)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_health failed: %s", type(exc).__name__)
        return False


# ----------------------------------------------------------------
# 4.1 Redis Key Namespacing
# ----------------------------------------------------------------

class RedisKeys:
    """
    قراردادهای نام‌گذاری کلید Redis برای ECHO.

    Session همیشه بر اساس user_id + city_id است، هرگز فقط user_id.
    """

    @staticmethod
    def session(user_id: int, city_id: int) -> str:
        return f"echo:session:{user_id}:{city_id}"

    @staticmethod
    def intent(user_id: int, city_id: int) -> str:
        return f"echo:intent:{user_id}:{city_id}"

    @staticmethod
    def cooldown(user_id: int, city_id: int, action: str) -> str:
        return f"echo:cooldown:{user_id}:{city_id}:{action}"

    @staticmethod
    def rate_limit(user_id: int, scope: str) -> str:
        return f"echo:ratelimit:{scope}:{user_id}"

    @staticmethod
    def lock(key: str) -> str:
        return f"echo:lock:{key}"

    @staticmethod
    def cache(namespace: str, key: str) -> str:
        return f"echo:cache:{namespace}:{key}"


# ----------------------------------------------------------------
# 4.2 Distributed Locks (جلوگیری از Double Reward / Double Purchase)
# ----------------------------------------------------------------

async def acquire_lock(key: str, ttl_seconds: int = 10) -> Optional[str]:
    """
    تلاش برای گرفتن یک Distributed Lock در Redis.

    Args:
        key: شناسه منطقی Lock (بدون Namespace؛ خودکار Prefix می‌شود).
        ttl_seconds: حداکثر زمان نگهداری Lock (جلوگیری از Deadlock).

    Returns:
        token اگر Lock گرفته شد (باید برای release نگه داشته شود)، یا None
        اگر Lock در حال حاضر توسط کسی دیگر گرفته شده است.
    """
    token = uuid.uuid4().hex
    full_key = RedisKeys.lock(key)
    acquired = await redis_client.set(full_key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None


_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def release_lock(key: str, token: str) -> bool:
    """
    آزادسازی امن Lock — فقط اگر token متعلق به همین Holder باشد
    (جلوگیری از حذف Lock متعلق به Process دیگر).
    """
    full_key = RedisKeys.lock(key)
    result = await redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, full_key, token)
    return bool(result)


@contextlib.asynccontextmanager
async def distributed_lock(key: str, ttl_seconds: int = 10, wait_seconds: float = 5.0,
                            retry_interval: float = 0.1) -> AsyncIterator[bool]:
    """
    Context Manager راحت برای استفاده از Distributed Lock.

    استفاده:
        async with distributed_lock(f"reward:{user_id}:{city_id}") as acquired:
            if not acquired:
                return  # عملیات دیگری در حال انجام است
            # عملیات حساس (Atomic)

    اگر wait_seconds > 0 باشد، تلاش مجدد برای گرفتن Lock تا Timeout انجام می‌شود.
    """
    deadline = time.monotonic() + wait_seconds
    token: Optional[str] = None
    while True:
        token = await acquire_lock(key, ttl_seconds=ttl_seconds)
        if token or time.monotonic() >= deadline:
            break
        import asyncio
        await asyncio.sleep(retry_interval)

    try:
        yield token is not None
    finally:
        if token:
            await release_lock(key, token)


# ----------------------------------------------------------------
# 4.3 Session / Intent Helpers (Redis-backed)
# ----------------------------------------------------------------

import json as _json  # noqa: E402


async def set_game_session(
    user_id: int,
    city_id: int,
    state: str,
    payload: Optional[dict[str, Any]] = None,
    ttl_seconds: int = 900,
) -> None:
    """ذخیره State موقت بازی در Redis برای یک (user_id, city_id) مشخص."""
    key = RedisKeys.session(user_id, city_id)
    data = {
        "state": state,
        "payload": payload or {},
        "updated_at": utcnow().isoformat(),
    }
    await redis_client.set(key, _json.dumps(data), ex=ttl_seconds)


async def get_game_session(user_id: int, city_id: int) -> Optional[dict[str, Any]]:
    """خواندن State موقت بازی از Redis."""
    key = RedisKeys.session(user_id, city_id)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    return _json.loads(raw)


async def clear_game_session(user_id: int, city_id: int) -> None:
    """پاک کردن Session موقت."""
    key = RedisKeys.session(user_id, city_id)
    await redis_client.delete(key)


async def set_intent_context(
    user_id: int,
    city_id: int,
    current_intent: str,
    current_state: str,
    context_payload: Optional[dict[str, Any]] = None,
    ttl_seconds: int = 600,
) -> None:
    """
    ذخیره Intent Context موقت در Redis.
    توجه: database.py هیچ Intent Detection انجام نمی‌دهد؛ این فقط Storage است.
    """
    key = RedisKeys.intent(user_id, city_id)
    data = {
        "current_intent": current_intent,
        "current_state": current_state,
        "context_payload": context_payload or {},
        "expires_at": None,  # کنترل واقعی TTL توسط Redis (ex=) انجام می‌شود
    }
    await redis_client.set(key, _json.dumps(data), ex=ttl_seconds)


async def get_intent_context(user_id: int, city_id: int) -> Optional[dict[str, Any]]:
    key = RedisKeys.intent(user_id, city_id)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    return _json.loads(raw)


async def clear_intent_context(user_id: int, city_id: int) -> None:
    key = RedisKeys.intent(user_id, city_id)
    await redis_client.delete(key)


# ----------------------------------------------------------------
# 4.4 Cooldown Helpers
# ----------------------------------------------------------------

async def set_cooldown(user_id: int, city_id: int, action: str, seconds: int) -> None:
    key = RedisKeys.cooldown(user_id, city_id, action)
    await redis_client.set(key, "1", ex=seconds)


async def is_on_cooldown(user_id: int, city_id: int, action: str) -> bool:
    key = RedisKeys.cooldown(user_id, city_id, action)
    return bool(await redis_client.exists(key))


async def cooldown_ttl(user_id: int, city_id: int, action: str) -> int:
    """ثانیه‌های باقی‌مانده Cooldown. اگر Cooldown فعال نباشد 0 برمی‌گردد."""
    key = RedisKeys.cooldown(user_id, city_id, action)
    ttl = await redis_client.ttl(key)
    return max(ttl, 0)


# ================================================================
# 5. Enums
# ================================================================

class CityMemberRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class GuildMemberRole(str, enum.Enum):
    FOUNDER = "founder"
    OFFICER = "officer"
    MEMBER = "member"


class MissionStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class EventScope(str, enum.Enum):
    GLOBAL = "global"
    CITY = "city"


class ReferralStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


# ================================================================
# 6. Models
# ================================================================

# ----------------------------------------------------------------
# 6.1 User (Global)
# ----------------------------------------------------------------

class User(Base):
    """
    User سراسری (Global).
    یک Telegram User فقط یک Account دارد و می‌تواند در چند City عضو باشد.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    stats: Mapped["UserStats"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    wallets: Mapped[list["UserWallet"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memberships: Mapped[list["CityMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_users_username", "username"),
    )


# ----------------------------------------------------------------
# 6.2 Global User Stats
# ----------------------------------------------------------------

class UserStats(Base):
    """
    آمار سراسری User.

    توجه: اگر در آینده Energy/Reputation کاملاً City-specific شوند،
    این Schema باید تغییر کند (فیلد مربوطه به Model City-aware منتقل شود).
    """
    __tablename__ = "user_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)

    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    xp: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fame: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reputation: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    energy: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    daily_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="stats")


# ----------------------------------------------------------------
# 6.3 Wallet (City-aware — NOT Global)
# ----------------------------------------------------------------

class UserWallet(Base):
    """
    Wallet مستقل به ازای هر (user_id, city_id).

    هرگز Wallet سراسری نساز — هر City کیف پول خودش را دارد.
    مقادیر پول به صورت Integer (کوچک‌ترین واحد پول ECHO) ذخیره می‌شوند.
    """
    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)

    cash: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bank: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="wallets")
    city: Mapped["City"] = relationship(back_populates="wallets")

    __table_args__ = (
        UniqueConstraint("user_id", "city_id", name="uq_wallet_user_city"),
        Index("ix_wallets_user_id", "user_id"),
        Index("ix_wallets_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.4 City
# ----------------------------------------------------------------

class City(Base):
    """
    یک Telegram Group = یک City مستقل.

    City هرگز Hard Delete نمی‌شود:
      - وقتی Bot از Group حذف شود -> is_active = False
      - وقتی Bot دوباره اضافه شود -> همان City Restore می‌شود (is_active = True)
    """
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    city_code: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    custom_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    treasury: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    owner_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    wallets: Mapped[list["UserWallet"]] = relationship(back_populates="city", cascade="all, delete-orphan")
    members: Mapped[list["CityMember"]] = relationship(back_populates="city", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_cities_telegram_chat_id", "telegram_chat_id"),
    )


# ----------------------------------------------------------------
# 6.5 City Membership
# ----------------------------------------------------------------

class CityMember(Base):
    """رابطه User <-> City (عضویت)."""
    __tablename__ = "city_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    role: Mapped[str] = mapped_column(String(16), default=CityMemberRole.MEMBER.value, nullable=False)
    contribution: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reputation: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    city: Mapped["City"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("city_id", "user_id", name="uq_citymember_city_user"),
        Index("ix_citymembers_city_id", "city_id"),
        Index("ix_citymembers_user_id", "user_id"),
    )


# ----------------------------------------------------------------
# 6.6 Game Session (Persistent / Audit — short-lived state lives in Redis)
# ----------------------------------------------------------------

class GameSession(Base):
    """
    نسخه Persistent از Session، برای:
      - Audit
      - Debug
      - Session طولانی‌مدت

    Session کوتاه‌مدت باید در Redis نگهداری شود (رجوع کنید به set_game_session).
    Session هرگز فقط با user_id شناخته نمی‌شود؛ همیشه user_id + city_id.
    """
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)

    state: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_gamesessions_user_id", "user_id"),
        Index("ix_gamesessions_city_id", "city_id"),
        Index("ix_gamesessions_user_city", "user_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.7 Mission Foundation
# ----------------------------------------------------------------

class Mission(Base):
    """
    Mission پایه. city_id nullable است (Mission سراسری در صورت null).
    """
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=True)

    mission_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    reward_cash: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reward_xp: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    energy_cost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_missions_city_id", "city_id"),
    )


class MissionProgress(Base):
    """پیشرفت User در یک Mission، به ازای City مشخص."""
    __tablename__ = "mission_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    mission_id: Mapped[int] = mapped_column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=MissionStatus.IN_PROGRESS.value, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "city_id", "mission_id", name="uq_missionprogress_user_city_mission"),
        Index("ix_missionprogress_user_id", "user_id"),
        Index("ix_missionprogress_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.8 Event Foundation (Living World)
# ----------------------------------------------------------------

class Event(Base):
    """
    Event پایه.
    city_id = null  -> Global Event
    city_id = <id>  -> City-specific Event
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_events_city_id", "city_id"),
        Index("ix_events_ends_at", "ends_at"),
    )


# ----------------------------------------------------------------
# 6.9 Discovery Foundation
# ----------------------------------------------------------------

class Discovery(Base):
    """
    Rare / World Discovery.
    برای جلوگیری از Duplicate Discovery در یک City، Unique روی
    (city_id, discovery_type, name) تعریف شده است.
    """
    __tablename__ = "discoveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    discovered_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    discovery_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rarity: Mapped[str] = mapped_column(String(32), default="common", nullable=False)
    discovery_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("city_id", "discovery_type", "name", name="uq_discovery_city_type_name"),
        Index("ix_discoveries_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.10 Guild Foundation
# ----------------------------------------------------------------

class Guild(Base):
    """Guild پایه (Gameplay کامل بعداً اضافه می‌شود)."""
    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    founder_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    treasury: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    members: Mapped[list["GuildMember"]] = relationship(back_populates="guild", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("city_id", "name", name="uq_guild_city_name"),
        Index("ix_guilds_city_id", "city_id"),
    )


class GuildMember(Base):
    """رابطه Guild <-> User."""
    __tablename__ = "guild_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    role: Mapped[str] = mapped_column(String(16), default=GuildMemberRole.MEMBER.value, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    guild: Mapped["Guild"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_guildmember_guild_user"),
        Index("ix_guildmembers_guild_id", "guild_id"),
        Index("ix_guildmembers_user_id", "user_id"),
    )


# ----------------------------------------------------------------
# 6.11 City History
# ----------------------------------------------------------------

class CityHistory(Base):
    """ثبت اتفاقات مهم City (founded, first_business, event_win, ...)."""
    __tablename__ = "city_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    history_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_cityhistory_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.12 Social / Activity Records (فقط Game Activity، نه پیام‌های معمولی)
# ----------------------------------------------------------------

class ActivityRecord(Base):
    """
    ثبت رویدادهای Game Activity (achievement, discovery, event,
    level_up, guild_win, city_milestone).

    پیام‌های معمولی Telegram هرگز اینجا ذخیره نمی‌شوند.
    """
    __tablename__ = "activity_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    city_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=True)

    activity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # achievement/discovery/event/level_up/guild_win/city_milestone
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    activity_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_activityrecords_user_id", "user_id"),
        Index("ix_activityrecords_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.13 Help Analytics
# ----------------------------------------------------------------

class HelpView(Base):
    """ثبت سبک بازدید از Help (Analytics سنگین بعداً پیاده می‌شود)."""
    __tablename__ = "help_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=True)

    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_helpviews_user_id", "user_id"),
        Index("ix_helpviews_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.14 Referral / Growth Foundation
# ----------------------------------------------------------------

class Referral(Base):
    """
    Foundation برای Referral / Network Effect.
    سیستم Referral کامل در Promptهای بعدی پیاده‌سازی می‌شود.
    """
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    inviter_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    invited_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=ReferralStatus.PENDING.value, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_referrals_city_id", "city_id"),
    )


# ----------------------------------------------------------------
# 6.15 City Growth
# ----------------------------------------------------------------

class CityGrowth(Base):
    """آمار رشد City؛ برای Ranking و Network Effect در آینده استفاده می‌شود."""
    __tablename__ = "city_growth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False, unique=True)

    active_citizens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weekly_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_citizens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_participation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    growth_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_citygrowth_city_id", "city_id"),
    )


# ================================================================
# 7. UI Style Architecture (Rule only — no implementation here)
# ================================================================
#
# Styleهای رسمی Button ECHO:
#
#   PRIMARY
#   SUCCESS
#   DANGER
#
# database.py هیچ Button نمی‌سازد و هیچ Style جدیدی اختراع نمی‌کند.
# پیاده‌سازی Button Factory و این Styleها در handlers.py انجام می‌شود.
#
# این ثابت‌ها فقط برای Reference و جلوگیری از Style پراکنده در آینده
# اینجا اعلام شده‌اند (بدون هیچ منطق UI):

class OfficialButtonStyle(str, enum.Enum):
    PRIMARY = "primary"
    SUCCESS = "success"
    DANGER = "danger"


# ================================================================
# 8. Wallet Transaction Helper (Transaction-safe, Atomic, Money-safe)
# ================================================================

class InsufficientFundsError(Exception):
    """موجودی کافی برای عملیات وجود ندارد."""


async def apply_wallet_delta(
    session: AsyncSession,
    user_id: int,
    city_id: int,
    cash_delta: int = 0,
    bank_delta: int = 0,
    allow_negative_result: bool = False,
) -> UserWallet:
    """
    اعمال Atomic تغییر روی Wallet یک (user_id, city_id).

    - از SELECT ... FOR UPDATE برای Row Lock استفاده می‌کند تا از
      race condition بین چند عملیات همزمان جلوگیری شود.
    - باید همیشه داخل `transaction(session)` صدا زده شود.
    - Float استفاده نمی‌شود؛ فقط Integer (کوچک‌ترین واحد پول ECHO).

    Raises:
        InsufficientFundsError: اگر نتیجه منفی شود و allow_negative_result=False.
    """
    from sqlalchemy import select

    stmt = (
        select(UserWallet)
        .where(UserWallet.user_id == user_id, UserWallet.city_id == city_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    wallet = result.scalar_one_or_none()

    if wallet is None:
        wallet = UserWallet(user_id=user_id, city_id=city_id, cash=0, bank=0)
        session.add(wallet)
        await session.flush()

    new_cash = wallet.cash + cash_delta
    new_bank = wallet.bank + bank_delta

    if not allow_negative_result and (new_cash < 0 or new_bank < 0):
        raise InsufficientFundsError(
            f"Insufficient funds for user_id={user_id} city_id={city_id}"
        )

    wallet.cash = new_cash
    wallet.bank = new_bank
    wallet.updated_at = utcnow()

    await session.flush()
    return wallet


# ================================================================
# 9. City Membership Helper (Idempotent Join)
# ================================================================

async def get_or_create_city_member(
    session: AsyncSession,
    city_id: int,
    user_id: int,
    role: str = CityMemberRole.MEMBER.value,
) -> CityMember:
    """
    Idempotent Join: اگر Membership وجود داشته باشد همان برگردانده می‌شود،
    در غیر این صورت ساخته می‌شود. هرگز Membership تکراری ایجاد نمی‌شود.
    """
    from sqlalchemy import select

    stmt = select(CityMember).where(
        CityMember.city_id == city_id,
        CityMember.user_id == user_id,
    )
    result = await session.execute(stmt)
    member = result.scalar_one_or_none()
    if member is not None:
        member.is_active = True
        member.last_active_at = utcnow()
        await session.flush()
        return member

    member = CityMember(city_id=city_id, user_id=user_id, role=role)
    session.add(member)
    await session.flush()
    return member


# ================================================================
# 10. City Restore Helper (Soft Disable / Restore)
# ================================================================

async def get_or_restore_city(
    session: AsyncSession,
    telegram_chat_id: int,
    name: str,
    username: Optional[str] = None,
) -> City:
    """
    اگر City با این telegram_chat_id از قبل وجود داشته باشد:
      - is_active = True (Restore)
    در غیر این صورت City جدید ساخته می‌شود.

    City هرگز Hard Delete نمی‌شود.
    """
    from sqlalchemy import select

    stmt = select(City).where(City.telegram_chat_id == telegram_chat_id)
    result = await session.execute(stmt)
    city = result.scalar_one_or_none()

    if city is not None:
        city.is_active = True
        city.name = name
        city.username = username
        city.updated_at = utcnow()
        await session.flush()
        return city

    city = City(
        telegram_chat_id=telegram_chat_id,
        name=name,
        username=username,
        is_active=True,
    )
    session.add(city)
    await session.flush()
    return city


async def deactivate_city(session: AsyncSession, telegram_chat_id: int) -> None:
    """Soft Disable یک City (وقتی Bot از Group حذف می‌شود)."""
    from sqlalchemy import select

    stmt = select(City).where(City.telegram_chat_id == telegram_chat_id)
    result = await session.execute(stmt)
    city = result.scalar_one_or_none()
    if city is not None:
        city.is_active = False
        city.updated_at = utcnow()
        await session.flush()


# ================================================================
# 11. Lifecycle Helpers
# ================================================================

async def close_engine() -> None:
    """بستن Connection Pool در زمان Shutdown برنامه."""
    await engine.dispose()


async def close_redis() -> None:
    """بستن اتصال Redis در زمان Shutdown برنامه."""
    await redis_client.aclose()


async def shutdown() -> None:
    """Shutdown کامل Data Layer (PostgreSQL + Redis)."""
    await close_engine()
    await close_redis()


# ================================================================
# 12. مستندات (Docstring سطح ماژول برای رفرنس سریع)
# ================================================================
#
# ------------------------------------------------
# 12.1 Schema منطقی (خلاصه)
# ------------------------------------------------
#
# users (Global)
#   └─ user_stats (1:1, Global)
#   └─ user_wallets (1:N, City-aware — Unique(user_id, city_id))
#   └─ city_members (1:N — Unique(city_id, user_id))
#
# cities
#   └─ user_wallets
#   └─ city_members
#   └─ missions (nullable city_id)
#   └─ events (nullable city_id)
#   └─ discoveries
#   └─ guilds
#   └─ city_history
#   └─ city_growth (1:1)
#   └─ referrals
#
# missions
#   └─ mission_progress (Unique(user_id, city_id, mission_id))
#
# guilds
#   └─ guild_members (Unique(guild_id, user_id))
#
# game_sessions  (Persistent/Audit؛ Live State در Redis است)
# activity_records (فقط Game Activity)
# help_views
#
# ------------------------------------------------
# 12.2 نمونه استفاده — PostgreSQL Session
# ------------------------------------------------
#
#   async def example_add_user():
#       async with get_session() as session:
#           user = User(id=12345, username="sample", nickname="Sample")
#           session.add(user)
#           # commit خودکار در خروج از with موفق انجام می‌شود
#
#   async def example_join_city():
#       async with get_session() as session:
#           async with transaction(session):
#               member = await get_or_create_city_member(session, city_id=10, user_id=12345)
#
#   async def example_reward_user():
#       async with get_session() as session:
#           async with distributed_lock(f"reward:12345:10") as acquired:
#               if not acquired:
#                   return
#               async with transaction(session):
#                   await apply_wallet_delta(session, user_id=12345, city_id=10, cash_delta=500)
#
# ------------------------------------------------
# 12.3 نمونه استفاده — Redis
# ------------------------------------------------
#
#   await set_game_session(12345, 10, state="mission_selection", payload={"mission_id": 7})
#   session_data = await get_game_session(12345, 10)
#   await clear_game_session(12345, 10)
#
#   await set_cooldown(12345, 10, action="daily_claim", seconds=86400)
#   on_cooldown = await is_on_cooldown(12345, 10, action="daily_claim")
#
#   async with distributed_lock("discovery:10:crystal_valley") as acquired:
#       if acquired:
#           ...  # فقط اولین درخواست موفق اجرا می‌شود
#
# ------------------------------------------------
# 12.4 Migration Strategy
# ------------------------------------------------
#
# در این مرحله (Foundation) از:
#
#   init_models()
#
# برای ایجاد جدول‌ها در محیط Development استفاده می‌شود (create_all).
#
# برای Production توصیه می‌شود:
#
#   - استفاده از Alembic برای Migration نسخه‌دار
#   - alembic.ini با sqlalchemy.url از settings.database_url (Normalized)
#   - هر تغییر Schema باید یک Migration جداگانه داشته باشد
#   - هرگز مستقیم روی Production از create_all استفاده نشود
#
# این فایل ساختار Model را طوری طراحی کرده که Promptهای بعدی بتوانند
# بدون Rewrite کامل، فیلد/جدول اضافه کنند (هر Model فقط یک‌بار تعریف
# شده و از الگوی یکنواخت BigInteger/Integer برای پول و شناسه پیروی می‌کند).
#
# ------------------------------------------------
# 12.5 Index و Constraint (خلاصه)
# ------------------------------------------------
#
# Unique:
#   users.username (نه Unique — می‌تواند تکراری/Null باشد، فقط Index)
#   user_wallets (user_id, city_id)
#   cities.telegram_chat_id
#   cities.city_code
#   city_members (city_id, user_id)
#   mission_progress (user_id, city_id, mission_id)
#   guild_members (guild_id, user_id)
#   discoveries (city_id, discovery_type, name)
#   guilds (city_id, name)
#   city_growth.city_id
#
# Index (پرتکرار):
#   users.id / users.username
#   cities.telegram_chat_id
#   city_members.city_id / city_members.user_id
#   user_wallets.user_id / user_wallets.city_id
#   game_sessions.user_id / game_sessions.city_id
#   events.city_id / events.ends_at
#   mission_progress.user_id / mission_progress.city_id
#   referrals.city_id
#
# ================================================================
# پایان database.py
# ================================================================
