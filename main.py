# ================================================================
# ECHO — main.py
# APPLICATION ENTRY POINT & RUNTIME ORCHESTRATION
# ================================================================
# Responsibilities:
#   - Application lifecycle (startup / shutdown)
#   - Bot & Dispatcher creation (exactly once)
#   - Router registration from handlers.py
#   - Database & Redis initialization from database.py
#   - Health checks before polling
#   - Telegram connection verification
#   - Background task management
#   - Structured logging
#   - Graceful shutdown on SIGTERM / SIGINT
#
# NOT responsible for:
#   - Game logic        → game.py
#   - Data models       → database.py
#   - Intent detection  → game.py
#   - UI / buttons      → handlers.py
#   - Configuration     → config.py
# ================================================================

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import List

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ── Internal imports ─────────────────────────────────────────────
from config import settings

from database import (
    init_database,
    close_database,
    database_health,
    redis_health,
    close_redis,
)

from game import get_game_engine          # noqa: F401  (engine wired to handlers)

from handlers import (
    group_router,
    private_router,
    setup_bot_commands,
)

# ================================================================
# LOGGING SETUP
# ================================================================

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | echo | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """Set up root logger with the ECHO format."""
    level = logging.DEBUG if getattr(settings, "debug", False) else logging.INFO

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
    )
    # Quiet noisy third-party loggers
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


log = logging.getLogger("echo")

# ================================================================
# BACKGROUND TASK REGISTRY
# ================================================================

_background_tasks: List[asyncio.Task] = []


def _register_background_task(coro) -> asyncio.Task:
    """Create an asyncio Task and keep a reference so it isn't garbage-collected."""
    task = asyncio.create_task(coro)
    _background_tasks.append(task)
    task.add_done_callback(_background_tasks.remove)
    return task


async def _cancel_background_tasks() -> None:
    """Cancel all tracked background tasks and wait for them to finish."""
    if not _background_tasks:
        return
    log.info("Cancelling %d background task(s)…", len(_background_tasks))
    for task in list(_background_tasks):
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    log.info("Background tasks stopped.")


# ================================================================
# STARTUP
# ================================================================

async def startup(bot: Bot, dp: Dispatcher) -> None:
    """
    Ordered startup sequence:
      Config (already loaded) → Database → Redis → Bot identity
      → Router registration → Bot commands → Webhook cleanup → Ready
    """
    log.info("=" * 50)
    log.info("===== ECHO STARTING =====")
    log.info("Environment : %s", getattr(settings, "app_env", "production"))
    log.info("=" * 50)

    # ── 1. Database ───────────────────────────────────────────────
    log.info("Initializing database…")
    try:
        await init_database()
    except Exception:
        log.exception("Database startup failed — aborting.")
        sys.exit(1)

    db_ok = await database_health()
    if not db_ok:
        log.error("Database health check failed — aborting startup.")
        sys.exit(1)
    log.info("Database : OK")

    # ── 2. Redis ──────────────────────────────────────────────────
    log.info("Checking Redis…")
    redis_ok = await redis_health()
    if not redis_ok:
        log.error("Redis health check failed — aborting startup.")
        sys.exit(1)
    log.info("Redis     : OK")

    # ── 3. Telegram connection ────────────────────────────────────
    log.info("Verifying Telegram token…")
    try:
        me = await bot.get_me()
    except Exception:
        log.exception("Telegram authentication failed — aborting startup.")
        sys.exit(1)
    log.info("Telegram  : @%s  (id=%d)", me.username, me.id)

    # ── 4. Register routers ───────────────────────────────────────
    dp.include_router(group_router)
    dp.include_router(private_router)
    log.info("Routers   : OK  (group + private)")

    # ── 5. Bot commands ───────────────────────────────────────────
    try:
        await setup_bot_commands(bot)
        log.info("Commands  : configured")
    except Exception:
        log.exception("Failed to configure bot commands — continuing anyway.")

    # ── 6. Delete webhook / drop stale updates ───────────────────
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Webhook   : cleared")

    log.info("=" * 50)
    log.info("===== ECHO READY — POLLING STARTED =====")
    log.info("=" * 50)


# ================================================================
# SHUTDOWN
# ================================================================

async def shutdown(bot: Bot) -> None:
    """
    Ordered shutdown sequence:
      Background tasks → Redis → Database engine → Bot session
    """
    log.info("Shutting down ECHO…")

    # 1. Background tasks
    await _cancel_background_tasks()

    # 2. Redis
    try:
        await close_redis()
        log.info("Redis connection closed.")
    except Exception:
        log.exception("Error closing Redis.")

    # 3. Database engine
    try:
        await close_database()
        log.info("Database connection closed.")
    except Exception:
        log.exception("Error closing database.")

    # 4. Bot session
    try:
        await bot.session.close()
        log.info("Bot session closed.")
    except Exception:
        log.exception("Error closing bot session.")

    log.info("ECHO stopped.")


# ================================================================
# APPLICATION ENTRY POINT
# ================================================================

async def main() -> None:
    configure_logging()

    # ── Create Bot (exactly once) ─────────────────────────────────
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # ── Create Dispatcher (exactly once) ─────────────────────────
    dp = Dispatcher()

    # ── Startup sequence ──────────────────────────────────────────
    await startup(bot, dp)

    # ── Install graceful shutdown hooks ──────────────────────────
    loop = asyncio.get_running_loop()

    def _signal_handler(sig: signal.Signals) -> None:
        log.info("Received signal %s — initiating shutdown…", sig.name)
        loop.create_task(dp.stop_polling())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler, sig)
        except (NotImplementedError, RuntimeError):
            # Windows does not support add_signal_handler for all signals
            pass

    # ── Polling (single loop, blocking until stopped) ─────────────
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await shutdown(bot)


# ================================================================
# SCRIPT ENTRY
# ================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
