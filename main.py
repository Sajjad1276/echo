from __future__ import annotations

# ================================================================
# ECHO — SINGLE FILE TELEGRAM GAME
# Group-First Multiplayer Text-Based Social World
#
# Every Group is a City.
# Play where your people are.
# ================================================================

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION

from pydantic_settings import BaseSettings, SettingsConfigDict

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ================================================================
# LOGGING
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)

log = logging.getLogger("echo")


# ================================================================
# CONFIG
# ================================================================

class Config(BaseSettings):
    BOT_TOKEN: str
    DATABASE_URL: str

    STARTING_CASH: int = 10_000
    STARTING_ENERGY: int = 100

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


cfg = Config()


# ================================================================
# DATABASE BASE
# ================================================================

class Base(AsyncAttrs, DeclarativeBase):
    pass


# ================================================================
# DATABASE URL
# ================================================================

def normalize_database_url(url: str) -> str:
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing."
        )

    url = url.strip()

    if url.startswith("postgres://"):
        return (
            "postgresql+asyncpg://"
            + url[len("postgres://"):]
        )

    if url.startswith("postgresql://"):
        return (
            "postgresql+asyncpg://"
            + url[len("postgresql://"):]
        )

    if url.startswith("postgresql+psycopg2://"):
        return (
            "postgresql+asyncpg://"
            + url[len("postgresql+psycopg2://"):]
        )

    if url.startswith("postgresql+asyncpg://"):
        return url

    raise RuntimeError(
        "Unsupported DATABASE_URL scheme."
    )


DATABASE_URL = normalize_database_url(
    cfg.DATABASE_URL
)

log.info(
    "Database driver: %s",
    DATABASE_URL.split("://", 1)[0],
)


# ================================================================
# DATABASE ENGINE
# ================================================================

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=3,
    max_overflow=2,
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


# ================================================================
# GLOBAL USER MODELS
# ================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    nickname: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UserStats(Base):
    __tablename__ = "user_stats"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
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

    reputation: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    energy: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )

    daily_streak: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        unique=True,
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


# ================================================================
# CITY MODELS
# ================================================================

class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    city_code: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    custom_name: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    username: Mapped[Optional[str]] = mapped_column(
        String(64),
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

    owner_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CityMember(Base):
    __tablename__ = "city_members"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cities.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(16),
        default="citizen",
        nullable=False,
    )

    contribution: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "city_id",
            "user_id",
            name="uq_city_member",
        ),
    )


class CityDashboard(Base):
    __tablename__ = "city_dashboard"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cities.id"),
        unique=True,
        nullable=False,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class HelpView(Base):
    __tablename__ = "help_views"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )

    city_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    topic: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(16),
        default="private",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ================================================================
# DATABASE HELPERS
# ================================================================

async def get_or_none(
    session: AsyncSession,
    model,
    **filters,
):
    result = await session.execute(
        select(model).filter_by(**filters)
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str],
    nickname: str,
) -> User:

    user = User(
        id=user_id,
        username=username,
        nickname=nickname,
    )

    session.add(user)

    stats = UserStats(
        user_id=user_id,
        level=1,
        xp=0,
        fame=0,
        reputation=0,
        energy=cfg.STARTING_ENERGY,
        daily_streak=0,
    )

    wallet = Wallet(
        user_id=user_id,
        cash=cfg.STARTING_CASH,
        bank=0,
    )

    session.add(stats)
    session.add(wallet)

    await session.flush()

    return user


async def touch_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str] = None,
    nickname: Optional[str] = None,
) -> Optional[User]:

    user = await get_or_none(
        session,
        User,
        id=user_id,
    )

    if not user:
        return None

    if username is not None:
        user.username = username

    if nickname:
        user.nickname = nickname[:64]

    user.last_active_at = (
        datetime.now(timezone.utc)
    )

    return user


# ================================================================
# CITY HELPERS
# ================================================================

def make_city_code() -> str:
    return f"EC-{random.randint(10000, 99999)}"


async def get_city_by_chat(
    session: AsyncSession,
    chat_id: int,
) -> Optional[City]:

    result = await session.execute(
        select(City).where(
            City.chat_id == chat_id
        )
    )

    return result.scalar_one_or_none()


async def get_or_create_city(
    session: AsyncSession,
    chat_id: int,
    title: str,
    username: Optional[str],
    owner_id: Optional[int],
) -> City:

    city = await get_city_by_chat(
        session,
        chat_id,
    )

    if city:
        if not city.is_active:
            city.is_active = True

        if (
            not city.custom_name
            and title
            and city.name != title
        ):
            city.name = title

        city.username = username

        return city

    city = City(
        chat_id=chat_id,
        city_code=make_city_code(),
        name=title or "ECHO City",
        username=username,
        owner_id=owner_id,
    )

    session.add(city)

    await session.flush()

    log.info(
        "City created: %s | %s",
        city.city_code,
        city.chat_id,
    )

    return city


async def deactivate_city(
    session: AsyncSession,
    chat_id: int,
) -> None:

    await session.execute(
        update(City)
        .where(City.chat_id == chat_id)
        .values(is_active=False)
    )


async def get_membership(
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


async def join_city(
    session: AsyncSession,
    city: City,
    tg_user,
) -> tuple[CityMember, bool]:

    existing = await get_membership(
        session,
        city.id,
        tg_user.id,
    )

    if existing:
        await touch_user(
            session,
            tg_user.id,
            tg_user.username,
            tg_user.first_name,
        )
        return existing, False

    user = await get_or_none(
        session,
        User,
        id=tg_user.id,
    )

    if not user:
        user = await create_user(
            session,
            tg_user.id,
            tg_user.username,
            (
                tg_user.first_name
                or tg_user.username
                or f"Player{tg_user.id}"
            )[:64],
        )
    else:
        await touch_user(
            session,
            tg_user.id,
            tg_user.username,
            tg_user.first_name,
        )

    member = CityMember(
        city_id=city.id,
        user_id=tg_user.id,
        role="citizen",
    )

    session.add(member)

    await session.flush()

    return member, True


async def city_population(
    session: AsyncSession,
    city_id: int,
) -> int:

    result = await session.execute(
        select(
            func.count(CityMember.id)
        ).where(
            CityMember.city_id == city_id
        )
    )

    return int(
        result.scalar_one() or 0
    )


async def city_ranking(
    session: AsyncSession,
    city_id: int,
    limit: int = 10,
):

    result = await session.execute(
        select(
            User,
            UserStats,
            Wallet,
        )
        .join(
            CityMember,
            CityMember.user_id == User.id,
        )
        .join(
            UserStats,
            UserStats.user_id == User.id,
        )
        .join(
            Wallet,
            Wallet.user_id == User.id,
        )
        .where(
            CityMember.city_id == city_id
        )
        .order_by(
            UserStats.level.desc(),
            UserStats.xp.desc(),
            Wallet.cash.desc(),
        )
        .limit(limit)
    )

    return result.all()


async def record_help_view(
    session: AsyncSession,
    user_id: int,
    city_id: Optional[int],
    topic: str,
    source: str,
) -> None:

    session.add(
        HelpView(
            user_id=user_id,
            city_id=city_id,
            topic=topic,
            source=source,
        )
    )


# ================================================================
# BUTTON FACTORY
# ================================================================
#
# Telegram / Aiogram 3.30 supports:
# primary = blue
# success = green
# danger  = red
#
# These are real button styles, not emoji simulations.
# ================================================================

class Buttons:

    @staticmethod
    def primary(
        text: str,
        callback_data: str,
    ) -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style="primary",
        )

    @staticmethod
    def success(
        text: str,
        callback_data: str,
    ) -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style="success",
        )

    @staticmethod
    def danger(
        text: str,
        callback_data: str,
    ) -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style="danger",
        )

    @staticmethod
    def url(
        text: str,
        url: str,
        style: str = "success",
    ) -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text=text,
            url=url,
            style=style,
        )

    @staticmethod
    def back(
        callback_data: str = "help:root",
    ) -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text="↩️ بازگشت",
            callback_data=callback_data,
            style="primary",
        )

    @staticmethod
    def close() -> InlineKeyboardButton:

        return InlineKeyboardButton(
            text="بستن",
            callback_data="close",
            style="danger",
        )

    @staticmethod
    def build(
        *rows,
    ) -> InlineKeyboardMarkup:

        return InlineKeyboardMarkup(
            inline_keyboard=list(rows)
        )


B = Buttons


# ================================================================
# SAFE MESSAGE HELPERS
# ================================================================

async def safe_edit_or_send(
    message: Message,
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:

    try:
        await message.edit_text(
            text,
            reply_markup=markup,
        )
        return message

    except TelegramBadRequest as exc:

        if "message is not modified" in str(exc).lower():
            return message

        return await message.answer(
            text,
            reply_markup=markup,
        )


# ================================================================
# ROUTERS
# ================================================================

private_router = Router(
    name="echo_private"
)

group_router = Router(
    name="echo_group"
)

help_router = Router(
    name="echo_help"
)


# ================================================================
# MIDDLEWARE
# ================================================================

class CityContextMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:

        chat = None

        if isinstance(event, Message):
            chat = event.chat

        elif isinstance(event, CallbackQuery):
            if event.message:
                chat = event.message.chat

        elif isinstance(event, ChatMemberUpdated):
            chat = event.chat

        is_group = bool(
            chat
            and chat.type in {
                ChatType.GROUP,
                ChatType.SUPERGROUP,
            }
        )

        data["is_group"] = is_group
        data["city"] = None

        if is_group and chat:
            async with get_session() as session:
                data["city"] = (
                    await get_city_by_chat(
                        session,
                        chat.id,
                    )
                )

        return await handler(
            event,
            data,
        )


# ================================================================
# BOT USERNAME
# ================================================================

async def get_bot_username(
    bot: Bot,
) -> str:

    me = await bot.get_me()

    if not me.username:
        raise RuntimeError(
            "Bot does not have a username."
        )

    return me.username


# ================================================================
# PRIVATE LANDING
# ================================================================

def private_landing_text() -> str:

    return (
        "🌐 <b>ECHO</b>\n\n"
        "به جهانی خوش آمدی که هر گروه Telegram "
        "می‌تواند یک شهر زنده باشد.\n\n"

        "در ECHO می‌توانی:\n"
        "💰 ثروت بسازی\n"
        "🏢 کسب‌وکار ایجاد کنی\n"
        "📈 در بازار فعالیت کنی\n"
        "🗺 مناطق جدید را کشف کنی\n"
        "🏆 در رتبه‌بندی رقابت کنی\n"
        "👥 عضو Guild شوی\n"
        "🌪 در Eventها شرکت کنی\n\n"

        "<b>اما بازی واقعی داخل Group انجام می‌شود.</b>\n\n"
        "هر Group = یک ECHO City"
    )


@private_router.message(
    CommandStart()
)
async def cmd_start(
    message: Message,
    bot: Bot,
):

    username = await get_bot_username(
        bot
    )

    add_url = (
        f"https://t.me/{username}"
        "?startgroup=echo"
    )

    markup = B.build(
        [
            B.url(
                "➕ افزودن ECHO به گروه",
                add_url,
                "success",
            )
        ],
        [
            B.primary(
                "📖 چگونه بازی کنم؟",
                "help:start",
            ),
            B.primary(
                "📚 راهنمای کامل",
                "help:root",
            ),
        ],
        [
            B.primary(
                "📜 قوانین",
                "help:rules",
            ),
            B.primary(
                "ℹ️ درباره ECHO",
                "help:about",
            ),
        ],
    )

    await message.answer(
        private_landing_text(),
        reply_markup=markup,
    )


# ================================================================
# PRIVATE GAMEPLAY REDIRECT
# ================================================================

PRIVATE_GAMEPLAY = {
    "market",
    "explore",
    "missions",
    "business",
    "guild",
    "world",
    "profile",
    "city",
    "rank",
}


def private_game_redirect_text(
    command: str,
) -> str:

    return (
        "📍 <b>این بخش داخل ECHO City اجرا می‌شود.</b>\n\n"
        f"دستور <code>/{command}</code> باید داخل Group اجرا شود.\n\n"
        "1. ECHO را به Group اضافه کن.\n"
        "2. داخل Group دستور <code>/join</code> را بزن.\n"
        "3. سپس بخش موردنظر را همان‌جا باز کن."
    )


@private_router.message(
    F.text.startswith("/")
)
async def private_command_redirect(
    message: Message,
    bot: Bot,
):

    if not message.text:
        return

    command_name = (
        message.text
        .split()[0]
        .split("@")[0]
        .lstrip("/")
        .lower()
    )

    if command_name not in PRIVATE_GAMEPLAY:
        return

    username = await get_bot_username(
        bot
    )

    add_url = (
        f"https://t.me/{username}"
        "?startgroup=echo"
    )

    markup = B.build(
        [
            B.url(
                "➕ افزودن به گروه",
                add_url,
                "success",
            )
        ],
        [
            B.primary(
                "📖 راهنمای City",
                "help:city",
            )
        ],
    )

    await message.answer(
        private_game_redirect_text(
            command_name
        ),
        reply_markup=markup,
    )


# ================================================================
# BOT ADDED TO GROUP
# ================================================================

@group_router.my_chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=JOIN_TRANSITION
    )
)
async def on_bot_added_to_group(
    event: ChatMemberUpdated,
    bot: Bot,
):

    chat = event.chat

    async with get_session() as session:

        city = await get_or_create_city(
            session,
            chat.id,
            chat.title or "ECHO City",
            chat.username,
            (
                event.from_user.id
                if event.from_user
                else None
            ),
        )

        city_name = city.name

    text = (
        "🌆 <b>ECHO CITY فعال شد!</b>\n\n"
        f"🏙 شهر: <b>{city_name}</b>\n"
        "👥 جمعیت: 0\n"
        "⭐ سطح شهر: 1\n"
        "💰 خزانه: $0\n\n"
        "<b>اولین قدم:</b>\n"
        "دستور <code>/join</code> را بزن.\n\n"
        "هر کسی که عضو شود، شهروند این City خواهد بود."
    )

    markup = B.build(
        [
            B.success(
                "🚀 ورود به City",
                "city:join",
            )
        ],
        [
            B.primary(
                "📖 راهنمای شروع",
                "help:start",
            ),
            B.primary(
                "🏆 رتبه‌بندی",
                "city:rank",
            ),
        ],
    )

    try:

        await bot.send_message(
            chat.id,
            text,
            reply_markup=markup,
        )

    except TelegramForbiddenError:

        log.warning(
            "Cannot send message to chat %s",
            chat.id,
        )


# ================================================================
# BOT REMOVED
# ================================================================

@group_router.my_chat_member(
    F.new_chat_member.status.in_(
        {"left", "kicked"}
    )
)
async def on_bot_removed_from_group(
    event: ChatMemberUpdated,
):

    async with get_session() as session:
        await deactivate_city(
            session,
            event.chat.id,
        )

    log.info(
        "City deactivated: %s",
        event.chat.id,
    )


# ================================================================
# JOIN CITY
# ================================================================

async def perform_join(
    city: Optional[City],
    tg_user,
):

    if not city:
        return (
            "⚠️ این Group هنوز به‌عنوان City ثبت نشده.",
            B.build(
                [
                    B.primary(
                        "📖 راهنما",
                        "help:city",
                    )
                ]
            ),
        )

    async with get_session() as session:

        member, created = await join_city(
            session,
            city,
            tg_user,
        )

        if created:
            text = (
                f"🌆 <b>به {city.name} خوش آمدی!</b>\n\n"

                "هویت ECHO تو ساخته شد.\n\n"

                f"💰 موجودی اولیه: "
                f"${cfg.STARTING_CASH:,}\n"

                f"⚡ انرژی اولیه: "
                f"{cfg.STARTING_ENERGY}\n\n"

                "🎯 قدم بعدی: اولین مأموریت."
            )

            markup = B.build(
                [
                    B.success(
                        "🎯 شروع مأموریت",
                        "help:start",
                    )
                ],
                [
                    B.primary(
                        "📚 راهنمای کامل",
                        "help:root",
                    )
                ],
            )

        else:
            text = (
                "✅ تو قبلاً شهروند این City هستی."
            )

            markup = B.build(
                [
                    B.primary(
                        "👤 پروفایل",
                        "city:profile",
                    ),
                    B.primary(
                        "📖 راهنما",
                        "help:root",
                    ),
                ]
            )

    return text, markup


@group_router.message(
    Command("join")
)
async def cmd_join(
    message: Message,
    city: Optional[City] = None,
):

    text, markup = await perform_join(
        city,
        message.from_user,
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.callback_query(
    F.data == "city:join"
)
async def cb_join(
    call: CallbackQuery,
    city: Optional[City] = None,
):

    text, markup = await perform_join(
        city,
        call.from_user,
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            text,
            markup,
        )

    await call.answer()


# ================================================================
# CITY DASHBOARD
# ================================================================

async def build_city_dashboard(
    city: City,
) -> tuple[
    str,
    InlineKeyboardMarkup,
]:

    async with get_session() as session:

        population = await city_population(
            session,
            city.id,
        )

    text = (
        f"🌆 <b>{city.name}</b>\n\n"
        f"⭐ سطح شهر: {city.level}\n"
        f"👥 جمعیت: {population}\n"
        f"💰 خزانه: ${city.treasury:,}\n"
        f"🏷 کد شهر: <code>{city.city_code}</code>\n\n"
        "این شهر توسط بازیکنان همین Group ساخته می‌شود."
    )

    markup = B.build(
        [
            B.success(
                "🎮 بازی",
                "city:play",
            ),
            B.primary(
                "📖 راهنما",
                "help:root",
            ),
        ],
        [
            B.primary(
                "📈 بازار",
                "help:market",
            ),
            B.primary(
                "🗺 اکتشاف",
                "help:exploration",
            ),
        ],
        [
            B.primary(
                "🎯 مأموریت",
                "help:start",
            ),
            B.primary(
                "👥 Guild",
                "help:city",
            ),
        ],
        [
            B.primary(
                "🏆 رتبه‌بندی",
                "city:rank",
            )
        ],
    )

    return text, markup


@group_router.message(
    Command("city")
)
async def cmd_city(
    message: Message,
    city: Optional[City] = None,
):

    if not city:
        await message.answer(
            "⚠️ این Group هنوز ECHO City نشده است."
        )
        return

    text, markup = await build_city_dashboard(
        city
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.callback_query(
    F.data == "city:dashboard"
)
async def cb_city_dashboard(
    call: CallbackQuery,
    city: Optional[City] = None,
):

    if not city:
        await call.answer()
        return

    text, markup = await build_city_dashboard(
        city
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            text,
            markup,
        )

    await call.answer()


# ================================================================
# PROFILE
# ================================================================

async def build_profile(
    user_id: int,
    city: Optional[City],
):

    async with get_session() as session:

        user = await get_or_none(
            session,
            User,
            id=user_id,
        )

        if not user:
            return (
                "⛔ ابتدا داخل City دستور /join را بزن.",
                B.build(
                    [
                        B.success(
                            "🚀 ورود به City",
                            "city:join",
                        )
                    ]
                ),
            )

        stats = await get_or_none(
            session,
            UserStats,
            user_id=user_id,
        )

        wallet = await get_or_none(
            session,
            Wallet,
            user_id=user_id,
        )

        membership = None

        if city:
            membership = await get_membership(
                session,
                city.id,
                user_id,
            )

    if not stats or not wallet:
        return (
            "⚠️ اطلاعات پروفایل کامل نیست.",
            None,
        )

    city_rank = "—"

    if city and membership:
        async with get_session() as session:

            rows = await city_ranking(
                session,
                city.id,
                limit=100,
            )

            for index, row in enumerate(rows, start=1):
                if row[0].id == user_id:
                    city_rank = str(index)
                    break

    name = (
        f"@{user.username}"
        if user.username
        else user.nickname
    )

    city_name = (
        city.name
        if city and membership
        else "بدون City"
    )

    text = (
        "👤 <b>پروفایل ECHO</b>\n\n"
        f"نام: <b>{name}</b>\n"
        f"🌆 City: <b>{city_name}</b>\n\n"
        f"⭐ Level: {stats.level}\n"
        f"✨ XP: {stats.xp:,}\n"
        f"💰 Cash: ${wallet.cash:,}\n"
        f"🏦 Bank: ${wallet.bank:,}\n"
        f"⚡ Energy: {stats.energy}\n"
        f"🌟 Fame: {stats.fame:,}\n"
        f"⭐ Reputation: {stats.reputation}\n"
        f"🏆 City Rank: #{city_rank}\n"
        f"🔥 Streak: {stats.daily_streak} روز"
    )

    markup = B.build(
        [
            B.primary(
                "📖 راهنما",
                "help:root",
            ),
            B.primary(
                "🏆 رتبه‌بندی",
                "city:rank",
            ),
        ]
    )

    return text, markup


@group_router.message(
    Command("profile")
)
async def cmd_profile(
    message: Message,
    city: Optional[City] = None,
):

    text, markup = await build_profile(
        message.from_user.id,
        city,
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.callback_query(
    F.data == "city:profile"
)
async def cb_profile(
    call: CallbackQuery,
    city: Optional[City] = None,
):

    text, markup = await build_profile(
        call.from_user.id,
        city,
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            text,
            markup,
        )

    await call.answer()


# ================================================================
# RANKING
# ================================================================

@group_router.message(
    Command("rank")
)
async def cmd_rank(
    message: Message,
    city: Optional[City] = None,
):

    if not city:
        await message.answer(
            "⚠️ این Group هنوز City نیست."
        )
        return

    async with get_session() as session:

        rows = await city_ranking(
            session,
            city.id,
            limit=10,
        )

    if not rows:

        text = (
            "🏆 <b>CITY RANKING</b>\n\n"
            "هنوز بازیکنی در رتبه‌بندی وجود ندارد."
        )

    else:

        medals = [
            "🥇",
            "🥈",
            "🥉",
        ]

        lines = []

        for index, (
            user,
            stats,
            wallet,
        ) in enumerate(rows):

            medal = (
                medals[index]
                if index < 3
                else f"{index + 1}."
            )

            display_name = (
                f"@{user.username}"
                if user.username
                else user.nickname
            )

            total = (
                wallet.cash
                + wallet.bank
            )

            lines.append(
                f"{medal} "
                f"<b>{display_name}</b> "
                f"— Lv.{stats.level} "
                f"— ${total:,}"
            )

        text = (
            "🏆 <b>CITY RANKING</b>\n\n"
            + "\n".join(lines)
        )

    await message.answer(
        text,
    )


@group_router.callback_query(
    F.data == "city:rank"
)
async def cb_rank(
    call: CallbackQuery,
    city: Optional[City] = None,
):

    if not city:
        await call.answer()
        return

    async with get_session() as session:

        rows = await city_ranking(
            session,
            city.id,
            limit=10,
        )

    medals = [
        "🥇",
        "🥈",
        "🥉",
    ]

    lines = []

    for index, (
        user,
        stats,
        wallet,
    ) in enumerate(rows):

        medal = (
            medals[index]
            if index < 3
            else f"{index + 1}."
        )

        display_name = (
            f"@{user.username}"
            if user.username
            else user.nickname
        )

        total = (
            wallet.cash
            + wallet.bank
        )

        lines.append(
            f"{medal} "
            f"<b>{display_name}</b> "
            f"— Lv.{stats.level} "
            f"— ${total:,}"
        )

    text = (
        "🏆 <b>CITY RANKING</b>\n\n"
        + (
            "\n".join(lines)
            if lines
            else "هنوز رتبه‌ای وجود ندارد."
        )
    )

    markup = B.build(
        [
            B.back(
                "city:dashboard"
            )
        ]
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            text,
            markup,
        )

    await call.answer()


# ================================================================
# HELP CENTER
# ================================================================

HELP_TOPICS = {

    "start": {
        "title": "🚀 شروع ECHO",
        "body": (
            "<b>ECHO چیست؟</b>\n"
            "ECHO یک بازی چندنفره متنی است که در آن "
            "هر Group یک City می‌شود.\n\n"

            "<b>چطور شروع کنم؟</b>\n"
            "1. Bot را به Group اضافه کن.\n"
            "2. /join را بزن.\n"
            "3. /city را باز کن.\n"
            "4. Profile خودت را بررسی کن.\n"
            "5. با مأموریت‌ها و فعالیت‌های بازی پیشرفت کن.\n\n"

            "<b>نکته:</b>\n"
            "بازی اصلی داخل Group انجام می‌شود."
        ),
    },

    "city": {
        "title": "🌆 ECHO CITY",
        "body": (
            "هر Group یک City مستقل است.\n\n"

            "City دارای:\n"
            "👥 جمعیت\n"
            "⭐ Level\n"
            "💰 خزانه\n"
            "🏆 رتبه‌بندی\n"
            "👥 Guildها\n"
            "🌪 Eventها\n\n"

            "هرچه اعضای City فعال‌تر باشند، "
            "شهر ظرفیت بیشتری برای رشد خواهد داشت.\n\n"

            "<b>دستور:</b> /city"
        ),
    },

    "profile": {
        "title": "👤 پروفایل",
        "body": (
            "Profile مرکز اطلاعات شخصیت تو است.\n\n"

            "در آن می‌توانی ببینی:\n"
            "⭐ Level\n"
            "✨ XP\n"
            "💰 Cash\n"
            "🏦 Bank\n"
            "⚡ Energy\n"
            "🌟 Fame\n"
            "⭐ Reputation\n"
            "🏆 City Rank\n"
            "🔥 Daily Streak\n\n"

            "<b>دستور:</b> /profile"
        ),
    },

    "market": {
        "title": "📈 بازار",
        "body": (
            "Market یکی از بخش‌های اقتصادی ECHO است.\n\n"

            "قیمت‌ها ثابت نیستند و در آینده بر اساس "
            "Supply و Demand تغییر خواهند کرد.\n\n"

            "<b>نکته:</b>\n"
            "خرید یا فروش همیشه به معنی سود نیست.\n"
            "تصمیم اقتصادی مهم است.\n\n"

            "<b>دستور آینده:</b> /market"
        ),
    },

    "exploration": {
        "title": "🗺 اکتشاف",
        "body": (
            "Exploration برای کشف جهان ECHO است.\n\n"

            "ممکن است پیدا کنی:\n"
            "💰 پاداش\n"
            "💎 آیتم نادر\n"
            "🧩 Discovery\n"
            "🌎 منطقه جدید\n"
            "⚠️ Risk Event\n\n"

            "همه Expeditionها تضمین سود ندارند."
        ),
    },

    "business": {
        "title": "🏢 کسب‌وکار",
        "body": (
            "Business قرار است یکی از منابع اصلی درآمد "
            "بلندمدت بازیکن باشد.\n\n"

            "اما Business هزینه دارد:\n"
            "• خرید\n"
            "• Upgrade\n"
            "• Maintenance\n"
            "• Production\n\n"

            "بنابراین هر Business لزوماً سودده نیست."
        ),
    },

    "missions": {
        "title": "🎯 مأموریت‌ها",
        "body": (
            "Missionها مسیر پیشرفت تو در ECHO هستند.\n\n"

            "انواع مأموریت:\n"
            "🎯 Daily\n"
            "📅 Weekly\n"
            "🔒 Secret\n"
            "🌎 Exploration\n"
            "👥 Guild\n"
            "🌪 Event\n\n"

            "Missionها می‌توانند XP، پول، Fame و Unlock به تو بدهند."
        ),
    },

    "guild": {
        "title": "👥 Guild",
        "body": (
            "Guild گروه رسمی بازیکنان داخل ECHO است.\n\n"

            "Guildها در آینده می‌توانند:\n"
            "🏢 پروژه بسازند\n"
            "💰 خزانه مشترک داشته باشند\n"
            "🌎 Territory کنترل کنند\n"
            "🏆 با Guildهای دیگر رقابت کنند."
        ),
    },

    "ranking": {
        "title": "🏆 رتبه‌بندی",
        "body": (
            "Ranking برای مقایسه بازیکنان است.\n\n"

            "در نسخه فعلی رتبه‌بندی City بر اساس:\n"
            "⭐ Level\n"
            "✨ XP\n"
            "💰 ثروت\n"
            "محاسبه می‌شود.\n\n"

            "<b>دستور:</b> /rank"
        ),
    },

    "rules": {
        "title": "📜 قوانین",
        "body": (
            "• Gameplay اصلی داخل Group است.\n"
            "• Spam ممنوع است.\n"
            "• سوءاستفاده از Bug ممنوع است.\n"
            "• تلاش برای ایجاد تراکنش جعلی ممنوع است.\n"
            "• استفاده از چند حساب برای Abuse ممکن است محدود شود.\n\n"

            "هدف ECHO ایجاد رقابت سالم در Groupهاست."
        ),
    },

    "faq": {
        "title": "❓ سؤالات متداول",
        "body": (
            "<b>آیا بازی در Private انجام می‌شود؟</b>\n"
            "خیر. Gameplay اصلی داخل Group است.\n\n"

            "<b>هر Group یک City است؟</b>\n"
            "بله.\n\n"

            "<b>اگر Bot حذف شود چه می‌شود؟</b>\n"
            "City حذف نمی‌شود. در حالت غیرفعال قرار می‌گیرد "
            "و با افزودن دوباره Bot بازیابی می‌شود.\n\n"

            "<b>آیا Progress من در همه Groupها یکی است؟</b>\n"
            "اطلاعات عمومی شخصیت می‌تواند Global باشد، "
            "اما Rank و Membership مربوط به City همان Group است."
        ),
    },

    "about": {
        "title": "ℹ️ درباره ECHO",
        "body": (
            "ECHO یک Multiplayer Text-Based Social World است.\n\n"

            "هدف آن تبدیل Telegram Group به یک جهان بازی زنده است.\n\n"

            "<b>Every Group is a City.</b>\n"
            "<b>Play where your people are.</b>"
        ),
    },
}


HELP_ORDER = (
    "start",
    "city",
    "profile",
    "missions",
    "market",
    "exploration",
    "business",
    "guild",
    "ranking",
    "rules",
    "faq",
    "about",
)


HELP_NAMES = {
    "start": "🚀 شروع بازی",
    "city": "🌆 City چیست؟",
    "profile": "👤 پروفایل",
    "missions": "🎯 مأموریت‌ها",
    "market": "📈 بازار",
    "exploration": "🗺 اکتشاف",
    "business": "🏢 کسب‌وکار",
    "guild": "👥 Guild",
    "ranking": "🏆 رتبه‌بندی",
    "rules": "📜 قوانین",
    "faq": "❓ سؤالات متداول",
    "about": "ℹ️ درباره ECHO",
}


def help_root_keyboard() -> InlineKeyboardMarkup:

    rows = []

    for key in HELP_ORDER:
        rows.append(
            [
                B.primary(
                    HELP_NAMES[key],
                    f"help:{key}",
                )
            ]
        )

    rows.append(
        [
            B.close()
        ]
    )

    return B.build(*rows)


def help_topic_keyboard(
    key: str,
) -> InlineKeyboardMarkup:

    rows = []

    if key != "start":
        rows.append(
            [
                B.back("help:root")
            ]
        )
    else:
        rows.append(
            [
                B.back("help:root")
            ]
        )

    rows.append(
        [
            B.close()
        ]
    )

    return B.build(*rows)


async def record_help(
    user_id: int,
    city_id: Optional[int],
    topic: str,
    source: str,
):

    try:
        async with get_session() as session:

            await record_help_view(
                session,
                user_id,
                city_id,
                topic,
                source,
            )

    except Exception as exc:

        log.warning(
            "Help analytics failed: %s",
            exc,
        )


@help_router.message(
    Command("help")
)
async def cmd_help(
    message: Message,
    city: Optional[City] = None,
    is_group: bool = False,
):

    await record_help(
        message.from_user.id,
        city.id if city else None,
        "root",
        "group" if is_group else "private",
    )

    await message.answer(
        "📚 <b>مرکز راهنمای ECHO</b>\n\n"
        "هر چیزی که برای فهمیدن بازی نیاز داری "
        "از این بخش در دسترس است.\n\n"
        "یک موضوع را انتخاب کن:",
        reply_markup=help_root_keyboard(),
    )


@help_router.callback_query(
    F.data == "help:root"
)
async def cb_help_root(
    call: CallbackQuery,
    city: Optional[City] = None,
    is_group: bool = False,
):

    await record_help(
        call.from_user.id,
        city.id if city else None,
        "root",
        "group" if is_group else "private",
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            "📚 <b>مرکز راهنمای ECHO</b>\n\n"
            "هر چیزی که برای فهمیدن بازی نیاز داری "
            "از این بخش در دسترس است.\n\n"
            "یک موضوع را انتخاب کن:",
            help_root_keyboard(),
        )

    await call.answer()


@help_router.callback_query(
    F.data.startswith("help:")
)
async def cb_help_topic(
    call: CallbackQuery,
    city: Optional[City] = None,
    is_group: bool = False,
):

    key = call.data.split(
        ":",
        1,
    )[1]

    topic = HELP_TOPICS.get(
        key
    )

    if not topic:
        await call.answer(
            "راهنما پیدا نشد.",
            show_alert=True,
        )
        return

    await record_help(
        call.from_user.id,
        city.id if city else None,
        key,
        "group" if is_group else "private",
    )

    text = (
        f"<b>{topic['title']}</b>\n\n"
        f"{topic['body']}"
    )

    if call.message:
        await safe_edit_or_send(
            call.message,
            text,
            help_topic_keyboard(key),
        )

    await call.answer()


@help_router.callback_query(
    F.data == "close"
)
async def cb_close(
    call: CallbackQuery,
):

    if call.message:

        try:

            await call.message.delete()

        except TelegramBadRequest:

            await safe_edit_or_send(
                call.message,
                "بسته شد.",
            )

    await call.answer()


# ================================================================
# SIMPLE CURRENT-GAME HANDLERS
# ================================================================
#
# این بخش عمدی است:
# فعلاً Store و Payment فعال نیست.
# این Commandها برای جلوگیری از پاسخ مبهم،
# Help مرتبط را باز می‌کنند.
# ================================================================

async def not_ready_message(
    title: str,
    help_key: str,
) -> tuple[
    str,
    InlineKeyboardMarkup,
]:

    text = (
        f"🔒 <b>{title}</b>\n\n"
        "این بخش در هسته فعلی ECHO هنوز فعال نشده است.\n\n"
        "ساختار آن برای مرحله بعد آماده شده است."
    )

    markup = B.build(
        [
            B.primary(
                "📖 راهنمای این بخش",
                f"help:{help_key}",
            )
        ],
        [
            B.primary(
                "📚 همه راهنماها",
                "help:root",
            )
        ],
    )

    return text, markup


@group_router.message(
    Command("market")
)
async def cmd_market(
    message: Message,
):

    text, markup = await not_ready_message(
        "Market",
        "market",
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.message(
    Command("explore")
)
async def cmd_explore(
    message: Message,
):

    text, markup = await not_ready_message(
        "Exploration",
        "exploration",
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.message(
    Command("missions")
)
async def cmd_missions(
    message: Message,
):

    text, markup = await not_ready_message(
        "Missions",
        "missions",
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.message(
    Command("business")
)
async def cmd_business(
    message: Message,
):

    text, markup = await not_ready_message(
        "Business",
        "business",
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.message(
    Command("guild")
)
async def cmd_guild(
    message: Message,
):

    text, markup = await not_ready_message(
        "Guild",
        "guild",
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


@group_router.message(
    Command("world")
)
async def cmd_world(
    message: Message,
):

    text = (
        "🌍 <b>ECHO WORLD</b>\n\n"
        "جهان ECHO به مرور با Eventها، "
        "Discoveryها و مناطق جدید فعال می‌شود.\n\n"
        "فعلاً از /city و /help استفاده کن."
    )

    markup = B.build(
        [
            B.primary(
                "🌆 City",
                "city:dashboard",
            ),
            B.primary(
                "📖 Help",
                "help:root",
            ),
        ]
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


# ================================================================
# DEFAULT GROUP HELP / COMMANDS
# ================================================================

@group_router.message(
    Command("start")
)
async def cmd_group_start(
    message: Message,
    city: Optional[City] = None,
):

    if not city:

        await message.answer(
            "🌐 ECHO\n\n"
            "این Group هنوز به City تبدیل نشده است."
        )

        return

    text, markup = await build_city_dashboard(
        city
    )

    await message.answer(
        text,
        reply_markup=markup,
    )


# ================================================================
# DATABASE INITIALIZATION
# ================================================================

async def init_database() -> None:

    async with engine.begin() as conn:

        await conn.run_sync(
            Base.metadata.create_all
        )

    log.info(
        "Database tables created/verified."
    )


# ================================================================
# BOT COMMAND MENU
# ================================================================

async def configure_commands(
    bot: Bot,
) -> None:

    from aiogram.types import BotCommand, BotCommandScopeAllGroupChats
    from aiogram.types import BotCommandScopeDefault

    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="شروع ECHO",
            ),
            BotCommand(
                command="help",
                description="راهنمای ECHO",
            ),
            BotCommand(
                command="join",
                description="ورود به City",
            ),
            BotCommand(
                command="city",
                description="نمایش City",
            ),
            BotCommand(
                command="profile",
                description="نمایش پروفایل",
            ),
            BotCommand(
                command="rank",
                description="رتبه‌بندی City",
            ),
            BotCommand(
                command="market",
                description="بازار",
            ),
            BotCommand(
                command="explore",
                description="اکتشاف",
            ),
            BotCommand(
                command="missions",
                description="مأموریت‌ها",
            ),
            BotCommand(
                command="business",
                description="کسب‌وکار",
            ),
            BotCommand(
                command="guild",
                description="Guild",
            ),
        ],
        scope=BotCommandScopeDefault(),
    )

    await bot.set_my_commands(
        [
            BotCommand(
                command="join",
                description="ورود به City",
            ),
            BotCommand(
                command="city",
                description="نمایش City",
            ),
            BotCommand(
                command="profile",
                description="پروفایل",
            ),
            BotCommand(
                command="rank",
                description="رتبه‌بندی",
            ),
            BotCommand(
                command="market",
                description="بازار",
            ),
            BotCommand(
                command="explore",
                description="اکتشاف",
            ),
            BotCommand(
                command="missions",
                description="مأموریت‌ها",
            ),
            BotCommand(
                command="business",
                description="کسب‌وکار",
            ),
            BotCommand(
                command="guild",
                description="Guild",
            ),
            BotCommand(
                command="help",
                description="راهنما",
            ),
        ],
        scope=BotCommandScopeAllGroupChats(),
    )


# ================================================================
# ROUTER SETUP
# ================================================================

def setup_routers(
    dp: Dispatcher,
) -> None:

    # Middleware for all messages/callbacks.
    dp.message.middleware(
        CityContextMiddleware()
    )

    dp.callback_query.middleware(
        CityContextMiddleware()
    )

    # Order:
    # group/private gameplay first,
    # help after.
    dp.include_router(
        group_router
    )

    dp.include_router(
        private_router
    )

    dp.include_router(
        help_router
    )

    log.info(
        "Routers registered."
    )


# ================================================================
# MAIN
# ================================================================

async def main() -> None:

    print(
        "===== ECHO MAIN.PY STARTED =====",
        flush=True,
    )

    log.info(
        "Starting ECHO..."
    )

    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()

    try:

        # 1. Database
        await init_database()

        # 2. Router / Middleware
        setup_routers(dp)

        # 3. Telegram Commands
        try:
            await configure_commands(bot)
        except Exception as exc:
            log.warning(
                "Could not configure command menu: %s",
                exc,
            )

        # 4. Remove old webhook
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        # 5. Test Telegram connection
        me = await bot.get_me()

        log.info(
            "✅ Connected to bot: @%s",
            me.username,
        )

        log.info(
            "✅ Database ready."
        )

        log.info(
            "🟢 Polling started."
        )

        # 6. Start bot
        await dp.start_polling(
            bot
        )

    except Exception:

        log.exception(
            "ECHO crashed during startup."
        )

        raise

    finally:

        await bot.session.close()

        await engine.dispose()

        log.info(
            "ECHO shutdown complete."
        )


# ================================================================
# ENTRY POINT
# ================================================================

if __name__ == "__main__":
    asyncio.run(
        main()
    )
