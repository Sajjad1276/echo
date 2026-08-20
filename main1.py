"""
╔══════════════════════════════════════════════════════════════════╗
║                         ECHO CITY                                ║
║       Group-First Multiplayer Text-Based Social World Bot        ║
║                 Final Architecture (Persian UI)                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional
from enum import Enum

from dotenv import load_dotenv
load_dotenv()

# ── Aiogram ──────────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, ChatMemberUpdated, ErrorEvent, TelegramObject
)

# ── SQLAlchemy ────────────────────────────────────────────────────
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, select, update, func
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship

# ── Redis & Scheduler ─────────────────────────────────────────────
from redis.asyncio import Redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ──────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://echo:echo@localhost:5432/echo")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ADMIN_IDS: list[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    MAX_ENERGY: int = 100
    ENERGY_REGEN_RATE: int = 5
    ENERGY_REGEN_INTERVAL: int = 600
    BASE_EXPLORE_COST_ENERGY: int = 20
    STARTING_CASH: int = 10_000
    STARTING_ENERGY: int = 100
    MARKET_VOLATILITY: float = 0.08
    MARKET_UPDATE_INTERVAL: int = 300
    RATE_LIMIT_CALLS: int = 20
    RATE_LIMIT_WINDOW: int = 60

cfg = Config()

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("echo")

# ──────────────────────────────────────────────────────────────────
# DATABASE — MODELS
# ──────────────────────────────────────────────────────────────────
class Base(DeclarativeBase): pass

# [Global Progression Models]
class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    nickname = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_banned = Column(Boolean, default=False)
    
    wallet = relationship("Wallet", back_populates="user", uselist=False, lazy="selectin")
    stats = relationship("UserStats", back_populates="user", uselist=False, lazy="selectin")
    city_memberships = relationship("CityMember", back_populates="user", lazy="selectin")

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
    user = relationship("User", back_populates="stats")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    type = Column(String(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    source = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# [City-First Models]
class City(Base):
    __tablename__ = "cities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    level = Column(Integer, default=1)
    xp = Column(BigInteger, default=0)
    treasury_cash = Column(BigInteger, default=0)
    population = Column(Integer, default=0)
    status = Column(String(16), default="active")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    members = relationship("CityMember", back_populates="city", lazy="selectin")
    dashboards = relationship("CityDashboard", back_populates="city", lazy="selectin")

class CityMember(Base):
    __tablename__ = "city_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), default="citizen")
    city_wealth = Column(BigInteger, default=0)
    contribution = Column(BigInteger, default=0)
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (UniqueConstraint("city_id", "user_id", name="uq_city_member"),)
    city = relationship("City", back_populates="members")
    user = relationship("User", back_populates="city_memberships")

class CityDashboard(Base):
    __tablename__ = "city_dashboards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    dashboard_type = Column(String(32), nullable=False) # main, market, etc.
    message_id = Column(Integer, nullable=True)
    chat_id = Column(BigInteger, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (UniqueConstraint("city_id", "dashboard_type", name="uq_city_dashboard_type"),)
    city = relationship("City", back_populates="dashboards")

class HelpTopic(Base):
    __tablename__ = "help_topics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(64), unique=True, nullable=False)
    category = Column(String(32), nullable=False)
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    order = Column(Integer, default=0)

class MarketItem(Base):
    __tablename__ = "market_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    price = Column(BigInteger, nullable=False)
    base_price = Column(BigInteger, nullable=False)
    change_pct = Column(Float, default=0.0)

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    symbol = Column(String(32), nullable=False)
    quantity = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_inventory_user_symbol"),)

# ──────────────────────────────────────────────────────────────────
# DATABASE — SESSION & REDIS
# ──────────────────────────────────────────────────────────────────
def normalize_database_url(url: str) -> str:
    if not url: raise RuntimeError("DATABASE_URL is not set")
    url = url.strip()
    if url.startswith("postgres://"): url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"): url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url

engine = create_async_engine(normalize_database_url(cfg.DATABASE_URL), pool_size=5, max_overflow=5, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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

redis: Redis = Redis.from_url(cfg.REDIS_URL, decode_responses=True)

async def rate_limit_check(user_id: int) -> bool:
    key = f"rl:{user_id}"
    calls = await redis.incr(key)
    if calls == 1: await redis.expire(key, cfg.RATE_LIMIT_WINDOW)
    return calls <= cfg.RATE_LIMIT_CALLS

async def check_cooldown(key: str) -> bool:
    return bool(await redis.exists(f"cd:{key}"))

async def set_cooldown(key: str, seconds: int) -> None:
    await redis.setex(f"cd:{key}", seconds, "1")

# ──────────────────────────────────────────────────────────────────
# HELPERS & BUTTON FACTORY
# ──────────────────────────────────────────────────────────────────
def fmt_money(amount: int) -> str:
    return f"{amount:,}"

def xp_for_level(level: int) -> int:
    return int(1000 * (level ** 1.8))

def level_from_xp(xp: int) -> int:
    lvl = 1
    while xp_for_level(lvl + 1) <= xp: lvl += 1
    return lvl

def btn(text: str, data: str, style: str = "primary") -> InlineKeyboardButton:
    colors = {"primary": "🔵", "success": "🟢", "danger": "🔴", "neutral": "⚪️"}
    emoji = colors.get(style, "🔵")
    return InlineKeyboardButton(text=f"{emoji} {text}", callback_data=data)

def url_btn(text: str, url: str, style: str = "success") -> InlineKeyboardButton:
    colors = {"primary": "🔵", "success": "🟢", "danger": "🔴", "neutral": "⚪️"}
    emoji = colors.get(style, "🟢")
    return InlineKeyboardButton(text=f"{emoji} {text}", url=url)

def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))

async def get_or_none(session: AsyncSession, model, **kwargs):
    result = await session.execute(select(model).filter_by(**kwargs))
    return result.scalar_one_or_none()

async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower(): log.warning(f"safe_edit error: {e}")

# ──────────────────────────────────────────────────────────────────
# SERVICES
# ──────────────────────────────────────────────────────────────────
async def create_user(session: AsyncSession, tg_id: int, username: Optional[str], nickname: str) -> User:
    user = User(id=tg_id, username=username, nickname=nickname)
    session.add(user)
    await session.flush()
    session.add(Wallet(user_id=tg_id, cash=cfg.STARTING_CASH, energy=cfg.STARTING_ENERGY))
    session.add(UserStats(user_id=tg_id))
    session.add(Transaction(user_id=tg_id, type="GENESIS", amount=cfg.STARTING_CASH, source="system"))
    return user

async def get_energy(session: AsyncSession, user_id: int) -> int:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    if not wallet: return 0
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
    if current < amount: return False
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    wallet.energy = current - amount
    wallet.energy_updated_at = datetime.now(timezone.utc)
    return True

async def add_cash(session: AsyncSession, user_id: int, amount: int, source: str) -> None:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    wallet.cash += amount
    session.add(Transaction(user_id=user_id, type="CREDIT", amount=amount, source=source))
    stats = await get_or_none(session, UserStats, user_id=user_id)
    if stats and amount > 0: stats.fame += amount // 10000

async def deduct_cash(session: AsyncSession, user_id: int, amount: int, source: str) -> bool:
    wallet = await get_or_none(session, Wallet, user_id=user_id)
    if wallet.cash < amount: return False
    wallet.cash -= amount
    session.add(Transaction(user_id=user_id, type="DEBIT", amount=-amount, source=source))
    return True

async def add_xp(session: AsyncSession, user_id: int, amount: int) -> tuple[bool, int]:
    stats = await get_or_none(session, UserStats, user_id=user_id)
    old_level = stats.level
    stats.xp += amount
    new_level = level_from_xp(stats.xp)
    stats.level = new_level
    return new_level > old_level, new_level

async def add_to_inventory(session: AsyncSession, user_id: int, symbol: str, qty: int) -> None:
    inv = await get_or_none(session, Inventory, user_id=user_id, symbol=symbol)
    if inv: inv.quantity += qty
    else: session.add(Inventory(user_id=user_id, symbol=symbol, quantity=qty))

# ──────────────────────────────────────────────────────────────────
# SEED DATA
# ──────────────────────────────────────────────────────────────────
MARKET_SEED = [
    ("GOLD", "طلا", 8_000, "💛"), ("ENERGY", "سلول انرژی", 400, "⚡"),
    ("CRYSTAL", "کریستال", 18_000, "💎"), ("METAL", "فلز", 3_500, "⚙️"),
]

REGIONS = {
    "downtown": {"name": "مرکز شهر", "emoji": "🌆", "cost": 0, "danger": 0.05},
    "market": {"name": "بازار بزرگ", "emoji": "🏪", "cost": 1_000, "danger": 0.03},
    "industrial": {"name": "منطقه صنعتی", "emoji": "🏭", "cost": 2_000, "danger": 0.08},
    "forest": {"name": "جنگل تاریک", "emoji": "🌲", "cost": 4_000, "danger": 0.12},
    "unknown": {"name": "منطقه ناشناخته", "emoji": "❓", "cost": 15_000, "danger": 0.35},
}

HELP_SEED = [
    ("start", "START", "🚀 شروع بازی", "ECHO یک جهان آنلاین متنی است. هر Group یک City است.\nبرای شروع:\n1. Bot را به Group اضافه کن.\n2. داخل Group دستور /join را بزن.\n3. مأموریت اول را انجام بده."),
    ("city", "CITY", "🌆 شهر چیست؟", "City همان Group شماست. هر بازیکن در یک City زندگی می‌کند.\nCity دارای Population، Economy، Level و Ranking است.\nبا فعالیت بازیکنان، شهر رشد می‌کند."),
    ("economy", "ECONOMY", "💰 اقتصاد و پول", "پول در ECHO بی‌نهایت نیست. هر سیستم Money Source و Money Sink دارد.\nتصمیم‌گیری اقتصادی مهم‌ترین بخش پیشرفت است."),
    ("market", "MARKET", "📈 راهنمای بازار", "بازاری که در آن بازیکنان منابع را معامله می‌کنند.\nقیمت‌ها بر اساس Supply و Demand تغییر می‌کنند.\nریسک و سود در بازار تضمین شده نیست."),
    ("explore", "EXPLORATION", "🗺 اکتشاف", "Exploration برای کشف مناطق، منابع و اتفاق‌های خاص است.\nممکن است Reward، Rare Item یا Risk Event رخ دهد.\nتضمین سود ندارد!"),
]

async def seed_data() -> None:
    async with get_session() as session:
        for symbol, name, price, _ in MARKET_SEED:
            if not await get_or_none(session, MarketItem, symbol=symbol):
                session.add(MarketItem(symbol=symbol, name=name, price=price, base_price=price))
        for slug, cat, title, content in HELP_SEED:
            if not await get_or_none(session, HelpTopic, slug=slug):
                session.add(HelpTopic(slug=slug, category=cat, title=title, content=content))
    log.info("Data seeded.")

# ──────────────────────────────────────────────────────────────────
# MIDDLEWARES
# ──────────────────────────────────────────────────────────────────
class RateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and not await rate_limit_check(user.id):
            if hasattr(event, "answer"):
                try: await event.answer("⚠️ لطفاً کمی آرام‌تر!")
                except: pass
            return
        return await handler(event, data)

class CityContextMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict) -> Any:
        if hasattr(event, "chat") and event.chat and event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            async with get_session() as session:
                city = await get_or_none(session, City, telegram_chat_id=event.chat.id)
                data["city"] = city
        return await handler(event, data)

GAMEPLAY_COMMANDS = {"market", "explore", "missions", "business", "guild", "profile", "daily", "rank", "wallet", "inventory"}

class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict) -> Any:
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            cmd = event.text.split()[0].lower().split("@")[0][1:]
            city = data.get("city")
            user = data.get("event_from_user")
            if city and user and cmd in GAMEPLAY_COMMANDS:
                async with get_session() as session:
                    member = await get_or_none(session, CityMember, city_id=city.id, user_id=user.id)
                    if not member:
                        kb_join = kb([btn("ورود به شهر", f"join_city:{city.id}", "success")])
                        await event.answer("⛔ تو هنوز شهروند این City نیستی.\nبرای شروع بازی ابتدا وارد شهر شو.", reply_markup=kb_join)
                        return
        return await handler(event, data)

# ──────────────────────────────────────────────────────────────────
# ROUTERS
# ──────────────────────────────────────────────────────────────────
private_router = Router(name="private")
group_router = Router(name="group")
help_router = Router(name="help")
admin_router = Router(name="admin")

# ── Private Chat (Landing & Onboarding) ──────────────────────────
@private_router.message(CommandStart())
async def cmd_start_private(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    
    me = await bot.get_me()
    add_url = f"https://t.me/{me.username}?startgroup=echo_city"
    
    text = (
        "🌐 <b>ECHO</b>\n"
        "به دنیایی خوش آمدی که هر شهر آن توسط بازیکنان ساخته می‌شود.\n"
        "اینجا فقط یک بازی نیست.\nهر گروه Telegram می‌تواند یک City باشد.\n\n"
        "تو می‌توانی:\n"
        "💰 ثروت بسازی\n"
        "🏢 کسب‌وکار راه بیندازی\n"
        "📈 وارد بازار شوی\n"
        "🗺 جهان را کشف کنی\n"
        "🏆 در رتبه‌بندی شهر قرار بگیری\n\n"
        "اما زندگی واقعی ECHO داخل Group اتفاق می‌افتد."
    )
    
    kb_menu = kb(
        [url_btn("افزودن ECHO به گروه", add_url, "success")],
        [btn("راهنمای کامل", "help_main", "primary")],
        [btn("درباره ECHO", "about_echo", "primary"), btn("قوانین بازی", "rules_echo", "primary")]
    )
    await message.answer(text, reply_markup=kb_menu, parse_mode=ParseMode.HTML)

@private_router.message(Command("help"))
@private_router.callback_query(F.data == "help_main")
async def cmd_help_private(event: Message | CallbackQuery) -> None:
    async with get_session() as session:
        result = await session.execute(select(HelpTopic).order_by(HelpTopic.order))
        topics = result.scalars().all()
        
        buttons = [[btn(t.title, f"help_view:{t.slug}", "primary")] for t in topics]
        buttons.append([btn("بستن", "close_msg", "danger")])
        kb_help = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        text = "📚 <b>مرکز راهنمای ECHO</b>\nیکی از بخش‌ها را انتخاب کن:"
        
        if isinstance(event, CallbackQuery):
            await safe_edit(event.message, text, kb_help)
            await event.answer()
        else:
            await event.answer(text, reply_markup=kb_help, parse_mode=ParseMode.HTML)

@private_router.callback_query(F.data.startswith("help_view:"))
async def help_view(call: CallbackQuery) -> None:
    slug = call.data.split(":")[1]
    async with get_session() as session:
        topic = await get_or_none(session, HelpTopic, slug=slug)
        if not topic:
            await call.answer("موضوع یافت نشد.")
            return
            
        text = f"📖 <b>{topic.title}</b>\n\n{topic.content}"
        kb_back = kb([btn("بازگشت به راهنما", "help_main", "primary"), btn("بستن", "close_msg", "danger")])
        await safe_edit(call.message, text, kb_back)
        await call.answer()

@private_router.callback_query(F.data == "close_msg")
async def close_msg(call: CallbackQuery) -> None:
    try: await call.message.delete()
    except: pass
    await call.answer()

@private_router.message(Command("market"))
@private_router.message(Command("explore"))
@private_router.message(Command("profile"))
async def gameplay_in_private(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    add_url = f"https://t.me/{me.username}?startgroup=echo_city"
    kb = kb([url_btn("افزودن به گروه و شروع بازی", add_url, "success")])
    await message.answer(
        "📍 این بخش داخل <b>ECHO City</b> اجرا می‌شود.\n"
        "برای بازی باید ربات را به گروه اضافه کنی و شهروند یک شهر شوی.",
        reply_markup=kb, parse_mode=ParseMode.HTML
    )

# ── Group Chat (City Gameplay) ───────────────────────────────────
@group_router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER, IS_MEMBER))
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    chat = event.chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]: return
    
    async with get_session() as session:
        city = await get_or_none(session, City, telegram_chat_id=chat.id)
        if not city:
            city = City(telegram_chat_id=chat.id, name=chat.title, level=1)
            session.add(city)
            log.info(f"New City created: {chat.title} ({chat.id})")
            
    text = (
        f"🌆 <b>به ECHO CITY خوش آمدید!</b>\n\n"
        f"این Group اکنون یک شهر رسمی در ECHO است.\n"
        f"🏙 شهر: <b>{chat.title.upper()}</b>\n"
        f"👥 جمعیت: 0\n"
        f"⭐ Level: 1\n\n"
        f"اولین کاری که باید انجام دهید:\n"
        f"هر بازیکن برای ورود به City باید <code>/join</code> را بزند."
    )
    kb_join = kb([btn("راهنمای بازی", "help_main", "primary")])
    await bot.send_message(chat.id, text, reply_markup=kb_join, parse_mode=ParseMode.HTML)

@group_router.message(Command("join"))
async def cmd_join(message: Message, city: Optional[City]) -> None:
    if not city:
        await message.answer("⚠️ این گروه هنوز به عنوان یک City ثبت نشده است.")
        return
        
    user = message.from_user
    async with get_session() as session:
        db_user = await get_or_none(session, User, id=user.id)
        if not db_user:
            nick = user.first_name[:30] or f"User_{user.id}"
            db_user = await create_user(session, user.id, user.username, nick)
            
        member = await get_or_none(session, CityMember, city_id=city.id, user_id=user.id)
        if member:
            await message.answer(f"🏙 تو قبلاً شهروند <b>{city.name}</b> هستی!", parse_mode=ParseMode.HTML)
            return
            
        session.add(CityMember(city_id=city.id, user_id=user.id, role="citizen"))
        city.population += 1
        
    text = (
        f"🌆 <b>خوش آمدی به {city.name.upper()}</b>\n"
        f"هویت ECHO تو ایجاد شد.\n"
        f"شروع با:\n"
        f"💰 {fmt_money(cfg.STARTING_CASH)}$\n"
        f"⚡ {cfg.STARTING_ENERGY} Energy\n"
        f"🎯 Starter Mission"
    )
    kb_start = kb([btn("شروع مأموریت", "mission_start", "success"), btn("راهنمای ECHO", "help_main", "primary")])
    await message.answer(text, reply_markup=kb_start, parse_mode=ParseMode.HTML)

@group_router.message(Command("city"))
async def cmd_city(message: Message, city: Optional[City], bot: Bot) -> None:
    if not city: return
    
    async with get_session() as session:
        text = (
            f"🌆 <b>ECHO CITY</b>\n"
            f"<b>{city.name.upper()}</b>\n\n"
            f"⭐ Level {city.level}\n"
            f"👥 Population {city.population}\n"
            f"💰 Economy: {fmt_money(city.treasury_cash)}$\n"
            f"🔥 Activity: HIGH\n"
        )
        kb_city = kb(
            [btn("پروفایل من", "my_profile", "primary"), btn("بازار", "city_market", "primary")],
            [btn("اکتشاف", "city_explore", "primary"), btn("مأموریت‌ها", "city_missions", "primary")],
            [btn("رتبه‌بندی", "city_rank", "primary"), btn("راهنما", "help_main", "neutral")]
        )
        
        # Persistent Dashboard Logic
        dashboard = await get_or_none(session, CityDashboard, city_id=city.id, dashboard_type="main")
        if dashboard and dashboard.message_id:
            try:
                await bot.edit_message_text(text, chat_id=message.chat.id, message_id=dashboard.message_id, reply_markup=kb_city, parse_mode=ParseMode.HTML)
                await message.delete() # Clean up the command message
                return
            except TelegramBadRequest:
                pass # Fallback to send new
                
        msg = await message.answer(text, reply_markup=kb_city, parse_mode=ParseMode.HTML)
        if not dashboard:
            session.add(CityDashboard(city_id=city.id, dashboard_type="main", message_id=msg.message_id, chat_id=message.chat.id))
        else:
            dashboard.message_id = msg.message_id
            dashboard.chat_id = message.chat.id

@group_router.message(Command("profile"))
@group_router.callback_query(F.data == "my_profile")
async def cmd_profile(event: Message | CallbackQuery, city: Optional[City]) -> None:
    user_id = event.from_user.id
    async with get_session() as session:
        user = await get_or_none(session, User, id=user_id)
        stats = await get_or_none(session, UserStats, user_id=user_id)
        wallet = await get_or_none(session, Wallet, user_id=user_id)
        member = await get_or_none(session, CityMember, city_id=city.id, user_id=user_id) if city else None
        
        if not user:
            msg = "❌ ابتدا /join را بزن."
            if isinstance(event, CallbackQuery): await event.answer(msg, show_alert=True)
            else: await event.answer(msg)
            return
            
        energy = await get_energy(session, user_id)
        city_rank = f"#{member.contribution}" if member else "—"
        
        text = (
            f"👤 <b>{user.nickname}</b>\n"
            f"🏙 City: <b>{city.name.upper() if city else '—'}</b>\n"
            f"⭐ Level: <b>{stats.level}</b>\n"
            f"💰 Cash: <b>{fmt_money(wallet.cash)}$</b>\n"
            f"⚡ Energy: <b>{energy}/{cfg.MAX_ENERGY}</b>\n"
            f"🌟 Fame: <b>{stats.fame}</b>\n"
            f"🏆 City Rank: <b>{city_rank}</b>"
        )
        kb_prof = kb([btn("بازگشت", "back_city", "primary")])
        
        if isinstance(event, CallbackQuery):
            await safe_edit(event.message, text, kb_prof)
            await event.answer()
        else:
            await event.answer(text, reply_markup=kb_prof, parse_mode=ParseMode.HTML)

@group_router.message(Command("explore"))
@group_router.callback_query(F.data == "city_explore")
async def cmd_explore(event: Message | CallbackQuery, city: Optional[City]) -> None:
    lines = []
    buttons = []
    for k, r in REGIONS.items():
        cost = f"{fmt_money(r['cost'])}$" if r['cost'] else "رایگان"
        lines.append(f"{r['emoji']} <b>{r['name']}</b> — {cost}")
        buttons.append([btn(f"{r['emoji']} {r['name']}", f"go_explore:{k}", "success")])
        
    text = "🗺 <b>اکتشاف</b>\n\n" + "\n".join(lines) + "\n\n⚡ هر اکتشاف انرژی مصرف می‌کند."
    buttons.append([btn("بازگشت", "back_city", "danger")])
    kb_exp = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, kb_exp)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb_exp, parse_mode=ParseMode.HTML)

@group_router.callback_query(F.data.startswith("go_explore:"))
async def do_explore(call: CallbackQuery, city: Optional[City]) -> None:
    region_key = call.data.split(":")[1]
    cd_key = f"explore:{call.from_user.id}"
    
    if await check_cooldown(cd_key):
        await call.answer("⏳ برای اکتشاف بعدی باید کمی صبر کنی.", show_alert=True)
        return
        
    region = REGIONS[region_key]
    async with get_session() as session:
        wallet = await get_or_none(session, Wallet, user_id=call.from_user.id)
        if wallet.cash < region["cost"]:
            await call.answer(f"❌ به {fmt_money(region['cost'])}$ نیاز داری.", show_alert=True)
            return
        if not await spend_energy(session, call.from_user.id, cfg.BASE_EXPLORE_COST_ENERGY):
            await call.answer("❌ انرژی کافی نداری.", show_alert=True)
            return
            
        await deduct_cash(session, call.from_user.id, region["cost"], "travel")
        await add_xp(session, call.from_user.id, 500)
        
        # Roll outcome
        roll = random.random()
        if roll < 0.3:
            reward = random.randint(2_000, 15_000)
            await add_cash(session, call.from_user.id, reward, "explore")
            msg = f"💰 در {region['name']} <b>{fmt_money(reward)}$</b> پیدا کردی!"
        elif roll < 0.5:
            sym = random.choice([s for s, *_ in MARKET_SEED])
            qty = random.randint(5, 20)
            await add_to_inventory(session, call.from_user.id, sym, qty)
            msg = f"📦 <b>{qty}x {sym}</b> پیدا کردی!"
        else:
            msg = "🌫 چیزی پیدا نکردی. منطقه آرام بود."
            
        if city:
            member = await get_or_none(session, CityMember, city_id=city.id, user_id=call.from_user.id)
            if member: member.contribution += 10
            
    await set_cooldown(cd_key, 60) # 1 min cooldown
    kb_back = kb([btn("اکتشاف مجدد", "city_explore", "success"), btn("بازگشت", "back_city", "primary")])
    await safe_edit(call.message, msg, kb_back)
    await call.answer()

@group_router.callback_query(F.data == "back_city")
async def back_city(call: CallbackQuery, city: Optional[City], bot: Bot) -> None:
    # Simulate /city dashboard
    if not city:
        await call.answer()
        return
    async with get_session() as session:
        text = (
            f"🌆 <b>ECHO CITY</b>\n<b>{city.name.upper()}</b>\n\n"
            f"⭐ Level {city.level}\n👥 Population {city.population}\n"
        )
        kb_city = kb(
            [btn("پروفایل من", "my_profile", "primary"), btn("بازار", "city_market", "primary")],
            [btn("اکتشاف", "city_explore", "primary"), btn("مأموریت‌ها", "city_missions", "primary")],
            [btn("رتبه‌بندی", "city_rank", "primary"), btn("راهنما", "help_main", "neutral")]
        )
        await safe_edit(call.message, text, kb_city)
        await call.answer()

# ── Admin Router ─────────────────────────────────────────────────
@admin_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if message.from_user.id not in cfg.ADMIN_IDS: return
    await message.answer("🔧 <b>پنل مدیریت ECHO</b>\nسیستم فعال است.", parse_mode=ParseMode.HTML)

# ──────────────────────────────────────────────────────────────────
# ERROR HANDLER
# ──────────────────────────────────────────────────────────────────
@private_router.errors()
@group_router.errors()
async def global_error_handler(event: ErrorEvent) -> None:
    log.exception(f"Unhandled error: {event.exception}", exc_info=event.exception)

# ──────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────
async def main() -> None:
    if not cfg.BOT_TOKEN:
        log.critical("BOT_TOKEN is not set!")
        return
        
    bot = Bot(token=cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = RedisStorage(redis=redis)
    dp = Dispatcher(storage=storage)
    
    # Middlewares
    for router in [private_router, group_router, help_router, admin_router]:
        router.message.middleware(RateLimitMiddleware())
        router.callback_query.middleware(RateLimitMiddleware())
        
    group_router.message.middleware(CityContextMiddleware())
    group_router.callback_query.middleware(CityContextMiddleware())
    group_router.message.middleware(MembershipMiddleware())
    
    # Include Routers
    dp.include_router(admin_router)
    dp.include_router(group_router)
    dp.include_router(help_router)
    dp.include_router(private_router)
    
    await init_db()
    await seed_data()
    
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start()
    
    log.info("ECHO is live. Group-First Architecture active.")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await redis.aclose()

if __name__ == "__main__":
    asyncio.run(main())