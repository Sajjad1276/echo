# ================================================================
# ECHO — handlers.py
# Telegram Handlers, Group UX, Private UX & UI System
# ================================================================
#
# این فایل مترجم بین Telegram و ECHO Game Engine است.
# Game Logic در game.py قرار دارد.
# Data Logic در database.py قرار دارد.
# Configuration در config.py قرار دارد.
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from config import config
from game import (
    process_message,
    process_callback,
    GameContext,
    GameResponse,
    ResponseType,
)

# ================================================================
# Logging
# ================================================================

logger = logging.getLogger(__name__)

# ================================================================
# Custom Emoji Registry
# ================================================================
# تمام Custom Emoji IDهای پروژه اینجا تعریف می‌شوند.
# هرگز در Handler یا Router پراکنده Hard-Code نشوند.
# اگر Custom Emoji در دسترس نبود، Fallback استفاده می‌شود.

UI_ICONS: dict[str, str] = {
    # format: "key": "custom_emoji_id||fallback_emoji"
    "play":      getattr(config, "EMOJI_PLAY",    "") or "▶️",
    "help":      getattr(config, "EMOJI_HELP",    "") or "📚",
    "back":      getattr(config, "EMOJI_BACK",    "") or "◀️",
    "close":     getattr(config, "EMOJI_CLOSE",   "") or "✖️",
    "danger":    getattr(config, "EMOJI_DANGER",  "") or "⚠️",
    "success":   getattr(config, "EMOJI_SUCCESS", "") or "✅",
    "city":      getattr(config, "EMOJI_CITY",    "") or "🌆",
    "mission":   getattr(config, "EMOJI_MISSION", "") or "🎯",
    "explore":   getattr(config, "EMOJI_EXPLORE", "") or "🧭",
    "market":    getattr(config, "EMOJI_MARKET",  "") or "📈",
    "guild":     getattr(config, "EMOJI_GUILD",   "") or "👥",
    "rank":      getattr(config, "EMOJI_RANK",    "") or "🏆",
    "event":     getattr(config, "EMOJI_EVENT",   "") or "🌪",
    "work":      getattr(config, "EMOJI_WORK",    "") or "🏢",
    "globe":     getattr(config, "EMOJI_GLOBE",   "") or "🌐",
    "add":       getattr(config, "EMOJI_ADD",     "") or "➕",
    "vote":      getattr(config, "EMOJI_VOTE",    "") or "🏛",
    "diamond":   getattr(config, "EMOJI_DIAMOND", "") or "💎",
    "alert":     getattr(config, "EMOJI_ALERT",   "") or "🚨",
}


def icon(key: str) -> str:
    """
    آیکون مناسب را از Registry برمی‌گرداند.
    اگر Custom Emoji ID تعریف شده باشد، آن را برمی‌گرداند.
    در غیر این صورت Fallback Emoji برمی‌گرداند.
    """
    return UI_ICONS.get(key, "")


# ================================================================
# Button Style System
# ================================================================
# سه Style رسمی ECHO:
#   PRIMARY  → انتخاب معمولی / مشاهده / ادامه / بازگشت
#   SUCCESS  → تأیید / شروع / شرکت / دریافت
#   DANGER   → لغو / خروج / حذف / خطر
#
# هیچ Handler مجاز به تعریف Style جدید نیست.

BUTTON_PREFIXES: dict[str, str] = {
    "PRIMARY": "",
    "SUCCESS": "✅ ",
    "DANGER":  "🚫 ",
}


# ================================================================
# Button Factory
# ================================================================
# تمام Buttonهای ECHO فقط از این توابع ساخته می‌شوند.
# هیچ Router یا Handler نباید مستقیم InlineKeyboardButton بسازد.

def _make_button(style: str, text: str, callback_data: str) -> InlineKeyboardButton:
    prefix = BUTTON_PREFIXES.get(style, "")
    return InlineKeyboardButton(
        text=f"{prefix}{text}",
        callback_data=callback_data,
    )


def primary_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """دکمه معمولی — مشاهده / ادامه / بازگشت"""
    return _make_button("PRIMARY", text, callback_data)


def success_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """دکمه مثبت — شروع / تأیید / شرکت / دریافت"""
    return _make_button("SUCCESS", text, callback_data)


def danger_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """دکمه خطر — لغو / خروج / حذف"""
    return _make_button("DANGER", text, callback_data)


def back_button(callback_data: str = "help:main") -> InlineKeyboardButton:
    """دکمه بازگشت — PRIMARY"""
    return primary_button(f"{icon('back')} بازگشت", callback_data)


def close_button(callback_data: str = "ui:close") -> InlineKeyboardButton:
    """دکمه بستن — DANGER"""
    return danger_button(f"{icon('close')} بستن", callback_data)


def url_button(text: str, url: str) -> InlineKeyboardButton:
    """دکمه لینک خارجی"""
    return InlineKeyboardButton(text=text, url=url)


def build_keyboard(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """
    یک InlineKeyboardMarkup از ردیف‌های دکمه می‌سازد.
    هر آرگومان یک ردیف (list) از دکمه‌هاست.
    """
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def add_to_group_button(bot_username: str) -> InlineKeyboardButton:
    """دکمه افزودن ربات به گروه — SUCCESS"""
    deep_link = f"https://t.me/{bot_username}?startgroup=start"
    return url_button(f"{icon('add')} افزودن ECHO به گروه", deep_link)


# ================================================================
# Help Texts
# ================================================================

HELP_TOPICS: dict[str, str] = {
    "mission": f"""{icon('mission')} مأموریت‌ها

با انجام مأموریت‌ها پول، XP و منابع به دست می‌آوری.

۱. این بخش چیست؟
   هر روز چند مأموریت در دسترس داری.

۲. چرا مهم است؟
   مأموریت‌ها منبع اصلی درآمد و رشد توست.

۳. چطور استفاده کنم؟
   در گروه بنویس «مأموریت» و شماره را انتخاب کن.

۴. چه نیاز دارد؟
   برخی مأموریت‌ها انرژی یا منابع خاص می‌خواهند.

۵. چه به دست می‌آورم؟
   پول، XP، منابع و گاهی Discovery.

۶. ریسک؟
   برخی مأموریت‌های پیشرفته ممکن است شکست بخورند.

۷. قدم بعدی؟
   بنویس «مأموریت» و شماره را انتخاب کن.

مثال:
   «مأموریت» → «2» → نتیجه""",

    "explore": f"""{icon('explore')} اکتشاف

با اکتشاف می‌توانی مناطق جدید پیدا کنی.

۱. این بخش چیست؟
   رفتن به مناطق ناشناخته و پیدا کردن منابع یا Discovery.

۲. چرا مهم است؟
   بعضی Discoveryها فقط از این راه به دست می‌آیند.

۳. چطور استفاده کنم؟
   در گروه بنویس «اکتشاف».

۴. چه نیاز دارد؟
   انرژی و گاهی تجهیزات خاص.

۵. چه به دست می‌آورم؟
   پول، XP، منابع و Discovery.

۶. ریسک؟
   هر اکتشاف تضمینی نیست. بعضی مناطق خطر دارند.

۷. قدم بعدی؟
   بنویس «اکتشاف» در گروه.

مثال:
   «اکتشاف» → نتیجه اعلام می‌شود""",

    "market": f"""{icon('market')} بازار

در بازار می‌توانی منابع و آیتم بخری یا بفروشی.

۱. این بخش چیست؟
   مبادله منابع و آیتم بین بازیکنان.

۲. چرا مهم است؟
   قیمت‌ها بر اساس عرضه و تقاضا تغییر می‌کنند.

۳. چطور استفاده کنم؟
   در گروه بنویس «بازار».

۴. چه نیاز دارد؟
   پول کافی برای خرید.

۵. چه به دست می‌آورم؟
   منابع، آیتم یا سود از فروش.

۶. ریسک؟
   قیمت‌ها ممکن است تغییر کنند.

۷. قدم بعدی؟
   بنویس «بازار» در گروه.""",

    "work": f"""{icon('work')} کسب‌وکار

با کار در کسب‌وکار می‌توانی درآمد منظم داشته باشی.

۱. این بخش چیست؟
   ایجاد یا مدیریت کسب‌وکار در City.

۲. چرا مهم است؟
   درآمد منظم و رشد ثروت.

۳. چطور استفاده کنم؟
   در گروه بنویس «کار».

۴. چه نیاز دارد؟
   سرمایه اولیه.

۵. چه به دست می‌آورم؟
   درآمد، XP و جایگاه اجتماعی.

۶. ریسک؟
   کسب‌وکار ممکن است زیان بدهد.

۷. قدم بعدی؟
   بنویس «کار» در گروه.""",

    "guild": f"""{icon('guild')} Guild

Guild یک گروه از بازیکنان است که با هم بازی می‌کنند.

۱. این بخش چیست؟
   تیم‌بندی با بازیکنان دیگر.

۲. چرا مهم است؟
   برخی Eventها و چالش‌ها فقط با Guild قابل انجام است.

۳. چطور استفاده کنم؟
   در گروه بنویس «Guild».

۴. چه نیاز دارد؟
   پیدا کردن یا ساختن Guild.

۵. چه به دست می‌آورم؟
   پاداش‌های دسته‌جمعی و Rank بالاتر.

۶. ریسک؟
   تصمیمات Guild بر همه اعضا تأثیر می‌گذارد.

۷. قدم بعدی؟
   بنویس «Guild» در گروه.""",

    "rank": f"""{icon('rank')} رتبه‌بندی

رتبه‌بندی نشان‌دهنده جایگاه تو در City و دنیای ECHO است.

۱. این بخش چیست؟
   جدول ترتیب بازیکنان بر اساس عملکرد.

۲. چرا مهم است؟
   Rank بالا پاداش‌های ویژه دارد.

۳. چطور مشاهده کنم؟
   در گروه بنویس «رتبه».

۴. معیار رتبه‌بندی؟
   XP، پول، مأموریت‌ها و Discoveryها.

۵. چه به دست می‌آورم؟
   پاداش هفتگی و اعتبار.

۶. ریسک؟
   رتبه ممکن است تغییر کند.

۷. قدم بعدی؟
   بنویس «رتبه» در گروه.""",
}

HELP_MAIN_TEXT = f"""{icon('help')} راهنمای ECHO

از اینجا می‌توانی درباره بخش‌های مختلف بازی اطلاعات کامل بگیری.

موضوع موردنظر را انتخاب کن."""

WELCOME_TEXT = f"""{icon('globe')} ECHO

هر Group می‌تواند یک City باشد.

بازی اصلی داخل Group انجام می‌شود.
ربات را به یک Group اضافه کن و از همان‌جا شروع کن."""

CITY_CREATED_TEXT = f"""{icon('city')} ECHO City فعال شد.

این گروه حالا یک شهر از دنیای ECHO است.

برای شروع، وارد شهر شو و بازی را آغاز کن."""

CITY_RESTORED_TEXT = f"""{icon('city')} ECHO City بازگردانده شد.

خوش برگشتی! شهر همان‌جایی که بودی منتظرت است."""

REDIRECT_TO_GROUP_TEXT = f"""{icon('play')} بازی اصلی ECHO داخل Group انجام می‌شود.

ربات را به یک Group اضافه کن و از همان‌جا شروع کن."""

NOT_MEMBER_TEXT = f"""{icon('city')} برای بازی در این City، اول وارد شهر شو."""

RATE_LIMIT_TEXT = "یک لحظه صبر کن و دوباره امتحان کن."

UNKNOWN_ERROR_TEXT = "مشکلی پیش آمد. لطفاً دوباره امتحان کن."

# ================================================================
# Routers
# ================================================================

private_router = Router(name="private")
private_router.message.filter(lambda m: m.chat.type == ChatType.PRIVATE)
private_router.callback_query.filter(lambda c: c.message.chat.type == ChatType.PRIVATE)

group_router = Router(name="group")
group_router.message.filter(
    lambda m: m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
)

# ================================================================
# Helpers
# ================================================================


def _log(
    handler: str,
    user_id: int,
    chat_id: int,
    action: str,
    city_id: Optional[int] = None,
    response_type: Optional[str] = None,
) -> None:
    """
    لاگ ایمن — هرگز Token یا Password لاگ نمی‌شود.
    """
    logger.info(
        "[%s] user=%s chat=%s city=%s action=%s response=%s",
        handler,
        user_id,
        chat_id,
        city_id,
        action,
        response_type,
    )


async def _safe_send(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    reply_to: Optional[int] = None,
) -> Optional[Message]:
    """
    ارسال پیام با مدیریت خطای Telegram.
    """
    try:
        return await message.answer(
            text,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.warning("Telegram send error: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected send error: %s", e)
        return None


async def _safe_edit(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """
    ویرایش پیام موجود. در صورت شکست False برمی‌گرداند.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as e:
        logger.debug("Edit failed (will send new): %s", e)
        return False
    except Exception as e:
        logger.warning("Unexpected edit error: %s", e)
        return False


def _build_action_keyboard(
    actions: list[dict],
) -> Optional[InlineKeyboardMarkup]:
    """
    بر اساس لیست actions از GameResponse، keyboard می‌سازد.
    هر action باید شامل: type، label، callback_data باشد.
    style اختیاری است و مقادیر مجاز: PRIMARY، SUCCESS، DANGER
    """
    if not actions:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for action in actions:
        action_type = action.get("type", "")
        label = action.get("label", action_type)
        cb = action.get("callback_data", f"action:{action_type.lower()}")
        style = action.get("style", "PRIMARY").upper()

        if style == "SUCCESS":
            btn = success_button(label, cb)
        elif style == "DANGER":
            btn = danger_button(label, cb)
        else:
            btn = primary_button(label, cb)

        rows.append([btn])

    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================================================================
# GameResponse Renderer
# ================================================================


async def render_game_response(
    response: GameResponse,
    message: Message,
    bot: Bot,
    previous_bot_message: Optional[Message] = None,
) -> None:
    """
    مرکزی‌ترین تابع Rendering.

    GameResponse → Telegram Message

    تصمیم‌ها بر اساس:
      - response.response_type
      - response.public
      - response.edit_preferred
      - response.actions
      - response.metadata
    """
    if response is None:
        return

    text = response.text or ""
    if not text:
        return

    response_type = getattr(response, "response_type", ResponseType.PERSONAL)
    is_public = getattr(response, "public", False)
    edit_preferred = getattr(response, "edit_preferred", False)
    actions = getattr(response, "actions", []) or []
    requires_ui = getattr(response, "requires_ui", False)

    # Build keyboard only for public/collective or requires_ui
    keyboard: Optional[InlineKeyboardMarkup] = None
    if is_public or requires_ui:
        keyboard = _build_action_keyboard(actions)

    _log(
        handler="renderer",
        user_id=message.from_user.id if message.from_user else 0,
        chat_id=message.chat.id,
        action=str(response_type),
        response_type=str(response_type),
    )

    # Rate limit
    if response_type == ResponseType.RATE_LIMITED:
        await _safe_send(message, RATE_LIMIT_TEXT)
        return

    # Error
    if response_type == ResponseType.ERROR:
        await _safe_send(message, text or UNKNOWN_ERROR_TEXT)
        return

    # Public → همیشه پیام جدید برای همه
    if is_public:
        await _safe_send(message, text, reply_markup=keyboard)
        return

    # Personal → edit یا send
    if edit_preferred and previous_bot_message is not None:
        edited = await _safe_edit(previous_bot_message, text, reply_markup=keyboard)
        if not edited:
            await _safe_send(message, text, reply_markup=keyboard)
        return

    await _safe_send(message, text, reply_markup=keyboard)


# ================================================================
# Private Router — /start
# ================================================================


@private_router.message(Command("start"))
async def private_start(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    _log("private_start", user_id, chat_id, "/start")

    bot_info = await bot.get_me()
    bot_username = bot_info.username

    keyboard = build_keyboard(
        [add_to_group_button(bot_username)],
        [primary_button(f"{icon('help')} چطور بازی کنم؟", "help:how_to_play")],
        [primary_button(f"{icon('help')} راهنمای کامل", "help:main")],
        [primary_button("📜 قوانین", "help:rules")],
    )

    await _safe_send(message, WELCOME_TEXT, reply_markup=keyboard)


# ================================================================
# Private Router — راهنما (متنی)
# ================================================================


@private_router.message(lambda m: m.text and "راهنما" in m.text)
async def private_help_text(message: Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    _log("private_help_text", user_id, chat_id, "help")
    await _send_help_main(message)


# ================================================================
# Private Router — Gameplay Redirect
# ================================================================

_GAMEPLAY_KEYWORDS = (
    "مأموریت", "اکتشاف", "بازار", "کار", "Guild", "گیلد",
    "رتبه", "شهر", "کسب", "خرید", "فروش", "انرژی",
)


@private_router.message(
    lambda m: m.text and any(kw in m.text for kw in _GAMEPLAY_KEYWORDS)
)
async def private_gameplay_redirect(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    _log("private_gameplay_redirect", user_id, chat_id, "redirect")

    bot_info = await bot.get_me()
    keyboard = build_keyboard(
        [add_to_group_button(bot_info.username)],
    )
    await _safe_send(message, REDIRECT_TO_GROUP_TEXT, reply_markup=keyboard)


# ================================================================
# Private Router — Callback: Help Navigation
# ================================================================


@private_router.callback_query(lambda c: c.data and c.data.startswith("help:"))
async def private_help_callback(callback: CallbackQuery) -> None:
    topic = callback.data.split(":", 1)[1]
    _log(
        "private_help_callback",
        callback.from_user.id,
        callback.message.chat.id,
        f"help:{topic}",
    )

    await callback.answer()

    if topic == "main" or topic == "how_to_play":
        await _edit_or_send_help_main(callback.message)
    elif topic in HELP_TOPICS:
        text = HELP_TOPICS[topic]
        keyboard = build_keyboard(
            [back_button("help:main")],
            [close_button()],
        )
        edited = await _safe_edit(callback.message, text, reply_markup=keyboard)
        if not edited:
            await _safe_send(callback.message, text, reply_markup=keyboard)
    elif topic == "rules":
        rules_text = (
            "📜 قوانین ECHO\n\n"
            "۱. احترام به بازیکنان دیگر.\n"
            "۲. استفاده از باگ یا روش‌های غیرمجاز ممنوع است.\n"
            "۳. تصمیمات City باید با رأی اکثریت باشد.\n"
            "۴. هر بازیکن فقط یک اکانت می‌تواند داشته باشد.\n"
            "۵. تخلف باعث محرومیت از بازی می‌شود."
        )
        keyboard = build_keyboard(
            [back_button("help:main")],
            [close_button()],
        )
        edited = await _safe_edit(callback.message, rules_text, reply_markup=keyboard)
        if not edited:
            await _safe_send(callback.message, rules_text, reply_markup=keyboard)


# ================================================================
# Private Router — Callback: UI Close
# ================================================================


@private_router.callback_query(lambda c: c.data == "ui:close")
async def private_ui_close(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


# ================================================================
# Help Utilities
# ================================================================


async def _send_help_main(message: Message) -> None:
    keyboard = _help_main_keyboard()
    await _safe_send(message, HELP_MAIN_TEXT, reply_markup=keyboard)


async def _edit_or_send_help_main(message: Message) -> None:
    keyboard = _help_main_keyboard()
    edited = await _safe_edit(message, HELP_MAIN_TEXT, reply_markup=keyboard)
    if not edited:
        await _safe_send(message, HELP_MAIN_TEXT, reply_markup=keyboard)


def _help_main_keyboard() -> InlineKeyboardMarkup:
    return build_keyboard(
        [primary_button(f"{icon('mission')} مأموریت‌ها", "help:mission")],
        [primary_button(f"{icon('explore')} اکتشاف", "help:explore")],
        [primary_button(f"{icon('market')} بازار", "help:market")],
        [primary_button(f"{icon('work')} کسب‌وکار", "help:work")],
        [primary_button(f"{icon('guild')} Guild", "help:guild")],
        [primary_button(f"{icon('rank')} رتبه‌بندی", "help:rank")],
        [close_button()],
    )


# ================================================================
# Group Router — Bot Added To Group
# ================================================================


@group_router.my_chat_member(
    ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER)
)
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    chat_id = event.chat.id
    _log("bot_added", 0, chat_id, "added_to_group")

    try:
        city_result = await process_message(
            GameContext(
                user_id=event.new_chat_member.user.id,
                chat_id=chat_id,
                text="__CITY_INIT__",
                reply_to_message_id=None,
                metadata={"event": "bot_added"},
            )
        )

        if city_result and getattr(city_result, "metadata", {}).get("city_restored"):
            text = CITY_RESTORED_TEXT
        else:
            text = CITY_CREATED_TEXT

        keyboard = build_keyboard(
            [success_button(f"{icon('city')} ورود به شهر", "city:enter")],
        )
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error("Error during bot_added_to_group: %s", e)
        await bot.send_message(chat_id, CITY_CREATED_TEXT, parse_mode="HTML")


# ================================================================
# Group Router — Bot Removed From Group
# ================================================================


@group_router.my_chat_member(
    ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER)
)
async def bot_removed_from_group(event: ChatMemberUpdated) -> None:
    chat_id = event.chat.id
    _log("bot_removed", 0, chat_id, "removed_from_group")

    try:
        # Soft Disable — حذف City انجام نمی‌شود
        await process_message(
            GameContext(
                user_id=event.old_chat_member.user.id,
                chat_id=chat_id,
                text="__CITY_SOFT_DISABLE__",
                reply_to_message_id=None,
                metadata={"event": "bot_removed"},
            )
        )
    except Exception as e:
        logger.error("Error during bot_removed_from_group: %s", e)


# ================================================================
# Group Router — Group Callback (Votes / Events)
# ================================================================


@group_router.callback_query()
async def group_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    data = callback.data or ""

    _log("group_callback", user_id, chat_id, data)

    await callback.answer()

    try:
        context = GameContext(
            user_id=user_id,
            chat_id=chat_id,
            text=f"__CALLBACK__{data}",
            reply_to_message_id=callback.message.message_id,
            metadata={"callback_data": data, "source": "group_callback"},
        )
        response = await process_callback(context)
        if response:
            await render_game_response(response, callback.message, bot)
    except Exception as e:
        logger.error("Group callback error: %s", e)
        await _safe_send(callback.message, UNKNOWN_ERROR_TEXT)


# ================================================================
# Group Router — Main Message Handler
# ================================================================


@group_router.message()
async def group_message_handler(message: Message, bot: Bot) -> None:
    """
    تمام پیام‌های Text گروه از اینجا عبور می‌کنند.

    Flow:
      Telegram Message
        ↓
      Extract IDs (فقط از Telegram Update)
        ↓
      Build GameContext
        ↓
      game.process_message()
        ↓
      GameResponse
        ↓
      render_game_response()
    """
    # پیام باید متن داشته باشد
    if not message.text:
        return

    # User ID فقط از Telegram Update
    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()

    # Reply context
    reply_to = None
    if message.reply_to_message:
        reply_to = message.reply_to_message.message_id

    _log("group_message", user_id, chat_id, text[:30])

    try:
        context = GameContext(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to,
            metadata={
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
            },
        )

        response: Optional[GameResponse] = await process_message(context)

        # اگر game.py پاسخی نداد → پیام عادی گروه → نادیده بگیر
        if response is None:
            return

        await render_game_response(response, message, bot)

    except Exception as e:
        logger.error(
            "Unhandled error in group_message_handler user=%s chat=%s: %s",
            user_id,
            chat_id,
            e,
        )
        await _safe_send(message, UNKNOWN_ERROR_TEXT)


# ================================================================
# Group Router — "ورود به شهر" Callback
# ================================================================


@group_router.callback_query(lambda c: c.data == "city:enter")
async def group_city_enter_callback(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    _log("city_enter", user_id, chat_id, "city:enter")

    await callback.answer()

    try:
        context = GameContext(
            user_id=user_id,
            chat_id=chat_id,
            text="__CITY_ENTER__",
            reply_to_message_id=None,
            metadata={"source": "city_enter_button"},
        )
        response = await process_message(context)
        if response:
            await render_game_response(response, callback.message, bot)
        else:
            # اگر عضو نیست، راهنمایی کن
            keyboard = build_keyboard(
                [success_button(f"{icon('city')} ورود به شهر", "city:join")],
            )
            await _safe_send(callback.message, NOT_MEMBER_TEXT, reply_markup=keyboard)
    except Exception as e:
        logger.error("City enter callback error: %s", e)
        await _safe_send(callback.message, UNKNOWN_ERROR_TEXT)


# ================================================================
# Group Router — "پیوستن به City" Callback
# ================================================================


@group_router.callback_query(lambda c: c.data == "city:join")
async def group_city_join_callback(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    _log("city_join", user_id, chat_id, "city:join")

    await callback.answer()

    try:
        context = GameContext(
            user_id=user_id,
            chat_id=chat_id,
            text="__CITY_JOIN__",
            reply_to_message_id=None,
            metadata={"source": "city_join_button"},
        )
        response = await process_message(context)
        if response:
            await render_game_response(response, callback.message, bot)
    except Exception as e:
        logger.error("City join callback error: %s", e)
        await _safe_send(callback.message, UNKNOWN_ERROR_TEXT)


# ================================================================
# Public Event Helpers (برای استفاده از game.py)
# ================================================================


async def send_public_event(
    bot: Bot,
    chat_id: int,
    text: str,
    actions: Optional[list[dict]] = None,
) -> Optional[Message]:
    """
    ارسال یک Public Event به گروه.
    این تابع توسط game.py یا task scheduler فراخوانی می‌شود.

    مثال actions:
      [
        {"type": "JOIN_EVENT", "label": "کمک به City",
         "callback_data": "event:join:123", "style": "SUCCESS"},
        {"type": "VIEW_EVENT", "label": "جزئیات Event",
         "callback_data": "event:view:123", "style": "PRIMARY"},
      ]
    """
    keyboard = _build_action_keyboard(actions or [])
    try:
        return await bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.warning("Public event send error chat=%s: %s", chat_id, e)
        return None
    except Exception as e:
        logger.error("Unexpected public event error: %s", e)
        return None


async def send_public_vote(
    bot: Bot,
    chat_id: int,
    text: str,
    yes_callback: str,
    no_callback: str,
    yes_label: str = "موافقم",
    no_label: str = "مخالفم",
) -> Optional[Message]:
    """
    ارسال یک رأی‌گیری عمومی به گروه.
    منطق رأی در game.py/database.py است.
    """
    keyboard = build_keyboard(
        [success_button(f"{icon('vote')} {yes_label}", yes_callback)],
        [danger_button(f"{icon('danger')} {no_label}", no_callback)],
    )
    try:
        return await bot.send_message(
            chat_id, text, reply_markup=keyboard, parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        logger.warning("Vote send error chat=%s: %s", chat_id, e)
        return None


# ================================================================
# Export
# ================================================================

__all__ = [
    # Routers
    "private_router",
    "group_router",
    # Button Factory
    "primary_button",
    "success_button",
    "danger_button",
    "back_button",
    "close_button",
    "url_button",
    "build_keyboard",
    "add_to_group_button",
    # Renderers
    "render_game_response",
    "send_public_event",
    "send_public_vote",
    # Emoji Registry
    "UI_ICONS",
    "icon",
]
