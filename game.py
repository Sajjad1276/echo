# ================================================================
# ECHO — CORE GAME ENGINE
# FILE: game.py
# ================================================================
# Pipeline:
#   GameContext → Intent/Session → Action → State → GameResponse
#
# Dependencies: config.py, database.py
# No Telegram UI objects. No circular imports.
# ================================================================

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


# ================================================================
# SECTION 1: ENUMS
# ================================================================

class IntentType(str, Enum):
    START         = "START"
    HELP          = "HELP"
    PROFILE       = "PROFILE"
    CITY          = "CITY"
    MISSIONS      = "MISSIONS"
    WORK          = "WORK"
    EXPLORE       = "EXPLORE"
    MARKET        = "MARKET"
    GUILD         = "GUILD"
    RANK          = "RANK"
    CONFIRM       = "CONFIRM"
    CANCEL        = "CANCEL"
    NUMERIC       = "NUMERIC"       # digit answer inside a session
    NO_INTENT     = "NO_INTENT"     # normal group chat — ignore


class SessionState(str, Enum):
    IDLE                   = "IDLE"
    WAITING_FOR_CHOICE     = "WAITING_FOR_CHOICE"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    INPUT_REQUIRED         = "INPUT_REQUIRED"
    MISSION_ACTIVE         = "MISSION_ACTIVE"
    EXPLORATION_ACTIVE     = "EXPLORATION_ACTIVE"
    COMPLETED              = "COMPLETED"
    EXPIRED                = "EXPIRED"


class ResponseType(str, Enum):
    PERSONAL         = "PERSONAL"        # private to the user
    PUBLIC           = "PUBLIC"          # broadcast to the group
    PUBLIC_VOTE      = "PUBLIC_VOTE"     # collective interaction
    PUBLIC_EVENT     = "PUBLIC_EVENT"    # living-world event
    ERROR            = "ERROR"
    SILENT           = "SILENT"          # do not reply at all


class ActionStyle(str, Enum):
    """Contract for handlers.py / Button Factory.  game.py never builds buttons."""
    PRIMARY = "PRIMARY"   # view / continue / neutral choice
    SUCCESS = "SUCCESS"   # join / confirm / start
    DANGER  = "DANGER"    # cancel / leave / destructive


class GameError(str, Enum):
    INVALID_ACTION       = "INVALID_ACTION"
    SESSION_EXPIRED      = "SESSION_EXPIRED"
    NOT_MEMBER           = "NOT_MEMBER"
    INSUFFICIENT_ENERGY  = "INSUFFICIENT_ENERGY"
    INSUFFICIENT_FUNDS   = "INSUFFICIENT_FUNDS"
    COOLDOWN             = "COOLDOWN"
    RATE_LIMITED         = "RATE_LIMITED"
    UNKNOWN_INTENT       = "UNKNOWN_INTENT"
    INVALID_INPUT        = "INVALID_INPUT"
    FEATURE_NOT_READY    = "FEATURE_NOT_READY"


# ================================================================
# SECTION 2: DATA CONTRACTS
# ================================================================

@dataclass
class GameContext:
    """
    Immutable snapshot of one incoming message.
    Independent of aiogram / Telegram objects.
    """
    user_id:             int
    city_id:             int
    chat_id:             int
    message_id:          int
    text:                str
    is_group:            bool              = True
    is_private:          bool              = False
    username:            str               = ""
    reply_to_message_id: int | None        = None
    session_id:          str | None        = None
    timestamp:           float             = field(default_factory=time.time)
    # Filled by normalizer — original preserved for debug
    normalized_text:     str               = ""
    raw_text:            str               = ""

    def __post_init__(self) -> None:
        if not self.raw_text:
            self.raw_text = self.text
        if not self.normalized_text:
            self.normalized_text = normalize_text(self.text)


@dataclass
class ActionButton:
    """
    Metadata for a single UI button.
    handlers.py reads this and builds the real InlineKeyboardButton.
    """
    action:  str         # e.g. "VOTE_YES"
    label:   str         # Persian label shown on button
    style:   ActionStyle = ActionStyle.PRIMARY
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GameResponse:
    """
    Everything handlers.py needs to render a reply.
    No Telegram objects inside.
    """
    text:            str                          = ""
    response_type:   ResponseType                 = ResponseType.PERSONAL
    public:          bool                         = False
    edit_preferred:  bool                         = False
    session_id:      str | None                   = None
    state:           SessionState                 = SessionState.IDLE
    # Action names — handlers.py maps these to buttons
    actions:         list[ActionButton]           = field(default_factory=list)
    requires_ui:     bool                         = False   # True → handlers MUST render buttons
    metadata:        dict[str, Any]               = field(default_factory=dict)
    error:           GameError | None             = None
    notification:    str | None                   = None    # optional inline toast / log note

    @property
    def is_silent(self) -> bool:
        return self.response_type == ResponseType.SILENT


# ================================================================
# SECTION 3: TEXT NORMALIZATION
# ================================================================

# Arabic/Urdu variants → Persian canonical
_CHAR_MAP: dict[str, str] = {
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "\u200c": " ",   # ZWNJ → space  (assists alias matching)
    "\u200b": "",    # ZWS  → drop
    "\u200f": "",    # RLM  → drop
    "\u200e": "",    # LRM  → drop
}

_MULTI_SPACE = re.compile(r" {2,}")
_PUNCTUATION = re.compile(r"[!؟?.,،؛;:\-]+$")   # trailing punctuation only


def normalize_text(text: str) -> str:
    """
    Normalize Persian input for intent matching.
    Original is always preserved in GameContext.raw_text.
    """
    t = text.strip().lower()
    for src, dst in _CHAR_MAP.items():
        t = t.replace(src, dst)
    t = _PUNCTUATION.sub("", t).strip()
    t = _MULTI_SPACE.sub(" ", t)
    return t


# ================================================================
# SECTION 4: INTENT REGISTRY
# ================================================================

# Each entry: (IntentType, [aliases])
# Matching order: exact → alias → pattern → NO_INTENT
_INTENT_REGISTRY: list[tuple[IntentType, list[str]]] = [
    (IntentType.START, [
        "/start", "start", "شروع",
    ]),
    (IntentType.HELP, [
        "/help", "راهنما", "کمک", "چطور بازی کنم", "help",
    ]),
    (IntentType.PROFILE, [
        "پروفایل", "وضعیت من", "اطلاعات من", "من کی هستم",
        "مشخصات من", "profile",
    ]),
    (IntentType.CITY, [
        "شهر", "شهر ما", "وضعیت شهر", "اوضاع شهر", "شهرمون", "city",
    ]),
    (IntentType.MISSIONS, [
        "مأموریت", "ماموریت", "مأموریت ها", "ماموریت ها",
        "ماموریت هام", "ماموریت هام", "چه مأموریتی دارم",
        "مأموریت های امروز", "missions", "mission",
    ]),
    (IntentType.WORK, [
        "کار", "کار امروز", "کار کنم", "چه کاری انجام بدم",
        "کار کردن", "work",
    ]),
    (IntentType.EXPLORE, [
        "اکتشاف", "کاوش", "بگردیم", "منطقه جدید", "جستجو", "explore",
    ]),
    (IntentType.MARKET, [
        "بازار", "بازار امروز", "قیمت ها", "قیمت بازار", "market",
    ]),
    (IntentType.GUILD, [
        "گیلد", "guild", "گروه بازی",
    ]),
    (IntentType.RANK, [
        "رتبه", "رتبه بندی", "نفرات برتر", "rank",
    ]),
    (IntentType.CONFIRM, [
        "بله", "آره", "باشه", "تأیید", "اوکی", "ok", "yes", "بلی",
    ]),
    (IntentType.CANCEL, [
        "نه", "خیر", "لغو", "انصراف", "بی خیال", "no",
    ]),
]

# Build fast lookup: normalized alias → IntentType
_ALIAS_MAP: dict[str, IntentType] = {}
for _intent, _aliases in _INTENT_REGISTRY:
    for _alias in _aliases:
        _ALIAS_MAP[normalize_text(_alias)] = _intent

_NUMERIC_RE = re.compile(r"^[۰-۹0-9]+$")


def detect_intent(normalized: str) -> IntentType:
    """
    Rule-first intent detection.  No LLM involved.
    Pipeline: exact → alias → numeric → NO_INTENT
    """
    if normalized in _ALIAS_MAP:
        return _ALIAS_MAP[normalized]
    if _NUMERIC_RE.match(normalized):
        return IntentType.NUMERIC
    return IntentType.NO_INTENT


# ================================================================
# SECTION 5: SESSION MANAGER
# ================================================================

# In production, replace _session_store with async Redis calls.
# Redis key pattern: echo:session:{user_id}:{city_id}
_session_store: dict[str, dict[str, Any]] = {}
_session_locks: dict[str, asyncio.Lock] = {}

SESSION_TTL = 300  # seconds


def _session_key(user_id: int, city_id: int) -> str:
    return f"echo:session:{user_id}:{city_id}"


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


async def get_session(user_id: int, city_id: int) -> dict[str, Any] | None:
    key = _session_key(user_id, city_id)
    session = _session_store.get(key)
    if session is None:
        return None
    if time.time() - session["updated_at"] > SESSION_TTL:
        await expire_session(user_id, city_id)
        return None
    return session


async def create_session(
    user_id: int,
    city_id: int,
    state: SessionState,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = _session_key(user_id, city_id)
    async with _get_lock(key):
        # Idempotency: if session already exists (concurrent message), return it
        existing = _session_store.get(key)
        if existing and time.time() - existing["updated_at"] <= SESSION_TTL:
            return existing
        session = {
            "session_id": str(uuid.uuid4()),
            "user_id":    user_id,
            "city_id":    city_id,
            "state":      state,
            "data":       data or {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _session_store[key] = session
        return session


async def update_session(
    user_id: int,
    city_id: int,
    state: SessionState,
    data: dict[str, Any] | None = None,
) -> None:
    key = _session_key(user_id, city_id)
    async with _get_lock(key):
        session = _session_store.get(key)
        if session:
            session["state"] = state
            session["updated_at"] = time.time()
            if data is not None:
                session["data"].update(data)


async def expire_session(user_id: int, city_id: int) -> None:
    key = _session_key(user_id, city_id)
    async with _get_lock(key):
        session = _session_store.get(key)
        if session:
            session["state"] = SessionState.EXPIRED
            _session_store.pop(key, None)


# ================================================================
# SECTION 6: ACCESS RULES
# ================================================================

@dataclass
class _AccessRule:
    allow_private:    bool
    allow_group:      bool
    requires_member:  bool


_ACCESS: dict[IntentType, _AccessRule] = {
    IntentType.START:    _AccessRule(True,  True,  False),
    IntentType.HELP:     _AccessRule(True,  True,  False),
    IntentType.PROFILE:  _AccessRule(True,  True,  True),
    IntentType.CITY:     _AccessRule(False, True,  True),
    IntentType.MISSIONS: _AccessRule(False, True,  True),
    IntentType.WORK:     _AccessRule(False, True,  True),
    IntentType.EXPLORE:  _AccessRule(False, True,  True),
    IntentType.MARKET:   _AccessRule(False, True,  True),
    IntentType.GUILD:    _AccessRule(False, True,  True),
    IntentType.RANK:     _AccessRule(False, True,  True),
}


def _check_access(intent: IntentType, ctx: GameContext) -> GameError | None:
    rule = _ACCESS.get(intent)
    if rule is None:
        return None
    if ctx.is_private and not rule.allow_private:
        return GameError.INVALID_ACTION
    if ctx.is_group and not rule.allow_group:
        return GameError.INVALID_ACTION
    return None


# ================================================================
# SECTION 7: ACTION HANDLERS
# ================================================================

# Signature: async (ctx, session | None) → GameResponse
ActionHandler = Callable[[GameContext, dict | None], Awaitable[GameResponse]]
_ACTION_REGISTRY: dict[IntentType, ActionHandler] = {}


def register_action(intent: IntentType) -> Callable[[ActionHandler], ActionHandler]:
    def decorator(fn: ActionHandler) -> ActionHandler:
        _ACTION_REGISTRY[intent] = fn
        return fn
    return decorator


# ----------------------------------------------------------------
# START
# ----------------------------------------------------------------
@register_action(IntentType.START)
async def _handle_start(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "⚡ به ECHO خوش اومدی!\n\n"
            "ECHO یک بازی گروهی متنی‌ه.\n"
            "توی گروه‌های Telegram بازی می‌کنی و هر گروه یک City داره.\n\n"
            "برای شروع، «راهنما» بفرست."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
    )


# ----------------------------------------------------------------
# HELP
# ----------------------------------------------------------------
@register_action(IntentType.HELP)
async def _handle_help(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "📖 راهنمای ECHO\n\n"
            "چیزایی که می‌تونی بفرستی:\n"
            "• «مأموریت» — مأموریت‌های فعال\n"
            "• «کار» — انجام کار روزانه\n"
            "• «اکتشاف» — کشف مناطق جدید\n"
            "• «پروفایل» — وضعیت شخصی‌ات\n"
            "• «شهر» — وضعیت City\n"
            "• «بازار» — قیمت‌ها و بازار\n"
            "• «گیلد» — گروه بازی‌ات\n"
            "• «رتبه» — نفرات برتر\n\n"
            "نیازی به دستوری خاص نیست — فقط بنویس."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
    )


# ----------------------------------------------------------------
# PROFILE
# ----------------------------------------------------------------
@register_action(IntentType.PROFILE)
async def _handle_profile(ctx: GameContext, _session: dict | None) -> GameResponse:
    # Real data will come from database.py
    # Placeholder shows contract: Global vs City separation
    profile_data: dict[str, Any] = {
        "global": {
            "level":             1,
            "xp":                0,
            "fame":              0,
            "global_reputation": 0,
        },
        "city": {
            "energy":            100,
            "city_reputation":   0,
            "wallet":            0,
            "contribution":      0,
            "role":              "شهروند",
        },
    }
    g = profile_data["global"]
    c = profile_data["city"]
    text = (
        f"👤 پروفایل @{ctx.username or ctx.user_id}\n\n"
        f"🌍 جهانی\n"
        f"  سطح: {g['level']}  |  XP: {g['xp']}  |  شهرت: {g['fame']}\n\n"
        f"🏙 این City\n"
        f"  ⚡ انرژی: {c['energy']}  |  💰 کیف‌پول: {c['wallet']}\n"
        f"  نقش: {c['role']}  |  مشارکت: {c['contribution']}"
    )
    return GameResponse(
        text=text,
        response_type=ResponseType.PERSONAL,
        public=False,
        state=SessionState.IDLE,
        metadata={"profile": profile_data},
    )


# ----------------------------------------------------------------
# CITY
# ----------------------------------------------------------------
@register_action(IntentType.CITY)
async def _handle_city(ctx: GameContext, _session: dict | None) -> GameResponse:
    city_data: dict[str, Any] = {
        "name":          "City نامشخص",
        "level":         1,
        "population":    0,
        "treasury":      0,
        "city_code":     f"C{ctx.city_id}",
        "activity":      "عادی",
    }
    d = city_data
    text = (
        f"🏙 {d['name']}  [{d['city_code']}]\n\n"
        f"  سطح: {d['level']}  |  جمعیت: {d['population']}\n"
        f"  خزانه: {d['treasury']}  |  فعالیت: {d['activity']}"
    )
    return GameResponse(
        text=text,
        response_type=ResponseType.PERSONAL,
        public=False,
        state=SessionState.IDLE,
        metadata={"city": city_data},
    )


# ----------------------------------------------------------------
# MISSIONS
# ----------------------------------------------------------------
@register_action(IntentType.MISSIONS)
async def _handle_missions(ctx: GameContext, _session: dict | None) -> GameResponse:
    # Foundation stub — Mission Engine built in a later Prompt
    missions = [
        {"id": 1, "title": "دو فعالیت انجام بده"},
        {"id": 2, "title": "یک منطقه را بررسی کن"},
        {"id": 3, "title": "۵۰۰۰ پول به دست بیار"},
    ]
    lines = "\n".join(f"{m['id']}. {m['title']}" for m in missions)
    session = await create_session(
        ctx.user_id, ctx.city_id,
        SessionState.WAITING_FOR_CHOICE,
        {"action": "mission_select", "missions": missions},
    )
    return GameResponse(
        text=(
            f"🎯 سه مأموریت برای امروز داری:\n\n{lines}\n\n"
            "برای انتخاب، شماره بفرست."
        ),
        response_type=ResponseType.PERSONAL,
        public=False,
        edit_preferred=False,
        session_id=session["session_id"],
        state=SessionState.WAITING_FOR_CHOICE,
        metadata={"missions": missions},
    )


# ----------------------------------------------------------------
# WORK
# ----------------------------------------------------------------
@register_action(IntentType.WORK)
async def _handle_work(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "⚙️ سیستم کار هنوز فعال نشده.\n"
            "به زودی می‌تونی کارهای روزانه انجام بدی."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
        error=GameError.FEATURE_NOT_READY,
    )


# ----------------------------------------------------------------
# EXPLORE
# ----------------------------------------------------------------
@register_action(IntentType.EXPLORE)
async def _handle_explore(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "🗺 سیستم اکتشاف هنوز فعال نشده.\n"
            "به زودی می‌تونی مناطق جدید پیدا کنی."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
        error=GameError.FEATURE_NOT_READY,
    )


# ----------------------------------------------------------------
# MARKET
# ----------------------------------------------------------------
@register_action(IntentType.MARKET)
async def _handle_market(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "🏪 بازار هنوز فعال نشده.\n"
            "به زودی می‌تونی خرید و فروش کنی."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
        error=GameError.FEATURE_NOT_READY,
    )


# ----------------------------------------------------------------
# GUILD
# ----------------------------------------------------------------
@register_action(IntentType.GUILD)
async def _handle_guild(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "⚔️ سیستم گیلد هنوز فعال نشده.\n"
            "به زودی می‌تونی گیلد تشکیل بدی یا بهش بپیوندی."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
        error=GameError.FEATURE_NOT_READY,
    )


# ----------------------------------------------------------------
# RANK
# ----------------------------------------------------------------
@register_action(IntentType.RANK)
async def _handle_rank(ctx: GameContext, _session: dict | None) -> GameResponse:
    return GameResponse(
        text=(
            "🏆 رتبه‌بندی هنوز فعال نشده.\n"
            "به زودی می‌تونی نفرات برتر رو ببینی."
        ),
        response_type=ResponseType.PERSONAL,
        state=SessionState.IDLE,
        error=GameError.FEATURE_NOT_READY,
    )


# ================================================================
# SECTION 8: SESSION CONTINUATION ENGINE
# ================================================================

async def _handle_session_input(
    ctx: GameContext,
    session: dict[str, Any],
) -> GameResponse:
    """
    Dispatch a message that belongs to an active session.
    Dispatches to the correct sub-handler based on session state.
    """
    state = SessionState(session["state"])
    norm = ctx.normalized_text

    # ── WAITING_FOR_CHOICE ──────────────────────────────────────
    if state == SessionState.WAITING_FOR_CHOICE:
        action = session["data"].get("action")

        if action == "mission_select":
            missions = session["data"].get("missions", [])
            try:
                choice = int(norm) if norm.isdigit() else int(
                    "".join(c for c in norm if c.isdigit())
                )
            except (ValueError, TypeError):
                return _error_response(GameError.INVALID_INPUT, ctx)

            mission = next((m for m in missions if m["id"] == choice), None)
            if not mission:
                return GameResponse(
                    text=(
                        f"گزینه‌ای به شماره {choice} وجود نداره.\n"
                        "یکی از شماره‌های نمایش داده شده رو بفرست."
                    ),
                    response_type=ResponseType.PERSONAL,
                    state=state,
                    session_id=session["session_id"],
                )

            await update_session(
                ctx.user_id, ctx.city_id,
                SessionState.WAITING_FOR_CONFIRMATION,
                {"selected_mission": mission},
            )
            return GameResponse(
                text=(
                    f"🎯 مأموریت انتخابی:\n«{mission['title']}»\n\n"
                    "شروع می‌کنی؟  (بله / نه)"
                ),
                response_type=ResponseType.PERSONAL,
                state=SessionState.WAITING_FOR_CONFIRMATION,
                session_id=session["session_id"],
            )

        # Fallback for unknown choice action
        return _error_response(GameError.INVALID_ACTION, ctx)

    # ── WAITING_FOR_CONFIRMATION ─────────────────────────────────
    if state == SessionState.WAITING_FOR_CONFIRMATION:
        intent = detect_intent(norm)

        if intent == IntentType.CONFIRM:
            selected = session["data"].get("selected_mission")
            await expire_session(ctx.user_id, ctx.city_id)
            # Real mission start logic lives in mission.py (future Prompt)
            return GameResponse(
                text=(
                    f"✅ مأموریت «{selected['title']}» شروع شد.\n"
                    "موفق باشی!"
                ),
                response_type=ResponseType.PERSONAL,
                state=SessionState.COMPLETED,
            )

        if intent == IntentType.CANCEL:
            await expire_session(ctx.user_id, ctx.city_id)
            return GameResponse(
                text="لغو شد. هر وقت خواستی دوباره «مأموریت» بفرست.",
                response_type=ResponseType.PERSONAL,
                state=SessionState.IDLE,
            )

        return GameResponse(
            text="«بله» یا «نه» بفرست.",
            response_type=ResponseType.PERSONAL,
            state=state,
            session_id=session["session_id"],
        )

    # ── INPUT_REQUIRED ───────────────────────────────────────────
    if state == SessionState.INPUT_REQUIRED:
        # Generic text collection — specific logic injected via session data
        handler_name = session["data"].get("input_handler")
        if handler_name:
            # Future: route to registered input handlers
            pass
        return _error_response(GameError.INVALID_INPUT, ctx)

    # ── Catch-all: unknown / expired state ───────────────────────
    await expire_session(ctx.user_id, ctx.city_id)
    return _error_response(GameError.SESSION_EXPIRED, ctx)


# ================================================================
# SECTION 9: PUBLIC EVENT FACTORY
# ================================================================

def create_city_vote(
    city_id: int,
    question: str,
    metadata: dict[str, Any] | None = None,
) -> GameResponse:
    """
    Factory for city-wide vote events.
    Returns a public GameResponse with action metadata.
    handlers.py builds the actual buttons using Button Factory.
    """
    return GameResponse(
        text=f"🏛 یک تصمیم مهم برای City داریم.\n\n{question}",
        response_type=ResponseType.PUBLIC_VOTE,
        public=True,
        requires_ui=True,
        actions=[
            ActionButton("VOTE_YES", "بله",  ActionStyle.SUCCESS),
            ActionButton("VOTE_NO",  "خیر",  ActionStyle.DANGER),
        ],
        metadata={"city_id": city_id, **(metadata or {})},
    )


def create_global_event(
    title: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> GameResponse:
    """
    Factory for world-wide events.
    handlers.py renders JOIN / VIEW buttons.
    """
    return GameResponse(
        text=f"🌍 {title}\n\n{description}",
        response_type=ResponseType.PUBLIC_EVENT,
        public=True,
        requires_ui=True,
        actions=[
            ActionButton("JOIN_EVENT", "شرکت در Event", ActionStyle.SUCCESS),
            ActionButton("VIEW_EVENT", "مشاهده",        ActionStyle.PRIMARY),
        ],
        metadata=metadata or {},
    )


def create_legendary_discovery(username: str, discovery: str) -> GameResponse:
    """
    Legendary discovery: public broadcast, no buttons needed.
    """
    return GameResponse(
        text=(
            f"💎 یک Discovery افسانه‌ای پیدا شد!\n\n"
            f"@{username} اولین کسی بود که «{discovery}» رو کشف کرد."
        ),
        response_type=ResponseType.PUBLIC,
        public=True,
        requires_ui=False,
    )


# ================================================================
# SECTION 10: ERROR HELPERS
# ================================================================

_ERROR_TEXT: dict[GameError, str] = {
    GameError.INVALID_ACTION:      "این کار الان امکان‌پذیر نیست.",
    GameError.SESSION_EXPIRED:     (
        "این مرحله منقضی شده.\n"
        "برای شروع دوباره، درخواستت رو دوباره بفرست."
    ),
    GameError.NOT_MEMBER:          "اول باید وارد این City بشی.",
    GameError.INSUFFICIENT_ENERGY: (
        "⚡ انرژی کافی نداری.\n"
        "برای این کار انرژی بیشتری لازمه."
    ),
    GameError.INSUFFICIENT_FUNDS:  "💰 موجودی کافی نداری.",
    GameError.COOLDOWN:            "⏳ هنوز باید کمی صبر کنی.",
    GameError.RATE_LIMITED:        "خیلی سریع درخواست می‌دی. کمی صبر کن.",
    GameError.UNKNOWN_INTENT:      "متوجه نشدم. «راهنما» بفرست.",
    GameError.INVALID_INPUT:       "ورودی معتبر نیست.",
    GameError.FEATURE_NOT_READY:   "این بخش هنوز فعال نشده.",
}


def _error_response(error: GameError, ctx: GameContext) -> GameResponse:
    return GameResponse(
        text=_ERROR_TEXT.get(error, "یه مشکل پیش اومد."),
        response_type=ResponseType.PERSONAL,
        public=False,
        state=SessionState.IDLE,
        error=error,
    )


# ================================================================
# SECTION 11: MEMBERSHIP CHECK
# ================================================================

async def check_membership(user_id: int, city_id: int) -> bool:
    """
    Stub — will call database.py in integration.
    Returns True when user is a member of city_id.
    """
    # TODO-integration: replace with db.is_member(user_id, city_id)
    return True  # default open during bootstrap


# ================================================================
# SECTION 12: MAIN ENGINE — process_message
# ================================================================

async def process_message(ctx: GameContext) -> GameResponse:
    """
    Central pipeline:
        GameContext
          → Session check (priority)
          → Intent detection
          → Access check
          → Membership check
          → Action dispatch
          → GameResponse
    """
    norm = ctx.normalized_text

    # ── 1. Active session takes priority ────────────────────────
    session = await get_session(ctx.user_id, ctx.city_id)
    if session:
        return await _handle_session_input(ctx, session)

    # ── 2. Intent detection ──────────────────────────────────────
    intent = detect_intent(norm)

    # Numeric without session → ignore
    if intent == IntentType.NUMERIC:
        return GameResponse(response_type=ResponseType.SILENT)

    # No game intent → ignore normal group chat
    if intent == IntentType.NO_INTENT:
        return GameResponse(response_type=ResponseType.SILENT)

    # Bare CONFIRM / CANCEL without session → ignore
    if intent in (IntentType.CONFIRM, IntentType.CANCEL):
        return GameResponse(response_type=ResponseType.SILENT)

    # ── 3. Access rules ─────────────────────────────────────────
    access_error = _check_access(intent, ctx)
    if access_error:
        return _error_response(access_error, ctx)

    # ── 4. Membership check for protected intents ────────────────
    rule = _ACCESS.get(intent)
    if rule and rule.requires_member:
        is_member = await check_membership(ctx.user_id, ctx.city_id)
        if not is_member:
            return _error_response(GameError.NOT_MEMBER, ctx)

    # ── 5. Dispatch to action handler ───────────────────────────
    handler = _ACTION_REGISTRY.get(intent)
    if handler is None:
        return _error_response(GameError.UNKNOWN_INTENT, ctx)

    return await handler(ctx, None)


# ================================================================
# SECTION 13: LIVING WORLD / NETWORK EFFECT CONTRACTS
# ================================================================

@dataclass
class CityEventContract:
    """
    Placeholder contract for CityEvent Engine (future Prompt).
    game.py creates this; the event dispatcher publishes it.
    """
    event_type:  str                # e.g. "CITY_LEVEL_UP", "CITY_CRISIS"
    city_id:     int
    payload:     dict[str, Any]     = field(default_factory=dict)
    response:    GameResponse | None = None


@dataclass
class NetworkSnapshot:
    """
    Data consumed from the Network Effect Engine (future Prompt).
    Passed into actions that depend on city activity.
    """
    city_id:           int
    active_citizens:   int  = 0
    city_growth_score: int  = 0
    activity_level:    str  = "low"   # low / medium / high / peak
    referral_count:    int  = 0
    milestone_count:   int  = 0


# ================================================================
# SECTION 14: ANTI-ABUSE HELPERS
# ================================================================

_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW  = 5.0   # seconds
RATE_LIMIT_MAX_MSG = 5     # messages per window


async def check_rate_limit(user_id: int, city_id: int) -> bool:
    """Returns True if the user is within rate limits."""
    key = f"{user_id}:{city_id}"
    now = time.time()
    history = _rate_limit_store.get(key, [])
    # Keep only events inside the window
    history = [t for t in history if now - t < RATE_LIMIT_WINDOW]
    if len(history) >= RATE_LIMIT_MAX_MSG:
        _rate_limit_store[key] = history
        return False
    history.append(now)
    _rate_limit_store[key] = history
    return True


# ================================================================
# SECTION 15: INTEGRATION NOTES
# ================================================================
#
# database.py integration points:
#   • check_membership(user_id, city_id)   → db.is_member(...)
#   • _handle_profile(...)                 → db.get_profile(user_id, city_id)
#   • _handle_city(...)                    → db.get_city(city_id)
#   • _handle_missions(...)                → db.get_missions(user_id, city_id)
#   • Session store                        → Redis (replace _session_store)
#
# handlers.py integration:
#   • Receives GameResponse
#   • Checks response_type / public / requires_ui
#   • Builds InlineKeyboardButton from response.actions via Button Factory
#   • Calls bot.send_message() / message.answer() / message.edit_text()
#   • Button styles: ActionStyle.PRIMARY / SUCCESS / DANGER
#
# config.py:
#   • SESSION_TTL, RATE_LIMIT_* pulled from config
#
# ================================================================


# ================================================================
# SECTION 16: TESTS
# ================================================================

if __name__ == "__main__":
    import asyncio

    async def _run_tests() -> None:
        sep = "─" * 55
        passed = failed = 0

        async def check(label: str, got: Any, expected: Any) -> None:
            nonlocal passed, failed
            ok = got == expected
            symbol = "✓" if ok else "✗"
            print(f"  {symbol}  {label}")
            if not ok:
                print(f"      expected: {expected!r}")
                print(f"      got:      {got!r}")
                failed += 1
            else:
                passed += 1

        def ctx(text: str, uid: int = 1, cid: int = 10, **kw) -> GameContext:
            return GameContext(user_id=uid, city_id=cid,
                               chat_id=cid, message_id=1,
                               text=text, is_group=True, **kw)

        # ── Normalization ──────────────────────────────────────
        print(f"\n{sep}\n  TEXT NORMALIZATION\n{sep}")
        await check("arabic ي→ی", normalize_text("ماموريت"), "ماموریت")
        await check("arabic ك→ک", normalize_text("كار"),     "کار")
        await check("trailing ؟", normalize_text("شهر؟"),    "شهر")
        await check("ZWNJ→space",
                    normalize_text("مأموریت\u200cها"), "مأموریت ها")

        # ── Intent detection ──────────────────────────────────
        print(f"\n{sep}\n  INTENT DETECTION\n{sep}")
        await check("مأموریت",          detect_intent("مأموریت"),      IntentType.MISSIONS)
        await check("شهرمون",           detect_intent("شهرمون"),       IntentType.CITY)
        await check("پروفایل",          detect_intent("پروفایل"),      IntentType.PROFILE)
        await check("بازار",            detect_intent("بازار"),        IntentType.MARKET)
        await check("رتبه",             detect_intent("رتبه"),         IntentType.RANK)
        await check("بله=CONFIRM",      detect_intent("بله"),          IntentType.CONFIRM)
        await check("نه=CANCEL",        detect_intent("نه"),           IntentType.CANCEL)
        await check("2=NUMERIC",        detect_intent("2"),            IntentType.NUMERIC)
        await check("سلام=NO_INTENT",   detect_intent("سلام"),         IntentType.NO_INTENT)
        await check("فوتبال=NO_INTENT", detect_intent("فوتبال"),       IntentType.NO_INTENT)

        # ── Normal group chat → silent ─────────────────────────
        print(f"\n{sep}\n  NORMAL GROUP CHAT (must be silent)\n{sep}")
        r = await process_message(ctx("امروز کسی فوتبال میاد؟"))
        await check("football chat silent", r.is_silent, True)
        r = await process_message(ctx("😂"))
        await check("emoji silent",         r.is_silent, True)
        r = await process_message(ctx("سلام"))
        await check("hello silent",         r.is_silent, True)

        # ── Numeric without session → silent ───────────────────
        print(f"\n{sep}\n  NUMERIC WITHOUT SESSION\n{sep}")
        r = await process_message(ctx("2", uid=999, cid=50))
        await check("bare 2 silent", r.is_silent, True)

        # ── Help ──────────────────────────────────────────────
        print(f"\n{sep}\n  HELP\n{sep}")
        r = await process_message(ctx("راهنما"))
        await check("help is personal", r.response_type, ResponseType.PERSONAL)
        await check("help not public",  r.public,        False)

        # ── Profile ───────────────────────────────────────────
        print(f"\n{sep}\n  PROFILE\n{sep}")
        r = await process_message(ctx("پروفایل", uid=10, cid=10))
        await check("profile personal", r.response_type, ResponseType.PERSONAL)
        await check("profile not public", r.public, False)
        await check("profile has metadata", "profile" in r.metadata, True)

        # ── Multi-City isolation ──────────────────────────────
        print(f"\n{sep}\n  MULTI-CITY ISOLATION\n{sep}")
        r_a = await process_message(ctx("مأموریت", uid=100, cid=10))
        r_b = await process_message(ctx("پروفایل", uid=100, cid=20))
        await check("city A session created",    r_a.state,  SessionState.WAITING_FOR_CHOICE)
        await check("city B profile personal",   r_b.response_type, ResponseType.PERSONAL)
        # City A session must not bleed into City B
        sess_b = await get_session(100, 20)
        await check("city B has no choice session", sess_b is None, True)
        # Clean up
        await expire_session(100, 10)

        # ── Session priority ──────────────────────────────────
        print(f"\n{sep}\n  SESSION PRIORITY\n{sep}")
        uid, cid = 200, 10
        # Start a mission flow
        await process_message(ctx("مأموریت", uid=uid, cid=cid))
        # Send numeric choice
        r = await process_message(ctx("2", uid=uid, cid=cid))
        await check("session intercepts numeric",
                    r.state, SessionState.WAITING_FOR_CONFIRMATION)
        # Confirm
        r = await process_message(ctx("بله", uid=uid, cid=cid))
        await check("confirm completes mission", r.state, SessionState.COMPLETED)
        # Ensure session cleared
        sess = await get_session(uid, cid)
        await check("session cleared after complete", sess is None, True)

        # ── Session cancellation ──────────────────────────────
        print(f"\n{sep}\n  SESSION CANCELLATION\n{sep}")
        uid2, cid2 = 300, 10
        await process_message(ctx("مأموریت", uid=uid2, cid=cid2))
        await process_message(ctx("1", uid=uid2, cid=cid2))   # select mission 1
        r = await process_message(ctx("نه", uid=uid2, cid=cid2))
        await check("cancel returns to idle", r.state, SessionState.IDLE)

        # ── Public event factory ──────────────────────────────
        print(f"\n{sep}\n  PUBLIC EVENT FACTORIES\n{sep}")
        vote = create_city_vote(10, "مالیات افزایش پیدا کند؟")
        await check("vote is public",       vote.public,       True)
        await check("vote requires_ui",     vote.requires_ui,  True)
        await check("vote actions count",   len(vote.actions), 2)
        await check("vote action VOTE_YES", vote.actions[0].action, "VOTE_YES")
        await check("vote YES style",
                    vote.actions[0].style, ActionStyle.SUCCESS)

        event = create_global_event("رویداد جهانی", "یک چالش بزرگ شروع شده.")
        await check("global event public",      event.public,      True)
        await check("global event requires_ui", event.requires_ui, True)
        await check("JOIN_EVENT action",
                    event.actions[0].action, "JOIN_EVENT")

        legendary = create_legendary_discovery("Sajad", "گنجینه گمشده")
        await check("legendary public",      legendary.public,      True)
        await check("legendary no ui",       legendary.requires_ui, False)

        # ── Feature not ready ─────────────────────────────────
        print(f"\n{sep}\n  FEATURE-NOT-READY RESPONSES\n{sep}")
        for word in ["کار", "اکتشاف", "بازار", "گیلد", "رتبه"]:
            r = await process_message(ctx(word, uid=1, cid=10))
            await check(f"{word} error=FEATURE_NOT_READY",
                        r.error, GameError.FEATURE_NOT_READY)

        # ── Rate limit ────────────────────────────────────────
        print(f"\n{sep}\n  RATE LIMIT\n{sep}")
        ok_count = sum(
            [await check_rate_limit(777, 10) for _ in range(5)]
        )
        blocked = not await check_rate_limit(777, 10)
        await check("5 messages allowed", ok_count, 5)
        await check("6th message blocked", blocked, True)

        # ── Summary ───────────────────────────────────────────
        total = passed + failed
        print(f"\n{sep}")
        print(f"  نتیجه: {passed}/{total} تست موفق"
              + (" ✓" if failed == 0 else f"  |  {failed} تست ناموفق ✗"))
        print(sep)

    asyncio.run(_run_tests())
