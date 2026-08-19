"""
╔══════════════════════════════════════════════════════════════════╗
║                         ECHO CITY                                ║
║         Multiplayer Text-Based Social World — Telegram Bot       ║
║                        main.py (MVP)                             ║
╚══════════════════════════════════════════════════════════════════╝

Dependencies (requirements.txt):
    aiogram==3.30.0
    SQLAlchemy[asyncio]==2.0.36
    asyncpg==0.30.0
    alembic==1.14.0
    redis[asyncio]==5.2.0
    pydantic-settings==2.7.0
    python-dotenv==1.0.1
    aiohttp==3.11.0
    apscheduler==3.10.4

Environment (.env):
    BOT_TOKEN=your_token_here
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/echo
    REDIS_URL=redis://localhost:6379/0
    ADMIN_IDS=123456789,987654321
    LOG_LEVEL=INFO
    ENVIRONMENT=development
"""

# ─────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, AsyncGenerator, Optional

from dotenv import load_dotenv

load_dotenv()

# ── Aiogram ──────────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ── SQLAlchemy ────────────────────────────────────────────────────
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    update,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship

# ── Redis ─────────────────────────────────────────────────────────
from redis.asyncio import Redis

# ── APScheduler ───────────────────────────────────────────────────
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://echo:echo@localhost:5432/echo")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ADMIN_IDS: list[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")


    # ── Game Balance ─────────────────────────────────────────────
    MAX_ENERGY: int = 100
    ENERGY_REGEN_RATE: int = 5          # per interval
    ENERGY_REGEN_INTERVAL: int = 600    # seconds (10 min)
    BASE_EXPLORE_COST_ENERGY: int = 20
    BASE_EXPLORE_COST_CASH: int = 5_000
    DAILY_LOGIN_REWARD_CASH: int = 1_000
    DAILY_LOGIN_REWARD_XP: int = 500
    STARTING_CASH: int = 10_000
    STARTING_ENERGY: int = 100
    XP_PER_MISSION: int = 1_000
    XP_PER_TRADE: int = 200
    XP_PER_EXPLORE: int = 500
    BUSINESS_TAX_RATE: float = 0.05
    MARKET_VOLATILITY: float = 0.08
    MARKET_UPDATE_INTERVAL: int = 300   # seconds
    BUSINESS_INCOME_INTERVAL: int = 3600  # seconds
    RATE_LIMIT_CALLS: int = 20
    RATE_LIMIT_WINDOW: int = 60

cfg = Config()

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("echo")

# ─────────────────────────────────────────────────────────────────
# DATABASE — MODELS
# ─────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)          # telegram user_id
    username = Column(String(64), nullable=True)
    nickname = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_banned = Column(Boolean, default=False)
    current_region = Column(String(32), default="downtown")
    archetype = Column(String(32), default="newcomer")  # dynamic role
    title = Column(String(32), default="Newcomer")

    wallet = relationship("Wallet", back_populates="user", uselist=False, lazy="selectin")
    stats = relationship("UserStats", back_populates="user", uselist=False, lazy="selectin")
    guild_member = relationship("GuildMember", back_populates="user", uselist=False, lazy="selectin")

    __table_args__ = (
        Index("ix_users_username", "username"),
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)
    cash = Column(BigInteger, default=0)
    bank = Column(BigInteger, default=0)
    energy = Column(Integer, default=cfg.MAX_ENERGY)
    energy_updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="wallet")


class UserStats(Base):
    __tablename__ = "user_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)
    level = Column(Integer, default=1)
    xp = Column(BigInteger, default=0)
    reputation = Column(Integer, default=0)
    fame = Column(Integer, default=0)
    daily_streak = Column(Integer, default=0)
    last_daily = Column(DateTime(timezone=True), nullable=True)
    missions_completed = Column(Integer, default=0)
    trades_completed = Column(Integer, default=0)
    explorations_done = Column(Integer, default=0)
    discoveries_count = Column(Integer, default=0)
    total_earned = Column(BigInteger, default=0)
    total_spent = Column(BigInteger, default=0)
    # archetype weights
    trader_score = Column(Integer, default=0)
    explorer_score = Column(Integer, default=0)
    leader_score = Column(Integer, default=0)

    user = relationship("User", back_populates="stats")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    type = Column(String(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    currency = Column(String(16), default="cash")
    source = Column(String(128), nullable=True)
    reference_id = Column(String(64), nullable=True)
    balance_before = Column(BigInteger, nullable=False)
    balance_after = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_transactions_user_id", "user_id"),
        Index("ix_transactions_created_at", "created_at"),
    )


class MarketItem(Base):
    __tablename__ = "market_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    price = Column(BigInteger, nullable=False)
    base_price = Column(BigInteger, nullable=False)
    change_pct = Column(Float, default=0.0)
    supply = Column(Integer, default=1000)
    demand = Column(Integer, default=1000)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    symbol = Column(String(32), nullable=False)
    quantity = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_inventory_user_symbol"),
        Index("ix_inventory_user_id", "user_id"),
    )


class Mission(Base):
    __tablename__ = "missions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(32), nullable=False)       # daily/weekly/story/secret
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    reward_cash = Column(BigInteger, default=0)
    reward_xp = Column(Integer, default=0)
    reward_item = Column(String(32), nullable=True)
    condition_type = Column(String(64), nullable=False)  # explore/trade/reputation/etc.
    condition_value = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)


class UserMission(Base):
    __tablename__ = "user_missions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    progress = Column(Integer, default=0)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    assigned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "mission_id", name="uq_user_mission"),
    )


class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    name = Column(String(64), nullable=False)
    type = Column(String(32), nullable=False)       # cafe/factory/warehouse/store
    level = Column(Integer, default=1)
    region = Column(String(32), default="downtown")
    income_per_hour = Column(BigInteger, default=0)
    visitors = Column(Integer, default=0)
    last_collected = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)

    __table_args__ = (Index("ix_businesses_owner_id", "owner_id"),)


class Guild(Base):
    __tablename__ = "guilds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    tag = Column(String(8), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    level = Column(Integer, default=1)
    power = Column(BigInteger, default=0)
    treasury_cash = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)

    members = relationship("GuildMember", back_populates="guild", lazy="selectin")


class GuildMember(Base):
    __tablename__ = "guild_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(Integer, ForeignKey("guilds.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)
    role = Column(String(16), default="member")    # owner/leader/officer/member/recruit
    contribution = Column(BigInteger, default=0)
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    guild = relationship("Guild", back_populates="members")
    user = relationship("User", back_populates="guild_member")


class WorldEvent(Base):
    __tablename__ = "world_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False)
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(16), default="pending")  # pending/active/ended
    started_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    effects = Column(Text, nullable=True)           # JSON string
    participants = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Discovery(Base):
    __tablename__ = "discoveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    discoverer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    discovered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    rarity = Column(String(16), default="rare")
    description = Column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(BigInteger, nullable=False)
    action = Column(String(64), nullable=False)
    target_id = Column(BigInteger, nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────
# DATABASE — SESSION
# ─────────────────────────────────────────────────────────────────

engine = create_async_engine(
    cfg.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables created/verified.")
    await seed_market()
    await seed_missions()


# ─────────────────────────────────────────────────────────────────
# REDIS
# ─────────────────────────────────────────────────────────────────

redis: Redis = Redis.from_url(cfg.REDIS_URL, decode_responses=True)


async def rate_limit_check(user_id: int) -> bool:
    """True = allowed, False = rate limited."""
    key = f"rl:{user_id}"
    calls = await redis.incr(key)
    if calls == 1:
        await redis.expire(key, cfg.RATE_LIMIT_WINDOW)
    return calls <= cfg.RATE_LIMIT_CALLS


async def set_cooldown(key: str, seconds: int) -> None:
    await redis.setex(f"cd:{key}", seconds, "1")


async def check_cooldown(key: str) -> bool:
    """True = still on cooldown."""
    return bool(await redis.exists(f"cd:{key}"))


async def get_cooldown_ttl(key: str) -> int:
    return await redis.ttl(f"cd:{key}")


async def cache_set(key: str, value: str, ttl: int = 300) -> None:
    await redis.setex(f"cache:{key}", ttl, value)


async def cache_get(key: str) -> Optional[str]:
    return await redis.get(f"cache:{key}")


async def cache_del(key: str) -> None:
    await redis.delete(f"cache:{key}")


# ─────────────────────────────────────────────────────────────────
# HELPERS — LEVEL / XP
# ─────────────────────────────────────────────────────────────────

def xp_for_level(level: int) -> int:
    return int(1000 * (level ** 1.8))


def level_from_xp(xp: int) -> int:
    lvl = 1
    while xp_for_level(lvl + 1) <= xp:
        lvl += 1
    return lvl


def archetype_from_scores(trader: int, explorer: int, leader: int) -> str:
    scores = {"Trader": trader, "Explorer": explorer, "Leader": leader}
    top = max(scores, key=scores.get)
    total = trader + explorer + leader
    if total < 10:
        return "Newcomer"
    return top


# ─────────────────────────────────────────────────────────────────
# SEED DATA
# ─────────────────────────────────────────────────────────────────

MARKET_SEED = [
    ("GOLD",    "Gold",          8_000,  "💛"),
    ("ENERGY",  "Energy Cell",   400,    "⚡"),
    ("CRYSTAL", "Crystal",       18_000, "💎"),
    ("FUEL",    "Fuel",          1_200,  "🛢"),
    ("METAL",   "Metal",         3_500,  "⚙️"),
    ("WOOD",    "Wood",          800,    "🪵"),
    ("FOOD",    "Food",          600,    "🍖"),
    ("WATER",   "Water",         300,    "💧"),
]

MISSION_SEED = [
    ("daily",  "Market Starter",     "Complete 1 trade on the market.",           2_000,  500,  "trade",      1),
    ("daily",  "Explorer",           "Explore any region once.",                   1_500,  400,  "explore",    1),
    ("daily",  "Business Day",       "Collect income from a business.",            1_000,  300,  "collect",    1),
    ("weekly", "Market Master",      "Complete 10 trades this week.",             20_000, 5_000, "trade",     10),
    ("story",  "First Steps",        "Reach Level 5.",                            10_000, 3_000, "level",      5),
    ("story",  "Into the Unknown",   "Explore the Unknown Zone.",                 50_000, 10_000,"explore_unk",1),
]

REGIONS = {
    "downtown":     {"name": "Downtown",      "emoji": "🌆", "travel_cost": 0,     "danger": 0.05},
    "market":       {"name": "Market District","emoji": "🏪", "travel_cost": 1_000, "danger": 0.03},
    "industrial":   {"name": "Industrial Zone","emoji": "🏭", "travel_cost": 2_000, "danger": 0.08},
    "harbor":       {"name": "Harbor",         "emoji": "⚓", "travel_cost": 3_000, "danger": 0.10},
    "forest":       {"name": "Forest",         "emoji": "🌲", "travel_cost": 4_000, "danger": 0.12},
    "desert":       {"name": "Desert",         "emoji": "🏜",  "travel_cost": 6_000, "danger": 0.18},
    "mountain":     {"name": "Mountain",       "emoji": "⛰",  "travel_cost": 8_000, "danger": 0.22},
    "unknown":      {"name": "Unknown Zone",   "emoji": "❓", "travel_cost": 15_000,"danger": 0.35},
}

EXPLORE_OUTCOMES = [
    ("cash",      0.35, "$2,000 – $15,000"),
    ("resource",  0.25, "Random resource x5-20"),
    ("rare_item", 0.10, "Rare item discovered!"),
    ("discovery", 0.05, "🌟 World First Discovery!"),
    ("nothing",   0.15, "Nothing found."),
    ("danger",    0.10, "⚠️ Danger! Lost some cash."),
]

BUSINESS_TYPES = {
    "cafe":      {"name": "Cafe",              "emoji": "☕", "cost": 50_000,   "income": 5_000,  "energy": 10},
    "factory":   {"name": "Factory",           "emoji": "🏭", "cost": 200_000,  "income": 20_000, "energy": 20},
    "warehouse": {"name": "Warehouse",         "emoji": "🏗",  "cost": 100_000,  "income": 10_000, "energy": 5},
    "store":     {"name": "Store",             "emoji": "🏪", "cost": 75_000,   "income": 8_000,  "energy": 8},
    "workshop":  {"name": "Workshop",          "emoji": "🔧", "cost": 120_000,  "income": 12_000, "energy": 12},
}

EVENT_TEMPLATES = {
    "meteor":       {"title": "☄️ METEOR STRIKE", "desc": "A meteor has struck ECHO CITY! New resources are exposed.", "duration": 1800, "effects": {"market_boost": "CRYSTAL", "new_region": True}},
    "market_crash": {"title": "📉 MARKET CRASH",  "desc": "Markets are in freefall! Buy low or sell now?",            "duration": 3600, "effects": {"price_drop": 0.4}},
    "blackout":     {"title": "⚡ CITY BLACKOUT",  "desc": "Power is out across multiple districts!",                 "duration": 2700, "effects": {"energy_bonus": True}},
    "festival":     {"title": "🎉 ECHO FESTIVAL",  "desc": "The city celebrates! Bonus XP and cash for all activity.", "duration": 7200, "effects": {"xp_multiplier": 2.0, "cash_multiplier": 1.5}},
}


async def seed_market() -> None:
    async with get_session() as session:
        for symbol, name, price, _ in MARKET_SEED:
            existing = await session.execute(select(MarketItem).where(MarketItem.symbol == symbol))
            if not existing.scalar_one_or_none():
                session.add(MarketItem(symbol=symbol, name=name, price=price, base_price=price))
    log.info("Market seeded.")


async def seed_missions() -> None:
    async with get_session() as session:
        for mtype, title, desc, cash, xp, cond, cval in MISSION_SEED:
            existing = await session.execute(select(Mission).where(Mission.title == title))
            if not existing.scalar_one_or_none():
                session.add(Mission(
                    type=mtype, title=title, description=desc,
                    reward_cash=cash, reward_xp=xp,
                    condition_type=cond, condition_value=cval,
                ))
    log.info("Missions seeded.")


# ─────────────────────────────────────────────────────────────────
# FSM STATES
# ─────────────────────────────────────────────────────────────────

class RegisterState(StatesGroup):
    waiting_nickname = State()

class GuildCreateState(StatesGroup):
    waiting_name = State()
    waiting_tag = State()
    waiting_desc = State()

class TradeState(StatesGroup):
    waiting_symbol = State()
    waiting_quantity = State()
    waiting_confirm = State()

class AdminState(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()
    waiting_event_type = State()
    waiting_broadcast = State()


# ─────────────────────────────────────────────────────────────────
# KEYBOARD BUILDERS
# ─────────────────────────────────────────────────────────────────

def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Quick inline keyboard builder. Each row is a list of (text, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return kb(
        [("🌍 World", "world"), ("👤 Profile", "profile")],
        [("💰 Wallet", "wallet"), ("📈 Market", "market")],
        [("🏢 Business", "business"), ("🗺 Explore", "explore")],
        [("🎯 Missions", "missions"), ("👥 Guild", "guild")],
        [("🏆 Ranking", "ranking"), ("🎁 Daily", "daily")],
    )


def back_kb(target: str = "menu") -> InlineKeyboardMarkup:
    return kb([("◀️ Back", target)])


# ─────────────────────────────────────────────────────────────────
# SERVICES — USER
# ─────────────────────────────────────────────────────────────────

async def get_or_none(session: AsyncSession, model, **kwargs):
    result = await session.execute(select(model).filter_by(**kwargs))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, tg_id: int, username: Optional[str], nickname: str) -> User:
    user = User(id=tg_id, username=username, nickname=nickname)
    session.add(user)
    await session.flush()

    wallet = Wallet(user_id=tg_id, cash=cfg.STARTING_CASH, energy=cfg.STARTING_ENERGY)
    session.add(wallet)

    stats = UserStats(user_id=tg_id)
    session.add(stats)

    # Record initial transaction
    tx = Transaction(
        user_id=tg_id, type="GENESIS", amount=cfg.STARTING_CASH, currency="cash",
        source="system", balance_before=0, balance_after=cfg.STARTING_CASH,
    )
    session.add(tx)

    # Assign daily missions
    daily = await session.execute(select(Mission).where(Mission.type == "daily", Mission.is_active == True))
    for m in daily.scalars().all():
        session.add(UserMission(user_id=tg_id, mission_id=m.id))

    log.info(f"New user created: {tg_id} ({nickname})")
    return user


async def touch_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(last_active=datetime.now(timezone.utc))
    )


# ─────────────────────────────────────────────────────────────────
# SERVICES — ENERGY
# ─────────────────────────────────────────────────────────────────

async def get_energy(session: AsyncSession, user_id: int) -> int:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    if not wallet:
        return 0
    now = datetime.now(timezone.utc)
    elapsed = (now - wallet.energy_updated_at.replace(tzinfo=timezone.utc)).total_seconds()
    regen = int(elapsed // cfg.ENERGY_REGEN_INTERVAL) * cfg.ENERGY_REGEN_RATE
    new_energy = min(cfg.MAX_ENERGY, wallet.energy + regen)
    if regen > 0:
        wallet.energy = new_energy
        wallet.energy_updated_at = now
    return new_energy


async def spend_energy(session: AsyncSession, user_id: int, amount: int) -> bool:
    current = await get_energy(session, user_id)
    if current < amount:
        return False
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    wallet.energy = current - amount
    wallet.energy_updated_at = datetime.now(timezone.utc)
    return True


# ─────────────────────────────────────────────────────────────────
# SERVICES — ECONOMY
# ─────────────────────────────────────────────────────────────────

async def _ledger(
    session: AsyncSession, user_id: int, tx_type: str,
    amount: int, source: str, ref_id: Optional[str] = None,
) -> Transaction:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    before = wallet.cash
    wallet.cash += amount
    if wallet.cash < 0:
        raise ValueError("Insufficient funds")
    tx = Transaction(
        user_id=user_id, type=tx_type, amount=amount,
        currency="cash", source=source, reference_id=ref_id,
        balance_before=before, balance_after=wallet.cash,
    )
    session.add(tx)

    stats = await get_or_none(session, UserStats, user_id=user_id)
    if stats:
        if amount > 0:
            stats.total_earned += amount
        else:
            stats.total_spent += abs(amount)
    return tx


async def add_cash(session: AsyncSession, user_id: int, amount: int, source: str) -> None:
    await _ledger(session, user_id, "CREDIT", amount, source)


async def deduct_cash(session: AsyncSession, user_id: int, amount: int, source: str) -> None:
    await _ledger(session, user_id, "DEBIT", -amount, source)


async def add_xp(session: AsyncSession, user_id: int, amount: int) -> tuple[bool, int]:
    """Returns (leveled_up, new_level)."""
    stats = await get_or_none(session, UserStats, user_id=user_id)
    old_level = stats.level
    stats.xp += amount
    new_level = level_from_xp(stats.xp)
    stats.level = new_level
    leveled_up = new_level > old_level
    if leveled_up:
        stats.fame += (new_level - old_level) * 10
    return leveled_up, new_level


async def add_to_inventory(session: AsyncSession, user_id: int, symbol: str, qty: int) -> None:
    inv = await get_or_none(session, Inventory, user_id=user_id, symbol=symbol)
    if inv:
        inv.quantity += qty
    else:
        session.add(Inventory(user_id=user_id, symbol=symbol, quantity=qty))


async def deduct_from_inventory(session: AsyncSession, user_id: int, symbol: str, qty: int) -> bool:
    inv = await get_or_none(session, Inventory, user_id=user_id, symbol=symbol)
    if not inv or inv.quantity < qty:
        return False
    inv.quantity -= qty
    return True


# ─────────────────────────────────────────────────────────────────
# SERVICES — MARKET
# ─────────────────────────────────────────────────────────────────

async def update_market_prices() -> None:
    async with get_session() as session:
        result = await session.execute(select(MarketItem))
        items = result.scalars().all()
        for item in items:
            change = random.uniform(-cfg.MARKET_VOLATILITY, cfg.MARKET_VOLATILITY)
            new_price = max(100, int(item.price * (1 + change)))
            item.change_pct = round(change * 100, 2)
            item.price = new_price
            item.updated_at = datetime.now(timezone.utc)
    log.info("Market prices updated.")


async def get_market_items(session: AsyncSession) -> list[MarketItem]:
    result = await session.execute(select(MarketItem))
    return result.scalars().all()


async def buy_item(session: AsyncSession, user_id: int, symbol: str, qty: int) -> tuple[bool, str]:
    item = await get_or_none(session, MarketItem, symbol=symbol)
    if not item:
        return False, "Item not found."
    total = item.price * qty
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    if wallet.cash < total:
        return False, f"Insufficient funds. Need ${total:,}."

    await deduct_cash(session, user_id, total, f"market:buy:{symbol}")
    await add_to_inventory(session, user_id, symbol, qty)

    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.trades_completed += 1
    stats.trader_score += 1
    await add_xp(session, user_id, cfg.XP_PER_TRADE)
    await update_mission_progress(session, user_id, "trade", 1)
    return True, f"✅ Bought {qty}x {symbol} for ${total:,}."


async def sell_item(session: AsyncSession, user_id: int, symbol: str, qty: int) -> tuple[bool, str]:
    if not await deduct_from_inventory(session, user_id, symbol, qty):
        return False, "Not enough in inventory."
    item = await get_or_none(session, MarketItem, symbol=symbol)
    total = int(item.price * qty * 0.95)   # 5% sell spread
    await add_cash(session, user_id, total, f"market:sell:{symbol}")
    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.trades_completed += 1
    stats.trader_score += 1
    await add_xp(session, user_id, cfg.XP_PER_TRADE)
    await update_mission_progress(session, user_id, "trade", 1)
    return True, f"✅ Sold {qty}x {symbol} for ${total:,}."


# ─────────────────────────────────────────────────────────────────
# SERVICES — EXPLORATION
# ─────────────────────────────────────────────────────────────────

async def perform_exploration(session: AsyncSession, user_id: int, region_key: str) -> tuple[bool, str]:
    region = REGIONS.get(region_key)
    if not region:
        return False, "Unknown region."

    wallet = await get_or_none(session, Wallet, user_id=user_id)
    travel_cost = region["travel_cost"]
    if wallet.cash < travel_cost:
        return False, f"Need ${travel_cost:,} to travel there."
    if wallet.energy < cfg.BASE_EXPLORE_COST_ENERGY:
        return False, f"Need {cfg.BASE_EXPLORE_COST_ENERGY} energy."

    await deduct_cash(session, user_id, travel_cost, f"travel:{region_key}")
    await spend_energy(session, user_id, cfg.BASE_EXPLORE_COST_ENERGY)

    # Roll outcome
    roll = random.random()
    danger_mod = region["danger"]
    cumulative = 0.0
    outcome = "nothing"
    for name, prob, _ in EXPLORE_OUTCOMES:
        if name == "danger":
            prob = danger_mod
        cumulative += prob
        if roll < cumulative:
            outcome = name
            break

    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.explorations_done += 1
    stats.explorer_score += 1
    user = await get_or_none(session, User, id=user_id)
    user.current_region = region_key

    await add_xp(session, user_id, cfg.XP_PER_EXPLORE)
    await update_mission_progress(session, user_id, "explore", 1)
    if region_key == "unknown":
        await update_mission_progress(session, user_id, "explore_unk", 1)

    msg = ""
    if outcome == "cash":
        reward = random.randint(2_000, 15_000)
        await add_cash(session, user_id, reward, f"explore:{region_key}")
        msg = f"💰 Found <b>${reward:,}</b> in {region['name']}!"

    elif outcome == "resource":
        symbols = [s for s, *_ in MARKET_SEED]
        sym = random.choice(symbols)
        qty = random.randint(5, 20)
        await add_to_inventory(session, user_id, sym, qty)
        msg = f"📦 Found <b>{qty}x {sym}</b>!"

    elif outcome == "rare_item":
        rare_items = ["ECHO_FRAGMENT", "ANCIENT_SHARD", "VOID_CORE"]
        item = random.choice(rare_items)
        await add_to_inventory(session, user_id, item, 1)
        msg = f"💎 Found a rare item: <b>{item}</b>!"
        stats.reputation += 2

    elif outcome == "discovery":
        discovery_name = f"ECHO_{region_key.upper()}_{random.randint(100,999)}"
        existing = await get_or_none(session, Discovery, symbol=discovery_name)
        if not existing:
            session.add(Discovery(
                symbol=discovery_name, name=f"Echo Site {region_key.title()}",
                discoverer_id=user_id, rarity="rare",
                description=f"Discovered in {region['name']} by a brave explorer.",
            ))
            stats.discoveries_count += 1
            stats.fame += 50
            stats.explorer_score += 5
            user.title = "Explorer"
            msg = f"🌟 <b>WORLD FIRST!</b> You discovered <b>{discovery_name}</b>!"
        else:
            reward = random.randint(5_000, 20_000)
            await add_cash(session, user_id, reward, f"explore:{region_key}")
            msg = f"🔍 Found ancient traces. Earned <b>${reward:,}</b>."

    elif outcome == "danger":
        loss = random.randint(1_000, 5_000)
        try:
            await deduct_cash(session, user_id, loss, f"explore_danger:{region_key}")
            msg = f"⚠️ Danger! Lost <b>${loss:,}</b>."
        except ValueError:
            msg = "⚠️ Danger! You barely escaped."
    else:
        msg = "🌫 Nothing found this time. The region was quiet."

    return True, msg


# ─────────────────────────────────────────────────────────────────
# SERVICES — MISSIONS
# ─────────────────────────────────────────────────────────────────

async def update_mission_progress(session: AsyncSession, user_id: int, condition: str, increment: int) -> None:
    result = await session.execute(
        select(UserMission, Mission)
        .join(Mission, UserMission.mission_id == Mission.id)
        .where(UserMission.user_id == user_id, UserMission.completed == False, Mission.condition_type == condition)
    )
    for um, m in result.all():
        um.progress = min(um.progress + increment, m.condition_value)
        if um.progress >= m.condition_value:
            um.completed = True
            um.completed_at = datetime.now(timezone.utc)
            await add_cash(session, user_id, m.reward_cash, f"mission:{m.id}")
            await add_xp(session, user_id, m.reward_xp)
            stats = await get_or_none(session, UserStats, user_id=user_id)
            if stats:
                stats.missions_completed += 1


# ─────────────────────────────────────────────────────────────────
# SERVICES — BUSINESS
# ─────────────────────────────────────────────────────────────────

async def collect_business_income(session: AsyncSession, user_id: int, biz_id: int) -> tuple[bool, str]:
    biz = await get_or_none(session, Business, id=biz_id)
    if not biz or biz.owner_id != user_id:
        return False, "Business not found."

    now = datetime.now(timezone.utc)
    last = biz.last_collected.replace(tzinfo=timezone.utc)
    hours = (now - last).total_seconds() / 3600
    if hours < 1:
        remaining = int(3600 - (now - last).total_seconds())
        return False, f"Next collection in {remaining//60}m {remaining%60}s."

    income = int(biz.income_per_hour * hours * (1 - cfg.BUSINESS_TAX_RATE))
    income = min(income, biz.income_per_hour * 24)  # cap at 24h
    await add_cash(session, user_id, income, f"business:{biz_id}")
    biz.last_collected = now
    biz.visitors += random.randint(10, 100)
    await update_mission_progress(session, user_id, "collect", 1)
    return True, f"💼 Collected <b>${income:,}</b> from {biz.name}."


async def buy_business(session: AsyncSession, user_id: int, biz_type: str, name: str) -> tuple[bool, str]:
    if biz_type not in BUSINESS_TYPES:
        return False, "Unknown business type."
    bdata = BUSINESS_TYPES[biz_type]
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    if wallet.cash < bdata["cost"]:
        return False, f"Need ${bdata['cost']:,}."

    user = await get_or_none(session, User, id=user_id)
    await deduct_cash(session, user_id, bdata["cost"], f"buy_business:{biz_type}")
    session.add(Business(
        owner_id=user_id, name=name, type=biz_type,
        region=user.current_region, income_per_hour=bdata["income"],
    ))
    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.leader_score += 2
    return True, f"🏢 <b>{name}</b> is now open for business!"


# ─────────────────────────────────────────────────────────────────
# SERVICES — GUILD
# ─────────────────────────────────────────────────────────────────

async def create_guild(session: AsyncSession, user_id: int, name: str, tag: str, desc: str) -> tuple[bool, str]:
    existing_member = await get_or_none(session, GuildMember, user_id=user_id)
    if existing_member:
        return False, "You are already in a guild."

    existing_guild = await get_or_none(session, Guild, name=name)
    if existing_guild:
        return False, "Guild name already taken."

    wallet = await get_or_none(session, Wallet, user_id=user_id)
    GUILD_COST = 100_000
    if wallet.cash < GUILD_COST:
        return False, f"Need ${GUILD_COST:,} to found a guild."

    await deduct_cash(session, user_id, GUILD_COST, "guild:create")
    guild = Guild(name=name, tag=tag.upper(), description=desc, owner_id=user_id)
    session.add(guild)
    await session.flush()
    session.add(GuildMember(guild_id=guild.id, user_id=user_id, role="owner"))

    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.leader_score += 10
    stats.reputation += 5
    return True, f"👥 Guild <b>[{tag.upper()}] {name}</b> founded!"


async def join_guild(session: AsyncSession, user_id: int, guild_id: int) -> tuple[bool, str]:
    existing = await get_or_none(session, GuildMember, user_id=user_id)
    if existing:
        return False, "Already in a guild."
    guild = await get_or_none(session, Guild, id=guild_id)
    if not guild:
        return False, "Guild not found."
    session.add(GuildMember(guild_id=guild_id, user_id=user_id, role="recruit"))
    stats = await get_or_none(session, UserStats, user_id=user_id)
    stats.leader_score += 1
    return True, f"👥 Joined <b>{guild.name}</b>!"


# ─────────────────────────────────────────────────────────────────
# SERVICES — DAILY
# ─────────────────────────────────────────────────────────────────

async def claim_daily(session: AsyncSession, user_id: int) -> tuple[bool, str]:
    stats = await get_or_none(session, UserStats, user_id=user_id)
    now = datetime.now(timezone.utc)

    if stats.last_daily:
        last = stats.last_daily.replace(tzinfo=timezone.utc)
        diff = (now - last).total_seconds()
        if diff < 82800:  # 23h grace
            remaining = int(86400 - diff)
            return False, f"⏳ Come back in {remaining//3600}h {(remaining%3600)//60}m."
        if diff < 172800:  # streak continues < 48h
            stats.daily_streak += 1
        else:
            stats.daily_streak = 1
    else:
        stats.daily_streak = 1

    stats.last_daily = now
    streak_bonus = min(stats.daily_streak, 30) * 100
    cash_reward = cfg.DAILY_LOGIN_REWARD_CASH + streak_bonus
    await add_cash(session, user_id, cash_reward, "daily:login")
    await add_xp(session, user_id, cfg.DAILY_LOGIN_REWARD_XP)

    # Restore some energy
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    wallet.energy = min(cfg.MAX_ENERGY, wallet.energy + 30)

    return True, (
        f"🎁 Daily reward claimed!\n"
        f"💰 +${cash_reward:,}\n"
        f"⭐ +{cfg.DAILY_LOGIN_REWARD_XP:,} XP\n"
        f"⚡ +30 Energy\n"
        f"🔥 Streak: {stats.daily_streak} days"
    )


# ─────────────────────────────────────────────────────────────────
# SERVICES — WORLD EVENT
# ─────────────────────────────────────────────────────────────────

async def start_world_event(event_type: str) -> Optional[str]:
    if event_type not in EVENT_TEMPLATES:
        return None
    tmpl = EVENT_TEMPLATES[event_type]
    async with get_session() as session:
        now = datetime.now(timezone.utc)
        event = WorldEvent(
            event_type=event_type,
            title=tmpl["title"],
            description=tmpl["desc"],
            status="active",
            started_at=now,
            ends_at=now + timedelta(seconds=tmpl["duration"]),
            effects=json.dumps(tmpl["effects"]),
        )
        session.add(event)
    log.info(f"World event started: {event_type}")
    return tmpl["title"]


async def get_active_event(session: AsyncSession) -> Optional[WorldEvent]:
    result = await session.execute(
        select(WorldEvent).where(WorldEvent.status == "active")
        .order_by(WorldEvent.started_at.desc())
    )
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────────────────────────────

async def format_profile(session: AsyncSession, user: User) -> str:
    stats = user.stats
    wallet = user.wallet
    guild_info = "—"
    if user.guild_member:
        g = await get_or_none(session, Guild, id=user.guild_member.guild_id)
        if g:
            guild_info = f"[{g.tag}] {g.name}"

    energy = await get_energy(session, user.id)
    lvl = stats.level
    xp = stats.xp
    next_xp = xp_for_level(lvl + 1)
    archetype = archetype_from_scores(stats.trader_score, stats.explorer_score, stats.leader_score)

    region = REGIONS.get(user.current_region, {})
    return (
        f"🌐 <b>ECHO CITY</b>\n\n"
        f"👤 <b>{user.nickname}</b>  <i>[{user.title}]</i>\n"
        f"🏷 Role: <b>{archetype}</b>\n"
        f"⭐ Level: <b>{lvl}</b>   XP: {xp:,} / {next_xp:,}\n\n"
        f"💰 Cash: <b>${wallet.cash:,}</b>\n"
        f"🏦 Bank: <b>${wallet.bank:,}</b>\n"
        f"⚡ Energy: <b>{energy}/{cfg.MAX_ENERGY}</b>\n\n"
        f"⚔️ Reputation: <b>{stats.reputation}</b>\n"
        f"🌟 Fame: <b>{stats.fame}</b>\n"
        f"🔥 Streak: <b>{stats.daily_streak}</b> days\n\n"
        f"👥 Guild: <b>{guild_info}</b>\n"
        f"📍 Region: <b>{region.get('emoji','')} {region.get('name', user.current_region)}</b>\n\n"
        f"📊 Missions: {stats.missions_completed} | Trades: {stats.trades_completed} | Explores: {stats.explorations_done}"
    )


async def format_wallet(session: AsyncSession, user_id: int) -> str:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    energy = await get_energy(session, user_id)

    inv_result = await session.execute(
        select(Inventory).where(Inventory.user_id == user_id, Inventory.quantity > 0)
    )
    inv = inv_result.scalars().all()
    inv_text = "\n".join(f"  • {i.symbol}: {i.quantity}" for i in inv) or "  (empty)"

    return (
        f"💼 <b>WALLET</b>\n\n"
        f"💰 Cash: <b>${wallet.cash:,}</b>\n"
        f"🏦 Bank: <b>${wallet.bank:,}</b>\n"
        f"⚡ Energy: <b>{energy}/{cfg.MAX_ENERGY}</b>\n\n"
        f"📦 <b>Inventory:</b>\n{inv_text}"
    )


async def format_market(session: AsyncSession) -> str:
    items = await get_market_items(session)
    lines = []
    for item in items:
        arrow = "▲" if item.change_pct >= 0 else "▼"
        color = "+" if item.change_pct >= 0 else ""
        lines.append(f"  <b>{item.symbol}</b>  ${item.price:,}  {arrow} {color}{item.change_pct:.1f}%")
    return "📈 <b>ECHO MARKET</b>\n\n" + "\n".join(lines) + "\n\n<i>Prices update every 5 min.</i>"


async def format_missions(session: AsyncSession, user_id: int) -> str:
    result = await session.execute(
        select(UserMission, Mission)
        .join(Mission, UserMission.mission_id == Mission.id)
        .where(UserMission.user_id == user_id)
    )
    rows = result.all()
    if not rows:
        return "🎯 No active missions."

    lines = []
    for um, m in rows:
        status = "✅" if um.completed else f"{um.progress}/{m.condition_value}"
        lines.append(
            f"{'✅' if um.completed else '🎯'} <b>{m.title}</b>\n"
            f"   {m.description}\n"
            f"   Progress: {status}  |  Reward: ${m.reward_cash:,}"
        )
    return "🎯 <b>MISSIONS</b>\n\n" + "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# ADMIN HELPERS
# ─────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in cfg.ADMIN_IDS


async def log_admin(session: AsyncSession, admin_id: int, action: str, target_id: Optional[int], details: str) -> None:
    session.add(AuditLog(admin_id=admin_id, action=action, target_id=target_id, details=details))


# ─────────────────────────────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────────────────────────────

router = Router(name="main")


# ── /start ───────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await rate_limit_check(message.from_user.id):
        await message.answer("⚠️ Slow down.")
        return

    async with get_session() as session:
        user = await get_or_none(session, User, id=message.from_user.id)
        if user and not user.is_banned:
            await touch_user(session, user.id)
            text = await format_profile(session, user)
            await message.answer(text, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)
        elif user and user.is_banned:
            await message.answer("🚫 You are banned from ECHO.")
        else:
            await state.set_state(RegisterState.waiting_nickname)
            await message.answer(
                "🌐 <b>ECHO CITY</b>\n\n"
                "You are entering a living world.\n"
                "Thousands of players have already built their lives here.\n\n"
                "What will <b>your name</b> be?\n\n"
                "Enter your nickname (3-20 characters):",
                parse_mode=ParseMode.HTML,
            )


@router.message(RegisterState.waiting_nickname)
async def register_nickname(message: Message, state: FSMContext) -> None:
    nick = message.text.strip() if message.text else ""
    if len(nick) < 3 or len(nick) > 20:
        await message.answer("❌ Nickname must be 3-20 characters. Try again:")
        return
    if not nick.replace("_", "").replace("-", "").isalnum():
        await message.answer("❌ Only letters, numbers, _ and - are allowed. Try again:")
        return

    async with get_session() as session:
        taken = await session.execute(select(User).where(User.nickname == nick))
        if taken.scalar_one_or_none():
            await message.answer("❌ That nickname is taken. Choose another:")
            return
        user = await create_user(session, message.from_user.id, message.from_user.username, nick)

    await state.clear()
    await message.answer(
        f"✅ Welcome to ECHO, <b>{nick}</b>!\n\n"
        f"💰 Starting cash: <b>${cfg.STARTING_CASH:,}</b>\n"
        f"⚡ Energy: <b>{cfg.STARTING_ENERGY}/{cfg.MAX_ENERGY}</b>\n\n"
        f"The city is alive. What will you do first?",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


# ── Profile ───────────────────────────────────────────────────────

@router.message(Command("profile"))
@router.callback_query(F.data == "profile")
async def show_profile(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id
    async with get_session() as session:
        user = await get_or_none(session, User, id=user_id)
        if not user:
            txt = "❌ Register first with /start"
            if isinstance(event, CallbackQuery):
                await event.answer(txt)
            else:
                await event.answer(txt)
            return
        text = await format_profile(session, user)

    kb_profile = kb(
        [("💰 Wallet", "wallet"), ("🎯 Missions", "missions")],
        [("◀️ Menu", "menu")],
    )
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_profile)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_profile, parse_mode=ParseMode.HTML)


# ── Wallet ────────────────────────────────────────────────────────

@router.callback_query(F.data == "wallet")
async def show_wallet(call: CallbackQuery) -> None:
    async with get_session() as session:
        text = await format_wallet(session, call.from_user.id)
    kb_wallet = kb(
        [("📈 Market", "market"), ("📦 Inventory", "inventory")],
        [("◀️ Menu", "menu")],
    )
    await safe_edit(call.message, text, kb_wallet)
    await call.answer()


# ── Market ────────────────────────────────────────────────────────

@router.message(Command("market"))
@router.callback_query(F.data == "market")
async def show_market(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        text = await format_market(session)
        items = await get_market_items(session)

    buttons = [[InlineKeyboardButton(text=f"Buy {i.symbol}", callback_data=f"buy_select:{i.symbol}")]
               for i in items[:6]]
    buttons.append([InlineKeyboardButton(text="◀️ Menu", callback_data="menu")])
    kb_market = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_market)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_market, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("buy_select:"))
async def buy_select(call: CallbackQuery, state: FSMContext) -> None:
    symbol = call.data.split(":")[1]
    await state.set_state(TradeState.waiting_quantity)
    await state.update_data(action="buy", symbol=symbol)
    async with get_session() as session:
        item = await get_or_none(session, MarketItem, symbol=symbol)
    await safe_edit(call.message,
        f"💹 <b>BUY {symbol}</b>\nPrice: ${item.price:,}\n\nHow many units?",
        back_kb("market"))
    await call.answer()


@router.message(TradeState.waiting_quantity)
async def trade_quantity(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a positive number:")
        return

    async with get_session() as session:
        if data["action"] == "buy":
            ok, msg = await buy_item(session, message.from_user.id, data["symbol"], qty)
        else:
            ok, msg = await sell_item(session, message.from_user.id, data["symbol"], qty)

    await state.clear()
    await message.answer(msg, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)


# ── Explore ───────────────────────────────────────────────────────

@router.message(Command("explore"))
@router.callback_query(F.data == "explore")
async def show_explore(event: Message | CallbackQuery) -> None:
    lines = []
    for key, r in REGIONS.items():
        cost = f"${r['travel_cost']:,}" if r["travel_cost"] else "Free"
        lines.append(f"{r['emoji']} <b>{r['name']}</b> — {cost}")

    text = "🗺 <b>EXPLORE</b>\n\n" + "\n".join(lines) + "\n\n⚡ Each expedition costs energy."
    buttons = [[InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"go:{k}")]
               for k, r in REGIONS.items()]
    buttons.append([InlineKeyboardButton(text="◀️ Menu", callback_data="menu")])
    kb_exp = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_exp)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_exp, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("go:"))
async def do_explore(call: CallbackQuery) -> None:
    region_key = call.data.split(":")[1]
    cd_key = f"explore:{call.from_user.id}"
    if await check_cooldown(cd_key):
        ttl = await get_cooldown_ttl(cd_key)
        await call.answer(f"⏳ Wait {ttl}s before next expedition.", show_alert=True)
        return

    async with get_session() as session:
        user = await get_or_none(session, User, id=call.from_user.id)
        if not user:
            await call.answer("Register first!")
            return
        ok, msg = await perform_exploration(session, call.from_user.id, region_key)

    if ok:
        await set_cooldown(cd_key, 300)  # 5 min cooldown
    await safe_edit(call.message, msg, kb(
        [("🗺 Explore Again", "explore")],
        [("◀️ Menu", "menu")],
    ))
    await call.answer()


# ── Missions ──────────────────────────────────────────────────────

@router.message(Command("missions"))
@router.callback_query(F.data == "missions")
async def show_missions(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        text = await format_missions(session, event.from_user.id)

    kb_m = kb([("◀️ Menu", "menu")])
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_m)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_m, parse_mode=ParseMode.HTML)


# ── Business ──────────────────────────────────────────────────────

@router.message(Command("business"))
@router.callback_query(F.data == "business")
async def show_business(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id
    async with get_session() as session:
        result = await session.execute(
            select(Business).where(Business.owner_id == user_id, Business.is_active == True)
        )
        businesses = result.scalars().all()

    if not businesses:
        text = (
            "🏢 <b>BUSINESS</b>\n\n"
            "You own no businesses yet.\n\n"
            "<b>Available types:</b>\n" +
            "\n".join(f"{v['emoji']} {v['name']} — ${v['cost']:,} (${v['income']:,}/h)"
                      for v in BUSINESS_TYPES.values())
        )
        buttons = [[InlineKeyboardButton(text=f"{v['emoji']} Open {v['name']}", callback_data=f"open_biz:{k}")]
                   for k, v in BUSINESS_TYPES.items()]
        buttons.append([InlineKeyboardButton(text="◀️ Menu", callback_data="menu")])
        kb_biz = InlineKeyboardMarkup(inline_keyboard=buttons)
    else:
        lines = []
        collect_buttons = []
        for b in businesses:
            bdata = BUSINESS_TYPES.get(b.type, {})
            lines.append(f"{bdata.get('emoji','🏢')} <b>{b.name}</b> (Lv.{b.level})\n  Income: ${b.income_per_hour:,}/h | Visitors: {b.visitors:,}")
            collect_buttons.append([InlineKeyboardButton(text=f"Collect from {b.name}", callback_data=f"collect:{b.id}")])

        text = "🏢 <b>MY BUSINESSES</b>\n\n" + "\n\n".join(lines)
        collect_buttons.append([InlineKeyboardButton(text="+ Open New", callback_data="new_biz")])
        collect_buttons.append([InlineKeyboardButton(text="◀️ Menu", callback_data="menu")])
        kb_biz = InlineKeyboardMarkup(inline_keyboard=collect_buttons)

    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_biz)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_biz, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("open_biz:"))
async def open_business_select(call: CallbackQuery, state: FSMContext) -> None:
    btype = call.data.split(":")[1]
    await state.update_data(biz_type=btype)
    bdata = BUSINESS_TYPES[btype]
    await safe_edit(call.message,
        f"{bdata['emoji']} <b>Open {bdata['name']}</b>\nCost: ${bdata['cost']:,}\n\nWhat will you name it?",
        back_kb("business"))
    await state.set_state(TradeState.waiting_confirm)
    await call.answer()


@router.callback_query(F.data.startswith("collect:"))
async def collect_business(call: CallbackQuery) -> None:
    biz_id = int(call.data.split(":")[1])
    async with get_session() as session:
        ok, msg = await collect_business_income(session, call.from_user.id, biz_id)
    await call.answer(msg, show_alert=True)


@router.callback_query(F.data == "new_biz")
async def new_business(call: CallbackQuery) -> None:
    await show_business(call)


# Business name input — reusing TradeState.waiting_confirm state
@router.message(TradeState.waiting_confirm)
async def business_name_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if "biz_type" not in data:
        await state.clear()
        return

    name = message.text.strip()
    if len(name) < 2 or len(name) > 40:
        await message.answer("❌ Name must be 2-40 characters.")
        return

    async with get_session() as session:
        ok, msg = await buy_business(session, message.from_user.id, data["biz_type"], name)

    await state.clear()
    await message.answer(msg, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)


# ── Guild ─────────────────────────────────────────────────────────

@router.message(Command("guild"))
@router.callback_query(F.data == "guild")
async def show_guild(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id
    async with get_session() as session:
        member = await get_or_none(session, GuildMember, user_id=user_id)
        if member:
            guild = await get_or_none(session, Guild, id=member.guild_id)
            member_count = len(guild.members)
            text = (
                f"👥 <b>[{guild.tag}] {guild.name}</b>\n\n"
                f"Level: {guild.level} | Members: {member_count}\n"
                f"Power: {guild.power:,}\n"
                f"Treasury: ${guild.treasury_cash:,}\n\n"
                f"Your role: <b>{member.role.title()}</b>\n"
                f"Contribution: {member.contribution:,}"
            )
            kb_guild = kb(
                [("📊 Members", "guild_members"), ("💰 Donate", "guild_donate")],
                [("◀️ Menu", "menu")],
            )
        else:
            result = await session.execute(select(Guild).where(Guild.is_active == True).order_by(Guild.power.desc()).limit(5))
            guilds = result.scalars().all()
            lines = [f"[{g.tag}] <b>{g.name}</b> — {len(g.members)} members" for g in guilds]
            text = "👥 <b>GUILDS</b>\n\nYou are not in a guild.\n\n<b>Top Guilds:</b>\n" + "\n".join(lines)
            buttons = [[InlineKeyboardButton(text=f"Join {g.name}", callback_data=f"join_guild:{g.id}")] for g in guilds]
            buttons.append([InlineKeyboardButton(text="➕ Create Guild", callback_data="create_guild")])
            buttons.append([InlineKeyboardButton(text="◀️ Menu", callback_data="menu")])
            kb_guild = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_guild)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_guild, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "create_guild")
async def create_guild_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GuildCreateState.waiting_name)
    await safe_edit(call.message,
        "👥 <b>CREATE GUILD</b>\n\nCost: $100,000\n\nEnter guild name (3-32 chars):",
        back_kb("guild"))
    await call.answer()


@router.message(GuildCreateState.waiting_name)
async def guild_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 3 or len(name) > 32:
        await message.answer("❌ Name must be 3-32 characters.")
        return
    await state.update_data(guild_name=name)
    await state.set_state(GuildCreateState.waiting_tag)
    await message.answer("Enter guild tag (2-6 chars, e.g. ECHO):")


@router.message(GuildCreateState.waiting_tag)
async def guild_tag(message: Message, state: FSMContext) -> None:
    tag = message.text.strip().upper()
    if len(tag) < 2 or len(tag) > 6 or not tag.isalnum():
        await message.answer("❌ Tag must be 2-6 alphanumeric chars.")
        return
    await state.update_data(guild_tag=tag)
    await state.set_state(GuildCreateState.waiting_desc)
    await message.answer("Enter a short description (optional, or send -):")


@router.message(GuildCreateState.waiting_desc)
async def guild_desc(message: Message, state: FSMContext) -> None:
    desc = "" if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    async with get_session() as session:
        ok, msg = await create_guild(session, message.from_user.id, data["guild_name"], data["guild_tag"], desc)
    await state.clear()
    await message.answer(msg, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("join_guild:"))
async def do_join_guild(call: CallbackQuery) -> None:
    guild_id = int(call.data.split(":")[1])
    async with get_session() as session:
        ok, msg = await join_guild(session, call.from_user.id, guild_id)
    await call.answer(msg, show_alert=True)


# ── Daily ─────────────────────────────────────────────────────────

@router.message(Command("daily"))
@router.callback_query(F.data == "daily")
async def show_daily(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        ok, msg = await claim_daily(session, event.from_user.id)

    kb_daily = kb([("◀️ Menu", "menu")])
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, msg, kb_daily)
        await event.answer()
    else:
        await event.answer(msg, reply_markup=kb_daily, parse_mode=ParseMode.HTML)


# ── World ─────────────────────────────────────────────────────────

@router.message(Command("world"))
@router.callback_query(F.data == "world")
async def show_world(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        active_event = await get_active_event(session)

        # World stats
        user_count_result = await session.execute(select(func.count()).select_from(User))
        user_count = user_count_result.scalar()

        guild_count_result = await session.execute(select(func.count()).select_from(Guild).where(Guild.is_active == True))
        guild_count = guild_count_result.scalar()

        disc_count_result = await session.execute(select(func.count()).select_from(Discovery))
        disc_count = disc_count_result.scalar()

    event_text = ""
    if active_event:
        ends = active_event.ends_at.replace(tzinfo=timezone.utc)
        remaining = max(0, int((ends - datetime.now(timezone.utc)).total_seconds()))
        event_text = (
            f"\n🚨 <b>ACTIVE EVENT</b>\n"
            f"{active_event.title}\n"
            f"{active_event.description}\n"
            f"⏱ Ends in: {remaining//60}m {remaining%60}s\n"
        )

    text = (
        f"🌐 <b>ECHO CITY — LIVE</b>\n\n"
        f"👥 Citizens: {user_count:,}\n"
        f"🏛 Guilds: {guild_count:,}\n"
        f"🔍 Discoveries: {disc_count:,}\n"
        f"{event_text}\n"
        f"<b>Regions:</b>\n" +
        "\n".join(f"{r['emoji']} {r['name']}" for r in REGIONS.values())
    )

    kb_world = kb(
        [("🗺 Explore", "explore"), ("📜 History", "history")],
        [("◀️ Menu", "menu")],
    )
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_world)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_world, parse_mode=ParseMode.HTML)


# ── Ranking ───────────────────────────────────────────────────────

@router.message(Command("ranking"))
@router.callback_query(F.data == "ranking")
async def show_ranking(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        # Top by XP/level
        result = await session.execute(
            select(User, UserStats, Wallet)
            .join(UserStats, User.id == UserStats.user_id)
            .join(Wallet, User.id == Wallet.user_id)
            .order_by(UserStats.xp.desc())
            .limit(10)
        )
        rows = result.all()

    lines = []
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    for i, (u, s, w) in enumerate(rows):
        lines.append(f"{medals[i]} <b>{u.nickname}</b> — Lv.{s.level}  ${w.cash:,}")

    text = "🏆 <b>GLOBAL RANKING</b>\n\n" + "\n".join(lines)
    kb_rank = kb([("◀️ Menu", "menu")])
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_rank)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_rank, parse_mode=ParseMode.HTML)


# ── Inventory ─────────────────────────────────────────────────────

@router.message(Command("inventory"))
@router.callback_query(F.data == "inventory")
async def show_inventory(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id
    async with get_session() as session:
        result = await session.execute(
            select(Inventory).where(Inventory.user_id == user_id, Inventory.quantity > 0)
        )
        items = result.scalars().all()

    if items:
        lines = [f"  📦 <b>{i.symbol}</b>: {i.quantity}" for i in items]
        text = "📦 <b>INVENTORY</b>\n\n" + "\n".join(lines)
    else:
        text = "📦 <b>INVENTORY</b>\n\nYour inventory is empty."

    buttons = []
    for i in items:
        buttons.append([InlineKeyboardButton(text=f"Sell {i.symbol}", callback_data=f"sell_select:{i.symbol}")])
    buttons.append([InlineKeyboardButton(text="◀️ Wallet", callback_data="wallet")])
    kb_inv = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_inv)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_inv, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("sell_select:"))
async def sell_select(call: CallbackQuery, state: FSMContext) -> None:
    symbol = call.data.split(":")[1]
    async with get_session() as session:
        item = await get_or_none(session, MarketItem, symbol=symbol)
        inv = await get_or_none(session, Inventory, user_id=call.from_user.id, symbol=symbol)
    price_text = f"${item.price:,}" if item else "N/A"
    qty_text = inv.quantity if inv else 0
    await state.set_state(TradeState.waiting_quantity)
    await state.update_data(action="sell", symbol=symbol)
    await safe_edit(call.message,
        f"💹 <b>SELL {symbol}</b>\nMarket price: {price_text}\nYou have: {qty_text}\n\nHow many to sell?",
        back_kb("inventory"))
    await call.answer()


# ── History ───────────────────────────────────────────────────────

@router.message(Command("history"))
@router.callback_query(F.data == "history")
async def show_history(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(WorldEvent).order_by(WorldEvent.started_at.desc()).limit(10)
        )
        events = result.scalars().all()

        disc_result = await session.execute(
            select(Discovery, User)
            .join(User, Discovery.discoverer_id == User.id)
            .order_by(Discovery.discovered_at.desc()).limit(5)
        )
        discoveries = disc_result.all()

    event_lines = []
    for e in events:
        started = e.started_at.strftime("%d %b %H:%M") if e.started_at else "?"
        event_lines.append(f"• {e.title} — {started}")

    disc_lines = []
    for d, u in discoveries:
        disc_lines.append(f"🔍 <b>{d.name}</b> by {u.nickname}")

    text = (
        "📜 <b>WORLD HISTORY</b>\n\n"
        "<b>Recent Events:</b>\n" +
        ("\n".join(event_lines) or "No events yet.") +
        "\n\n<b>Recent Discoveries:</b>\n" +
        ("\n".join(disc_lines) or "No discoveries yet.")
    )
    kb_hist = kb([("◀️ Menu", "menu")])
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_hist)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_hist, parse_mode=ParseMode.HTML)


# ── Menu ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu")
async def show_menu(call: CallbackQuery) -> None:
    async with get_session() as session:
        user = await get_or_none(session, User, id=call.from_user.id)
        if not user:
            await call.answer("Register with /start", show_alert=True)
            return
        wallet = user.wallet
        energy = await get_energy(session, user.id)

    text = (
        f"🌐 <b>ECHO CITY</b>\n\n"
        f"👤 <b>{user.nickname}</b>  Lv.{user.stats.level}\n"
        f"💰 ${wallet.cash:,}  🏦 ${wallet.bank:,}\n"
        f"⚡ {energy}/{cfg.MAX_ENERGY}  🔥 {user.stats.daily_streak}d streak\n"
        f"📍 {REGIONS.get(user.current_region,{}).get('emoji','')} "
        f"{REGIONS.get(user.current_region,{}).get('name', user.current_region)}"
    )
    await safe_edit(call.message, text, main_menu_kb())
    await call.answer()


# ── Help ──────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📖 <b>ECHO CITY — COMMANDS</b>\n\n"
        "/start — Enter ECHO\n"
        "/profile — Your profile\n"
        "/wallet — Cash & inventory\n"
        "/market — Buy & sell resources\n"
        "/explore — Explore regions\n"
        "/missions — Active missions\n"
        "/business — Your businesses\n"
        "/guild — Guild system\n"
        "/ranking — Leaderboard\n"
        "/daily — Daily reward\n"
        "/inventory — Your items\n"
        "/history — World history\n"
        "/world — World overview\n\n"
        "Need help? The world is waiting.",
        parse_mode=ParseMode.HTML,
    )


# ── Admin ─────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    async with get_session() as session:
        user_count = (await session.execute(select(func.count()).select_from(User))).scalar()
        guild_count = (await session.execute(select(func.count()).select_from(Guild).where(Guild.is_active == True))).scalar()
        event = await get_active_event(session)

    text = (
        f"🔧 <b>ADMIN DASHBOARD</b>\n\n"
        f"👥 Users: {user_count:,}\n"
        f"🏛 Guilds: {guild_count:,}\n"
        f"🌍 Active Event: {event.title if event else 'None'}\n"
    )
    kb_admin = kb(
        [("👥 Users", "adm_users"), ("💰 Economy", "adm_economy")],
        [("🌍 Events", "adm_events"), ("📢 Broadcast", "adm_broadcast")],
        [("📜 Audit Log", "adm_audit")],
    )
    await message.answer(text, reply_markup=kb_admin, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "adm_events")
async def admin_events(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    buttons = [[InlineKeyboardButton(text=f"Start: {k.upper()}", callback_data=f"adm_start_event:{k}")]
               for k in EVENT_TEMPLATES.keys()]
    kb_ev = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit(call.message, "🌍 <b>WORLD EVENTS</b>\n\nSelect event to start:", kb_ev)
    await call.answer()


@router.callback_query(F.data.startswith("adm_start_event:"))
async def admin_start_event(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    event_type = call.data.split(":")[1]
    title = await start_world_event(event_type)
    async with get_session() as session:
        await log_admin(session, call.from_user.id, "START_EVENT", None, f"event_type={event_type}")
    await call.answer(f"✅ {title} started!", show_alert=True)


@router.callback_query(F.data == "adm_audit")
async def admin_audit(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    async with get_session() as session:
        result = await session.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
        )
        logs = result.scalars().all()

    lines = [f"• [{l.action}] admin:{l.admin_id} target:{l.target_id} — {l.details}" for l in logs]
    text = "📜 <b>AUDIT LOG</b>\n\n" + ("\n".join(lines) or "No logs yet.")
    await safe_edit(call.message, text, back_kb("admin"))
    await call.answer()


@router.callback_query(F.data == "adm_broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    await state.set_state(AdminState.waiting_broadcast)
    await safe_edit(call.message, "📢 Enter broadcast message:", back_kb("admin"))
    await call.answer()


@router.message(AdminState.waiting_broadcast)
async def admin_do_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = message.text.strip()
    async with get_session() as session:
        result = await session.execute(select(User.id))
        user_ids = result.scalars().all()
        await log_admin(session, message.from_user.id, "BROADCAST", None, f"msg={text[:100]}")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 <b>ECHO BROADCAST</b>\n\n{text}", parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await state.clear()
    await message.answer(f"✅ Broadcast sent to {sent} users. Failed: {failed}.")


@router.message(Command("users"))
async def admin_users(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /users <user_id_or_nickname>")
        return
    query = parts[1]
    async with get_session() as session:
        try:
            uid = int(query)
            user = await get_or_none(session, User, id=uid)
        except ValueError:
            result = await session.execute(select(User).where(User.nickname == query))
            user = result.scalar_one_or_none()

        if not user:
            await message.answer("❌ User not found.")
            return
        text = await format_profile(session, user)

    buttons = [
        [InlineKeyboardButton(text="➕ Add Cash", callback_data=f"adm_addcash:{user.id}"),
         InlineKeyboardButton(text="🚫 Ban", callback_data=f"adm_ban:{user.id}")],
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm_ban:"))
async def admin_ban_user(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    target_id = int(call.data.split(":")[1])
    async with get_session() as session:
        await session.execute(update(User).where(User.id == target_id).values(is_banned=True))
        await log_admin(session, call.from_user.id, "BAN", target_id, "banned")
    await call.answer(f"✅ User {target_id} banned.", show_alert=True)


@router.callback_query(F.data.startswith("adm_addcash:"))
async def admin_addcash_start(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("❌ Unauthorized")
        return
    target_id = int(call.data.split(":")[1])
    await state.set_state(AdminState.waiting_amount)
    await state.update_data(target_id=target_id)
    await call.message.answer(f"Enter amount to add for user {target_id}:")
    await call.answer()


@router.message(AdminState.waiting_amount)
async def admin_addcash_do(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid amount.")
        return

    target_id = data["target_id"]
    async with get_session() as session:
        await add_cash(session, target_id, amount, "admin:grant")
        await log_admin(session, message.from_user.id, "ADD_CASH", target_id, f"amount={amount}")
    await state.clear()
    await message.answer(f"✅ Added ${amount:,} to user {target_id}.")


# ─────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────

async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    """Edit message text safely, ignoring 'not modified' errors."""
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"safe_edit error: {e}")


# ─────────────────────────────────────────────────────────────────
# BACKGROUND TASKS
# ─────────────────────────────────────────────────────────────────

async def task_market_update() -> None:
    await update_market_prices()


async def task_business_income() -> None:
    """Auto-notify users with pending business income (optional enhancement)."""
    log.debug("Business income tick.")


async def task_energy_regen() -> None:
    """Energy regeneration is handled on-demand in get_energy()."""
    pass


# ─────────────────────────────────────────────────────────────────
# MIDDLEWARE — Rate Limit
# ─────────────────────────────────────────────────────────────────

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class RateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and not await rate_limit_check(user.id):
            if hasattr(event, "answer"):
                try:
                    await event.answer("⚠️ Too many requests. Slow down.")
                except Exception:
                    pass
            return
        return await handler(event, data)


# ─────────────────────────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────────────────────────

from aiogram.types import ErrorEvent


@router.errors()
async def global_error_handler(event: ErrorEvent) -> None:
    log.exception(f"Unhandled error: {event.exception}", exc_info=event.exception)
    update = event.update
    if update.message:
        try:
            await update.message.answer("⚠️ Something went wrong. Try again.")
        except Exception:
            pass
    elif update.callback_query:
        try:
            await update.callback_query.answer("⚠️ Something went wrong.", show_alert=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

async def main() -> None:
    if not cfg.BOT_TOKEN:
        log.critical("BOT_TOKEN is not set! Check your .env file.")
        return

    log.info(f"Starting ECHO bot — environment: {cfg.ENVIRONMENT}")

    # ── Bot & Dispatcher ─────────────────────────────────────────
    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = RedisStorage(redis=redis)
    dp = Dispatcher(storage=storage)

    # ── Middleware ───────────────────────────────────────────────
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # ── Routers ──────────────────────────────────────────────────
    dp.include_router(router)

    # ── Database ──────────────────────────────────────────────────
    await init_db()

    # ── Scheduler ────────────────────────────────────────────────
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(task_market_update, "interval", seconds=cfg.MARKET_UPDATE_INTERVAL, id="market")
    scheduler.add_job(task_business_income, "interval", seconds=cfg.BUSINESS_INCOME_INTERVAL, id="biz_income")
    scheduler.start()
    log.info("Scheduler started.")

    # ── Start Polling ────────────────────────────────────────────
    log.info("ECHO is live. Waiting for players...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()
        await redis.aclose()
        log.info("ECHO shut down.")


if __name__ == "__main__":
    asyncio.run(main())
