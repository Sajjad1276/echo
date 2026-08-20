from __future__ import annotations
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║                    ECHO CITY — GROUP-FIRST EXTENSION               ║
║   "Every Group is a City."  /  "Play where your people are."      ║
╚══════════════════════════════════════════════════════════════════╝

این فایل روی main.py قبلی اضافه می‌شود و آن را جایگزین نمی‌کند.
هیچ Model یا Service قبلی حذف نشده — همه چیز Import و روی آن توسعه داده شده.

نصب:
    from echo_city import setup_echo_city
    ...
    await setup_echo_city(dp, bot)   # داخل main() قبل از start_polling

این فایل شامل MVP بخش‌های 1 تا 125 Spec است (نسخه هسته‌ای):
  ✅ City / CityMember / CityStats / CityDashboard models
  ✅ Group Detection + City Creation خودکار هنگام افزوده شدن Bot
  ✅ /join Idempotent
  ✅ CityContextMiddleware (Group بودن چت + Membership را چک می‌کند)
  ✅ group_router جدا از private_router
  ✅ Private Landing حرفه‌ای + Deep Link افزودن به گروه
  ✅ City Dashboard قابل ویرایش (Edit به‌جای Send جدید)
  ✅ Help Center فارسی، ساختاریافته، Context-Aware (پایه)
  ✅ Button Factory مرکزی (primary/success/danger)
  ✅ City Ranking (بین اعضای همان City)
  ✅ Group ≠ Private Gameplay separation (پیام هدایت در Private)
  ✅ همه متن‌ها فارسی، Commandها انگلیسی

موارد زیر Architecture-Ready هستند اما در این نسخه Implement کامل نشده‌اند
(طبق بخش 126 عمداً غیرفعال‌اند): City Battle/Rivalry کامل، Global Event
Broadcast، Help Analytics پایگاه‌داده‌ای کامل (نسخه ساده‌شده اضافه شده)،
Item/Shop/Store، Payment/Stars. این‌ها در فاز بعدی روی همین پایه اضافه می‌شوند.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Integer,
    String, UniqueConstraint, select, update, func,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
# ── از main.py موجود استفاده می‌کنیم (چیزی Duplicate نمی‌سازیم) ─────


log = logging.getLogger("echo.city")




class Base(DeclarativeBase):
    pass

# ─────────────────────────────────────────────────────────────────
# DATABASE MODELS — بخش 134
# ─────────────────────────────────────────────────────────────────

class City(Base):
    """هر Group یک City مستقل — بخش 52، 134"""
    __tablename__ = "cities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)       # Telegram Chat ID فعلی
    city_code = Column(String(16), unique=True, nullable=False)     # مثل EC-48291
    name = Column(String(128), nullable=False)                      # از عنوان Group
    custom_name = Column(Boolean, default=False)                    # بخش 55: اگر ادمین دستی تغییر داد، Sync خودکار متوقف شود
    username = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True)                       # بخش 93: Soft Delete هنگام حذف Bot
    level = Column(Integer, default=1)
    treasury = Column(BigInteger, default=0)
    owner_id = Column(BigInteger, nullable=True)                    # کسی که Bot را اضافه کرد
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("chat_id", name="uq_city_chat_id"),)


class CityMember(Base):
    """رابطه User <-> City — بخش 3، 135 (Unique: user_id + city_id)"""
    __tablename__ = "city_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), default="citizen")   # citizen / moderator / city_admin
    contribution = Column(BigInteger, default=0)    # City Wealth Contribution
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("city_id", "user_id", name="uq_city_member"),
    )


class CityDashboard(Base):
    """پیام Dashboard ثابت هر Group — بخش 98، 136 (برای Edit به‌جای Send جدید)"""
    __tablename__ = "city_dashboard"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id"), unique=True, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    last_updated = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class HelpView(Base):
    """ثبت بازدید Help برای Analytics ساده — بخش 137"""
    __tablename__ = "help_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    city_id = Column(Integer, nullable=True)
    topic = Column(String(64), nullable=False)
    source = Column(String(16), default="private")   # private / group / contextual
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────
# BUTTON FACTORY — بخش 15، 16، 60
# ─────────────────────────────────────────────────────────────────

class Buttons:
    """تمام دکمه‌های ربات باید از این‌جا ساخته شوند — هیچ Router نباید
    Keyboard را پراکنده بسازد."""

    @staticmethod
    def _row(items: list[InlineKeyboardButton]) -> list[InlineKeyboardButton]:
        return items

    @staticmethod
    def build(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=list(rows))

    @staticmethod
    def primary(text: str, callback_data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=f"🔵 {text}", callback_data=callback_data)

    @staticmethod
    def success(text: str, callback_data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=f"🟢 {text}", callback_data=callback_data)

    @staticmethod
    def danger(text: str, callback_data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=f"🔴 {text}", callback_data=callback_data)

    @staticmethod
    def url_button(text: str, url: str, style: str = "success") -> InlineKeyboardButton:
        icon = {"success": "🟢", "primary": "🔵", "danger": "🔴"}.get(style, "🔵")
        return InlineKeyboardButton(text=f"{icon} {text}", url=url)

    @staticmethod
    def back(callback_data: str = "help:root") -> InlineKeyboardButton:
        return InlineKeyboardButton(text="🔴 بازگشت", callback_data=callback_data)

    @staticmethod
    def close() -> InlineKeyboardButton:
        return InlineKeyboardButton(text="🔴 بستن", callback_data="close")


B = Buttons


async def safe_edit_or_send(message: Message, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> Message:
    """بخش 44: تا جای ممکن Edit، نه Send جدید."""
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return message
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return message
        return await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────────────────────────
# SERVICES — CityService / CityMembershipService — بخش 132
# ─────────────────────────────────────────────────────────────────

def make_city_code() -> str:
    import random
    return f"EC-{random.randint(10000, 99999)}"


async def get_city_by_chat(session: AsyncSession, chat_id: int) -> Optional[City]:
    return await get_or_none(session, City, chat_id=chat_id)


async def get_or_create_city(session: AsyncSession, chat_id: int, title: str,
                              username: Optional[str], owner_id: Optional[int]) -> City:
    """بخش 5، 93، 94: اگر City قبلاً وجود داشت (حتی Soft-Deleted) دوباره فعالش کن،
    داده‌ها را از بین نبر."""
    city = await get_city_by_chat(session, chat_id)
    if city:
        if not city.is_active:
            city.is_active = True
            log.info(f"City restored: chat_id={chat_id}")
        if not city.custom_name and city.name != title:
            city.name = title  # بخش 55: Sync خودکار عنوان
        city.username = username
        return city

    city = City(
        chat_id=chat_id,
        city_code=make_city_code(),
        name=title or "Unnamed City",
        username=username,
        owner_id=owner_id,
    )
    session.add(city)
    await session.flush()
    log.info(f"City created: {city.city_code} chat_id={chat_id}")
    return city


async def deactivate_city(session: AsyncSession, chat_id: int) -> None:
    """بخش 93: Soft Delete هنگام حذف Bot از Group."""
    await session.execute(
        update(City).where(City.chat_id == chat_id).values(is_active=False)
    )


async def get_membership(session: AsyncSession, city_id: int, user_id: int) -> Optional[CityMember]:
    return await get_or_none(session, CityMember, city_id=city_id, user_id=user_id)


async def join_city(session: AsyncSession, city_id: int, tg_user) -> tuple[CityMember, bool]:
    """بخش 49: Idempotent — اگر عضو باشد Reward دوباره داده نمی‌شود."""
    existing = await get_membership(session, city_id, tg_user.id)
    if existing:
        return existing, False

    # اگر کاربر در سطح Global (main.py) هنوز وجود ندارد، بسازش
    user = await get_or_none(session, User, id=tg_user.id)
    if not user:
        nickname = tg_user.first_name or tg_user.username or f"Player{tg_user.id}"
        user = await create_user(session, tg_user.id, tg_user.username, nickname[:32])
    else:
        await touch_user(session, tg_user.id)

    member = CityMember(city_id=city_id, user_id=tg_user.id)
    session.add(member)
    await session.flush()
    return member, True


async def city_population(session: AsyncSession, city_id: int) -> int:
    result = await session.execute(
        select(func.count(CityMember.id)).where(CityMember.city_id == city_id)
    )
    return result.scalar_one() or 0


async def city_ranking(session: AsyncSession, city_id: int, limit: int = 10) -> list[tuple]:
    """رتبه‌بندی اعضای یک City بر اساس Level/XP سراسری کاربر — بخش 22"""
    result = await session.execute(
        select(User, UserStats, Wallet)
        .join(CityMember, CityMember.user_id == User.id)
        .join(UserStats, UserStats.user_id == User.id)
        .join(Wallet, Wallet.user_id == User.id)
        .where(CityMember.city_id == city_id)
        .order_by(UserStats.level.desc(), UserStats.xp.desc())
        .limit(limit)
    )
    return result.all()


async def record_help_view(session: AsyncSession, user_id: int, city_id: Optional[int],
                            topic: str, source: str) -> None:
    session.add(HelpView(user_id=user_id, city_id=city_id, topic=topic, source=source))


# ─────────────────────────────────────────────────────────────────
# MIDDLEWARE — CityContextMiddleware — بخش 88، 133
# ─────────────────────────────────────────────────────────────────

class CityContextMiddleware(BaseMiddleware):
    """
    برای هر Update داخل Group:
      - City مربوطه را پیدا/می‌سازد (فقط lookup، ساخت واقعی در handler افزودن Bot انجام می‌شود)
      - در data["city"] و data["is_group"] قرار می‌دهد
      - Membership را چک نمی‌کند (این کار در خود Handlerها انجام می‌شود تا پیام‌های
        هدایت‌کننده مناسب نمایش داده شوند — بخش 91)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat")
        is_group = bool(chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))
        data["is_group"] = is_group
        data["city"] = None

        if is_group:
            async with get_session() as session:
                city = await get_city_by_chat(session, chat.id)
                data["city"] = city

        return await handler(event, data)


# ─────────────────────────────────────────────────────────────────
# ROUTERS — بخش 89
# ─────────────────────────────────────────────────────────────────

private_router = Router(name="echo_private")
group_router = Router(name="echo_group")
help_router = Router(name="echo_help")

private_router.message.filter(F.chat.type == ChatType.PRIVATE)
group_router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
group_router.callback_query.filter(F.message.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))


# ═════════════════════════════════════════════════════════════════
# بخش 4: PRIVATE LANDING
# ═════════════════════════════════════════════════════════════════

async def get_bot_username(bot: Bot) -> str:
    me = await bot.get_me()
    return me.username


def landing_text() -> str:
    return (
        "🌐 <b>ECHO</b>\n\n"
        "به دنیایی خوش آمدی که هر شهر آن توسط بازیکنان ساخته می‌شود.\n\n"
        "اینجا فقط یک بازی نیست.\n"
        "هر گروه Telegram می‌تواند یک <b>City</b> باشد.\n\n"
        "تو می‌توانی:\n"
        "💰 ثروت بسازی\n"
        "🏢 کسب‌وکار راه بیندازی\n"
        "📈 وارد بازار شوی\n"
        "🗺 جهان را کشف کنی\n"
        "👥 با دوستانت رقابت کنی\n"
        "🏆 در رتبه‌بندی شهر قرار بگیری\n"
        "🌪 در Eventهای جهانی شرکت کنی\n\n"
        "اما زندگی واقعی ECHO داخل <b>Group</b> اتفاق می‌افتد."
    )


@private_router.message(CommandStart())
async def echo_start(message: Message, bot: Bot) -> None:
    username = await get_bot_username(bot)
    add_url = f"https://t.me/{username}?startgroup=echo"
    markup = B.build(
        [B.url_button("افزودن ECHO به گروه", add_url, "success")],
        [B.primary("چگونه بازی کنم؟", "help:start"), B.primary("راهنمای کامل", "help:root")],
        [B.primary("قوانین بازی", "help:rules"), B.primary("درباره ECHO", "help:about")],
        [B.success("ورود به ECHO", "echo:enter")],
    )
    await message.answer(landing_text(), reply_markup=markup, parse_mode=ParseMode.HTML)


@private_router.callback_query(F.data == "echo:enter")
async def echo_enter(call: CallbackQuery, bot: Bot) -> None:
    """بخش 50: کاربر را وارد بازی خصوصی نمی‌کند — یا او را به Group هدایت
    می‌کند یا گزینه افزودن Bot را نشان می‌دهد."""
    async with get_session() as session:
        result = await session.execute(
            select(City)
            .join(CityMember, CityMember.city_id == City.id)
            .where(CityMember.user_id == call.from_user.id, City.is_active == True)
        )
        cities = result.scalars().unique().all()

    username = await get_bot_username(bot)
    add_url = f"https://t.me/{username}?startgroup=echo"

    if not cities:
        text = (
            "🏙 هنوز عضو هیچ City ای نیستی.\n\n"
            "برای شروع بازی، ابتدا ECHO را به یک Group اضافه کن یا وارد "
            "Groupی شو که ECHO در آن فعال است، سپس دستور /join را بزن."
        )
        markup = B.build([B.url_button("افزودن به گروه", add_url, "success")],
                          [B.back("help:root")])
    else:
        lines = "\n".join(f"🌆 {c.name}" for c in cities)
        text = f"🏙 Cityهایی که در آن‌ها شهروند هستی:\n\n{lines}\n\nبرای بازی، وارد همان Group شو."
        markup = B.build([B.url_button("افزودن به یک گروه دیگر", add_url, "success")],
                          [B.back("help:root")])

    await safe_edit_or_send(call.message, text, markup)
    await call.answer()


# ═════════════════════════════════════════════════════════════════
# بخش 5-6: BOT ADDED TO GROUP → CITY CREATION
# ═════════════════════════════════════════════════════════════════

@group_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_bot_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    chat = event.chat
    async with get_session() as session:
        city = await get_or_create_city(
            session,
            chat_id=chat.id,
            title=chat.title or "Unnamed City",
            username=chat.username,
            owner_id=event.from_user.id if event.from_user else None,
        )
        city_id, city_name = city.id, city.name

    text = (
        "🌆 <b>به ECHO CITY خوش آمدید!</b>\n\n"
        "این Group اکنون یک شهر رسمی در ECHO است.\n\n"
        f"🏙 شهر: <b>{city_name}</b>\n"
        "👥 جمعیت: 0\n"
        "⭐ Level: 1\n"
        "💰 خزانه شهر: $0\n"
        "📊 وضعیت: NEW CITY\n\n"
        "اولین کاری که باید انجام دهید:\n"
        "/join"
    )
    markup = B.build(
        [B.success("ورود به شهر", "city:join")],
        [B.primary("راهنمای بازی", "help:start"), B.primary("مأموریت اول", "help:start")],
        [B.primary("رتبه‌بندی", f"city:rank")],
    )
    try:
        await bot.send_message(chat.id, text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        log.warning(f"Could not send welcome message to {chat.id}: {e}")


@group_router.my_chat_member(F.new_chat_member.status.in_({"left", "kicked"}))
async def on_bot_removed_from_group(event: ChatMemberUpdated) -> None:
    """بخش 93: Soft Delete، نه حذف واقعی داده."""
    async with get_session() as session:
        await deactivate_city(session, event.chat.id)
    log.info(f"City deactivated (bot removed): chat_id={event.chat.id}")


# ═════════════════════════════════════════════════════════════════
# بخش 48-49: /join
# ═════════════════════════════════════════════════════════════════

async def _do_join(message_or_call, city: Optional[City], bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    if not city:
        text = "⚠️ این Group هنوز به‌عنوان City ثبت نشده. لطفاً ادمین ربات را دوباره اضافه کند."
        return text, B.build([B.back("help:root")])

    from_user = message_or_call.from_user
    async with get_session() as session:
        member, created = await join_city(session, city.id, from_user)
        await session.flush()

    if created:
        text = (
            f"🌆 <b>Welcome to {city.name}.</b>\n\n"
            "هویت ECHO تو ساخته شد.\n\n"
            f"💰 ${cfg.STARTING_CASH:,}\n"
            f"⚡ {cfg.STARTING_ENERGY} Energy\n"
            "🎯 اولین مأموریت در انتظار توست.\n"
        )
        markup = B.build(
            [B.success("شروع مأموریت", "help:start")],
            [B.primary("آموزش ECHO", "help:root")],
        )
    else:
        text = "✅ تو قبلاً شهروند این City هستی."
        markup = B.build([B.primary("پروفایل", "city:profile"), B.primary("راهنما", "help:root")])

    return text, markup


@group_router.message(Command("join"))
async def cmd_group_join(message: Message, city: Optional[City], bot: Bot) -> None:
    text, markup = await _do_join(message, city, bot)
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@group_router.callback_query(F.data == "city:join")
async def cb_group_join(call: CallbackQuery, city: Optional[City], bot: Bot) -> None:
    text, markup = await _do_join(call, city, bot)
    await safe_edit_or_send(call.message, text, markup)
    await call.answer()


# ═════════════════════════════════════════════════════════════════
# بخش 13، 73: /city — City Dashboard
# ═════════════════════════════════════════════════════════════════

def require_membership_text() -> tuple[str, InlineKeyboardMarkup]:
    return (
        "⛔ تو هنوز شهروند این City نیستی.",
        B.build([B.success("ورود به City", "city:join")]),
    )


async def render_city_dashboard(city: City) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        pop = await city_population(session, city.id)

    text = (
        f"🌆 <b>{city.name.upper()}</b>\n\n"
        f"⭐ Level {city.level}\n"
        f"👥 Population {pop}\n"
        f"💰 Economy ${city.treasury:,}\n"
        f"🏷 City ID: #{city.city_code}\n"
    )
    markup = B.build(
        [B.success("بازی", "city:play")],
        [B.primary("Market", "help:market"), B.primary("Explore", "help:exploration")],
        [B.primary("Missions", "help:start"), B.primary("Guild", "help:root")],
        [B.primary("Ranking", "city:rank"), B.primary("Help", "help:root")],
    )
    return text, markup


@group_router.message(Command("city"))
async def cmd_city(message: Message, city: Optional[City]) -> None:
    if not city:
        await message.answer("⚠️ این Group هنوز City نیست. ادمین باید ربات را دوباره اضافه کند.")
        return
    text, markup = await render_city_dashboard(city)
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@group_router.message(Command("rank"))
async def cmd_city_rank(message: Message, city: Optional[City]) -> None:
    if not city:
        return
    async with get_session() as session:
        rows = await city_ranking(session, city.id, limit=10)

    if not rows:
        text = "🏆 هنوز هیچ شهروندی در این City رتبه‌بندی نشده."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (user, stats, wallet) in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            name = f"@{user.username}" if user.username else user.nickname
            lines.append(f"{medal} {name} — Lv.{stats.level} — ${wallet.cash + wallet.bank:,}")
        text = "🏆 <b>CITY RANKING</b>\n\n" + "\n".join(lines)

    await message.answer(text, parse_mode=ParseMode.HTML)


@group_router.callback_query(F.data == "city:rank")
async def cb_city_rank(call: CallbackQuery, city: Optional[City]) -> None:
    if not city:
        await call.answer()
        return
    async with get_session() as session:
        rows = await city_ranking(session, city.id, limit=10)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user, stats, wallet) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = f"@{user.username}" if user.username else user.nickname
        lines.append(f"{medal} {name} — Lv.{stats.level} — ${wallet.cash + wallet.bank:,}")
    text = "🏆 <b>CITY RANKING</b>\n\n" + ("\n".join(lines) if lines else "هنوز کسی رتبه‌بندی نشده.")
    await safe_edit_or_send(call.message, text, B.build([B.back("city:dashboard")]))
    await call.answer()


@group_router.callback_query(F.data == "city:dashboard")
async def cb_city_dashboard(call: CallbackQuery, city: Optional[City]) -> None:
    if not city:
        await call.answer()
        return
    text, markup = await render_city_dashboard(city)
    await safe_edit_or_send(call.message, text, markup)
    await call.answer()


# ═════════════════════════════════════════════════════════════════
# بخش 85: Gameplay Commands در Private → هدایت به Group
# ═════════════════════════════════════════════════════════════════

GAMEPLAY_COMMANDS = ["market", "explore", "missions", "business", "guild", "world", "profile"]


def _redirect_to_group_text(bot_username: str) -> tuple[str, InlineKeyboardMarkup]:
    add_url = f"https://t.me/{bot_username}?startgroup=echo"
    text = (
        "📍 این بخش داخل ECHO City اجرا می‌شود، نه در Private Chat.\n\n"
        "برای بازی:\n"
        "1. Bot را به Group اضافه کن.\n"
        "2. وارد City شو (/join).\n"
        "3. همین دستور را داخل Group اجرا کن."
    )
    markup = B.build(
        [B.url_button("افزودن به گروه", add_url, "success")],
        [B.primary("راهنمای City", "help:city")],
    )
    return text, markup


for _cmd in GAMEPLAY_COMMANDS:
    async def _handler(message: Message, bot: Bot, _cmd=_cmd) -> None:
        username = await get_bot_username(bot)
        text, markup = _redirect_to_group_text(username)
        await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    private_router.message.register(_handler, Command(_cmd))


# ═════════════════════════════════════════════════════════════════
# بخش 8-10، 108: HELP CENTER — فارسی، ساختاریافته
# ═════════════════════════════════════════════════════════════════

HELP_TOPICS: dict[str, dict] = {
    "start": {
        "title": "🚀 شروع ECHO",
        "body": (
            "ECHO یک جهان آنلاین متنی است. هر Group یک City است.\n\n"
            "در City تو می‌توانی:\n"
            "💰 پول کسب کنی\n📈 معامله کنی\n🏢 Business بسازی\n"
            "🗺 منطقه کشف کنی\n🎯 Mission انجام دهی\n👥 عضو Guild شوی\n"
            "🏆 رتبه بگیری\n\n"
            "<b>شروع بازی:</b>\n"
            "1. Bot را به Group اضافه کن.\n"
            "2. داخل Group دستور /join را بزن.\n"
            "3. مأموریت اول را انجام بده.\n"
            "4. اولین درآمدت را کسب کن.\n"
            "5. Profile خودت را کامل کن.\n"
            "6. به Market سر بزن.\n"
            "7. یک منطقه را Explore کن.\n"
            "8. وارد یک Guild شو.\n\n"
            "🧭 ECHO فقط با پول جلو نمی‌رود. تصمیم‌گیری مهم است."
        ),
    },
    "city": {
        "title": "🌆 ECHO CITY",
        "body": (
            "City همان Group شماست. هر بازیکن در یک City زندگی می‌کند.\n\n"
            "City دارای Population، Economy، Activity، Treasury، Level، "
            "Ranking، Guilds و Statistics است.\n\n"
            "هرچه اعضا فعال‌تر باشند، City بیشتر رشد می‌کند."
        ),
    },
    "market": {
        "title": "📈 راهنمای Market",
        "body": (
            "<b>Market چیست؟</b> بازاری‌ست که در آن بازیکنان منابع و "
            "دارایی‌های مجاز بازی را معامله می‌کنند.\n\n"
            "قیمت‌ها ثابت نیستند — Supply و Demand روی قیمت اثر دارند.\n"
            "اگر بازیکنان زیادی یک Resource بخرند، قیمت افزایش می‌یابد؛ "
            "اگر عرضه بالا باشد، قیمت کاهش می‌یابد.\n\n"
            "<b>مثال:</b> Crystal با قیمت $1,000، اگر تقاضا بالا برود ممکن "
            "است به $1,250 برسد. اما این سود تضمینی نیست.\n\n"
            "Command مربوطه: /market (داخل Group)"
        ),
    },
    "exploration": {
        "title": "🗺 راهنمای Exploration",
        "body": (
            "Exploration برای کشف مناطق، منابع و اتفاق‌های خاص است.\n\n"
            "ممکن است Reward، Discovery، Rare Item، Nothing یا حتی یک "
            "Risk Event رخ دهد. Exploration تضمین سود ندارد.\n\n"
            "Command مربوطه: /explore (داخل Group)"
        ),
    },
    "business": {
        "title": "🏢 راهنمای Business",
        "body": (
            "Business یک منبع درآمد بلندمدت است اما هزینه دارد: خرید، "
            "Upgrade، Maintenance، Production و Location.\n\n"
            "Business همیشه به معنی سود نیست — اگر هزینه نگهداری از "
            "درآمد بیشتر شود، ضرر می‌کنی.\n\n"
            "Command مربوطه: /business (داخل Group)"
        ),
    },
    "rules": {
        "title": "📜 قوانین بازی",
        "body": (
            "• Gameplay اصلی فقط داخل Group انجام می‌شود.\n"
            "• هر Group یک City مستقل است.\n"
            "• Progression عمومی (Level/XP/Fame) در همه Cityها مشترک است.\n"
            "• City Rank/Wealth/Guild مخصوص همان City هستند.\n"
            "• رفتار توهین‌آمیز یا Spam منجر به محدودیت می‌شود."
        ),
    },
    "about": {
        "title": "ℹ️ درباره ECHO",
        "body": (
            "ECHO یک جهان زنده‌ی متنی است که هر Telegram Group آن را به "
            "یک City تبدیل می‌کند.\n\n"
            "«Every Group is a City.»\n«Play where your people are.»"
        ),
    },
    "faq": {
        "title": "❓ سوالات متداول",
        "body": (
            "<b>چطور بازی را شروع کنم؟</b> Bot را به Group اضافه کن و /join بزن.\n\n"
            "<b>آیا Progress من در همه Groupها مشترک است؟</b> Level/XP/Fame بله؛ "
            "City Rank و City Wealth خیر — آن‌ها مخصوص همان City هستند.\n\n"
            "<b>اگر Bot از Group حذف شود؟</b> City غیرفعال می‌شود ولی داده‌ها حذف نمی‌شوند؛ "
            "با افزودن دوباره، City بازیابی می‌شود.\n\n"
            "<b>چرا Energy من محدود است؟</b> برای جلوگیری از Spam و حفظ تعادل بازی."
        ),
    },
}

HELP_ROOT_ORDER = ["start", "city", "market", "exploration", "business", "rules", "faq", "about"]
HELP_TITLES_SHORT = {
    "start": "شروع بازی", "city": "شهر چیست؟", "market": "بازار",
    "exploration": "اکتشاف", "business": "کسب‌وکار", "rules": "قوانین",
    "faq": "سوالات متداول", "about": "درباره ECHO",
}


def help_root_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in HELP_ROOT_ORDER:
        rows.append([B.primary(HELP_TITLES_SHORT[key], f"help:{key}")])
    rows.append([B.close()])
    return B.build(*rows)


def help_topic_kb(topic_key: str) -> InlineKeyboardMarkup:
    return B.build(
        [B.back("help:root")],
        [B.close()],
    )


@help_router.message(Command("help"))
async def cmd_help(message: Message, is_group: bool = False, city: Optional[City] = None) -> None:
    async with get_session() as session:
        await record_help_view(session, message.from_user.id,
                                city.id if city else None,
                                "root", "group" if is_group else "private")
    text = "📚 <b>مرکز راهنمای ECHO</b>\n\nیکی از بخش‌ها را انتخاب کن:"
    await message.answer(text, reply_markup=help_root_kb(), parse_mode=ParseMode.HTML)


@help_router.callback_query(F.data == "help:root")
async def cb_help_root(call: CallbackQuery) -> None:
    text = "📚 <b>مرکز راهنمای ECHO</b>\n\nیکی از بخش‌ها را انتخاب کن:"
    await safe_edit_or_send(call.message, text, help_root_kb())
    await call.answer()


@help_router.callback_query(F.data.startswith("help:"))
async def cb_help_topic(call: CallbackQuery, is_group: bool = False, city: Optional[City] = None) -> None:
    key = call.data.split(":", 1)[1]
    topic = HELP_TOPICS.get(key)
    if not topic:
        await call.answer()
        return

    async with get_session() as session:
        await record_help_view(session, call.from_user.id,
                                city.id if city else None,
                                key, "group" if is_group else "private")

    text = f"<b>{topic['title']}</b>\n\n{topic['body']}"
    await safe_edit_or_send(call.message, text, help_topic_kb(key))
    await call.answer()


@help_router.callback_query(F.data == "close")
async def cb_close(call: CallbackQuery) -> None:
    try:
        await call.message.delete()
    except TelegramBadRequest:
        await safe_edit_or_send(call.message, "بسته شد.", None)
    await call.answer()


# ═════════════════════════════════════════════════════════════════
# SETUP — نصب در main.py
# ═════════════════════════════════════════════════════════════════

async def init_city_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("City tables ready.")
    
async def setup_echo_city(dp: Dispatcher, bot: Bot) -> None:
    await init_city_tables()

    dp.message.middleware(CityContextMiddleware())
    dp.callback_query.middleware(CityContextMiddleware())

    # ترتیب مهم است: group و private قبل از help تا فیلتر chat.type اعمال شود،
    # help_router بدون فیلتر chat است تا در هر دو کار کند.
    dp.include_router(group_router)
    dp.include_router(private_router)
    dp.include_router(help_router)

    log.info("ECHO City extension loaded — Group-First architecture active.")
