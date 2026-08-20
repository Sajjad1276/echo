# ================================================================
# ECHO — CORE GAME ENGINE
# FILE: game.py
# ================================================================
#
# ECHO GAME ENGINE
#
# Pipeline:
#
# Telegram Update
#       ↓
# GameContext
#       ↓
# Active Session
#       ↓
# Intent Detection
#       ↓
# Access / Membership
#       ↓
# Action
#       ↓
# Database / Redis
#       ↓
# GameResponse
#
# Rules:
#   - Personal Gameplay = Text First
#   - Public / Collective Gameplay = Text + Optional Buttons
#   - Multi-City isolation
#   - Redis-backed live Session
#   - PostgreSQL-backed persistent state
#
# This file must not create:
#   - Telegram Message
#   - Bot
#   - Dispatcher
#   - InlineKeyboardButton
#
# ================================================================

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import func, select

from database import (
    City,
    CityMember,
    User,
    UserStats,
    UserWallet,
    Mission,
    MissionProgress,
    Event,
    get_session as db_session,
    get_game_session as redis_get_session,
    set_game_session as redis_set_session,
    clear_game_session as redis_clear_session,
    get_intent_context,
    set_intent_context,
    clear_intent_context,
    is_on_cooldown,
    set_cooldown,
    distributed_lock,
    get_city_member,
    get_city_by_chat,
    city_population,
)


# ================================================================
# 1. ENUMS
# ================================================================

class IntentType(str, Enum):
    START = "START"
    HELP = "HELP"
    PROFILE = "PROFILE"
    CITY = "CITY"
    MISSIONS = "MISSIONS"
    WORK = "WORK"
    EXPLORE = "EXPLORE"
    MARKET = "MARKET"
    GUILD = "GUILD"
    RANK = "RANK"

    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    NUMERIC = "NUMERIC"
    NO_INTENT = "NO_INTENT"


class SessionState(str, Enum):
    IDLE = "IDLE"
    WAITING_FOR_CHOICE = "WAITING_FOR_CHOICE"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    MISSION_ACTIVE = "MISSION_ACTIVE"
    EXPLORATION_ACTIVE = "EXPLORATION_ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"


class ResponseType(str, Enum):
    PERSONAL = "PERSONAL"
    PUBLIC = "PUBLIC"
    PUBLIC_VOTE = "PUBLIC_VOTE"
    PUBLIC_EVENT = "PUBLIC_EVENT"
    ERROR = "ERROR"
    SILENT = "SILENT"


class ActionStyle(str, Enum):
    PRIMARY = "PRIMARY"
    SUCCESS = "SUCCESS"
    DANGER = "DANGER"


class GameError(str, Enum):
    INVALID_ACTION = "INVALID_ACTION"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    NOT_MEMBER = "NOT_MEMBER"
    INSUFFICIENT_ENERGY = "INSUFFICIENT_ENERGY"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    COOLDOWN = "COOLDOWN"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_INTENT = "UNKNOWN_INTENT"
    INVALID_INPUT = "INVALID_INPUT"
    FEATURE_NOT_READY = "FEATURE_NOT_READY"
    CITY_NOT_FOUND = "CITY_NOT_FOUND"
    USER_NOT_FOUND = "USER_NOT_FOUND"


# ================================================================
# 2. DATA CONTRACTS
# ================================================================

@dataclass
class GameContext:
    """
    Telegram-independent game input.
    """

    user_id: int
    city_id: int
    chat_id: int
    message_id: int
    text: str

    is_group: bool = True
    is_private: bool = False

    username: str = ""

    reply_to_message_id: Optional[int] = None

    session_id: Optional[str] = None

    timestamp: float = field(
        default_factory=time.time
    )

    normalized_text: str = ""

    raw_text: str = ""

    def __post_init__(self) -> None:

        if not self.raw_text:
            self.raw_text = self.text

        if not self.normalized_text:
            self.normalized_text = normalize_text(
                self.text
            )


@dataclass
class ActionButton:
    """
    Button metadata only.

    handlers.py creates the real Telegram Button.
    """

    action: str

    label: str

    style: ActionStyle = ActionStyle.PRIMARY

    payload: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class GameResponse:
    """
    Telegram-independent response.
    """

    text: str = ""

    response_type: ResponseType = (
        ResponseType.PERSONAL
    )

    public: bool = False

    edit_preferred: bool = False

    session_id: Optional[str] = None

    state: SessionState = SessionState.IDLE

    actions: list[ActionButton] = field(
        default_factory=list
    )

    requires_ui: bool = False

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    error: Optional[GameError] = None

    notification: Optional[str] = None

    @property
    def is_silent(self) -> bool:
        return (
            self.response_type
            == ResponseType.SILENT
        )


# ================================================================
# 3. TEXT NORMALIZATION
# ================================================================

_CHAR_MAP = {
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "\u200c": " ",
    "\u200b": "",
    "\u200f": "",
    "\u200e": "",
}

_MULTI_SPACE = re.compile(r"\s+")

_TRAILING_PUNCTUATION = re.compile(
    r"[!؟?.,،؛;:]+$"
)

_NUMERIC_RE = re.compile(
    r"^[۰-۹0-9]+$"
)


def normalize_text(text: str) -> str:
    """
    Normalize Persian text without destroying original input.
    """

    if not text:
        return ""

    value = text.strip().lower()

    for source, target in _CHAR_MAP.items():
        value = value.replace(
            source,
            target,
        )

    value = _TRAILING_PUNCTUATION.sub(
        "",
        value,
    )

    value = _MULTI_SPACE.sub(
        " ",
        value,
    )

    return value.strip()


def normalize_digits(text: str) -> str:
    """
    Convert Persian digits to English digits.
    """

    mapping = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹",
        "0123456789",
    )

    return text.translate(mapping)


# ================================================================
# 4. INTENT REGISTRY
# ================================================================

INTENT_ALIASES: dict[
    IntentType,
    tuple[str, ...],
] = {
    IntentType.START: (
        "/start",
        "start",
        "شروع",
    ),

    IntentType.HELP: (
        "/help",
        "help",
        "راهنما",
        "کمک",
        "چطور بازی کنم",
        "چطور شروع کنم",
    ),

    IntentType.PROFILE: (
        "پروفایل",
        "وضعیت من",
        "اطلاعات من",
        "من کی هستم",
        "مشخصات من",
        "profile",
    ),

    IntentType.CITY: (
        "شهر",
        "شهر ما",
        "شهرمون",
        "وضعیت شهر",
        "اوضاع شهر",
        "city",
    ),

    IntentType.MISSIONS: (
        "مأموریت",
        "ماموریت",
        "مأموریت ها",
        "ماموریت ها",
        "مأموریت هام",
        "ماموریت هام",
        "مأموریت‌هام",
        "ماموریت‌هام",
        "چه مأموریتی دارم",
        "چه ماموریتی دارم",
        "مأموریت های امروز",
        "ماموریت های امروز",
        "mission",
        "missions",
    ),

    IntentType.WORK: (
        "کار",
        "کار امروز",
        "کار کنم",
        "چه کاری انجام بدم",
        "کار کردن",
        "work",
    ),

    IntentType.EXPLORE: (
        "اکتشاف",
        "کاوش",
        "بگردیم",
        "منطقه جدید",
        "جستجو",
        "explore",
    ),

    IntentType.MARKET: (
        "بازار",
        "بازار امروز",
        "قیمت ها",
        "قیمت‌های بازار",
        "قیمت بازار",
        "market",
    ),

    IntentType.GUILD: (
        "گیلد",
        "guild",
        "گروه بازی",
    ),

    IntentType.RANK: (
        "رتبه",
        "رتبه بندی",
        "رتبه‌بندی",
        "نفرات برتر",
        "rank",
    ),

    IntentType.CONFIRM: (
        "بله",
        "بلی",
        "آره",
        "باشه",
        "تأیید",
        "تایید",
        "اوکی",
        "ok",
        "yes",
    ),

    IntentType.CANCEL: (
        "نه",
        "خیر",
        "لغو",
        "انصراف",
        "بی خیال",
        "بی‌خیال",
        "no",
    ),
}


_ALIAS_MAP: dict[str, IntentType] = {}

for intent, aliases in INTENT_ALIASES.items():

    for alias in aliases:

        _ALIAS_MAP[
            normalize_text(alias)
        ] = intent


def detect_intent(
    normalized: str,
) -> IntentType:
    """
    Rule-first intent detection.
    """

    normalized = normalize_text(
        normalized
    )

    exact = _ALIAS_MAP.get(
        normalized
    )

    if exact is not None:
        return exact

    if _NUMERIC_RE.fullmatch(
        normalized
    ):
        return IntentType.NUMERIC

    return IntentType.NO_INTENT


# ================================================================
# 5. ACCESS RULES
# ================================================================

@dataclass(frozen=True)
class AccessRule:
    allow_private: bool
    allow_group: bool
    requires_member: bool


ACCESS_RULES: dict[
    IntentType,
    AccessRule,
] = {
    IntentType.START:
        AccessRule(
            True,
            True,
            False,
        ),

    IntentType.HELP:
        AccessRule(
            True,
            True,
            False,
        ),

    IntentType.PROFILE:
        AccessRule(
            True,
            True,
            False,
        ),

    IntentType.CITY:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.MISSIONS:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.WORK:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.EXPLORE:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.MARKET:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.GUILD:
        AccessRule(
            False,
            True,
            True,
        ),

    IntentType.RANK:
        AccessRule(
            False,
            True,
            True,
        ),
}


def check_access(
    intent: IntentType,
    context: GameContext,
) -> Optional[GameError]:

    rule = ACCESS_RULES.get(
        intent
    )

    if rule is None:
        return None

    if (
        context.is_private
        and not rule.allow_private
    ):
        return GameError.INVALID_ACTION

    if (
        context.is_group
        and not rule.allow_group
    ):
        return GameError.INVALID_ACTION

    return None


# ================================================================
# 6. DATABASE CONTEXT HELPERS
# ================================================================

async def ensure_user(
    context: GameContext,
) -> Optional[User]:

    async with db_session() as session:

        result = await session.execute(
            select(User).where(
                User.id
                == context.user_id
            )
        )

        return result.scalar_one_or_none()


async def get_city(
    context: GameContext,
) -> Optional[City]:

    async with db_session() as session:

        result = await session.execute(
            select(City).where(
                City.id
                == context.city_id
            )
        )

        return result.scalar_one_or_none()


async def is_city_member(
    user_id: int,
    city_id: int,
) -> bool:

    async with db_session() as session:

        member = await get_city_member(
            session,
            city_id,
            user_id,
        )

        return bool(
            member
            and member.is_active
        )


async def get_user_city_data(
    user_id: int,
    city_id: int,
) -> Optional[
    tuple[
        User,
        UserStats,
        City,
        CityMember,
        UserWallet,
    ]
]:

    async with db_session() as session:

        user_result = await session.execute(
            select(User).where(
                User.id
                == user_id
            )
        )

        user = (
            user_result
            .scalar_one_or_none()
        )

        if user is None:
            return None

        stats_result = await session.execute(
            select(UserStats).where(
                UserStats.user_id
                == user_id
            )
        )

        stats = (
            stats_result
            .scalar_one_or_none()
        )

        if stats is None:
            return None

        city_result = await session.execute(
            select(City).where(
                City.id
                == city_id
            )
        )

        city = (
            city_result
            .scalar_one_or_none()
        )

        if city is None:
            return None

        member_result = await session.execute(
            select(CityMember).where(
                CityMember.city_id
                == city_id,
                CityMember.user_id
                == user_id,
                CityMember.is_active.is_(True),
            )
        )

        member = (
            member_result
            .scalar_one_or_none()
        )

        if member is None:
            return None

        wallet_result = await session.execute(
            select(UserWallet).where(
                UserWallet.city_id
                == city_id,
                UserWallet.user_id
                == user_id,
            )
        )

        wallet = (
            wallet_result
            .scalar_one_or_none()
        )

        if wallet is None:
            return None

        return (
            user,
            stats,
            city,
            member,
            wallet,
        )


# ================================================================
# 7. RESPONSE HELPERS
# ================================================================

_ERROR_TEXT = {
    GameError.INVALID_ACTION:
        "این کار الان امکان‌پذیر نیست.",

    GameError.SESSION_EXPIRED:
        "این مرحله منقضی شده. برای شروع دوباره، دوباره درخواستت را بفرست.",

    GameError.NOT_MEMBER:
        "اول باید وارد این City شوی.",

    GameError.INSUFFICIENT_ENERGY:
        "⚡ انرژی کافی نداری.",

    GameError.INSUFFICIENT_FUNDS:
        "💰 موجودی کافی نداری.",

    GameError.COOLDOWN:
        "⏳ هنوز باید کمی صبر کنی.",

    GameError.RATE_LIMITED:
        "⏳ کمی آرام‌تر پیش برو و دوباره امتحان کن.",

    GameError.UNKNOWN_INTENT:
        "متوجه درخواستت نشدم. «راهنما» را بفرست.",

    GameError.INVALID_INPUT:
        "این گزینه درست نیست. یکی از گزینه‌های نمایش‌داده‌شده را بفرست.",

    GameError.FEATURE_NOT_READY:
        "این بخش هنوز آماده نشده.",

    GameError.CITY_NOT_FOUND:
        "این City پیدا نشد.",

    GameError.USER_NOT_FOUND:
        "حساب کاربری تو پیدا نشد.",
}


def error_response(
    error: GameError,
    *,
    state: SessionState = SessionState.IDLE,
    session_id: Optional[str] = None,
) -> GameResponse:

    return GameResponse(
        text=_ERROR_TEXT.get(
            error,
            "یک مشکل پیش آمد.",
        ),
        response_type=ResponseType.ERROR,
        public=False,
        state=state,
        session_id=session_id,
        error=error,
    )


# ================================================================
# 8. SESSION HELPERS
# ================================================================

async def get_active_session(
    user_id: int,
    city_id: int,
) -> Optional[dict[str, Any]]:

    data = await redis_get_session(
        user_id,
        city_id,
    )

    if not data:
        return None

    return data


async def create_session(
    user_id: int,
    city_id: int,
    state: SessionState,
    payload: Optional[
        dict[str, Any]
    ] = None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:

    existing = await get_active_session(
        user_id,
        city_id,
    )

    if existing:
        return existing

    session_id = str(
        uuid.uuid4()
    )

    session_data = {
        "session_id": session_id,
        "state": state.value,
        "payload": payload or {},
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    await redis_set_session(
        user_id,
        city_id,
        state.value,
        payload=session_data["payload"],
        ttl_seconds=ttl_seconds,
    )

    return session_data


async def update_session(
    user_id: int,
    city_id: int,
    state: SessionState,
    payload: Optional[
        dict[str, Any]
    ] = None,
    ttl_seconds: int = 300,
) -> None:

    current = await get_active_session(
        user_id,
        city_id,
    )

    merged_payload = {}

    session_id = str(
        uuid.uuid4()
    )

    if current:

        session_id = current.get(
            "session_id",
            session_id,
        )

        merged_payload.update(
            current.get(
                "payload",
                {},
            )
        )

    if payload:
        merged_payload.update(
            payload
        )

    merged_payload["session_id"] = (
        session_id
    )

    await redis_set_session(
        user_id,
        city_id,
        state.value,
        payload=merged_payload,
        ttl_seconds=ttl_seconds,
    )


async def expire_session(
    user_id: int,
    city_id: int,
) -> None:

    await redis_clear_session(
        user_id,
        city_id,
    )

    await clear_intent_context(
        user_id,
        city_id,
    )


# ================================================================
# 9. GAME ACTION REGISTRY
# ================================================================

ActionHandler = Callable[
    [GameContext, Optional[dict[str, Any]]],
    Awaitable[GameResponse],
]

_ACTIONS: dict[
    IntentType,
    ActionHandler,
] = {}


def register_action(
    intent: IntentType,
) -> Callable[
    [ActionHandler],
    ActionHandler,
]:

    def decorator(
        function: ActionHandler,
    ) -> ActionHandler:

        _ACTIONS[intent] = function

        return function

    return decorator


# ================================================================
# 10. START
# ================================================================

@register_action(IntentType.START)
async def handle_start(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "🌐 <b>ECHO</b>\n\n"
            "ECHO یک بازی چندنفره متنی است که "
            "داخل Groupهای Telegram اجرا می‌شود.\n\n"
            "هر Group یک City است.\n"
            "برای شروع، ECHO را به یک Group اضافه کن."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
    )


# ================================================================
# 11. HELP
# ================================================================

@register_action(IntentType.HELP)
async def handle_help(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "📚 <b>راهنمای ECHO</b>\n\n"
            "بازی اصلی داخل Group انجام می‌شود.\n\n"
            "می‌توانی بنویسی:\n\n"
            "🎯 «مأموریت»\n"
            "برای دیدن مأموریت‌های فعال.\n\n"
            "💼 «کار»\n"
            "برای دیدن فرصت‌های کاری.\n\n"
            "🧭 «اکتشاف»\n"
            "برای بررسی مناطق جدید.\n\n"
            "👤 «پروفایل»\n"
            "برای دیدن وضعیت خودت.\n\n"
            "🏙 «شهر»\n"
            "برای دیدن وضعیت City.\n\n"
            "📈 «بازار»\n"
            "برای دیدن بازار.\n\n"
            "⚔️ «گیلد»\n"
            "برای بخش Guild.\n\n"
            "🏆 «رتبه»\n"
            "برای دیدن رتبه‌بندی.\n\n"
            "در طول بازی لازم نیست Command خاصی حفظ کنی. "
            "فقط کاری که می‌خواهی انجام دهی را بنویس."
        ),
        response_type=ResponseType.PERSONAL,
    )


# ================================================================
# 12. PROFILE
# ================================================================

@register_action(IntentType.PROFILE)
async def handle_profile(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    data = await get_user_city_data(
        context.user_id,
        context.city_id,
    )

    if data is None:

        user = await ensure_user(
            context
        )

        if user is None:

            return error_response(
                GameError.USER_NOT_FOUND
            )

        return error_response(
            GameError.NOT_MEMBER
        )

    (
        user,
        stats,
        city,
        member,
        wallet,
    ) = data

    name = (
        f"@{user.username}"
        if user.username
        else user.nickname
        or str(user.id)
    )

    role = {
        "owner": "مالک City",
        "admin": "مدیر City",
        "member": "شهروند",
    }.get(
        member.role,
        "شهروند",
    )

    text = (
        f"👤 <b>{name}</b>\n\n"

        f"🌍 <b>پیشرفت کلی</b>\n"
        f"سطح: {stats.level}\n"
        f"XP: {stats.xp:,}\n"
        f"شهرت: {stats.fame:,}\n"
        f"اعتبار جهانی: {stats.global_reputation:,}\n\n"

        f"🏙 <b>{city.name}</b>\n"
        f"⚡ انرژی: {member.energy}\n"
        f"⭐ اعتبار City: {member.city_reputation:,}\n"
        f"🤝 مشارکت: {member.contribution:,}\n"
        f"💰 پول نقد: {wallet.cash:,}\n"
        f"🏦 بانک: {wallet.bank:,}\n"
        f"👤 نقش: {role}"
    )

    return GameResponse(
        text=text,
        response_type=ResponseType.PERSONAL,
        metadata={
            "user_id": user.id,
            "city_id": city.id,
            "global": {
                "level": stats.level,
                "xp": stats.xp,
                "fame": stats.fame,
                "global_reputation":
                    stats.global_reputation,
            },
            "city": {
                "energy": member.energy,
                "city_reputation":
                    member.city_reputation,
                "contribution":
                    member.contribution,
                "cash": wallet.cash,
                "bank": wallet.bank,
            },
        },
    )


# ================================================================
# 13. CITY
# ================================================================

@register_action(IntentType.CITY)
async def handle_city(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    async with db_session() as session:

        city_result = await session.execute(
            select(City).where(
                City.id
                == context.city_id
            )
        )

        city = (
            city_result
            .scalar_one_or_none()
        )

        if city is None:

            return error_response(
                GameError.CITY_NOT_FOUND
            )

        population = await city_population(
            session,
            city.id,
        )

    activity = (
        "زیاد"
        if population >= 50
        else "متوسط"
        if population >= 15
        else "کم"
    )

    text = (
        f"🏙 <b>{city.name}</b>\n\n"
        f"⭐ سطح: {city.level}\n"
        f"👥 جمعیت: {population}\n"
        f"💰 خزانه: {city.treasury:,}\n"
        f"🔥 فعالیت: {activity}\n"
        f"🏷 کد City: {city.city_code or '-'}"
    )

    return GameResponse(
        text=text,
        response_type=ResponseType.PERSONAL,
        metadata={
            "city_id": city.id,
            "population": population,
            "activity": activity,
        },
    )


# ================================================================
# 14. MISSIONS
# ================================================================

async def get_available_missions(
    user_id: int,
    city_id: int,
) -> list[dict[str, Any]]:

    async with db_session() as session:

        result = await session.execute(
            select(Mission)
            .where(
                Mission.is_active.is_(True),
                (
                    (Mission.city_id == city_id)
                    | (Mission.city_id.is_(None))
                ),
            )
            .order_by(
                Mission.difficulty.asc(),
                Mission.id.asc(),
            )
            .limit(3)
        )

        missions = result.scalars().all()

        if missions:
            return [
                {
                    "id": mission.id,
                    "title": mission.title,
                    "description":
                        mission.description or "",
                    "difficulty":
                        mission.difficulty,
                    "reward_cash":
                        mission.reward_cash,
                    "reward_xp":
                        mission.reward_xp,
                    "energy_cost":
                        mission.energy_cost,
                }
                for mission in missions
            ]

    # Foundation fallback.
    return [
        {
            "id": 1,
            "title": "دو فعالیت انجام بده",
            "description":
                "دو فعالیت بازی را کامل کن.",
            "difficulty": 1,
            "reward_cash": 3000,
            "reward_xp": 150,
            "energy_cost": 10,
        },
        {
            "id": 2,
            "title": "یک منطقه را بررسی کن",
            "description":
                "یک اکتشاف انجام بده.",
            "difficulty": 2,
            "reward_cash": 5000,
            "reward_xp": 250,
            "energy_cost": 15,
        },
        {
            "id": 3,
            "title": "۵۰۰۰ پول به دست بیاور",
            "description":
                "درآمد امروزت را به حد مشخص برسان.",
            "difficulty": 2,
            "reward_cash": 6000,
            "reward_xp": 300,
            "energy_cost": 0,
        },
    ]


@register_action(IntentType.MISSIONS)
async def handle_missions(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    missions = await get_available_missions(
        context.user_id,
        context.city_id,
    )

    lines = []

    for index, mission in enumerate(
        missions,
        start=1,
    ):

        lines.append(
            f"{index}. {mission['title']}"
        )

    async with distributed_lock(
        f"session:{context.user_id}:{context.city_id}",
        ttl_seconds=5,
        wait_seconds=1,
    ) as acquired:

        if not acquired:

            return error_response(
                GameError.COOLDOWN
            )

        session = await create_session(
            context.user_id,
            context.city_id,
            SessionState.WAITING_FOR_CHOICE,
            {
                "action":
                    "mission_select",

                "missions":
                    missions,
            },
            ttl_seconds=300,
        )

    await set_intent_context(
        context.user_id,
        context.city_id,
        IntentType.MISSIONS.value,
        SessionState.WAITING_FOR_CHOICE.value,
        {
            "session_id":
                session["session_id"]
        },
        ttl_seconds=300,
    )

    text = (
        "🎯 <b>مأموریت‌های امروز</b>\n\n"
        + "\n".join(lines)
        + "\n\n"
        "برای دیدن جزئیات، شماره مأموریت را بفرست."
    )

    return GameResponse(
        text=text,
        response_type=ResponseType.PERSONAL,
        state=SessionState.WAITING_FOR_CHOICE,
        session_id=session[
            "session_id"
        ],
        metadata={
            "missions": missions
        },
    )


# ================================================================
# 15. WORK
# ================================================================

@register_action(IntentType.WORK)
async def handle_work(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "💼 <b>بخش کار</b>\n\n"
            "سیستم اصلی کار هنوز در حال آماده‌سازی است.\n\n"
            "در نسخه کامل، چند فرصت با درآمد و ریسک متفاوت "
            "در اختیارت قرار می‌گیرد."
        ),
        response_type=ResponseType.PERSONAL,
        error=GameError.FEATURE_NOT_READY,
    )


# ================================================================
# 16. EXPLORE
# ================================================================

@register_action(IntentType.EXPLORE)
async def handle_explore(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "🧭 <b>اکتشاف</b>\n\n"
            "سیستم اصلی اکتشاف هنوز در حال آماده‌سازی است.\n\n"
            "در نسخه کامل می‌توانی مناطق جدید را بررسی کنی، "
            "Discovery پیدا کنی و با ریسک‌های مختلف روبه‌رو شوی."
        ),
        response_type=ResponseType.PERSONAL,
        error=GameError.FEATURE_NOT_READY,
    )


# ================================================================
# 17. MARKET
# ================================================================

@register_action(IntentType.MARKET)
async def handle_market(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "📈 <b>بازار</b>\n\n"
            "بازار هنوز فعال نشده است.\n\n"
            "در نسخه کامل قیمت‌ها و فرصت‌های معامله "
            "بر اساس وضعیت اقتصاد City تغییر می‌کنند."
        ),
        response_type=ResponseType.PERSONAL,
        error=GameError.FEATURE_NOT_READY,
    )


# ================================================================
# 18. GUILD
# ================================================================

@register_action(IntentType.GUILD)
async def handle_guild(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    return GameResponse(
        text=(
            "⚔️ <b>Guild</b>\n\n"
            "سیستم Guild هنوز فعال نشده است.\n\n"
            "بعداً می‌توانی Guild بسازی، عضو Guild شوی "
            "و در رقابت‌های گروهی شرکت کنی."
        ),
        response_type=ResponseType.PERSONAL,
        error=GameError.FEATURE_NOT_READY,
    )


# ================================================================
# 19. RANK
# ================================================================

@register_action(IntentType.RANK)
async def handle_rank(
    context: GameContext,
    _session: Optional[
        dict[str, Any]
    ],
) -> GameResponse:

    async with db_session() as session:

        result = await session.execute(
            select(
                User,
                UserStats,
                UserWallet,
            )
            .join(
                UserStats,
                UserStats.user_id
                == User.id,
            )
            .join(
                UserWallet,
                UserWallet.user_id
                == User.id,
            )
            .where(
                UserWallet.city_id
                == context.city_id,
            )
            .order_by(
                UserStats.level.desc(),
                UserStats.xp.desc(),
            )
            .limit(10)
        )

        rows = result.all()

    if not rows:

        return GameResponse(
            text=(
                "🏆 هنوز کسی برای رتبه‌بندی ثبت نشده است."
            ),
            response_type=ResponseType.PERSONAL,
        )

    medals = (
        "🥇",
        "🥈",
        "🥉",
    )

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
            or str(user.id)
        )

        total_money = (
            wallet.cash
            + wallet.bank
        )

        lines.append(
            f"{medal} {name} — "
            f"سطح {stats.level} — "
            f"${total_money:,}"
        )

    return GameResponse(
        text=(
            "🏆 <b>رتبه‌بندی City</b>\n\n"
            + "\n".join(lines)
        ),
        response_type=ResponseType.PERSONAL,
    )


# ================================================================
# 20. SESSION INPUT
# ================================================================

async def handle_session_input(
    context: GameContext,
    session: dict[str, Any],
) -> GameResponse:

    state_value = session.get(
        "state",
        SessionState.IDLE.value,
    )

    try:
        state = SessionState(
            state_value
        )
    except ValueError:

        await expire_session(
            context.user_id,
            context.city_id,
        )

        return error_response(
            GameError.SESSION_EXPIRED
        )

    payload = session.get(
        "payload",
        {},
    )

    normalized = context.normalized_text

    # ------------------------------------------------------------
    # CHOICE
    # ------------------------------------------------------------

    if (
        state
        == SessionState.WAITING_FOR_CHOICE
    ):

        action = payload.get(
            "action"
        )

        if action != "mission_select":

            await expire_session(
                context.user_id,
                context.city_id,
            )

            return error_response(
                GameError.INVALID_ACTION
            )

        numeric = normalize_digits(
            normalized
        )

        if not numeric.isdigit():

            return GameResponse(
                text=(
                    "شماره مأموریت را بفرست.\n"
                    "مثلاً: 1"
                ),
                response_type=(
                    ResponseType.PERSONAL
                ),
                state=state,
                session_id=payload.get(
                    "session_id"
                ),
            )

        choice = int(
            numeric
        )

        missions = payload.get(
            "missions",
            [],
        )

        if (
            choice < 1
            or choice > len(missions)
        ):

            return GameResponse(
                text=(
                    "این شماره در فهرست مأموریت‌ها نیست.\n"
                    "یکی از شماره‌های نمایش‌داده‌شده را بفرست."
                ),
                response_type=(
                    ResponseType.PERSONAL
                ),
                state=state,
                session_id=payload.get(
                    "session_id"
                ),
            )

        selected = missions[
            choice - 1
        ]

        await update_session(
            context.user_id,
            context.city_id,
            SessionState.WAITING_FOR_CONFIRMATION,
            {
                "selected_mission":
                    selected
            },
        )

        return GameResponse(
            text=(
                "🎯 <b>مأموریت انتخابی</b>\n\n"
                f"{selected['title']}\n\n"
                f"📌 {selected.get('description', '')}\n"
                f"💰 پاداش: {selected.get('reward_cash', 0):,}\n"
                f"⭐ XP: {selected.get('reward_xp', 0):,}\n"
                f"⚡ هزینه انرژی: {selected.get('energy_cost', 0)}\n\n"
                "برای شروع بنویس «بله».\n"
                "برای لغو بنویس «نه»."
            ),
            response_type=ResponseType.PERSONAL,
            state=SessionState.WAITING_FOR_CONFIRMATION,
            session_id=payload.get(
                "session_id"
            ),
        )

    # ------------------------------------------------------------
    # CONFIRMATION
    # ------------------------------------------------------------

    if (
        state
        == SessionState.WAITING_FOR_CONFIRMATION
    ):

        intent = detect_intent(
            normalized
        )

        selected = payload.get(
            "selected_mission"
        )

        if intent == IntentType.CANCEL:

            await expire_session(
                context.user_id,
                context.city_id,
            )

            return GameResponse(
                text=(
                    "مأموریت لغو شد.\n\n"
                    "هر وقت خواستی دوباره «مأموریت» را بفرست."
                ),
                response_type=(
                    ResponseType.PERSONAL
                ),
            )

        if intent != IntentType.CONFIRM:

            return GameResponse(
                text=(
                    "برای ادامه «بله» و برای لغو «نه» را بفرست."
                ),
                response_type=(
                    ResponseType.PERSONAL
                ),
                state=state,
                session_id=payload.get(
                    "session_id"
                ),
            )

        if not selected:

            await expire_session(
                context.user_id,
                context.city_id,
            )

            return error_response(
                GameError.INVALID_ACTION
            )

        mission_id = selected.get(
            "id"
        )

        energy_cost = int(
            selected.get(
                "energy_cost",
                0,
            )
        )

        async with distributed_lock(
            f"mission-start:"
            f"{context.user_id}:"
            f"{context.city_id}:"
            f"{mission_id}",
            ttl_seconds=8,
            wait_seconds=1,
        ) as acquired:

            if not acquired:

                return error_response(
                    GameError.COOLDOWN
                )

            async with db_session() as session:

                member_result = (
                    await session.execute(
                        select(
                            CityMember
                        )
                        .where(
                            CityMember.city_id
                            == context.city_id,
                            CityMember.user_id
                            == context.user_id,
                            CityMember.is_active.is_(True),
                        )
                        .with_for_update()
                    )
                )

                member = (
                    member_result
                    .scalar_one_or_none()
                )

                if member is None:

                    return error_response(
                        GameError.NOT_MEMBER
                    )

                if (
                    member.energy
                    < energy_cost
                ):

                    return error_response(
                        GameError.INSUFFICIENT_ENERGY
                    )

                member.energy -= (
                    energy_cost
                )

                member.last_active_at = (
                    datetime_now()
                )

                if (
                    selected.get("id")
                    and isinstance(
                        selected.get("id"),
                        int,
                    )
                ):

                    progress = (
                        MissionProgress(
                            user_id=
                                context.user_id,
                            city_id=
                                context.city_id,
                            mission_id=
                                mission_id,
                            progress=0,
                            status=
                                "in_progress",
                        )
                    )

                    session.add(
                        progress
                    )

                await session.flush()

                await expire_session(
                    context.user_id,
                    context.city_id,
                )

        return GameResponse(
            text=(
                "✅ <b>مأموریت شروع شد.</b>\n\n"
                f"{selected.get('title', 'مأموریت')}\n\n"
                f"⚡ {energy_cost} انرژی مصرف شد.\n\n"
                "پیشرفت مأموریت از اینجا ادامه پیدا می‌کند."
            ),
            response_type=ResponseType.PERSONAL,
            state=SessionState.MISSION_ACTIVE,
        )

    # ------------------------------------------------------------
    # UNKNOWN SESSION
    # ------------------------------------------------------------

    await expire_session(
        context.user_id,
        context.city_id,
    )

    return error_response(
        GameError.SESSION_EXPIRED
    )


# ================================================================
# 21. UTILITY
# ================================================================

def datetime_now():
    """
    Lightweight local import to avoid exposing datetime as
    part of the public Game API.
    """
    from datetime import datetime, timezone

    return datetime.now(
        timezone.utc
    )


# ================================================================
# 22. PUBLIC EVENT FACTORIES
# ================================================================

def create_city_vote(
    city_id: int,
    question: str,
    metadata: Optional[
        dict[str, Any]
    ] = None,
) -> GameResponse:

    return GameResponse(
        text=(
            "🏛 <b>تصمیم City</b>\n\n"
            f"{question}\n\n"
            "نظر خودت را انتخاب کن."
        ),
        response_type=(
            ResponseType.PUBLIC_VOTE
        ),
        public=True,
        requires_ui=True,
        actions=[
            ActionButton(
                action="VOTE_YES",
                label="موافقم",
                style=ActionStyle.SUCCESS,
            ),
            ActionButton(
                action="VOTE_NO",
                label="مخالفم",
                style=ActionStyle.DANGER,
            ),
        ],
        metadata={
            "city_id": city_id,
            **(metadata or {}),
        },
    )


def create_global_event(
    title: str,
    description: str,
    metadata: Optional[
        dict[str, Any]
    ] = None,
) -> GameResponse:

    return GameResponse(
        text=(
            f"🌍 <b>{title}</b>\n\n"
            f"{description}"
        ),
        response_type=(
            ResponseType.PUBLIC_EVENT
        ),
        public=True,
        requires_ui=True,
        actions=[
            ActionButton(
                action="JOIN_EVENT",
                label="شرکت در Event",
                style=ActionStyle.SUCCESS,
            ),
            ActionButton(
                action="VIEW_EVENT",
                label="جزئیات",
                style=ActionStyle.PRIMARY,
            ),
        ],
        metadata=(
            metadata or {}
        ),
    )


def create_legendary_discovery(
    username: str,
    discovery: str,
) -> GameResponse:

    return GameResponse(
        text=(
            "💎 <b>Discovery افسانه‌ای</b>\n\n"
            f"@{username} اولین کسی بود که "
            f"«{discovery}» را کشف کرد."
        ),
        response_type=(
            ResponseType.PUBLIC
        ),
        public=True,
        requires_ui=False,
    )


# ================================================================
# 23. PUBLIC NETWORK CONTRACTS
# ================================================================

@dataclass
class CityEventContract:
    event_type: str

    city_id: int

    payload: dict[str, Any] = field(
        default_factory=dict
    )

    response: Optional[
        GameResponse
    ] = None


@dataclass
class NetworkSnapshot:
    city_id: int

    active_citizens: int = 0

    city_growth_score: int = 0

    activity_level: str = "low"

    referral_count: int = 0

    milestone_count: int = 0


# ================================================================
# 24. RATE LIMIT
# ================================================================

_rate_history: dict[
    tuple[int, int],
    list[float],
] = {}

RATE_LIMIT_WINDOW = 5.0

RATE_LIMIT_MAX_MESSAGES = 5


async def check_rate_limit(
    user_id: int,
    city_id: int,
) -> bool:
    """
    Lightweight in-process guard.

    Full distributed rate limit can use Redis later.
    """

    key = (
        user_id,
        city_id,
    )

    now = time.time()

    history = _rate_history.get(
        key,
        [],
    )

    history = [
        timestamp
        for timestamp in history
        if now - timestamp
        < RATE_LIMIT_WINDOW
    ]

    if (
        len(history)
        >= RATE_LIMIT_MAX_MESSAGES
    ):

        _rate_history[key] = history

        return False

    history.append(
        now
    )

    _rate_history[key] = history

    return True


# ================================================================
# 25. CENTRAL MESSAGE PROCESSOR
# ================================================================

async def process_message(
    context: GameContext,
) -> GameResponse:
    """
    Main ECHO Game Pipeline.
    """

    # ------------------------------------------------------------
    # 1. Empty input
    # ------------------------------------------------------------

    if not context.normalized_text:

        return GameResponse(
            response_type=ResponseType.SILENT
        )

    # ------------------------------------------------------------
    # 2. Rate limit
    # ------------------------------------------------------------

    allowed = await check_rate_limit(
        context.user_id,
        context.city_id,
    )

    if not allowed:

        return error_response(
            GameError.RATE_LIMITED
        )

    # ------------------------------------------------------------
    # 3. Active Session FIRST
    # ------------------------------------------------------------

    session = await get_active_session(
        context.user_id,
        context.city_id,
    )

    if session:

        return await handle_session_input(
            context,
            session,
        )

    # ------------------------------------------------------------
    # 4. Intent Detection
    # ------------------------------------------------------------

    intent = detect_intent(
        context.normalized_text
    )

    # ------------------------------------------------------------
    # 5. Normal Group Chat
    # ------------------------------------------------------------

    if (
        intent
        == IntentType.NO_INTENT
    ):

        return GameResponse(
            response_type=ResponseType.SILENT
        )

    # ------------------------------------------------------------
    # 6. Numeric without Session
    # ------------------------------------------------------------

    if (
        intent
        == IntentType.NUMERIC
    ):

        return GameResponse(
            response_type=ResponseType.SILENT
        )

    # ------------------------------------------------------------
    # 7. Bare Confirmation / Cancel
    # ------------------------------------------------------------

    if intent in (
        IntentType.CONFIRM,
        IntentType.CANCEL,
    ):

        return GameResponse(
            response_type=ResponseType.SILENT
        )

    # ------------------------------------------------------------
    # 8. Access
    # ------------------------------------------------------------

    access_error = check_access(
        intent,
        context,
    )

    if access_error:

        return error_response(
            access_error
        )

    # ------------------------------------------------------------
    # 9. Membership
    # ------------------------------------------------------------

    rule = ACCESS_RULES.get(
        intent
    )

    if (
        rule
        and rule.requires_member
    ):

        member = await is_city_member(
            context.user_id,
            context.city_id,
        )

        if not member:

            return error_response(
                GameError.NOT_MEMBER
            )

    # ------------------------------------------------------------
    # 10. Handler
    # ------------------------------------------------------------

    action = _ACTIONS.get(
        intent
    )

    if action is None:

        return error_response(
            GameError.UNKNOWN_INTENT
        )

    return await action(
        context,
        None,
    )


# ================================================================
# 26. GAME ENGINE FACADE
# ================================================================

class GameEngine:
    """
    Public ECHO Engine API.

    handlers.py and main.py should use this facade.
    """

    async def process_message(
        self,
        context: GameContext,
    ) -> GameResponse:

        return await process_message(
            context
        )

    async def detect_intent(
        self,
        text: str,
    ) -> IntentType:

        return detect_intent(
            normalize_text(text)
        )

    async def check_rate_limit(
        self,
        user_id: int,
        city_id: int,
    ) -> bool:

        return await check_rate_limit(
            user_id,
            city_id,
        )

    async def get_session(
        self,
        user_id: int,
        city_id: int,
    ) -> Optional[
        dict[str, Any]
    ]:

        return await get_active_session(
            user_id,
            city_id,
        )

    async def create_city_vote(
        self,
        city_id: int,
        question: str,
        metadata: Optional[
            dict[str, Any]
        ] = None,
    ) -> GameResponse:

        return create_city_vote(
            city_id,
            question,
            metadata,
        )

    async def create_global_event(
        self,
        title: str,
        description: str,
        metadata: Optional[
            dict[str, Any]
        ] = None,
    ) -> GameResponse:

        return create_global_event(
            title,
            description,
            metadata,
        )

    async def create_legendary_discovery(
        self,
        username: str,
        discovery: str,
    ) -> GameResponse:

        return create_legendary_discovery(
            username,
            discovery,
        )


# ================================================================
# 27. SINGLETON ENGINE
# ================================================================

_game_engine: Optional[
    GameEngine
] = None


def get_game_engine() -> GameEngine:
    """
    Return shared ECHO Game Engine.
    """

    global _game_engine

    if _game_engine is None:

        _game_engine = GameEngine()

    return _game_engine


# ================================================================
# 28. PUBLIC EXPORTS
# ================================================================

__all__ = [
    "IntentType",
    "SessionState",
    "ResponseType",
    "ActionStyle",
    "GameError",
    "GameContext",
    "ActionButton",
    "GameResponse",
    "normalize_text",
    "detect_intent",
    "process_message",
    "GameEngine",
    "get_game_engine",
    "create_city_vote",
    "create_global_event",
    "create_legendary_discovery",
    "check_rate_limit",
    "CityEventContract",
    "NetworkSnapshot",
]


# ================================================================
# 29. LOCAL FOUNDATION TESTS
# ================================================================

if __name__ == "__main__":

    import asyncio

    async def run_tests() -> None:

        passed = 0
        failed = 0

        def check(
            name: str,
            actual: Any,
            expected: Any,
        ) -> None:

            nonlocal passed, failed

            if actual == expected:

                print(
                    f"✓ {name}"
                )

                passed += 1

            else:

                print(
                    f"✗ {name}"
                    f"\n  expected={expected!r}"
                    f"\n  actual={actual!r}"
                )

                failed += 1

        # --------------------------------------------------------
        # Normalize
        # --------------------------------------------------------

        check(
            "Persian normalization",
            normalize_text("كار"),
            "کار",
        )

        check(
            "Question punctuation",
            normalize_text("شهر؟"),
            "شهر",
        )

        check(
            "ZWNJ",
            normalize_text(
                "مأموریت\u200cها"
            ),
            "مأموریت ها",
        )

        # --------------------------------------------------------
        # Intent
        # --------------------------------------------------------

        check(
            "Mission intent",
            detect_intent("مأموریت"),
            IntentType.MISSIONS,
        )

        check(
            "Profile intent",
            detect_intent("پروفایل"),
            IntentType.PROFILE,
        )

        check(
            "City intent",
            detect_intent("شهر"),
            IntentType.CITY,
        )

        check(
            "Help intent",
            detect_intent("راهنما"),
            IntentType.HELP,
        )

        check(
            "Numeric",
            detect_intent("2"),
            IntentType.NUMERIC,
        )

        check(
            "Normal chat",
            detect_intent(
                "سلام خوبی؟"
            ),
            IntentType.NO_INTENT,
        )

        # --------------------------------------------------------
        # Engine
        # --------------------------------------------------------

        engine = get_game_engine()

        check(
            "Engine getter",
            isinstance(
                engine,
                GameEngine,
            ),
            True,
        )

        # --------------------------------------------------------
        # Public Vote
        # --------------------------------------------------------

        vote = create_city_vote(
            10,
            "مالیات افزایش پیدا کند؟",
        )

        check(
            "Vote public",
            vote.public,
            True,
        )

        check(
            "Vote requires UI",
            vote.requires_ui,
            True,
        )

        check(
            "Vote button count",
            len(vote.actions),
            2,
        )

        check(
            "Vote yes style",
            vote.actions[0].style,
            ActionStyle.SUCCESS,
        )

        check(
            "Vote no style",
            vote.actions[1].style,
            ActionStyle.DANGER,
        )

        # --------------------------------------------------------
        # Normal Chat
        # --------------------------------------------------------

        normal_context = GameContext(
            user_id=100,
            city_id=10,
            chat_id=-10010,
            message_id=1,
            text="امروز کسی فوتبال میاد؟",
        )

        response = await process_message(
            normal_context
        )

        check(
            "Normal group chat silent",
            response.is_silent,
            True,
        )

        # --------------------------------------------------------
        # Numeric without session
        # --------------------------------------------------------

        numeric_context = GameContext(
            user_id=101,
            city_id=10,
            chat_id=-10010,
            message_id=2,
            text="2",
        )

        response = await process_message(
            numeric_context
        )

        check(
            "Numeric without session silent",
            response.is_silent,
            True,
        )

        # --------------------------------------------------------
        # Help
        # --------------------------------------------------------

        help_context = GameContext(
            user_id=102,
            city_id=10,
            chat_id=-10010,
            message_id=3,
            text="راهنما",
        )

        response = await process_message(
            help_context
        )

        check(
            "Help response",
            response.response_type,
            ResponseType.PERSONAL,
        )

        # --------------------------------------------------------
        # Summary
        # --------------------------------------------------------

        print()
        print(
            f"Tests: {passed} passed / "
            f"{failed} failed"
        )

    asyncio.run(
        run_tests()
    )
