# ================================================================
# ECHO — handlers.py
# TELEGRAM HANDLERS, GROUP UX, PRIVATE UX & UI SYSTEM
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings

from database import (
    City,
    User,
    UserWallet,
    CityMember,
    get_session,
    get_city_by_chat,
    get_or_restore_city,
    get_or_create_user,
    get_or_create_city_member,
    deactivate_city,
)

from game import (
    ActionButton,
    ActionStyle,
    GameContext,
    GameResponse,
    ResponseType,
    process_message,
)


# ================================================================
# LOGGER
# ================================================================

logger = logging.getLogger("echo.handlers")


# ================================================================
# ROUTERS
# ================================================================

private_router = Router(name="echo_private")
group_router = Router(name="echo_group")


# ================================================================
# CHAT FILTERS
# ================================================================

private_router.message.filter(
    F.chat.type == ChatType.PRIVATE
)

private_router.callback_query.filter(
    F.message.chat.type == ChatType.PRIVATE
)

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
# CUSTOM EMOJI REGISTRY
# ================================================================
#
# فقط ID مربوط به Custom Emoji در این بخش قرار می‌گیرد.
#
# اگر مقدار خالی باشد:
# Button بدون Custom Emoji ساخته می‌شود.
#
# توجه:
# Custom Emoji روی Button باید از فیلد رسمی
# icon_custom_emoji_id استفاده کند.
# ================================================================

UI_ICONS: dict[str, str] = {
    "play": getattr(
        settings,
        "emoji_play_custom_id",
        "",
    ),

    "help": getattr(
        settings,
        "emoji_help_custom_id",
        "",
    ),

    "back": getattr(
        settings,
        "emoji_back_custom_id",
        "",
    ),

    "close": getattr(
        settings,
        "emoji_close_custom_id",
        "",
    ),

    "city": getattr(
        settings,
        "emoji_city_custom_id",
        "",
    ),

    "mission": getattr(
        settings,
        "emoji_mission_custom_id",
        "",
    ),

    "explore": getattr(
        settings,
        "emoji_explore_custom_id",
        "",
    ),

    "market": getattr(
        settings,
        "emoji_market_custom_id",
        "",
    ),

    "guild": getattr(
        settings,
        "emoji_guild_custom_id",
        "",
    ),

    "rank": getattr(
        settings,
        "emoji_rank_custom_id",
        "",
    ),

    "event": getattr(
        settings,
        "emoji_event_custom_id",
        "",
    ),

    "vote": getattr(
        settings,
        "emoji_vote_custom_id",
        "",
    ),

    "add": getattr(
        settings,
        "emoji_add_custom_id",
        "",
    ),
}


# ================================================================
# NORMAL EMOJI FALLBACKS
# ================================================================

FALLBACK_ICONS: dict[str, str] = {
    "play": "▶️",
    "help": "📚",
    "back": "◀️",
    "close": "✖️",
    "city": "🌆",
    "mission": "🎯",
    "explore": "🧭",
    "market": "📈",
    "guild": "👥",
    "rank": "🏆",
    "event": "🌪",
    "vote": "🏛",
    "add": "➕",
}


def fallback_icon(
    key: str,
) -> str:
    return FALLBACK_ICONS.get(
        key,
        "",
    )


# ================================================================
# BOT STYLE
# ================================================================

STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"


# ================================================================
# BUTTON FACTORY
# ================================================================

def make_button(
    text: str,
    callback_data: str,
    style: str = STYLE_PRIMARY,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:
    """
    تمام Buttonهای ECHO فقط از این Factory ساخته می‌شوند.

    Style رسمی Telegram:

    primary = آبی
    success = سبز
    danger  = قرمز

    Custom Emoji در icon_custom_emoji_id قرار می‌گیرد.
    """

    style = style.lower().strip()

    if style not in {
        STYLE_PRIMARY,
        STYLE_SUCCESS,
        STYLE_DANGER,
    }:
        style = STYLE_PRIMARY

    kwargs = {
        "text": text,
        "callback_data": callback_data,
        "style": style,
    }

    if icon_key:
        custom_id = UI_ICONS.get(
            icon_key,
            "",
        )

        if custom_id:
            kwargs[
                "icon_custom_emoji_id"
            ] = custom_id

    return InlineKeyboardButton(
        **kwargs
    )


def primary_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text=text,
        callback_data=callback_data,
        style=STYLE_PRIMARY,
        icon_key=icon_key,
    )


def success_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text=text,
        callback_data=callback_data,
        style=STYLE_SUCCESS,
        icon_key=icon_key,
    )


def danger_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text=text,
        callback_data=callback_data,
        style=STYLE_DANGER,
        icon_key=icon_key,
    )


def url_button(
    text: str,
    url: str,
    style: str = STYLE_PRIMARY,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    style = style.lower()

    if style not in {
        STYLE_PRIMARY,
        STYLE_SUCCESS,
        STYLE_DANGER,
    }:
        style = STYLE_PRIMARY

    kwargs = {
        "text": text,
        "url": url,
        "style": style,
    }

    if icon_key:
        custom_id = UI_ICONS.get(
            icon_key,
            "",
        )

        if custom_id:
            kwargs[
                "icon_custom_emoji_id"
            ] = custom_id

    return InlineKeyboardButton(
        **kwargs
    )


def build_keyboard(
    *rows: list[InlineKeyboardButton],
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=list(rows)
    )


def back_button(
    callback_data: str = "help:main",
) -> InlineKeyboardButton:

    return primary_button(
        "بازگشت",
        callback_data,
        icon_key="back",
    )


def close_button() -> InlineKeyboardButton:

    return danger_button(
        "بستن",
        "ui:close",
        icon_key="close",
    )


# ================================================================
# ADD TO GROUP
# ================================================================

def add_to_group_button(
    bot_username: str,
) -> InlineKeyboardButton:

    url = (
        f"https://t.me/"
        f"{bot_username}"
        f"?startgroup=echo"
    )

    return url_button(
        text="افزودن ECHO به گروه",
        url=url,
        style=STYLE_SUCCESS,
        icon_key="add",
    )


# ================================================================
# HELP DATA
# ================================================================

HELP_TOPICS: dict[str, str] = {

    "start": (
        "🚀 <b>شروع ECHO</b>\n\n"
        "ECHO یک بازی چندنفره متنی است.\n\n"
        "هر Group یک City است و بازی اصلی داخل همان Group انجام می‌شود.\n\n"
        "<b>برای شروع:</b>\n"
        "1. ECHO را به Group اضافه کن.\n"
        "2. داخل Group وارد شهر شو.\n"
        "3. بنویس «مأموریت».\n"
        "4. ادامه بازی را با متن انجام بده.\n\n"
        "برای Gameplay معمولی نیازی به حفظ Command نداری."
    ),

    "mission": (
        "🎯 <b>مأموریت‌ها</b>\n\n"
        "<b>چیست؟</b>\n"
        "مأموریت‌ها هدف‌های مشخصی هستند که برای انجام آن‌ها پاداش می‌گیری.\n\n"
        "<b>چطور استفاده کنم؟</b>\n"
        "داخل Group فقط بنویس «مأموریت».\n\n"
        "<b>چه چیزی لازم دارد؟</b>\n"
        "بسته به مأموریت، ممکن است انرژی، پول یا منابع لازم باشد.\n\n"
        "<b>پاداش:</b>\n"
        "پول، XP، منابع و در بعضی موارد Discovery.\n\n"
        "<b>مثال:</b>\n"
        "«مأموریت» → انتخاب شماره → تأیید → شروع مأموریت."
    ),

    "explore": (
        "🧭 <b>اکتشاف</b>\n\n"
        "<b>چیست؟</b>\n"
        "با اکتشاف می‌توانی مناطق جدید و Discoveryهای نادر را پیدا کنی.\n\n"
        "<b>چطور شروع کنم؟</b>\n"
        "داخل Group بنویس «اکتشاف».\n\n"
        "<b>ممکن است چه اتفاقی بیفتد؟</b>\n"
        "ممکن است پاداش، منابع، Discovery یا یک اتفاق پرریسک پیدا کنی.\n\n"
        "<b>نکته:</b>\n"
        "نتیجه هر اکتشاف تضمینی نیست."
    ),

    "market": (
        "📈 <b>بازار</b>\n\n"
        "بازار محل خرید و فروش منابع و دارایی‌های ECHO است.\n\n"
        "<b>چطور وارد شوم؟</b>\n"
        "داخل Group بنویس «بازار».\n\n"
        "<b>قیمت‌ها ثابت هستند؟</b>\n"
        "نه. عرضه و تقاضا می‌تواند روی قیمت‌ها اثر بگذارد.\n\n"
        "<b>نکته:</b>\n"
        "خرید یک دارایی به معنی سود قطعی نیست."
    ),

    "work": (
        "💼 <b>کار</b>\n\n"
        "بخش کار یکی از روش‌های کسب درآمد در ECHO است.\n\n"
        "هر فعالیت می‌تواند زمان، انرژی، ریسک و درآمد متفاوتی داشته باشد.\n\n"
        "برای استفاده، داخل Group بنویس «کار»."
    ),

    "guild": (
        "⚔️ <b>Guild</b>\n\n"
        "Guild یک گروه از بازیکنان است که با هم همکاری می‌کنند.\n\n"
        "Guild می‌تواند برای Eventهای گروهی، رقابت‌ها و پاداش‌های جمعی مهم باشد.\n\n"
        "برای دیدن وضعیت Guild، داخل Group بنویس «گیلد»."
    ),

    "rank": (
        "🏆 <b>رتبه‌بندی</b>\n\n"
        "رتبه‌بندی جایگاه بازیکنان را در City نشان می‌دهد.\n\n"
        "رتبه می‌تواند با عملکرد، XP، فعالیت و دیگر شاخص‌های بازی تغییر کند.\n\n"
        "برای دیدن رتبه‌بندی بنویس «رتبه»."
    ),

    "rules": (
        "📜 <b>قوانین ECHO</b>\n\n"
        "۱. استفاده از باگ و روش‌های غیرمجاز ممنوع است.\n"
        "۲. هر بازیکن فقط باید از حساب خودش استفاده کند.\n"
        "۳. Spam و ایجاد اختلال در Group ممنوع است.\n"
        "۴. پاداش‌ها و Economy نباید با Exploit دستکاری شوند.\n"
        "۵. تصمیمات مدیریتی Group خارج از قوانین بازی هستند."
    ),

    "about": (
        "ℹ️ <b>درباره ECHO</b>\n\n"
        "ECHO یک دنیای بازی متنی برای Groupهای Telegram است.\n\n"
        "هر Group یک City است.\n"
        "بازیکنان داخل City با هم رقابت، همکاری و در Eventهای مشترک شرکت می‌کنند."
    ),
}


# ================================================================
# HELP MAIN
# ================================================================

def help_main_keyboard() -> InlineKeyboardMarkup:

    return build_keyboard(
        [
            primary_button(
                "مأموریت‌ها",
                "help:mission",
                "mission",
            )
        ],
        [
            primary_button(
                "اکتشاف",
                "help:explore",
                "explore",
            )
        ],
        [
            primary_button(
                "بازار",
                "help:market",
                "market",
            )
        ],
        [
            primary_button(
                "کار",
                "help:work",
            )
        ],
        [
            primary_button(
                "Guild",
                "help:guild",
                "guild",
            )
        ],
        [
            primary_button(
                "رتبه‌بندی",
                "help:rank",
                "rank",
            )
        ],
        [
            primary_button(
                "قوانین",
                "help:rules",
            )
        ],
        [
            primary_button(
                "درباره ECHO",
                "help:about",
            )
        ],
        [
            close_button()
        ],
    )


HELP_MAIN_TEXT = (
    "📚 <b>مرکز راهنمای ECHO</b>\n\n"
    "هر بخش را انتخاب کن تا توضیح کامل، روش استفاده و نکات مهم آن را ببینی."
)


# ================================================================
# PRIVATE LANDING
# ================================================================

LANDING_TEXT = (
    "🌐 <b>ECHO</b>\n\n"
    "ECHO یک بازی چندنفره متنی است.\n\n"
    "هر Telegram Group می‌تواند یک City باشد.\n\n"
    "داخل City می‌توانی:\n"
    "🎯 مأموریت انجام بدهی\n"
    "💼 کار کنی\n"
    "🧭 اکتشاف کنی\n"
    "📈 وارد بازار شوی\n"
    "🏆 در رتبه‌بندی رقابت کنی\n"
    "🌍 در Eventهای جمعی شرکت کنی\n\n"
    "<b>بازی اصلی داخل Group انجام می‌شود.</b>"
)


# ================================================================
# PRIVATE /START
# ================================================================

@private_router.message(
    CommandStart()
)
async def private_start(
    message: Message,
    bot: Bot,
) -> None:

    if not message.from_user:
        return

    bot_info = await bot.get_me()

    keyboard = build_keyboard(
        [
            add_to_group_button(
                bot_info.username
            )
        ],
        [
            primary_button(
                "چطور بازی کنم؟",
                "help:start",
                "help",
            )
        ],
        [
            primary_button(
                "راهنمای کامل",
                "help:main",
                "help",
            )
        ],
        [
            primary_button(
                "قوانین",
                "help:rules",
            ),
            primary_button(
                "درباره ECHO",
                "help:about",
            ),
        ],
    )

    await safe_answer(
        message,
        LANDING_TEXT,
        keyboard,
    )


# ================================================================
# PRIVATE TEXT HELP
# ================================================================

@private_router.message(
    F.text.regexp(
        r"^(راهنما|کمک)$"
    )
)
async def private_help(
    message: Message,
) -> None:

    await safe_answer(
        message,
        HELP_MAIN_TEXT,
        help_main_keyboard(),
    )


# ================================================================
# PRIVATE GAMEPLAY REDIRECT
# ================================================================

PRIVATE_GAMEPLAY_WORDS = (
    "مأموریت",
    "ماموریت",
    "کار",
    "اکتشاف",
    "بازار",
    "گیلد",
    "guild",
    "رتبه",
)


@private_router.message(
    F.text
)
async def private_text_router(
    message: Message,
    bot: Bot,
) -> None:

    text = (
        message.text or ""
    ).strip()

    normalized = (
        text.lower()
    )

    if normalized in {
        "راهنما",
        "کمک",
    }:

        return

    if not any(
        keyword in normalized
        for keyword in PRIVATE_GAMEPLAY_WORDS
    ):
        return

    bot_info = await bot.get_me()

    keyboard = build_keyboard(
        [
            add_to_group_button(
                bot_info.username
            )
        ],
    )

    await safe_answer(
        message,
        (
            "🎮 <b>بازی اصلی ECHO داخل Group انجام می‌شود.</b>\n\n"
            "ربات را به یک Group اضافه کن و از همان‌جا بازی را شروع کن."
        ),
        keyboard,
    )


# ================================================================
# PRIVATE HELP CALLBACK
# ================================================================

@private_router.callback_query(
    F.data.startswith("help:")
)
async def private_help_callback(
    callback: CallbackQuery,
) -> None:

    if not callback.message:
        await callback.answer()
        return

    data = callback.data or ""

    topic = data.split(
        ":",
        1,
    )[1]

    await callback.answer()

    if topic == "main":

        await safe_edit(
            callback.message,
            HELP_MAIN_TEXT,
            help_main_keyboard(),
        )

        return

    if topic == "start":

        text = HELP_TOPICS[
            "start"
        ]

    else:

        text = HELP_TOPICS.get(
            topic
        )

        if not text:

            return

    keyboard = build_keyboard(
        [
            back_button()
        ],
        [
            close_button()
        ],
    )

    edited = await safe_edit(
        callback.message,
        text,
        keyboard,
    )

    if not edited:

        await safe_answer(
            callback.message,
            text,
            keyboard,
        )


# ================================================================
# GROUP HELP CALLBACK
# ================================================================

@group_router.callback_query(
    F.data.startswith("help:")
)
async def group_help_callback(
    callback: CallbackQuery,
) -> None:

    if not callback.message:
        await callback.answer()
        return

    data = callback.data or ""

    topic = data.split(
        ":",
        1,
    )[1]

    await callback.answer()

    if topic == "main":

        await safe_edit(
            callback.message,
            HELP_MAIN_TEXT,
            help_main_keyboard(),
        )

        return

    text = HELP_TOPICS.get(
        topic
    )

    if not text:
        return

    keyboard = build_keyboard(
        [
            back_button()
        ],
        [
            close_button()
        ],
    )

    edited = await safe_edit(
        callback.message,
        text,
        keyboard,
    )

    if not edited:

        await safe_answer(
            callback.message,
            text,
            keyboard,
        )


# ================================================================
# CLOSE CALLBACK
# ================================================================

@private_router.callback_query(
    F.data == "ui:close"
)
async def private_close(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    if not callback.message:
        return

    try:

        await callback.message.delete()

    except TelegramBadRequest:

        await safe_edit(
            callback.message,
            "بسته شد.",
        )


@group_router.callback_query(
    F.data == "ui:close"
)
async def group_close(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    if not callback.message:
        return

    try:

        await callback.message.delete()

    except TelegramBadRequest:

        await safe_edit(
            callback.message,
            "بسته شد.",
        )


# ================================================================
# BOT ADDED TO GROUP
# ================================================================

def _is_bot_added(
    event: ChatMemberUpdated,
) -> bool:

    old_status = (
        event.old_chat_member.status
    )

    new_status = (
        event.new_chat_member.status
    )

    old_inactive = old_status in {
        "left",
        "kicked",
    }

    new_active = new_status in {
        "member",
        "administrator",
    }

    return (
        old_inactive
        and new_active
    )


def _is_bot_removed(
    event: ChatMemberUpdated,
) -> bool:

    old_status = (
        event.old_chat_member.status
    )

    new_status = (
        event.new_chat_member.status
    )

    old_active = old_status in {
        "member",
        "administrator",
    }

    new_inactive = new_status in {
        "left",
        "kicked",
    }

    return (
        old_active
        and new_inactive
    )


@group_router.my_chat_member()
async def group_membership_changed(
    event: ChatMemberUpdated,
    bot: Bot,
) -> None:

    if not _is_bot_added(
        event
    ) and not _is_bot_removed(
        event
    ):
        return

    chat = event.chat

    if _is_bot_removed(
        event
    ):

        try:

            async with get_session() as session:

                await deactivate_city(
                    session,
                    chat.id,
                )

            logger.info(
                "City soft-disabled chat_id=%s",
                chat.id,
            )

        except Exception:

            logger.exception(
                "Failed to deactivate City chat_id=%s",
                chat.id,
            )

        return

    # ------------------------------------------------------------
    # BOT ADDED
    # ------------------------------------------------------------

    actor_id = (
        event.from_user.id
        if event.from_user
        else None
    )

    try:

        async with get_session() as session:

            city = await get_or_restore_city(
                session=session,
                telegram_chat_id=chat.id,
                name=chat.title or "ECHO City",
                username=chat.username,
                owner_user_id=actor_id,
            )

            is_new = city.created_at == city.updated_at

            # Do not create a player automatically.
            # City exists. Members join through city join action.

            city_id = city.id
            city_name = (
                city.custom_name
                or city.name
            )

        keyboard = build_keyboard(
            [
                success_button(
                    "ورود به City",
                    f"city:join:{city_id}",
                    "city",
                )
            ],
            [
                primary_button(
                    "راهنمای بازی",
                    "help:start",
                    "help",
                )
            ],
        )

        if is_new:

            text = (
                "🌆 <b>ECHO City فعال شد</b>\n\n"
                f"نام City: <b>{escape_html(city_name)}</b>\n\n"
                "این Group حالا بخشی از دنیای ECHO است.\n\n"
                "برای شروع بازی، وارد City شو."
            )

        else:

            text = (
                "🌆 <b>ECHO City بازگردانده شد</b>\n\n"
                f"نام City: <b>{escape_html(city_name)}</b>\n\n"
                "اطلاعات City قبلی حفظ شده است.\n\n"
                "برای ادامه، وارد City شو."
            )

        await bot.send_message(
            chat.id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Failed to initialize City chat_id=%s",
            chat.id,
        )


# ================================================================
# CITY JOIN
# ================================================================

@group_router.callback_query(
    F.data.startswith("city:join:")
)
async def city_join_callback(
    callback: CallbackQuery,
) -> None:

    if not callback.message:
        await callback.answer()
        return

    try:

        city_id = int(
            (callback.data or "")
            .split(":")[-1]
        )

    except (
        ValueError,
        TypeError,
    ):

        await callback.answer(
            "شناسه City معتبر نیست.",
            show_alert=True,
        )

        return

    user = callback.from_user

    try:

        async with get_session() as session:

            existing_user = await session.execute(
                __import__(
                    "sqlalchemy"
                ).select(
                    User
                ).where(
                    User.id
                    == user.id
                )
            )

            db_user = (
                existing_user
                .scalar_one_or_none()
            )

            if db_user is None:

                db_user = await get_or_create_user(
                    session=session,
                    user_id=user.id,
                    username=user.username,
                    nickname=(
                        user.first_name
                        or user.username
                        or f"Player {user.id}"
                    ),
                )

            else:

                db_user.username = (
                    user.username
                )

                if user.first_name:

                    db_user.nickname = (
                        user.first_name[:64]
                    )

            member = await get_or_create_city_member(
                session=session,
                city_id=city_id,
                user_id=user.id,
            )

            city_result = await session.execute(
                __import__(
                    "sqlalchemy"
                ).select(
                    City
                ).where(
                    City.id
                    == city_id
                )
            )

            city = (
                city_result
                .scalar_one_or_none()
            )

            if city is None:

                await callback.answer(
                    "City پیدا نشد.",
                    show_alert=True,
                )

                return

            city_name = (
                city.custom_name
                or city.name
            )

        await callback.answer(
            "با موفقیت وارد City شدی."
        )

        text = (
            f"🌆 <b>به {escape_html(city_name)} خوش آمدی.</b>\n\n"
            "حالا می‌توانی بازی را شروع کنی.\n\n"
            "برای شروع بنویس:\n"
            "<b>مأموریت</b>"
        )

        keyboard = build_keyboard(
            [
                primary_button(
                    "راهنمای بازی",
                    "help:start",
                    "help",
                )
            ]
        )

        edited = await safe_edit(
            callback.message,
            text,
            keyboard,
        )

        if not edited:

            await safe_answer(
                callback.message,
                text,
                keyboard,
            )

    except Exception:

        logger.exception(
            "City join failed user=%s city=%s",
            user.id,
            city_id,
        )

        await callback.answer(
            "ورود به City انجام نشد.",
            show_alert=True,
        )


# ================================================================
# CITY ENTER
# ================================================================

@group_router.callback_query(
    F.data.startswith("city:enter:")
)
async def city_enter_callback(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    if not callback.message:
        return

    city_id_raw = (
        callback.data or ""
    ).split(":")[-1]

    try:
        city_id = int(city_id_raw)
    except ValueError:
        return

    async with get_session() as session:

        member = await get_city_member(
            session,
            city_id,
            callback.from_user.id,
        )

    if member is None:

        keyboard = build_keyboard(
            [
                success_button(
                    "ورود به City",
                    f"city:join:{city_id}",
                    "city",
                )
            ]
        )

        await safe_answer(
            callback.message,
            (
                "برای بازی در این City "
                "اول باید وارد شهر شوی."
            ),
            keyboard,
        )

        return

    await safe_answer(
        callback.message,
        (
            "✅ تو داخل این City هستی.\n\n"
            "برای شروع بنویس «مأموریت»."
        ),
    )


# ================================================================
# GROUP MESSAGE HANDLER
# ================================================================

@group_router.message(
    F.text
)
async def group_message_handler(
    message: Message,
) -> None:

    if not message.from_user:
        return

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    try:

        async with get_session() as session:

            city = await get_city_by_chat(
                session,
                message.chat.id,
            )

        if city is None:

            # This Group has not been registered.
            # Do not reply to normal chat.
            return

        if not city.is_active:

            return

        reply_id = None

        if message.reply_to_message:

            reply_id = (
                message
                .reply_to_message
                .message_id
            )

        context = GameContext(
            user_id=message.from_user.id,
            city_id=city.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            is_group=True,
            is_private=False,
            username=(
                message.from_user.username
                or ""
            ),
            reply_to_message_id=reply_id,
        )

        response = await process_message(
            context
        )

        if response is None:
            return

        await render_game_response(
            response,
            message,
        )

    except Exception:

        logger.exception(
            "Group message handling failed "
            "user=%s chat=%s",
            message.from_user.id,
            message.chat.id,
        )


# ================================================================
# CALLBACK ACTIONS
# ================================================================

@group_router.callback_query(
    F.data.startswith("echo:action:")
)
async def generic_group_action(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    if not callback.message:
        return

    action = (
        callback.data or ""
    ).split(
        ":",
        2,
    )[-1]

    # Foundation behavior.
    #
    # Full action processing will be connected to game.py
    # when collective Event engine is implemented.

    if action == "VOTE_YES":

        await safe_answer(
            callback.message,
            "✅ رأی تو ثبت شد.",
        )

        return

    if action == "VOTE_NO":

        await safe_answer(
            callback.message,
            "🔴 رأی تو ثبت شد.",
        )

        return

    if action == "JOIN_EVENT":

        await safe_answer(
            callback.message,
            "ورود به Event ثبت شد.",
        )

        return

    if action == "VIEW_EVENT":

        await safe_answer(
            callback.message,
            "جزئیات Event هنوز در حال آماده‌سازی است.",
        )

        return

    await safe_answer(
        callback.message,
        "این گزینه هنوز به بازی متصل نشده است.",
    )


# ================================================================
# GAME RESPONSE RENDERER
# ================================================================

def action_to_callback(
    action: ActionButton,
) -> str:
    """
    تبدیل Action به callback_data.

    Callback data باید کوتاه باشد.
    """

    return (
        f"echo:action:{action.action}"
    )


def build_response_keyboard(
    response: GameResponse,
) -> Optional[
    InlineKeyboardMarkup
]:

    if not response.actions:
        return None

    rows: list[
        list[InlineKeyboardButton]
    ] = []

    for action in response.actions:

        style = (
            action.style.value.lower()
        )

        button = make_button(
            text=action.label,
            callback_data=(
                action_to_callback(
                    action
                )
            ),
            style=style,
        )

        rows.append(
            [button]
        )

    if not rows:

        return None

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def render_game_response(
    response: GameResponse,
    message: Message,
) -> None:

    if response is None:
        return

    if response.is_silent:
        return

    text = response.text

    if not text:
        return

    keyboard = None

    if (
        response.requires_ui
        or response.public
    ):

        keyboard = (
            build_response_keyboard(
                response
            )
        )

    # Public Event
    if response.public:

        await safe_answer(
            message,
            text,
            keyboard,
        )

        return

    # Personal Response

    # Game Engine controls whether edit is preferred.
    if response.edit_preferred:

        edited = await safe_edit(
            message,
            text,
            keyboard,
        )

        if edited:
            return

    await safe_answer(
        message,
        text,
        keyboard,
    )


# ================================================================
# PUBLIC EVENT HELPERS
# ================================================================

async def send_public_event(
    bot: Bot,
    chat_id: int,
    text: str,
    actions: Optional[
        list[ActionButton]
    ] = None,
) -> Optional[Message]:

    keyboard = None

    if actions:

        rows = []

        for action in actions:

            rows.append(
                [
                    make_button(
                        text=action.label,
                        callback_data=action_to_callback(
                            action
                        ),
                        style=(
                            action.style
                            .value
                            .lower()
                        ),
                    )
                ]
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=rows
        )

    try:

        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Public event send failed chat=%s",
            chat_id,
        )

        return None


async def send_public_vote(
    bot: Bot,
    chat_id: int,
    text: str,
    yes_callback: str,
    no_callback: str,
) -> Optional[Message]:

    keyboard = build_keyboard(
        [
            success_button(
                "موافقم",
                yes_callback,
                "vote",
            ),
            danger_button(
                "مخالفم",
                no_callback,
            ),
        ]
    )

    try:

        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Public vote send failed chat=%s",
            chat_id,
        )

        return None


# ================================================================
# SAFE TELEGRAM HELPERS
# ================================================================

async def safe_answer(
    message: Message,
    text: str,
    reply_markup: Optional[
        InlineKeyboardMarkup
    ] = None,
) -> Optional[Message]:

    try:

        return await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    except TelegramBadRequest as exc:

        logger.warning(
            "Telegram answer failed: %s",
            str(exc),
        )

    except Exception:

        logger.exception(
            "Unexpected Telegram answer error"
        )

    return None


async def safe_edit(
    message: Message,
    text: str,
    reply_markup: Optional[
        InlineKeyboardMarkup
    ] = None,
) -> bool:

    try:

        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

        return True

    except TelegramBadRequest:

        return False

    except Exception:

        logger.exception(
            "Unexpected Telegram edit error"
        )

        return False


def escape_html(
    value: str,
) -> str:

    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ================================================================
# ROUTER REGISTRATION
# ================================================================

def register_handlers(
    dp: Dispatcher,
) -> None:

    dp.include_router(
        group_router
    )

    dp.include_router(
        private_router
    )


def setup_handlers(
    dp: Dispatcher,
) -> None:

    register_handlers(
        dp
    )


# ================================================================
# PUBLIC API
# ================================================================

__all__ = [
    "private_router",
    "group_router",
    "register_handlers",
    "setup_handlers",

    "render_game_response",
    "send_public_event",
    "send_public_vote",

    "primary_button",
    "success_button",
    "danger_button",
    "back_button",
    "close_button",
    "url_button",
    "build_keyboard",
    "add_to_group_button",

    "UI_ICONS",
]
