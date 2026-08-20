# ================================================================
# ECHO CITY — GROUP-FIRST EXTENSION
# Single-file architecture
# ================================================================

# IMPORTANT:
# این کد باید بعد از تعریف:
#
# - cfg
# - engine
# - AsyncSessionLocal
# - Base
# - User
# - UserStats
# - Wallet
# - get_or_none()
# - create_user()
# - touch_user()
#
# قرار بگیرد.
#
# هیچ from main import ... استفاده نکن.

from __future__ import annotations

import logging
import random

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from aiogram import (
    BaseMiddleware,
    Bot,
    Dispatcher,
    F,
    Router,
)

from aiogram.enums import ChatType, ParseMode

from aiogram.exceptions import TelegramBadRequest

from aiogram.filters import (
    Command,
    CommandStart,
    ChatMemberUpdatedFilter,
    JOIN_TRANSITION,
)

from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
class Base(DeclarativeBase):
    pass

log = logging.getLogger("echo.city")


# ================================================================
# DATABASE
# ================================================================

# اگر Base قبلاً در main.py تعریف شده است:
#
# class Base(DeclarativeBase):
#     pass
#
# اینجا دوباره Base تعریف نکن.


# ================================================================
# CITY MODELS
# ================================================================

class City(Base):
    __tablename__ = "cities"

    id = Column(Integer, primary_key=True, autoincrement=True)

    chat_id = Column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    city_code = Column(
        String(16),
        unique=True,
        nullable=False,
    )

    name = Column(
        String(128),
        nullable=False,
    )

    custom_name = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    username = Column(
        String(64),
        nullable=True,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    level = Column(
        Integer,
        default=1,
        nullable=False,
    )

    treasury = Column(
        BigInteger,
        default=0,
        nullable=False,
    )

    owner_id = Column(
        BigInteger,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CityMember(Base):
    __tablename__ = "city_members"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id = Column(
        Integer,
        ForeignKey("cities.id"),
        nullable=False,
        index=True,
    )

    user_id = Column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    role = Column(
        String(16),
        default="citizen",
        nullable=False,
    )

    contribution = Column(
        BigInteger,
        default=0,
        nullable=False,
    )

    joined_at = Column(
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

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    city_id = Column(
        Integer,
        ForeignKey("cities.id"),
        unique=True,
        nullable=False,
    )

    chat_id = Column(
        BigInteger,
        nullable=False,
    )

    message_id = Column(
        BigInteger,
        nullable=True,
    )

    last_updated = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class HelpView(Base):
    __tablename__ = "help_views"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id = Column(
        BigInteger,
        nullable=False,
        index=True,
    )

    city_id = Column(
        Integer,
        nullable=True,
        index=True,
    )

    topic = Column(
        String(64),
        nullable=False,
    )

    source = Column(
        String(16),
        default="private",
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ================================================================
# CITY HELPERS
# ================================================================

def make_city_code() -> str:
    return f"EC-{random.randint(10000, 99999)}"


async def get_city_by_chat(
    session,
    chat_id: int,
) -> Optional[City]:

    result = await session.execute(
        select(City).where(
            City.chat_id == chat_id
        )
    )

    return result.scalar_one_or_none()


async def get_or_create_city(
    session,
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

            log.info(
                "City restored: %s",
                chat_id,
            )

        if (
            not city.custom_name
            and city.name != title
            and title
        ):
            city.name = title

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

    log.info(
        "City created: %s / %s",
        city.city_code,
        chat_id,
    )

    return city


async def deactivate_city(
    session,
    chat_id: int,
) -> None:

    await session.execute(
        update(City)
        .where(City.chat_id == chat_id)
        .values(is_active=False)
    )


async def get_membership(
    session,
    city_id: int,
    user_id: int,
) -> Optional[CityMember]:

    result = await session.execute(
        select(CityMember)
        .where(
            CityMember.city_id == city_id,
            CityMember.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def join_city(
    session,
    city_id: int,
    tg_user,
):

    existing = await get_membership(
        session,
        city_id,
        tg_user.id,
    )

    if existing:
        return existing, False

    # این سه مورد باید از بخش اصلی ECHO موجود باشند.
    user = await get_or_none(
        session,
        User,
        id=tg_user.id,
    )

    if not user:

        nickname = (
            tg_user.first_name
            or tg_user.username
            or f"Player{tg_user.id}"
        )

        user = await create_user(
            session,
            tg_user.id,
            tg_user.username,
            nickname[:32],
        )

    else:

        await touch_user(
            session,
            tg_user.id,
        )

    member = CityMember(
        city_id=city_id,
        user_id=tg_user.id,
    )

    session.add(member)

    await session.flush()

    return member, True


async def city_population(
    session,
    city_id: int,
) -> int:

    result = await session.execute(
        select(
            func.count(CityMember.id)
        ).where(
            CityMember.city_id == city_id
        )
    )

    return result.scalar_one() or 0


async def city_ranking(
    session,
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
        )

        .limit(limit)
    )

    return result.all()


async def record_help_view(
    session,
    user_id: int,
    city_id: Optional[int],
    topic: str,
    source: str,
):

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

class Buttons:

    @staticmethod
    def primary(
        text: str,
        callback_data: str,
    ):

        return InlineKeyboardButton(
            text=f"🔵 {text}",
            callback_data=callback_data,
        )

    @staticmethod
    def success(
        text: str,
        callback_data: str,
    ):

        return InlineKeyboardButton(
            text=f"🟢 {text}",
            callback_data=callback_data,
        )

    @staticmethod
    def danger(
        text: str,
        callback_data: str,
    ):

        return InlineKeyboardButton(
            text=f"🔴 {text}",
            callback_data=callback_data,
        )

    @staticmethod
    def url_button(
        text: str,
        url: str,
    ):

        return InlineKeyboardButton(
            text=f"🟢 {text}",
            url=url,
        )

    @staticmethod
    def back(
        callback_data: str = "help:root",
    ):

        return InlineKeyboardButton(
            text="🔴 بازگشت",
            callback_data=callback_data,
        )

    @staticmethod
    def close():

        return InlineKeyboardButton(
            text="🔴 بستن",
            callback_data="close",
        )

    @staticmethod
    def build(*rows):

        return InlineKeyboardMarkup(
            inline_keyboard=list(rows)
        )


B = Buttons


# ================================================================
# MESSAGE UTILITY
# ================================================================

async def safe_edit_or_send(
    message: Message,
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
):

    try:

        await message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

        return message

    except TelegramBadRequest as exc:

        if "not modified" in str(exc).lower():
            return message

        return await message.answer(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )


# ================================================================
# CITY CONTEXT MIDDLEWARE
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
    ):

        city = None
        is_group = False

        chat = None

        # Message
        if isinstance(event, Message):

            chat = event.chat

        # CallbackQuery
        elif isinstance(event, CallbackQuery):

            if event.message:
                chat = event.message.chat

        # ChatMemberUpdated
        elif isinstance(event, ChatMemberUpdated):

            chat = event.chat

        if chat:

            is_group = chat.type in {
                ChatType.GROUP,
                ChatType.SUPERGROUP,
            }

        data["is_group"] = is_group
        data["city"] = None

        if is_group and chat:

            async with get_session() as session:

                city = await get_city_by_chat(
                    session,
                    chat.id,
                )

                data["city"] = city

        return await handler(
            event,
            data,
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
# PRIVATE FILTER
# ================================================================

private_router.message.filter(
    F.chat.type == ChatType.PRIVATE
)


# ================================================================
# GROUP FILTER
# ================================================================

group_router.message.filter(
    F.chat.type.in_(
        {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }
    )
)

group_router.callback_query.filter(
    F.message.chat.type.in_(
        {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }
    )
)


# ================================================================
# PRIVATE LANDING
# ================================================================

async def get_bot_username(
    bot: Bot,
) -> str:

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


@private_router.message(
    CommandStart()
)
async def echo_start(
    message: Message,
    bot: Bot,
):

    username = await get_bot_username(
        bot
    )

    add_url = (
        f"https://t.me/{username}"
        f"?startgroup=echo"
    )

    markup = B.build(

        [
            B.url_button(
                "افزودن ECHO به گروه",
                add_url,
            )
        ],

        [
            B.primary(
                "چگونه بازی کنم؟",
                "help:start",
            ),

            B.primary(
                "راهنمای کامل",
                "help:root",
            ),
        ],

        [
            B.primary(
                "قوانین بازی",
                "help:rules",
            ),

            B.primary(
                "درباره ECHO",
                "help:about",
            ),
        ],
    )

    await message.answer(
        landing_text(),
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
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
            session=session,
            chat_id=chat.id,
            title=chat.title or "Unnamed City",
            username=chat.username,
            owner_id=(
                event.from_user.id
                if event.from_user
                else None
            ),
        )

        city_name = city.name

    text = (
        "🌆 <b>به ECHO CITY خوش آمدید!</b>\n\n"

        "این Group اکنون یک شهر رسمی در ECHO است.\n\n"

        f"🏙 شهر: <b>{city_name}</b>\n"
        "👥 جمعیت: 0\n"
        "⭐ Level: 1\n"
        "💰 خزانه شهر: $0\n"
        "📊 وضعیت: NEW CITY\n\n"

        "برای ورود به شهر:\n"
        "<code>/join</code>"
    )

    markup = B.build(

        [
            B.success(
                "ورود به شهر",
                "city:join",
            )
        ],

        [
            B.primary(
                "راهنمای بازی",
                "help:start",
            ),

            B.primary(
                "مأموریت اول",
                "help:start",
            ),
        ],

        [
            B.primary(
                "رتبه‌بندی",
                "city:rank",
            )
        ],
    )

    try:

        await bot.send_message(
            chat.id,
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except TelegramBadRequest as exc:

        log.warning(
            "Could not send welcome message: %s",
            exc,
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
# JOIN
# ================================================================

async def _do_join(
    message_or_call,
    city: Optional[City],
):

    if not city:

        return (
            "⚠️ این Group هنوز به‌عنوان City ثبت نشده.",
            B.build(
                [
                    B.back()
                ]
            ),
        )

    from_user = message_or_call.from_user

    async with get_session() as session:

        member, created = await join_city(
            session,
            city.id,
            from_user,
        )

    if created:

        starting_cash = getattr(
            cfg,
            "STARTING_CASH",
            10000,
        )

        starting_energy = getattr(
            cfg,
            "STARTING_ENERGY",
            100,
        )

        text = (
            f"🌆 <b>به {city.name} خوش آمدی!</b>\n\n"

            "هویت ECHO تو ساخته شد.\n\n"

            f"💰 ${starting_cash:,}\n"
            f"⚡ {starting_energy} Energy\n\n"

            "🎯 اولین مأموریتت آماده است."
        )

        markup = B.build(

            [
                B.success(
                    "شروع مأموریت",
                    "help:start",
                )
            ],

            [
                B.primary(
                    "آموزش ECHO",
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
                    "راهنما",
                    "help:root",
                )
            ]
        )

    return text, markup


@group_router.message(
    Command("join")
)
async def cmd_group_join(
    message: Message,
    city: Optional[City] = None,
):

    text, markup = await _do_join(
        message,
        city,
    )

    await message.answer(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


@group_router.callback_query(
    F.data == "city:join"
)
async def cb_group_join(
    call: CallbackQuery,
    city: Optional[City] = None,
):

    text, markup = await _do_join(
        call,
        city,
    )

    await safe_edit_or_send(
        call.message,
        text,
        markup,
    )

    await call.answer()


# ================================================================
# CITY DASHBOARD
# ================================================================

async def render_city_dashboard(
    city: City,
):

    async with get_session() as session:

        population = await city_population(
            session,
            city.id,
        )

    text = (
        f"🌆 <b>{city.name}</b>\n\n"

        f"⭐ Level: {city.level}\n"
        f"👥 جمعیت: {population}\n"
        f"💰 خزانه: ${city.treasury:,}\n"
        f"🏷 شناسه: #{city.city_code}\n"
    )

    markup = B.build(

        [
            B.success(
                "بازی",
                "city:play",
            )
        ],

        [
            B.primary(
                "Market",
                "help:market",
            ),

            B.primary(
                "Explore",
                "help:exploration",
            ),
        ],

        [
            B.primary(
                "Missions",
                "help:start",
            ),

            B.primary(
                "Guild",
                "help:root",
            ),
        ],

        [
            B.primary(
                "Ranking",
                "city:rank",
            ),

            B.primary(
                "Help",
                "help:root",
            ),
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
            "⚠️ این Group هنوز City نیست."
        )

        return

    text, markup = await render_city_dashboard(
        city
    )

    await message.answer(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


# ================================================================
# CITY RANKING
# ================================================================

@group_router.message(
    Command("rank")
)
async def cmd_city_rank(
    message: Message,
    city: Optional[City] = None,
):

    if not city:
        return

    async with get_session() as session:

        rows = await city_ranking(
            session,
            city.id,
        )

    if not rows:

        text = (
            "🏆 هنوز شهروندی برای رتبه‌بندی وجود ندارد."
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

            name = (
                f"@{user.username}"
                if user.username
                else user.nickname
            )

            total_money = (
                wallet.cash
                + wallet.bank
            )

            lines.append(
                f"{medal} {name} "
                f"— Lv.{stats.level} "
                f"— ${total_money:,}"
            )

        text = (
            "🏆 <b>CITY RANKING</b>\n\n"
            + "\n".join(lines)
        )

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
    )


@group_router.callback_query(
    F.data == "city:rank"
)
async def cb_city_rank(
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

        name = (
            f"@{user.username}"
            if user.username
            else user.nickname
        )

        total_money = (
            wallet.cash
            + wallet.bank
        )

        lines.append(
            f"{medal} {name} "
            f"— Lv.{stats.level} "
            f"— ${total_money:,}"
        )

    text = (
        "🏆 <b>CITY RANKING</b>\n\n"
        + (
            "\n".join(lines)
            if lines
            else "هنوز کسی در رتبه‌بندی نیست."
        )
    )

    await safe_edit_or_send(
        call.message,
        text,
        B.build(
            [
                B.back("city:dashboard")
            ]
        ),
    )

    await call.answer()


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

    text, markup = await render_city_dashboard(
        city
    )

    await safe_edit_or_send(
        call.message,
        text,
        markup,
    )

    await call.answer()


# ================================================================
# PRIVATE GAMEPLAY REDIRECT
# ================================================================

GAMEPLAY_COMMANDS = (
    "market",
    "explore",
    "missions",
    "business",
    "guild",
    "world",
    "profile",
)


def redirect_to_group(
    bot_username: str,
):

    add_url = (
        f"https://t.me/{bot_username}"
        f"?startgroup=echo"
    )

    text = (
        "📍 این بخش داخل ECHO City اجرا می‌شود.\n\n"

        "برای بازی:\n"
        "1. ECHO را به یک Group اضافه کن.\n"
        "2. داخل Group دستور /join را بزن.\n"
        "3. سپس همین دستور را داخل City اجرا کن."
    )

    markup = B.build(

        [
            B.url_button(
                "افزودن به گروه",
                add_url,
            )
        ],

        [
            B.primary(
                "راهنمای City",
                "help:city",
            )
        ],
    )

    return text, markup


def register_private_gameplay_commands():

    for command_name in GAMEPLAY_COMMANDS:

        async def handler(
            message: Message,
            bot: Bot,
            _command_name=command_name,
        ):

            username = await get_bot_username(
                bot
            )

            text, markup = redirect_to_group(
                username
            )

            await message.answer(
                text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )

        private_router.message.register(
            handler,
            Command(command_name),
        )


# ================================================================
# HELP
# ================================================================

HELP_TOPICS = {

    "start": {
        "title": "🚀 شروع ECHO",

        "body": (
            "ECHO یک جهان آنلاین متنی است و هر Group یک City است.\n\n"

            "برای شروع:\n"
            "1. Bot را به Group اضافه کن.\n"
            "2. /join را بزن.\n"
            "3. اولین مأموریت را انجام بده.\n"
            "4. درآمد کسب کن.\n"
            "5. Profile را کامل کن.\n"
            "6. Market را بررسی کن.\n"
            "7. Explore کن.\n"
            "8. وارد Guild شو."
        ),
    },

    "city": {
        "title": "🌆 ECHO CITY",

        "body": (
            "City همان Group توست.\n\n"

            "هر City دارای:\n"
            "👥 Population\n"
            "⭐ Level\n"
            "💰 Treasury\n"
            "🏆 Ranking\n"
            "👥 Guilds\n"
            "🌪 Events\n\n"

            "فعالیت بازیکنان باعث رشد City می‌شود."
        ),
    },

    "market": {
        "title": "📈 راهنمای Market",

        "body": (
            "Market محل معامله منابع و دارایی‌های بازی است.\n\n"

            "قیمت‌ها ثابت نیستند.\n"
            "Supply و Demand روی قیمت اثر می‌گذارند.\n\n"

            "مثال:\n"
            "اگر Crystal برابر $1,000 باشد و تقاضا افزایش یابد، "
            "ممکن است قیمت آن بیشتر شود.\n\n"

            "هیچ سودی تضمین‌شده نیست."
        ),
    },

    "exploration": {
        "title": "🗺 راهنمای Exploration",

        "body": (
            "با Exploration می‌توانی مناطق، منابع و Discoveryهای جدید پیدا کنی.\n\n"

            "ممکن است:\n"
            "💰 Reward\n"
            "💎 Rare Item\n"
            "🧩 Discovery\n"
            "⚠️ Risk Event\n"
            "یا هیچ چیز پیدا نکنی."
        ),
    },

    "business": {
        "title": "🏢 راهنمای Business",

        "body": (
            "Business یک روش درآمد بلندمدت است.\n\n"

            "اما هزینه دارد:\n"
            "• Upgrade\n"
            "• Maintenance\n"
            "• Production\n\n"

            "Business همیشه سودده نیست."
        ),
    },

    "rules": {
        "title": "📜 قوانین ECHO",

        "body": (
            "• Gameplay اصلی داخل Group انجام می‌شود.\n"
            "• هر Group یک City مستقل است.\n"
            "• Spam و Abuse ممنوع است.\n"
            "• استفاده از Exploit ممنوع است.\n"
            "• Trading و Economy باید طبق قوانین سیستم انجام شود."
        ),
    },

    "faq": {
        "title": "❓ سوالات متداول",

        "body": (
            "<b>چطور شروع کنم؟</b>\n"
            "Bot را به Group اضافه کن و /join بزن.\n\n"

            "<b>اگر Bot حذف شود؟</b>\n"
            "City حذف نمی‌شود و در حالت غیرفعال قرار می‌گیرد.\n\n"

            "<b>چرا بعضی قابلیت‌ها قفل هستند؟</b>\n"
            "برای حفظ Progression و تعادل بازی."
        ),
    },

    "about": {
        "title": "ℹ️ درباره ECHO",

        "body": (
            "ECHO یک Multiplayer Text-Based Social World است.\n\n"
            "هر Group یک City است.\n\n"
            "<b>Every Group is a City.</b>\n"
            "<b>Play where your people are.</b>"
        ),
    },
}


HELP_ORDER = (
    "start",
    "city",
    "market",
    "exploration",
    "business",
    "rules",
    "faq",
    "about",
)


HELP_SHORT_NAMES = {

    "start": "شروع بازی",
    "city": "شهر چیست؟",
    "market": "بازار",
    "exploration": "اکتشاف",
    "business": "کسب‌وکار",
    "rules": "قوانین",
    "faq": "سؤالات متداول",
    "about": "درباره ECHO",
}


def help_root_kb():

    rows = []

    for key in HELP_ORDER:

        rows.append(
            [
                B.primary(
                    HELP_SHORT_NAMES[key],
                    f"help:{key}",
                )
            ]
        )

    rows.append(
        [
            B.close()
        ]
    )

    return B.build(
        *rows
    )


def help_topic_kb(
    topic_key: str,
):

    return B.build(

        [
            B.back(
                "help:root"
            )
        ],

        [
            B.close()
        ],
    )


@help_router.message(
    Command("help")
)
async def cmd_help(
    message: Message,
    is_group: bool = False,
    city: Optional[City] = None,
):

    async with get_session() as session:

        await record_help_view(
            session,
            message.from_user.id,
            city.id if city else None,
            "root",
            "group" if is_group else "private",
        )

    await message.answer(

        "📚 <b>مرکز راهنمای ECHO</b>\n\n"
        "یکی از بخش‌ها را انتخاب کن:",

        reply_markup=help_root_kb(),

        parse_mode=ParseMode.HTML,
    )


@help_router.callback_query(
    F.data == "help:root"
)
async def cb_help_root(
    call: CallbackQuery,
):

    await safe_edit_or_send(
        call.message,

        "📚 <b>مرکز راهنمای ECHO</b>\n\n"
        "یکی از بخش‌ها را انتخاب کن:",

        help_root_kb(),
    )

    await call.answer()


@help_router.callback_query(
    F.data.startswith("help:")
)
async def cb_help_topic(
    call: CallbackQuery,
    is_group: bool = False,
    city: Optional[City] = None,
):

    key = call.data.split(
        ":",
        1,
    )[1]

    topic = HELP_TOPICS.get(
        key
    )

    if not topic:

        await call.answer()
        return

    async with get_session() as session:

        await record_help_view(
            session,
            call.from_user.id,
            city.id if city else None,
            key,
            "group" if is_group else "private",
        )

    text = (
        f"<b>{topic['title']}</b>\n\n"
        f"{topic['body']}"
    )

    await safe_edit_or_send(
        call.message,
        text,
        help_topic_kb(key),
    )

    await call.answer()


@help_router.callback_query(
    F.data == "close"
)
async def cb_close(
    call: CallbackQuery,
):

    try:

        await call.message.delete()

    except TelegramBadRequest:

        await safe_edit_or_send(
            call.message,
            "بسته شد.",
        )

    await call.answer()


# ================================================================
# DATABASE INITIALIZATION
# ================================================================

async def init_city_tables():

    async with engine.begin() as conn:

        await conn.run_sync(
            Base.metadata.create_all
        )

    log.info(
        "ECHO City tables ready."
    )


# ================================================================
# SETUP
# ================================================================

async def setup_echo_city(
    dp: Dispatcher,
    bot: Bot,
):

    await init_city_tables()

    # Middleware
    dp.message.middleware(
        CityContextMiddleware()
    )

    dp.callback_query.middleware(
        CityContextMiddleware()
    )

    # Router registration
    #
    # مهم:
    # Help باید قبل از Gameplay Callbackهای عمومی ثبت شود.
    #
    dp.include_router(
        group_router
    )

    dp.include_router(
        private_router
    )

    dp.include_router(
        help_router
    )

    # ثبت Commandهای Private
    register_private_gameplay_commands()

    log.info(
        "ECHO City extension loaded."
    )
