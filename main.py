# ================================================================
# ECHO — main.py
# APPLICATION ENTRY POINT & RUNTIME ORCHESTRATION
# ================================================================

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings

from database import (
    init_database,
    database_health,
    redis_health,
    close_database,
    close_redis,
)

from handlers import (
    group_router,
    private_router,
)


# ================================================================
# LOGGING
# ================================================================

LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)-8s | "
    "echo | "
    "%(message)s"
)

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("echo")


def configure_logging() -> None:
    """
    Configure application logging.
    """

    debug = bool(
        getattr(
            settings,
            "debug",
            False,
        )
    )

    logging.basicConfig(
        level=(
            logging.DEBUG
            if debug
            else logging.INFO
        ),
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )

    # Reduce third-party noise.
    logging.getLogger(
        "aiogram"
    ).setLevel(logging.WARNING)

    logging.getLogger(
        "aiohttp"
    ).setLevel(logging.WARNING)

    logging.getLogger(
        "asyncio"
    ).setLevel(logging.WARNING)


# ================================================================
# BACKGROUND TASK MANAGEMENT
# ================================================================

_background_tasks: set[
    asyncio.Task[Any]
] = set()


def register_background_task(
    coroutine: Any,
) -> asyncio.Task[Any]:
    """
    Register a background task.
    """

    task = asyncio.create_task(
        coroutine
    )

    _background_tasks.add(
        task
    )

    task.add_done_callback(
        _background_tasks.discard
    )

    return task


async def cancel_background_tasks() -> None:
    """
    Stop all registered background tasks.
    """

    if not _background_tasks:
        return

    logger.info(
        "Stopping %d background task(s)...",
        len(_background_tasks),
    )

    tasks = list(
        _background_tasks
    )

    for task in tasks:
        if not task.done():
            task.cancel()

    await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    _background_tasks.clear()

    logger.info(
        "Background tasks stopped."
    )


# ================================================================
# STARTUP
# ================================================================

async def startup(
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    """
    Complete ECHO startup sequence.
    """

    logger.info(
        "=================================================="
    )

    logger.info(
        "===== ECHO STARTING ====="
    )

    logger.info(
        "Environment: %s",
        getattr(
            settings,
            "app_env",
            "production",
        ),
    )

    logger.info(
        "=================================================="
    )

    # ------------------------------------------------------------
    # 1. Database + Redis
    # ------------------------------------------------------------

    logger.info(
        "Initializing Data Layer..."
    )

    try:

        await init_database()

    except Exception:

        logger.exception(
            "Data Layer initialization failed."
        )

        raise

    # ------------------------------------------------------------
    # 2. PostgreSQL health
    # ------------------------------------------------------------

    if not await database_health():

        raise RuntimeError(
            "PostgreSQL health check failed."
        )

    logger.info(
        "PostgreSQL: OK"
    )

    # ------------------------------------------------------------
    # 3. Redis health
    # ------------------------------------------------------------

    if not await redis_health():

        raise RuntimeError(
            "Redis health check failed."
        )

    logger.info(
        "Redis: OK"
    )

    # ------------------------------------------------------------
    # 4. Telegram authentication
    # ------------------------------------------------------------

    logger.info(
        "Checking Telegram connection..."
    )

    try:

        me = await bot.get_me()

    except Exception:

        logger.exception(
            "Telegram authentication failed."
        )

        raise

    logger.info(
        "Telegram: @%s | id=%s",
        me.username,
        me.id,
    )

    # ------------------------------------------------------------
    # 5. Register routers
    # ------------------------------------------------------------

    dispatcher.include_router(
        group_router
    )

    dispatcher.include_router(
        private_router
    )

    logger.info(
        "Routers: group + private registered."
    )

   
    # ------------------------------------------------------------
    # 7. Clear old webhook
    # ------------------------------------------------------------

    try:

        await bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Webhook: cleared."
        )

    except Exception:

        logger.exception(
            "Failed to clear Telegram webhook."
        )

        raise

    # ------------------------------------------------------------
    # 8. Ready
    # ------------------------------------------------------------

    logger.info(
        "=================================================="
    )

    logger.info(
        "===== ECHO READY ====="
    )

    logger.info(
        "Polling is ready to start."
    )

    logger.info(
        "=================================================="
    )


# ================================================================
# SHUTDOWN
# ================================================================

async def shutdown(
    bot: Bot,
) -> None:
    """
    Graceful ECHO shutdown.
    """

    logger.info(
        "===== ECHO SHUTDOWN ====="
    )

    # ------------------------------------------------------------
    # 1. Background tasks
    # ------------------------------------------------------------

    try:

        await cancel_background_tasks()

    except Exception:

        logger.exception(
            "Background task shutdown error."
        )

    # ------------------------------------------------------------
    # 2. Redis
    # ------------------------------------------------------------

    try:

        await close_redis()

        logger.info(
            "Redis: closed."
        )

    except Exception:

        logger.exception(
            "Redis shutdown error."
        )

    # ------------------------------------------------------------
    # 3. PostgreSQL
    # ------------------------------------------------------------

    try:

        await close_database()

        logger.info(
            "PostgreSQL: closed."
        )

    except Exception:

        logger.exception(
            "Database shutdown error."
        )

    # ------------------------------------------------------------
    # 4. Telegram HTTP session
    # ------------------------------------------------------------

    try:

        await bot.session.close()

        logger.info(
            "Telegram session: closed."
        )

    except Exception:

        logger.exception(
            "Telegram session shutdown error."
        )

    logger.info(
        "ECHO stopped."
    )


# ================================================================
# MAIN
# ================================================================

async def main() -> None:
    """
    Main application entry point.
    """

    configure_logging()

    bot: Bot | None = None

    try:

        # --------------------------------------------------------
        # Create Bot
        # --------------------------------------------------------

        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML
            ),
        )

        # --------------------------------------------------------
        # Create Dispatcher
        # --------------------------------------------------------

        dispatcher = Dispatcher()

        # --------------------------------------------------------
        # Startup
        # --------------------------------------------------------

        await startup(
            bot,
            dispatcher,
        )

        # --------------------------------------------------------
        # Signal Handling
        # --------------------------------------------------------

        loop = asyncio.get_running_loop()

        def handle_signal(
            sig: signal.Signals,
        ) -> None:

            logger.info(
                "Received %s. Stopping polling...",
                sig.name,
            )

            asyncio.create_task(
                dispatcher.stop_polling()
            )

        for sig in (
            signal.SIGINT,
            signal.SIGTERM,
        ):

            try:

                loop.add_signal_handler(
                    sig,
                    handle_signal,
                    sig,
                )

            except (
                NotImplementedError,
                RuntimeError,
            ):
                # Windows / limited environments.
                pass

        # --------------------------------------------------------
        # Polling
        # --------------------------------------------------------

        logger.info(
            "===== ECHO POLLING STARTED ====="
        )

        await dispatcher.start_polling(
            bot,
            allowed_updates=(
                dispatcher
                .resolve_used_update_types()
            ),
        )

    except KeyboardInterrupt:

        logger.info(
            "Keyboard interrupt received."
        )

    except Exception:

        logger.exception(
            "Fatal ECHO runtime error."
        )

        raise

    finally:

        if bot is not None:

            await shutdown(
                bot
            )


# ================================================================
# SCRIPT ENTRY
# ================================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        pass
