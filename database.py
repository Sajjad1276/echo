# ================================================================
# ECHO — database.py
# DATABASE, MODELS, REDIS & PERSISTENT STATE
# ================================================================
#
# مسئولیت:
#   - PostgreSQL / SQLAlchemy Async
#   - Redis
#   - Database Models
#   - Transactions
#   - Locks
#   - Session Storage
#   - Persistent State
#
# این فایل شامل موارد زیر نیست:
#   - Telegram Handler
#   - Router
#   - UI
#   - Button
#   - Intent Detection
#   - Gameplay Logic
#
# Architecture:
#
#   config.py
#       ↓
#   database.py
#       ↓
#   game.py
#       ↓
#   handlers.py
#       ↓
#   main.py
# ================================================================

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import time
import uuid

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import redis.asyncio as aioredis

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
    func,
    select,
    text,
)

from sqlalchemy.dialects.postgresql import JSONB

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from config import settings


# ================================================================
# LOGGER
# ================================================================

logger = logging.getLogger("echo.database")


# ================================================================
# GENERAL HELPERS
# ================================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_postgres_url(
    raw_url: str,
) -> str:

    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL is missing."
        )

    raw_url = raw_url.strip()

    if raw_url.startswith(
        "postgresql+asyncpg://"
    ):
        return raw_url

    if raw_url.startswith(
        "postgresql://"
    ):
        return raw_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    if raw_url.startswith(
        "postgres://"
    ):
        return raw_url.replace(
            "postgres://",
            "postgresql+asyncpg://",
            1,
        )

    if raw_url.startswith(
        "postgresql+psycopg2://"
    ):
        return raw_url.replace(
            "postgresql+psycopg2://",
            "postgresql+asyncpg://",
            1,
        )

    return raw_url


# ================================================================
# DATABASE URL
# ================================================================

DATABASE_URL = normalize_postgres_url(
    settings.database_url
)

REDIS_URL = settings.redis_url


# ================================================================
# DECLARATIVE BASE
# ================================================================

class Base(DeclarativeBase):
    pass


# ================================================================
# SQLALCHEMY ENGINE
# ================================================================

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ================================================================
# SESSION MANAGEMENT
# ================================================================

@contextlib.asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:

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
async def transaction(
    session: AsyncSession,
) -> AsyncIterator[AsyncSession]:

    if session.in_transaction():

        async with session.begin_nested():

            yield session

    else:

        async with session.begin():

            yield session


# ================================================================
# DATABASE INITIALIZATION
# ================================================================

async def init_database() -> None:

    async with engine.begin() as conn:

        await conn.run_sync(
            Base.metadata.create_all
        )

    if not await database_health():

        raise RuntimeError(
            "PostgreSQL health check failed."
        )

    if not await redis_health():

        raise RuntimeError(
            "Redis health check failed."
        )

    logger.info(
        "Database and Redis initialized successfully."
    )


async def init_models() -> None:
    """
    Backward-compatible alias.
    """

    await init_database()


# ================================================================
# DATABASE HEALTH
# ================================================================

async def database_health() -> bool:

    try:

        async with engine.connect() as conn:

            await conn.execute(
                text("SELECT 1")
            )

        return True

    except Exception as exc:

        logger.warning(
            "database_health failed: %s",
            type(exc).__name__,
        )

        return False


# ================================================================
# REDIS
# ================================================================

redis_client: aioredis.Redis = (
    aioredis.from_url(
        REDIS_URL,
        decode_responses=True,
        max_connections=20,
    )
)


async def redis_health() -> bool:

    try:

        result = await redis_client.ping()

        return bool(result)

    except Exception as exc:

        logger.warning(
            "redis_health failed: %s",
            type(exc).__name__,
        )

        return False


# ================================================================
# REDIS KEY NAMESPACE
# ================================================================

class RedisKeys:

    @staticmethod
    def session(
        user_id: int,
        city_id: int,
    ) -> str:

        return (
            f"echo:session:"
            f"{user_id}:{city_id}"
        )

    @staticmethod
    def intent(
        user_id: int,
        city_id: int,
    ) -> str:

        return (
            f"echo:intent:"
            f"{user_id}:{city_id}"
        )

    @staticmethod
    def cooldown(
        user_id: int,
        city_id: int,
        action: str,
    ) -> str:

        return (
            f"echo:cooldown:"
            f"{user_id}:{city_id}:{action}"
        )

    @staticmethod
    def rate_limit(
        user_id: int,
        scope: str,
    ) -> str:

        return (
            f"echo:ratelimit:"
            f"{scope}:{user_id}"
        )

    @staticmethod
    def lock(
        key: str,
    ) -> str:

        return f"echo:lock:{key}"

    @staticmethod
    def cache(
        namespace: str,
        key: str,
    ) -> str:

        return (
            f"echo:cache:"
            f"{namespace}:{key}"
        )


# ================================================================
# REDIS GAME SESSION
# ================================================================

async def set_game_session(
    user_id: int,
    city_id: int,
    state: str,
    payload: Optional[dict[str, Any]] = None,
    ttl_seconds: int = 900,
) -> None:

    key = RedisKeys.session(
        user_id,
        city_id,
    )

    data = {
        "state": state,
        "payload": payload or {},
        "updated_at": utcnow().isoformat(),
    }

    await redis_client.set(
        key,
        json.dumps(
            data,
            ensure_ascii=False,
        ),
        ex=ttl_seconds,
    )


async def get_game_session(
    user_id: int,
    city_id: int,
) -> Optional[dict[str, Any]]:

    key = RedisKeys.session(
        user_id,
        city_id,
    )

    raw = await redis_client.get(
        key
    )

    if raw is None:
        return None

    return json.loads(raw)


async def clear_game_session(
    user_id: int,
    city_id: int,
) -> None:

    key = RedisKeys.session(
        user_id,
        city_id,
    )

    await redis_client.delete(
        key
    )


# ================================================================
# REDIS INTENT CONTEXT
# ================================================================

async def set_intent_context(
    user_id: int,
    city_id: int,
    current_intent: str,
    current_state: str,
    context_payload: Optional[
        dict[str, Any]
    ] = None,
    ttl_seconds: int = 600,
) -> None:

    key = RedisKeys.intent(
        user_id,
        city_id,
    )

    data = {
        "current_intent": current_intent,
        "current_state": current_state,
        "context_payload": (
            context_payload or {}
        ),
        "updated_at": utcnow().isoformat(),
    }

    await redis_client.set(
        key,
        json.dumps(
            data,
            ensure_ascii=False,
        ),
        ex=ttl_seconds,
    )


async def get_intent_context(
    user_id: int,
    city_id: int,
) -> Optional[dict[str, Any]]:

    key = RedisKeys.intent(
        user_id,
        city_id,
    )

    raw = await redis_client.get(
        key
    )

    if raw is None:
        return None

    return json.loads(raw)


async def clear_intent_context(
    user_id: int,
    city_id: int,
) -> None:

    key = RedisKeys.intent(
        user_id,
        city_id,
    )

    await redis_client.delete(
        key
    )


# ================================================================
# COOLDOWN
# ================================================================

async def set_cooldown(
    user_id: int,
    city_id: int,
    action: str,
    seconds: int,
) -> None:

    key = RedisKeys.cooldown(
        user_id,
        city_id,
        action,
    )

    await redis_client.set(
        key,
        "1",
        ex=seconds,
    )


async def is_on_cooldown(
    user_id: int,
    city_id: int,
    action: str,
) -> bool:

    key = RedisKeys.cooldown(
        user_id,
        city_id,
        action,
    )

    return bool(
        await redis_client.exists(key)
    )


async def cooldown_ttl(
    user_id: int,
    city_id: int,
    action: str,
) -> int:

    key = RedisKeys.cooldown(
        user_id,
        city_id,
        action,
    )

    ttl = await redis_client.ttl(
        key
    )

    return max(ttl, 0)


# ================================================================
# DISTRIBUTED LOCK
# ================================================================

async def acquire_lock(
    key: str,
    ttl_seconds: int = 10,
) -> Optional[str]:

    token = uuid.uuid4().hex

    full_key = RedisKeys.lock(
        key
    )

    acquired = await redis_client.set(
        full_key,
        token,
        nx=True,
        ex=ttl_seconds,
    )

    if acquired:
        return token

    return None


_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def release_lock(
    key: str,
    token: str,
) -> bool:

    full_key = RedisKeys.lock(
        key
    )

    result = await redis_client.eval(
        _RELEASE_LOCK_SCRIPT,
        1,
        full_key,
        token,
    )

    return bool(result)


@contextlib.asynccontextmanager
async def distributed_lock(
    key: str,
    ttl_seconds: int = 10,
    wait_seconds: float = 5.0,
    retry_interval: float = 0.1,
) -> AsyncIterator[bool]:

    deadline = (
        time.monotonic()
        + wait_seconds
    )

    token: Optional[str] = None

    while True:

        token = await acquire_lock(
            key,
            ttl_seconds=ttl_seconds,
        )

        if token:
            break

        if time.monotonic() >= deadline:
            break

        await asyncio.sleep(
            retry_interval
        )

    try:

        yield token is not None

    finally:

        if token:

            await release_lock(
                key,
                token,
            )


# ================================================================
# ENUMS
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
# USER
# ================================================================

class User(Base):

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )

    username: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    nickname: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    last_active_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    stats: Mapped[
        "UserStats"
    ] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    wallets: Mapped[
        list["UserWallet"]
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    memberships: Mapped[
        list["CityMember"]
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_users_username",
            "username",
        ),
    )


# ================================================================
# GLOBAL USER STATS
# ================================================================

class UserStats(Base):

    __tablename__ = "user_stats"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        unique=True,
        nullable=False,
    )

    level: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    xp: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    fame: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    global_reputation: Mapped[
        int
    ] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    daily_streak: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user: Mapped[
        "User"
    ] = relationship(
        back_populates="stats"
    )


# ================================================================
# CITY
# ================================================================

class City(Base):

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
    )

    city_code: Mapped[
        Optional[str]
    ] = mapped_column(
        String(32),
        unique=True,
        nullable=True,
    )

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    username: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    custom_name: Mapped[
        Optional[str]
    ] = mapped_column(
        String(128),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    level: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    treasury: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    owner_user_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    wallets: Mapped[
        list["UserWallet"]
    ] = relationship(
        back_populates="city",
        cascade="all, delete-orphan",
    )

    members: Mapped[
        list["CityMember"]
    ] = relationship(
        back_populates="city",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_cities_telegram_chat_id",
            "telegram_chat_id",
        ),
    )


# ================================================================
# CITY MEMBER
# ================================================================

class CityMember(Base):

    __tablename__ = "city_members"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(16),
        default=CityMemberRole.MEMBER.value,
        nullable=False,
    )

    energy: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )

    city_reputation: Mapped[
        int
    ] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    contribution: Mapped[
        int
    ] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    joined_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    last_active_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    city: Mapped[
        "City"
    ] = relationship(
        back_populates="members"
    )

    user: Mapped[
        "User"
    ] = relationship(
        back_populates="memberships"
    )

    __table_args__ = (
        UniqueConstraint(
            "city_id",
            "user_id",
            name="uq_citymember_city_user",
        ),
        Index(
            "ix_citymembers_city_id",
            "city_id",
        ),
        Index(
            "ix_citymembers_user_id",
            "user_id",
        ),
    )


# ================================================================
# CITY WALLET
# ================================================================

class UserWallet(Base):

    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    cash: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    bank: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user: Mapped[
        "User"
    ] = relationship(
        back_populates="wallets"
    )

    city: Mapped[
        "City"
    ] = relationship(
        back_populates="wallets"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "city_id",
            name="uq_wallet_user_city",
        ),
        Index(
            "ix_wallets_user_id",
            "user_id",
        ),
        Index(
            "ix_wallets_city_id",
            "city_id",
        ),
    )


# ================================================================
# GAME SESSION
# ================================================================

class GameSession(Base):

    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    intent: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    payload: Mapped[
        dict[str, Any]
    ] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    source_message_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        nullable=True,
    )

    expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_gamesessions_user_id",
            "user_id",
        ),
        Index(
            "ix_gamesessions_city_id",
            "city_id",
        ),
        Index(
            "ix_gamesessions_user_city",
            "user_id",
            "city_id",
        ),
    )


# ================================================================
# MISSION
# ================================================================

class Mission(Base):

    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    mission_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    description: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )

    difficulty: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    reward_cash: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    reward_xp: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    energy_cost: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_missions_city_id",
            "city_id",
        ),
    )


class MissionProgress(Base):

    __tablename__ = "mission_progress"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    mission_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "missions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        default=MissionStatus.IN_PROGRESS.value,
        nullable=False,
    )

    started_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    completed_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "city_id",
            "mission_id",
            name="uq_missionprogress_user_city_mission",
        ),
        Index(
            "ix_missionprogress_user_id",
            "user_id",
        ),
        Index(
            "ix_missionprogress_city_id",
            "city_id",
        ),
    )


# ================================================================
# EVENT
# ================================================================

class Event(Base):

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    scope: Mapped[str] = mapped_column(
        String(16),
        default=EventScope.CITY.value,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    description: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )

    state: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
    )

    payload: Mapped[
        dict[str, Any]
    ] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    starts_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ends_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_events_city_id",
            "city_id",
        ),
        Index(
            "ix_events_ends_at",
            "ends_at",
        ),
    )


# ================================================================
# DISCOVERY
# ================================================================

class Discovery(Base):

    __tablename__ = "discoveries"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    discovered_by: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    discovery_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    rarity: Mapped[str] = mapped_column(
        String(32),
        default="common",
        nullable=False,
    )

    discovery_metadata: Mapped[
        dict[str, Any]
    ] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "city_id",
            "discovery_type",
            "name",
            name="uq_discovery_city_type_name",
        ),
        Index(
            "ix_discoveries_city_id",
            "city_id",
        ),
    )


# ================================================================
# GUILD
# ================================================================

class Guild(Base):

    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    founder_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    level: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    treasury: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    members: Mapped[
        list["GuildMember"]
    ] = relationship(
        back_populates="guild",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "city_id",
            "name",
            name="uq_guild_city_name",
        ),
        Index(
            "ix_guilds_city_id",
            "city_id",
        ),
    )


class GuildMember(Base):

    __tablename__ = "guild_members"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    guild_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "guilds.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(16),
        default=GuildMemberRole.MEMBER.value,
        nullable=False,
    )

    joined_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    guild: Mapped[
        "Guild"
    ] = relationship(
        back_populates="members"
    )

    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "user_id",
            name="uq_guildmember_guild_user",
        ),
        Index(
            "ix_guildmembers_guild_id",
            "guild_id",
        ),
        Index(
            "ix_guildmembers_user_id",
            "user_id",
        ),
    )


# ================================================================
# CITY HISTORY
# ================================================================

class CityHistory(Base):

    __tablename__ = "city_history"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    actor_user_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    description: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )

    history_metadata: Mapped[
        dict[str, Any]
    ] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_cityhistory_city_id",
            "city_id",
        ),
    )


# ================================================================
# ACTIVITY RECORD
# ================================================================

class ActivityRecord(Base):

    __tablename__ = "activity_records"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    city_id: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    activity_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    activity_metadata: Mapped[
        dict[str, Any]
    ] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_activityrecords_user_id",
            "user_id",
        ),
        Index(
            "ix_activityrecords_city_id",
            "city_id",
        ),
    )


# ================================================================
# HELP VIEW
# ================================================================

class HelpView(Base):

    __tablename__ = "help_views"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    city_id: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    topic: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    source: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_helpviews_user_id",
            "user_id",
        ),
        Index(
            "ix_helpviews_city_id",
            "city_id",
        ),
    )


# ================================================================
# REFERRAL
# ================================================================

class Referral(Base):

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    inviter_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    invited_user_id: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    source: Mapped[
        Optional[str]
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        default=ReferralStatus.PENDING.value,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    qualified_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_referrals_city_id",
            "city_id",
        ),
    )


# ================================================================
# CITY GROWTH
# ================================================================

class CityGrowth(Base):

    __tablename__ = "city_growth"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="CASCADE",
        ),
        unique=True,
        nullable=False,
    )

    active_citizens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    daily_active: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    weekly_active: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    total_citizens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    event_participation: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    growth_score: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_citygrowth_city_id",
            "city_id",
        ),
    )


# ================================================================
# OFFICIAL UI STYLE CONTRACT
# ================================================================

class OfficialButtonStyle(str, enum.Enum):

    PRIMARY = "primary"

    SUCCESS = "success"

    DANGER = "danger"


# ================================================================
# QUERY HELPERS
# ================================================================

async def get_user(
    session: AsyncSession,
    user_id: int,
) -> Optional[User]:

    result = await session.execute(
        select(User).where(
            User.id == user_id
        )
    )

    return result.scalar_one_or_none()


async def get_user_stats(
    session: AsyncSession,
    user_id: int,
) -> Optional[UserStats]:

    result = await session.execute(
        select(UserStats).where(
            UserStats.user_id == user_id
        )
    )

    return result.scalar_one_or_none()


async def get_city_by_chat(
    session: AsyncSession,
    telegram_chat_id: int,
) -> Optional[City]:

    result = await session.execute(
        select(City).where(
            City.telegram_chat_id
            == telegram_chat_id
        )
    )

    return result.scalar_one_or_none()


async def get_city_member(
    session: AsyncSession,
    city_id: int,
    user_id: int,
) -> Optional[CityMember]:

    result = await session.execute(
        select(CityMember).where(
            CityMember.city_id == city_id,
            CityMember.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def get_wallet(
    session: AsyncSession,
    user_id: int,
    city_id: int,
) -> Optional[UserWallet]:

    result = await session.execute(
        select(UserWallet).where(
            UserWallet.user_id == user_id,
            UserWallet.city_id == city_id,
        )
    )

    return result.scalar_one_or_none()


# ================================================================
# USER CREATION
# ================================================================

async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str] = None,
    nickname: Optional[str] = None,
) -> User:

    user = await get_user(
        session,
        user_id,
    )

    if user is None:

        user = User(
            id=user_id,
            username=username,
            nickname=nickname,
            is_active=True,
        )

        session.add(user)

        await session.flush()

        stats = UserStats(
            user_id=user_id,
            level=1,
            xp=0,
            fame=0,
            global_reputation=0,
            daily_streak=0,
        )

        session.add(stats)

        await session.flush()

        return user

    if username is not None:

        user.username = username

    if nickname:

        user.nickname = nickname[:64]

    user.is_active = True

    user.last_active_at = utcnow()

    await session.flush()

    return user


# ================================================================
# CITY HELPERS
# ================================================================

def generate_city_code() -> str:

    return (
        "EC-"
        + uuid.uuid4()
        .hex[:8]
        .upper()
    )


async def get_or_restore_city(
    session: AsyncSession,
    telegram_chat_id: int,
    name: str,
    username: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> City:
    """
    Create or restore City.

    IMPORTANT:
    Owner User is always created first.
    This prevents ForeignKeyViolationError.
    """

    city = await get_city_by_chat(
        session,
        telegram_chat_id,
    )

    # ------------------------------------------------------------
    # EXISTING CITY
    # ------------------------------------------------------------

    if city is not None:

        city.is_active = True

        if name:
            city.name = name

        city.username = username

        # فقط اگر Owner قبلی وجود نداشته باشد.
        if (
            owner_user_id is not None
            and city.owner_user_id is None
        ):

            await get_or_create_user(
                session=session,
                user_id=owner_user_id,
            )

            city.owner_user_id = (
                owner_user_id
            )

        city.updated_at = utcnow()

        await session.flush()

        # CityGrowth نیز اگر به هر دلیل وجود نداشت ساخته شود.
        growth_result = await session.execute(
            select(CityGrowth).where(
                CityGrowth.city_id == city.id
            )
        )

        growth = (
            growth_result
            .scalar_one_or_none()
        )

        if growth is None:

            growth = CityGrowth(
                city_id=city.id,
                active_citizens=0,
                daily_active=0,
                weekly_active=0,
                total_citizens=0,
                event_participation=0,
                growth_score=0,
            )

            session.add(growth)

            await session.flush()

        return city

    # ------------------------------------------------------------
    # NEW CITY
    # ------------------------------------------------------------

    if owner_user_id is not None:

        await get_or_create_user(
            session=session,
            user_id=owner_user_id,
        )

    city = City(
        telegram_chat_id=telegram_chat_id,
        city_code=generate_city_code(),
        name=name or "ECHO City",
        username=username,
        owner_user_id=owner_user_id,
        is_active=True,
        level=1,
        treasury=0,
    )

    session.add(city)

    await session.flush()

    growth = CityGrowth(
        city_id=city.id,
        active_citizens=0,
        daily_active=0,
        weekly_active=0,
        total_citizens=0,
        event_participation=0,
        growth_score=0,
    )

    session.add(growth)

    await session.flush()

    return city


async def deactivate_city(
    session: AsyncSession,
    telegram_chat_id: int,
) -> None:

    city = await get_city_by_chat(
        session,
        telegram_chat_id,
    )

    if city is None:
        return

    city.is_active = False

    city.updated_at = utcnow()

    await session.flush()


# ================================================================
# CITY MEMBERSHIP
# ================================================================

async def get_or_create_city_member(
    session: AsyncSession,
    city_id: int,
    user_id: int,
    role: str = CityMemberRole.MEMBER.value,
) -> CityMember:
    """
    Idempotent membership.

    Also guarantees:
      User exists
      Wallet exists
    """

    # ------------------------------------------------------------
    # Ensure User
    # ------------------------------------------------------------

    await get_or_create_user(
        session=session,
        user_id=user_id,
    )

    # ------------------------------------------------------------
    # Existing Membership
    # ------------------------------------------------------------

    member = await get_city_member(
        session,
        city_id,
        user_id,
    )

    if member is not None:

        member.is_active = True

        member.last_active_at = utcnow()

        await session.flush()

        wallet = await get_wallet(
            session,
            user_id,
            city_id,
        )

        if wallet is None:

            wallet = UserWallet(
                user_id=user_id,
                city_id=city_id,
                cash=0,
                bank=0,
            )

            session.add(wallet)

            await session.flush()

        return member

    # ------------------------------------------------------------
    # New Membership
    # ------------------------------------------------------------

    member = CityMember(
        city_id=city_id,
        user_id=user_id,
        role=role,
        energy=100,
        city_reputation=0,
        contribution=0,
        is_active=True,
    )

    session.add(member)

    await session.flush()

    wallet = UserWallet(
        user_id=user_id,
        city_id=city_id,
        cash=0,
        bank=0,
    )

    session.add(wallet)

    await session.flush()

    # Update City Growth
    growth_result = await session.execute(
        select(CityGrowth).where(
            CityGrowth.city_id == city_id
        )
    )

    growth = (
        growth_result
        .scalar_one_or_none()
    )

    if growth is not None:

        growth.total_citizens += 1

        growth.active_citizens += 1

        growth.updated_at = utcnow()

        await session.flush()

    return member


# ================================================================
# WALLET
# ================================================================

class InsufficientFundsError(Exception):
    pass


async def apply_wallet_delta(
    session: AsyncSession,
    user_id: int,
    city_id: int,
    cash_delta: int = 0,
    bank_delta: int = 0,
    allow_negative_result: bool = False,
) -> UserWallet:
    """
    Atomic Wallet Update.
    Uses SELECT FOR UPDATE.
    """

    result = await session.execute(
        select(UserWallet)
        .where(
            UserWallet.user_id
            == user_id,
            UserWallet.city_id
            == city_id,
        )
        .with_for_update()
    )

    wallet = (
        result
        .scalar_one_or_none()
    )

    if wallet is None:

        wallet = UserWallet(
            user_id=user_id,
            city_id=city_id,
            cash=0,
            bank=0,
        )

        session.add(wallet)

        await session.flush()

    new_cash = (
        wallet.cash
        + cash_delta
    )

    new_bank = (
        wallet.bank
        + bank_delta
    )

    if (
        not allow_negative_result
        and (
            new_cash < 0
            or new_bank < 0
        )
    ):

        raise InsufficientFundsError(
            "Insufficient ECHO funds."
        )

    wallet.cash = new_cash

    wallet.bank = new_bank

    wallet.updated_at = utcnow()

    await session.flush()

    return wallet


# ================================================================
# CITY POPULATION
# ================================================================

async def city_population(
    session: AsyncSession,
    city_id: int,
) -> int:

    result = await session.execute(
        select(
            func.count(
                CityMember.id
            )
        ).where(
            CityMember.city_id
            == city_id,
            CityMember.is_active.is_(True),
        )
    )

    return int(
        result.scalar_one() or 0
    )


# ================================================================
# CITY RANKING
# ================================================================

async def city_ranking(
    session: AsyncSession,
    city_id: int,
    limit: int = 10,
) -> list[tuple]:

    result = await session.execute(
        select(
            User,
            UserStats,
            UserWallet,
            CityMember,
        )
        .join(
            CityMember,
            CityMember.user_id
            == User.id,
        )
        .join(
            UserStats,
            UserStats.user_id
            == User.id,
        )
        .join(
            UserWallet,
            (
                UserWallet.user_id
                == User.id
            )
            & (
                UserWallet.city_id
                == city_id
            ),
        )
        .where(
            CityMember.city_id
            == city_id,
            CityMember.is_active.is_(True),
        )
        .order_by(
            UserStats.level.desc(),
            UserStats.xp.desc(),
            CityMember.city_reputation.desc(),
        )
        .limit(limit)
    )

    return result.all()


# ================================================================
# LIFECYCLE
# ================================================================

async def close_database() -> None:

    await engine.dispose()


async def close_engine() -> None:

    await close_database()


async def close_redis() -> None:

    await redis_client.aclose()


async def shutdown() -> None:

    await close_redis()

    await close_database()


# ================================================================
# EXPORT CONTRACT
# ================================================================

__all__ = [

    # Core
    "Base",
    "engine",
    "AsyncSessionLocal",
    "get_session",
    "transaction",

    # Initialization
    "init_database",
    "init_models",

    # Lifecycle
    "close_database",
    "close_engine",
    "close_redis",
    "shutdown",

    # Health
    "database_health",
    "redis_health",

    # Redis
    "redis_client",
    "RedisKeys",
    "set_game_session",
    "get_game_session",
    "clear_game_session",
    "set_intent_context",
    "get_intent_context",
    "clear_intent_context",
    "set_cooldown",
    "is_on_cooldown",
    "cooldown_ttl",
    "acquire_lock",
    "release_lock",
    "distributed_lock",

    # Models
    "User",
    "UserStats",
    "City",
    "CityMember",
    "UserWallet",
    "GameSession",
    "Mission",
    "MissionProgress",
    "Event",
    "Discovery",
    "Guild",
    "GuildMember",
    "CityHistory",
    "ActivityRecord",
    "HelpView",
    "Referral",
    "CityGrowth",

    # Enums
    "CityMemberRole",
    "GuildMemberRole",
    "MissionStatus",
    "EventScope",
    "ReferralStatus",
    "OfficialButtonStyle",

    # User
    "get_user",
    "get_user_stats",
    "get_or_create_user",

    # City
    "get_city_by_chat",
    "get_or_restore_city",
    "deactivate_city",
    "city_population",
    "city_ranking",

    # Membership
    "get_city_member",
    "get_or_create_city_member",

    # Wallet
    "get_wallet",
    "apply_wallet_delta",
    "InsufficientFundsError",
]
