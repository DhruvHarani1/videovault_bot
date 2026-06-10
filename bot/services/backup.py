"""Database backup & restore via Telegram (free persistence on Render's free tier).

The SQLite DB is wiped on every Render redeploy. To survive that without a paid
disk or external DB, we snapshot the DB into a private backup channel and pin the
latest snapshot. A bot can read a channel's pinned message, so on startup — if the
local DB is missing/empty — we download the pinned snapshot and restore it.

Only meaningful for SQLite. On Postgres these functions no-op (Postgres persists).
"""
import os
import json
import sqlite3
import asyncio
import logging
from datetime import datetime

from telegram import Bot

from bot.config import settings
from bot.services.config import get_config, set_config

logger = logging.getLogger(__name__)

_SNAPSHOT_PREFIX = "videovault_snapshot_"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_COUNT_TABLES = ["users", "plans", "content_items", "plan_access", "payment_tickets", "deliveries"]

# config keys (live in bot_config, so they ride along inside the snapshots)
_CFG_MSG_IDS = "BACKUP_MESSAGE_IDS"
_CFG_LAST_AT = "BACKUP_LAST_AT"


# ── helpers ──────────────────────────────────────────────────────────────────

def db_path() -> str | None:
    """Local SQLite file path, or None if not using SQLite."""
    url = settings.DATABASE_URL
    if "sqlite" not in url:
        return None
    parts = url.split("///")
    return parts[-1] if len(parts) > 1 else None


def is_backup_enabled() -> bool:
    return bool(db_path()) and bool(settings.BACKUP_CHANNEL_ID)


def _is_sqlite_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == _SQLITE_MAGIC
    except Exception:
        return False


def _make_snapshot_sync(src_path: str, dst_path: str) -> dict:
    """Consistent copy via SQLite's online backup API + row counts (runs in a thread)."""
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return _row_counts_sync(dst_path)


def _row_counts_sync(path: str) -> dict:
    counts = {}
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        for t in _COUNT_TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                counts[t] = cur.fetchone()[0]
            except Exception:
                counts[t] = "?"
    finally:
        conn.close()
    return counts


def _counts_line(counts: dict) -> str:
    return " · ".join(f"{k}={v}" for k, v in counts.items())


# ── snapshot creation + upload ───────────────────────────────────────────────

async def create_snapshot_file() -> tuple[str, dict] | None:
    """Create a consistent snapshot file under data/. Returns (path, counts)."""
    src = db_path()
    if not src or not os.path.exists(src):
        return None
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join("data", f"{_SNAPSHOT_PREFIX}{ts}.db")
    counts = await asyncio.to_thread(_make_snapshot_sync, src, dst)
    return dst, counts


async def upload_and_pin(bot: Bot) -> dict:
    """Snapshot the DB, upload it to the backup channel, pin it (replacing the old
    pin), prune to BACKUP_RETENTION. Returns a result dict."""
    if not is_backup_enabled():
        return {"ok": False, "reason": "Backups disabled (need SQLite + BACKUP_CHANNEL_ID)."}

    made = await create_snapshot_file()
    if not made:
        return {"ok": False, "reason": "No database file to snapshot yet."}
    path, counts = made
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    fname = os.path.basename(path)

    try:
        with open(path, "rb") as f:
            msg = await bot.send_document(
                chat_id=settings.BACKUP_CHANNEL_ID,
                document=f,
                filename=fname,
                caption=f"🗄 DB snapshot — {ts}\n{_counts_line(counts)}",
            )
        # Keep exactly one pinned message = the latest snapshot.
        pinned_ok = True
        try:
            await bot.unpin_all_chat_messages(chat_id=settings.BACKUP_CHANNEL_ID)
            await bot.pin_chat_message(
                chat_id=settings.BACKUP_CHANNEL_ID, message_id=msg.message_id, disable_notification=True
            )
        except Exception as pe:
            pinned_ok = False
            logger.error(f"Backup pin failed (check the bot's Pin permission): {pe}")

        await _record_and_prune(bot, msg.message_id)
        await set_config(_CFG_LAST_AT, ts)
        logger.info(f"Backup uploaded (msg {msg.message_id}); {_counts_line(counts)}")
        return {"ok": True, "message_id": msg.message_id, "ts": ts, "counts": counts, "pinned": pinned_ok}
    except Exception as e:
        logger.error(f"Backup upload failed: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)}
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


async def _record_and_prune(bot: Bot, new_msg_id: int) -> None:
    """Track recent snapshot message ids in config and delete ones beyond retention."""
    try:
        ids = json.loads(get_config(_CFG_MSG_IDS, "[]"))
        if not isinstance(ids, list):
            ids = []
    except Exception:
        ids = []
    ids.append(new_msg_id)

    keep = max(1, settings.BACKUP_RETENTION)
    while len(ids) > keep:
        old = ids.pop(0)
        try:
            await bot.delete_message(chat_id=settings.BACKUP_CHANNEL_ID, message_id=old)
        except Exception as e:
            logger.info(f"Could not delete old snapshot {old}: {e}")
    await set_config(_CFG_MSG_IDS, json.dumps(ids))


async def send_to_admin(bot: Bot, chat_id: int) -> dict:
    """Create a fresh snapshot and send the .db file to an admin's chat."""
    if not db_path():
        return {"ok": False, "reason": "Not using SQLite — nothing to export."}
    made = await create_snapshot_file()
    if not made:
        return {"ok": False, "reason": "No database file to snapshot yet."}
    path, counts = made
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with open(path, "rb") as f:
            await bot.send_document(
                chat_id=chat_id, document=f, filename=os.path.basename(path),
                caption=f"🗄 DB snapshot — {ts}\n{_counts_line(counts)}",
            )
        return {"ok": True, "counts": counts, "ts": ts}
    except Exception as e:
        logger.error(f"send_to_admin failed: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)}
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ── restore ──────────────────────────────────────────────────────────────────

async def _download_pinned_snapshot(bot: Bot, dest: str) -> bool:
    """Download the backup channel's pinned snapshot to dest. Returns True on success."""
    try:
        chat = await bot.get_chat(settings.BACKUP_CHANNEL_ID)
    except Exception as e:
        logger.warning(f"Could not read backup channel: {e}")
        return False
    pinned = getattr(chat, "pinned_message", None)
    doc = getattr(pinned, "document", None) if pinned else None
    if not doc:
        logger.info("No pinned snapshot found in backup channel.")
        return False
    try:
        f = await bot.get_file(doc.file_id)
        await f.download_to_drive(custom_path=dest)
        return True
    except Exception as e:
        logger.error(f"Failed to download pinned snapshot: {e}", exc_info=True)
        return False


def db_has_data() -> bool:
    """True if the local DB exists and already contains app data (don't clobber)."""
    path = db_path()
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            for t in ("plans", "users"):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    if (cur.fetchone()[0] or 0) > 0:
                        return True
                except Exception:
                    continue
        finally:
            conn.close()
    except Exception:
        return False
    return False


async def restore_on_startup(token: str) -> bool:
    """Called before the DB engine is first used. If AUTO_RESTORE is on and the local
    DB is missing/empty, download + validate the pinned snapshot and put it in place."""
    if not settings.AUTO_RESTORE or not is_backup_enabled():
        return False
    if db_has_data():
        logger.info("Local DB already has data — skipping restore.")
        return False

    path = db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".restore_tmp"

    try:
        async with Bot(token) as bot:
            got = await _download_pinned_snapshot(bot, tmp)
    except Exception as e:
        logger.error(f"Restore bot error: {e}", exc_info=True)
        return False

    if not got:
        return False
    if not _is_sqlite_file(tmp):
        logger.error("Downloaded snapshot is not a valid SQLite file — ignoring.")
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False

    os.replace(tmp, path)
    counts = _row_counts_sync(path)
    logger.info(f"✅ Restored DB from pinned snapshot — {_counts_line(counts)}")
    return True


async def restore_now(bot: Bot, file_id: str | None = None) -> dict:
    """Runtime restore (admin /restore or /restorefrom). Disposes the engine, swaps the
    DB file, then re-inits schema + config cache. file_id=None uses the pinned snapshot."""
    if not is_backup_enabled() and file_id is None:
        return {"ok": False, "reason": "Backups disabled."}
    path = db_path()
    if not path:
        return {"ok": False, "reason": "Not using SQLite."}
    tmp = path + ".restore_tmp"

    # Download the source snapshot.
    if file_id:
        try:
            f = await bot.get_file(file_id)
            await f.download_to_drive(custom_path=tmp)
        except Exception as e:
            return {"ok": False, "reason": f"download failed: {e}"}
    else:
        if not await _download_pinned_snapshot(bot, tmp):
            return {"ok": False, "reason": "No pinned snapshot to restore."}

    if not _is_sqlite_file(tmp):
        try:
            os.remove(tmp)
        except Exception:
            pass
        return {"ok": False, "reason": "File is not a valid SQLite database."}

    # Swap the live file: close pool, replace, re-init.
    try:
        from bot.models.user import engine
        from bot.models import init_db
        from bot.services.config import preload_config
        await engine.dispose()
        os.replace(tmp, path)
        await init_db()
        await preload_config()
        counts = _row_counts_sync(path)
        logger.info(f"✅ Runtime restore complete — {_counts_line(counts)}")
        return {"ok": True, "counts": counts}
    except Exception as e:
        logger.error(f"Runtime restore failed: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)}


# ── scheduling (periodic + debounced event-driven) ───────────────────────────

async def run_scheduled_backup(bot: Bot) -> None:
    if not is_backup_enabled():
        return
    logger.info("Running scheduled DB backup...")
    await upload_and_pin(bot)


def schedule_backup_soon(bot: Bot) -> None:
    """Debounced backup: coalesces a burst of changes into one snapshot. Safe to call
    from any handler after a meaningful mutation."""
    if not is_backup_enabled():
        return
    try:
        from bot.services.scheduler import scheduler
        from apscheduler.triggers.date import DateTrigger
        from datetime import timedelta
        run_at = datetime.now() + timedelta(seconds=settings.BACKUP_DEBOUNCE_SECONDS)
        scheduler.add_job(
            run_scheduled_backup,
            trigger=DateTrigger(run_date=run_at),
            args=[bot],
            id="backup_debounced",
            replace_existing=True,
        )
    except Exception as e:
        logger.error(f"Could not schedule debounced backup: {e}")


async def backup_status(bot: Bot) -> dict:
    """Info for /backupstatus."""
    info = {
        "enabled": is_backup_enabled(),
        "channel": settings.BACKUP_CHANNEL_ID,
        "interval_hours": settings.BACKUP_INTERVAL_HOURS,
        "last_at": get_config(_CFG_LAST_AT, "never"),
        "retention": settings.BACKUP_RETENTION,
        "has_pinned": False,
        "local_counts": {},
    }
    p = db_path()
    if p and os.path.exists(p):
        info["local_counts"] = _row_counts_sync(p)
    try:
        chat = await bot.get_chat(settings.BACKUP_CHANNEL_ID)
        pinned = getattr(chat, "pinned_message", None)
        info["has_pinned"] = bool(pinned and getattr(pinned, "document", None))
    except Exception as e:
        info["channel_error"] = str(e)
    return info
