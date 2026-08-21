# ================================================================
# ECHO — handlers.py
# TELEGRAM HANDLERS, GROUP UX, PRIVATE UX & HELP SYSTEM
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
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from sqlalchemy import select

from config import settings

from database import (
    City,
    get_city_by_chat,
    get_or_restore_city,
    get_or_create_user,
    get_or_create_city_member,
    deactivate_city,
    get_session,
)

from game import (
    ActionButton,
    ActionStyle,
    GameContext,
    GameResponse,
    ResponseType,
    process_message,
)


logger = logging.getLogger("echo.handlers")


# ================================================================
# ROUTERS
# ================================================================

private_router = Router(
    name="echo_private"
)

group_router = Router(
    name="echo_group"
)


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
# UI ICON REGISTRY
# ================================================================

UI_ICONS: dict[str, str] = {
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
# FALLBACK ICONS
# ================================================================

FALLBACK_ICONS = {
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


def icon(
    key: str,
) -> str:
    return FALLBACK_ICONS.get(
        key,
        "",
    )


# ================================================================
# BUTTON STYLE SYSTEM
# ================================================================

PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"


# ================================================================
# BUTTON FACTORY
# ================================================================

def make_button(
    text: str,
    callback_data: str,
    style: str = PRIMARY,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:
    """
    Button واقعی Telegram.

    PRIMARY = آبی
    SUCCESS = سبز
    DANGER = قرمز
    """

    style = style.lower()

    if style not in {
        PRIMARY,
        SUCCESS,
        DANGER,
    }:
        style = PRIMARY

    data = {
        "text": text,
        "callback_data": callback_data,
        "style": style,
    }

    custom_emoji_id = (
        UI_ICONS.get(
            icon_key,
            "",
        )
        if icon_key
        else ""
    )

    if custom_emoji_id:
        data["icon_custom_emoji_id"] = (
            custom_emoji_id
        )

    return InlineKeyboardButton(
        **data
    )


def primary_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text,
        callback_data,
        PRIMARY,
        icon_key,
    )


def success_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text,
        callback_data,
        SUCCESS,
        icon_key,
    )


def danger_button(
    text: str,
    callback_data: str,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    return make_button(
        text,
        callback_data,
        DANGER,
        icon_key,
    )


def url_button(
    text: str,
    url: str,
    style: str = PRIMARY,
    icon_key: Optional[str] = None,
) -> InlineKeyboardButton:

    style = style.lower()

    if style not in {
        PRIMARY,
        SUCCESS,
        DANGER,
    }:
        style = PRIMARY

    data = {
        "text": text,
        "url": url,
        "style": style,
    }

    custom_emoji_id = (
        UI_ICONS.get(
            icon_key,
            "",
        )
        if icon_key
        else ""
    )

    if custom_emoji_id:
        data["icon_custom_emoji_id"] = (
            custom_emoji_id
        )

    return InlineKeyboardButton(
        **data
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
        "back",
    )


def close_button() -> InlineKeyboardButton:

    return danger_button(
        "بستن",
        "ui:close",
        "close",
    )


# ================================================================
# COPY BUTTON
# ================================================================

def copy_button(
    label: str,
    text_to_copy: str,
) -> InlineKeyboardButton:
    """
    دکمه رسمی Telegram برای کپی متن.
    """

    return InlineKeyboardButton(
        text=label,
        copy_text=CopyTextButton(
            text=text_to_copy,
        ),
        style=PRIMARY,
    )


def command_copy_row(
    command: str,
) -> list[InlineKeyboardButton]:
    """
    هر دستور یک دکمه Copy مستقل دارد.
    """

    return [
        copy_button(
            f"کپی «{command}»",
            command,
        )
    ]


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
        "افزودن ECHO به گروه",
        url,
        SUCCESS,
        "add",
    )


# ================================================================
# HELP CONTENT
# ================================================================

HELP_TOPICS: dict[str, str] = {

    "start": (
        "🚀 <b>شروع ECHO</b>\n\n"
        "ECHO یک بازی چندنفره متنی است.\n\n"
        "هر گروه یک شهر مستقل در دنیای ECHO است.\n\n"
        "<b>برای شروع:</b>\n"
        "۱. ECHO را به گروه اضافه کن.\n"
        "۲. وارد شهر شو.\n"
        "۳. یکی از عبارت‌های زیر را در گروه بفرست.\n\n"
        "برای بازی معمولی، نیازی به حفظ دستورهای خاص نداری."
    ),

    "mission": (
        "🎯 <b>مأموریت‌ها</b>\n\n"
        "<b>چیست؟</b>\n"
        "مأموریت‌ها هدف‌های مشخصی هستند که با انجام آن‌ها پاداش می‌گیری.\n\n"
        "<b>چطور استفاده کنم؟</b>\n"
        "برای شروع، عبارت زیر را در گروه بفرست:\n\n"
        "🎯 <code>مأموریت</code>\n\n"
        "<b>چه چیزی لازم دارد؟</b>\n"
        "بسته به مأموریت، ممکن است انرژی، پول یا منابع لازم باشد.\n\n"
        "<b>پاداش:</b>\n"
        "پول، XP، منابع و گاهی Discovery.\n\n"
        "<b>روند انجام:</b>\n"
        "انتخاب مأموریت → بررسی جزئیات → تأیید → شروع مأموریت."
    ),

    "explore": (
        "🧭 <b>اکتشاف</b>\n\n"
        "<b>چیست؟</b>\n"
        "با اکتشاف می‌توانی مناطق جدید و Discoveryهای نادر را پیدا کنی.\n\n"
        "<b>برای شروع:</b>\n\n"
        "🧭 <code>اکتشاف</code>\n\n"
        "<b>چه چیزی ممکن است پیدا کنی؟</b>\n"
        "پول، منابع، Discovery و اتفاق‌های ویژه.\n\n"
        "<b>نکته:</b>\n"
        "نتیجه هر اکتشاف مشخص نیست و بعضی مناطق خطر دارند."
    ),

    "market": (
        "📈 <b>بازار</b>\n\n"
        "<b>چیست؟</b>\n"
        "بازار محل خرید و فروش دارایی‌های بازی است.\n\n"
        "<b>برای ورود:</b>\n\n"
        "📈 <code>بازار</code>\n\n"
        "<b>قیمت‌ها ثابت هستند؟</b>\n"
        "نه. عرضه و تقاضا می‌توانند قیمت‌ها را تغییر دهند.\n\n"
        "<b>نکته:</b>\n"
        "هیچ معامله‌ای سود قطعی ندارد."
    ),

    "work": (
        "💼 <b>کار</b>\n\n"
        "<b>چیست؟</b>\n"
        "کار یکی از روش‌های کسب درآمد در ECHO است.\n\n"
        "<b>برای شروع:</b>\n\n"
        "💼 <code>کار</code>\n\n"
        "<b>چه چیزی روی نتیجه اثر می‌گذارد؟</b>\n"
        "نوع کار، انرژی، زمان و سطح بازیکن.\n\n"
        "<b>نکته:</b>\n"
        "همه کارها درآمد یکسان ندارند."
    ),

    "guild": (
        "⚔️ <b>Guild</b>\n\n"
        "Guild یک گروه از بازیکنان است که با هم همکاری می‌کنند.\n\n"
        "<b>برای مشاهده:</b>\n\n"
        "⚔️ <code>گیلد</code>\n\n"
        "در آینده Guildها در رقابت‌های گروهی و رویدادهای بزرگ نقش خواهند داشت."
    ),

    "rank": (
        "🏆 <b>رتبه‌بندی</b>\n\n"
        "رتبه‌بندی جایگاه بازیکنان را در شهر نشان می‌دهد.\n\n"
        "<b>برای مشاهده:</b>\n\n"
        "🏆 <code>رتبه</code>\n\n"
        "رتبه می‌تواند با سطح، تجربه، فعالیت و عملکرد بازیکن تغییر کند."
    ),

    "profile": (
        "👤 <b>پروفایل</b>\n\n"
        "پروفایل اطلاعات کلی و وضعیت تو در شهر را نشان می‌دهد.\n\n"
        "<b>برای مشاهده:</b>\n\n"
        "👤 <code>پروفایل</code>\n\n"
        "اطلاعات کلی و اطلاعات مربوط به همین شهر از هم جدا نمایش داده می‌شوند."
    ),

    "city": (
        "🌆 <b>شهر</b>\n\n"
        "هر گروه یک شهر مستقل است.\n\n"
        "<b>برای مشاهده وضعیت شهر:</b>\n\n"
        "🌆 <code>شهر</code>\n\n"
        "در این بخش می‌توانی سطح، جمعیت، خزانه و وضعیت فعالیت شهر را ببینی."
    ),

    "rules": (
        "📜 <b>قوانین ECHO</b>\n\n"
        "۱. استفاده از باگ یا روش غیرمجاز ممنوع است.\n"
        "۲. ایجاد اختلال عمدی در روند بازی ممنوع است.\n"
        "۳. پاداش‌ها و دارایی‌ها نباید با روش غیرمجاز افزایش پیدا کنند.\n"
        "۴. هر بازیکن مسئول حساب خودش است.\n"
        "۵. رفتار مناسب با دیگر بازیکنان الزامی است."
    ),

    "about": (
        "ℹ️ <b>درباره ECHO</b>\n\n"
        "ECHO یک دنیای بازی متنی برای گروه‌های Telegram است.\n\n"
        "هر گروه یک شهر است.\n"
        "بازیکنان در شهر خود فعالیت می‌کنند، با هم رقابت می‌کنند و در رویدادهای جمعی شرکت می‌کنند."
    ),
}


# ================================================================
# HELP MAIN
# ================================================================

HELP_MAIN_TEXT = (
    "📚 <b>مرکز راهنمای ECHO</b>\n\n"
    "بخش موردنظر را انتخاب کن.\n\n"
    "در هر راهنما، عبارت مربوط به آن بخش را "
    "به صورت جداگانه می‌بینی و می‌توانی با یک لمس آن را کپی کنی."
)


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
                "پروفایل",
                "help:profile",
            )
        ],
        [
            primary_button(
                "شهر",
                "help:city",
                "city",
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
        [
            close_button()
        ],
    )


# ================================================================
# PRIVATE START
# ================================================================

@private_router.message(
    CommandStart()
)
async def private_start(
    message: Message,
    bot: Bot,
) -> None:

    bot_info = await bot.get_me()

    text = (
        "🌐 <b>ECHO</b>\n\n"
        "ECHO یک بازی چندنفره متنی است.\n\n"
        "هر گروه یک شهر از دنیای ECHO است.\n\n"
        "بازی اصلی داخل گروه انجام می‌شود.\n"
        "ECHO را به گروه خودت اضافه کن و بازی را با اعضای گروه شروع کن."
    )

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
        text,
        keyboard,
    )


# ================================================================
# PRIVATE HELP
# ================================================================

@private_router.message(
    F.text.in_(
        {
            "راهنما",
            "کمک",
        }
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

@private_router.message(
    F.text
)
async def private_gameplay_redirect(
    message: Message,
    bot: Bot,
) -> None:

    text = (
        message.text or ""
    ).strip()

    normalized = (
        text.replace(
            "ي",
            "ی",
        )
        .replace(
            "ك",
            "ک",
        )
        .lower()
    )

    gameplay_words = (
        "مأموریت",
        "ماموریت",
        "کار",
        "اکتشاف",
        "بازار",
        "گیلد",
        "guild",
        "رتبه",
        "پروفایل",
        "شهر",
    )

    if not any(
        word in normalized
        for word in gameplay_words
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
            "🎮 <b>بازی اصلی ECHO داخل گروه انجام می‌شود.</b>\n\n"
            "ربات را به یک گروه اضافه کن و از همان‌جا بازی را شروع کن."
        ),
        keyboard,
    )


# ================================================================
# HELP CALLBACK - PRIVATE
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

    topic = (
        callback.data or ""
    ).split(
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

    keyboard = help_topic_keyboard(
        topic
    )

    await safe_edit_or_send(
        callback.message,
        text,
        keyboard,
    )


# ================================================================
# HELP CALLBACK - GROUP
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

    topic = (
        callback.data or ""
    ).split(
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

    keyboard = help_topic_keyboard(
        topic
    )

    await safe_edit_or_send(
        callback.message,
        text,
        keyboard,
    )


def help_topic_keyboard(
    topic: str,
) -> InlineKeyboardMarkup:

    rows: list[
        list[InlineKeyboardButton]
    ] = []

    # ------------------------------------------------------------
    # دستور مخصوص همان بخش
    # ------------------------------------------------------------

    command_map = {
        "mission": "مأموریت",
        "explore": "اکتشاف",
        "market": "بازار",
        "work": "کار",
        "guild": "گیلد",
        "rank": "رتبه",
        "profile": "پروفایل",
        "city": "شهر",
    }

    command = command_map.get(
        topic
    )

    if command:

        rows.append(
            command_copy_row(
                command
            )
        )

    # ------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------

    rows.append(
        [
            back_button()
        ]
    )

    rows.append(
        [
            close_button()
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ================================================================
# CLOSE
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
# BOT ADDED / REMOVED
# ================================================================

def bot_was_added(
    event: ChatMemberUpdated,
) -> bool:

    old_status = (
        event.old_chat_member.status
    )

    new_status = (
        event.new_chat_member.status
    )

    return (
        old_status in {
            "left",
            "kicked",
        }
        and
        new_status in {
            "member",
            "administrator",
        }
    )


def bot_was_removed(
    event: ChatMemberUpdated,
) -> bool:

    old_status = (
        event.old_chat_member.status
    )

    new_status = (
        event.new_chat_member.status
    )

    return (
        old_status in {
            "member",
            "administrator",
        }
        and
        new_status in {
            "left",
            "kicked",
        }
    )


@group_router.my_chat_member()
async def group_membership_handler(
    event: ChatMemberUpdated,
    bot: Bot,
) -> None:

    if bot_was_removed(
        event
    ):

        try:

            async with get_session() as session:

                await deactivate_city(
                    session,
                    event.chat.id,
                )

            logger.info(
                "City disabled: %s",
                event.chat.id,
            )

        except Exception:

            logger.exception(
                "Failed to disable City."
            )

        return

    if not bot_was_added(
        event
    ):
        return

    actor_id = (
        event.from_user.id
        if event.from_user
        else None
    )

    try:

        async with get_session() as session:

            city = await get_or_restore_city(
                session=session,
                telegram_chat_id=event.chat.id,
                name=(
                    event.chat.title
                    or "ECHO City"
                ),
                username=event.chat.username,
                owner_user_id=actor_id,
            )

            city_name = (
                city.custom_name
                or city.name
            )

            city_id = city.id

        keyboard = build_keyboard(
            [
                success_button(
                    "ورود به شهر",
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

        await bot.send_message(
            event.chat.id,
            (
                "🌆 <b>ECHO City فعال شد</b>\n\n"
                f"نام شهر: <b>{escape_html(city_name)}</b>\n\n"
                "این گروه حالا یک شهر از دنیای ECHO است.\n\n"
                "برای شروع، وارد شهر شو."
            ),
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Failed to create City."
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
            (
                callback.data
                or ""
            ).split(":")[-1]
        )

    except ValueError:

        await callback.answer(
            "شناسه شهر معتبر نیست.",
            show_alert=True,
        )

        return

    user = callback.from_user

    try:

        async with get_session() as session:

            db_user = await get_or_create_user(
                session=session,
                user_id=user.id,
                username=user.username,
                nickname=(
                    user.first_name
                    or user.username
                    or f"بازیکن {user.id}"
                ),
            )

            await get_or_create_city_member(
                session=session,
                city_id=city_id,
                user_id=user.id,
            )

            result = await session.execute(
                select(City).where(
                    City.id == city_id
                )
            )

            city = (
                result
                .scalar_one_or_none()
            )

            if city is None:

                await callback.answer(
                    "این شهر پیدا نشد.",
                    show_alert=True,
                )

                return

            city_name = (
                city.custom_name
                or city.name
            )

        await callback.answer(
            "ورود به شهر انجام شد."
        )

        await safe_edit_or_send(
            callback.message,
            (
                f"✅ <b>به {escape_html(city_name)} خوش آمدی.</b>\n\n"
                "حالا بازی را شروع کن.\n\n"
                "برای شروع بنویس:"
            )
            + "\n\n"
            + "<code>مأموریت</code>",
            build_keyboard(
                [
                    copy_button(
                        "کپی «مأموریت»",
                        "مأموریت",
                    )
                ],
                [
                    primary_button(
                        "راهنمای بازی",
                        "help:start",
                        "help",
                    )
                ],
            ),
        )

    except Exception:

        logger.exception(
            "City join failed."
        )

        await callback.answer(
            "ورود به شهر انجام نشد.",
            show_alert=True,
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

            # ----------------------------------------------------
            # مهم:
            # اگر رویداد اضافه‌شدن ربات از دست رفته باشد،
            # با اولین پیام بازی City ساخته می‌شود.
            # ----------------------------------------------------

            if city is None:

                city = await get_or_restore_city(
                    session=session,
                    telegram_chat_id=message.chat.id,
                    name=(
                        message.chat.title
                        or "ECHO City"
                    ),
                    username=message.chat.username,
                    owner_user_id=message.from_user.id,
                )

            if not city.is_active:
                return

            city_id = city.id

        reply_id = None

        if message.reply_to_message:

            reply_id = (
                message
                .reply_to_message
                .message_id
            )

        context = GameContext(
            user_id=message.from_user.id,
            city_id=city_id,
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

        if response.is_silent:
            return

        await render_game_response(
            response,
            message,
        )

    except Exception:

        logger.exception(
            "Group message failed: "
            "user=%s chat=%s",
            message.from_user.id,
            message.chat.id,
        )


# ================================================================
# PUBLIC ACTIONS
# ================================================================

@group_router.callback_query(
    F.data.startswith("echo:action:")
)
async def group_action_callback(
    callback: CallbackQuery,
) -> None:

    if not callback.message:
        await callback.answer()
        return

    action = (
        callback.data or ""
    ).split(
        ":",
        2,
    )[-1]

    await callback.answer()

    # این بخش فعلاً فقط Foundation است.
    # منطق رأی و Event در نسخه کامل Game Engine
    # باید Server-Side پردازش شود.

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
            "✅ درخواست شرکت در رویداد ثبت شد.",
        )
        return

    if action == "VIEW_EVENT":

        await safe_answer(
            callback.message,
            "جزئیات رویداد هنوز آماده نیست.",
        )
        return

    await safe_answer(
        callback.message,
        "این گزینه هنوز فعال نیست.",
    )


# ================================================================
# RESPONSE RENDERER
# ================================================================

def response_keyboard(
    response: GameResponse,
) -> Optional[
    InlineKeyboardMarkup
]:

    if not response.actions:

        return None

    rows = []

    for action in response.actions:

        callback_data = (
            f"echo:action:"
            f"{action.action}"
        )

        style = (
            action.style
            .value
            .lower()
        )

        rows.append(
            [
                make_button(
                    action.label,
                    callback_data,
                    style,
                )
            ]
        )

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

    if not response.text:
        return

    keyboard = None

    # فقط زمانی Button نمایش داده می‌شود
    # که Response واقعاً رابط کاربری بخواهد.
    if response.requires_ui:
        keyboard = response_keyboard(
            response
        )

    if response.public:

        await safe_answer(
            message,
            response.text,
            keyboard,
        )

        return

    if response.edit_preferred:

        edited = await safe_edit(
            message,
            response.text,
            keyboard,
        )

        if edited:
            return

    await safe_answer(
        message,
        response.text,
        keyboard,
    )


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
            "Telegram send error: %s",
            exc,
        )

    except Exception:

        logger.exception(
            "Unexpected Telegram send error."
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
            "Telegram edit error."
        )

        return False


async def safe_edit_or_send(
    message: Message,
    text: str,
    reply_markup: Optional[
        InlineKeyboardMarkup
    ] = None,
) -> None:

    edited = await safe_edit(
        message,
        text,
        reply_markup,
    )

    if not edited:

        await safe_answer(
            message,
            text,
            reply_markup,
        )


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
                        action.label,
                        f"echo:action:{action.action}",
                        action.style.value.lower(),
                    )
                ]
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=rows
        )

    try:

        return await bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Public event send failed."
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
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Public vote send failed."
        )

        return None


# ================================================================
# BOT COMMAND MENU
# ================================================================

async def setup_bot_commands(
    bot: Bot,
) -> None:

    from aiogram.types import BotCommand

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
        ]
    )


# ================================================================
# HANDLER SETUP
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
# EXPORT
# ================================================================

__all__ = [
    "private_router",
    "group_router",
    "register_handlers",
    "setup_handlers",
    "setup_bot_commands",

    "render_game_response",
    "send_public_event",
    "send_public_vote",

    "primary_button",
    "success_button",
    "danger_button",
    "back_button",
    "close_button",
    "url_button",
    "copy_button",
    "build_keyboard",
    "add_to_group_button",

    "UI_ICONS",
]
