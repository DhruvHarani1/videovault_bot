import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import uuid
import uvicorn
from telegram import Update
from bot.config import settings
from bot.models import init_db, seed_legacy_video_if_needed, backfill_plans_phase0
from bot.services import start_scheduler, preload_config, set_config
from bot.bots import build_all_applications, BOT_KEYS

INSTANCE_ID = str(uuid.uuid4())

from fastapi import FastAPI, Request, HTTPException, Header
from contextlib import asynccontextmanager

# Create logs and data directories if not exist
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

# Configure logging with rotation
log_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

file_handler = RotatingFileHandler(
    "logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Holds {bot_key: Application} once built.
applications: dict = {}


def _representative_bot():
    """The bot used for scheduler jobs (daily reminders/reports, alerts).
    Prefer the payment bot (legacy users have started it); else any available."""
    if "payment" in applications:
        return applications["payment"].bot
    if applications:
        return next(iter(applications.values())).bot
    return None


async def _register_command_menus(bot_key, application) -> None:
    """Register the "/" autocomplete menus for a bot (public + admin-scoped)."""
    try:
        from admin.panel import apply_command_menus
        await apply_command_menus(application.bot, bot_key, settings.ADMIN_USER_IDS)
    except Exception as e:
        logger.error(f"Failed to register command menus for '{bot_key}': {e}")


async def init_shared_state() -> None:
    """One-time process-wide initialization (DB, seed, backfill, config cache).
    Must run exactly once regardless of how many bot Applications exist."""
    logger.info("Initializing database...")
    await init_db()

    logger.info("Seeding legacy video row (if needed)...")
    await seed_legacy_video_if_needed()

    logger.info("Running Phase 0 plan backfill (if needed)...")
    await backfill_plans_phase0()

    logger.info(f"Registering active instance ID: {INSTANCE_ID}")
    await set_config("ACTIVE_INSTANCE_ID", INSTANCE_ID)

    logger.info("Preloading configuration...")
    await preload_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Webhook-mode lifespan: init shared state, start every bot Application, and
    register each bot's own webhook path with its own secret token."""
    apps = app.state.applications

    await init_shared_state()

    # Start each bot Application + register its "/" command menus.
    for key, application in apps.items():
        await application.initialize()
        await application.start()
        await _register_command_menus(key, application)

    # Scheduler uses a single representative bot.
    rep = _representative_bot()
    if rep is not None:
        logger.info("Starting scheduler...")
        start_scheduler(rep)

    # Register a distinct webhook path + secret per bot.
    for key, application in apps.items():
        webhook_url = f"{settings.WEBHOOK_URL}/telegram/{key}"
        secret = settings.webhook_secret_for(key)
        logger.info(f"Setting webhook for '{key}' bot → {webhook_url}")
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=secret,
            drop_pending_updates=True,
        )

    logger.info(f"All {len(apps)} bot(s) initialized and ready (webhook mode).")
    yield

    # Graceful shutdown.
    logger.info("Shutting down bots (webhook mode)...")
    for key, application in apps.items():
        try:
            await application.stop()
            await application.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down '{key}' bot: {e}")
    await _shutdown_shared()


async def _shutdown_shared() -> None:
    """Stop scheduler and close the DB pool."""
    try:
        from bot.services.scheduler import scheduler
        if scheduler.running:
            logger.info("Stopping scheduler...")
            scheduler.shutdown(wait=False)
    except Exception as e:
        logger.error(f"Error stopping scheduler: {e}")
    try:
        from bot.models.user import engine
        logger.info("Closing database connection pool...")
        await engine.dispose()
    except Exception as e:
        logger.error(f"Error closing DB connection: {e}")
    logger.info("Graceful shutdown complete.")


# Create FastAPI app with lifespan for Webhook Mode
webhook_app = FastAPI(title="VideoVault Multi-Bot Webhook Server", lifespan=lifespan)

# Mount the Razorpay webhook sub-app (dormant but preserved).
from bot.webhook_server import app as razorpay_app
webhook_app.mount("/razorpay-webhook", razorpay_app)


@webhook_app.post("/telegram/{bot_key}")
async def telegram_webhook(bot_key: str, request: Request):
    """Receives Telegram updates for a specific bot and feeds its Application."""
    apps = request.app.state.applications
    application = apps.get(bot_key)
    if application is None:
        raise HTTPException(status_code=404, detail=f"Unknown bot '{bot_key}'")

    expected_secret = settings.webhook_secret_for(bot_key)
    received_secret = request.headers.get("x-telegram-bot-api-secret-token")
    if received_secret != expected_secret:
        logger.warning(f"Unauthorized webhook for '{bot_key}': bad secret token.")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update for '{bot_key}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")

    return {"status": "ok"}


@webhook_app.post("/telegram")
async def telegram_webhook_legacy(request: Request):
    """Backward-compatible alias: the original single-bot path maps to the payment bot."""
    return await telegram_webhook("payment", request)


@webhook_app.get("/")
async def root_check():
    return {"status": "ok"}


@webhook_app.get("/health")
async def health_check():
    return {"status": "ok", "bots": list(webhook_app.state.applications.keys()) if hasattr(webhook_app.state, "applications") else []}


@webhook_app.delete("/telegram-webhook")
async def delete_telegram_webhook(request: Request, x_admin_token: str = Header(None)):
    """Admin endpoint to deregister ALL bots' Telegram webhooks."""
    if x_admin_token != settings.WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    apps = request.app.state.applications
    results = {}
    for key, application in apps.items():
        try:
            await application.bot.delete_webhook()
            results[key] = "deleted"
        except Exception as e:
            results[key] = f"error: {e}"
    return {"status": "done", "results": results}


async def main_async() -> None:
    """Build all bots and run in Webhook (prod) or Polling (local dev) mode."""
    global applications

    applications = build_all_applications()
    if not applications:
        logger.error("No bots configured — set at least one *_BOT_TOKEN. Exiting.")
        return

    # Share with the webhook router (used by lifespan + routes).
    webhook_app.state.applications = applications

    port = int(os.getenv("PORT", "8443"))
    server = None

    try:
        if settings.WEBHOOK_URL:
            logger.info(f"Starting VideoVault in WEBHOOK mode with bots: {list(applications)}")
            config = uvicorn.Config(app=webhook_app, host="0.0.0.0", port=port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()
        else:
            logger.info(f"Starting VideoVault in POLLING mode with bots: {list(applications)}")
            await init_shared_state()

            for key, application in applications.items():
                await application.initialize()
                await application.start()
                await _register_command_menus(key, application)
                await application.updater.start_polling(drop_pending_updates=True)
                logger.info(f"Bot '{key}' polling for updates...")

            rep = _representative_bot()
            if rep is not None:
                logger.info("Starting scheduler...")
                start_scheduler(rep)

            # Serve Razorpay webhook + health endpoints alongside polling.
            config = uvicorn.Config(app="bot.webhook_server:app", host="0.0.0.0", port=port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

    finally:
        logger.info("Termination received. Starting graceful shutdown...")
        if not settings.WEBHOOK_URL:
            # Polling mode: stop updaters + apps here (webhook mode does it in lifespan).
            for key, application in applications.items():
                try:
                    if application.updater and application.updater.running:
                        await application.updater.stop()
                    await application.stop()
                    await application.shutdown()
                except Exception as e:
                    logger.error(f"Error during '{key}' bot shutdown: {e}")
            await _shutdown_shared()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
