import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings:
    def __init__(self):
        # BOT_TOKEN
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        if not self.BOT_TOKEN:
            raise ValueError("Missing required environment variable: BOT_TOKEN")
        
        # RAZORPAY_KEY_ID
        self.RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
        if not self.RAZORPAY_KEY_ID:
            raise ValueError("Missing required environment variable: RAZORPAY_KEY_ID")
            
        # RAZORPAY_KEY_SECRET
        self.RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
        if not self.RAZORPAY_KEY_SECRET:
            raise ValueError("Missing required environment variable: RAZORPAY_KEY_SECRET")

        # DATABASE_URL
        self.DATABASE_URL = os.getenv("DATABASE_URL")
        if not self.DATABASE_URL:
            raise ValueError("Missing required environment variable: DATABASE_URL")
            
        # Automatically create parent directory if it's a SQLite database
        if "sqlite" in self.DATABASE_URL:
            parts = self.DATABASE_URL.split("///")
            if len(parts) > 1:
                db_path = parts[-1]
                db_dir = os.path.dirname(db_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)


        # ADMIN_USER_IDS
        admin_ids_raw = os.getenv("ADMIN_USER_IDS")
        if not admin_ids_raw:
            raise ValueError("Missing required environment variable: ADMIN_USER_IDS")
        
        try:
            self.ADMIN_USER_IDS = [
                int(uid.strip())
                for uid in admin_ids_raw.split(",")
                if uid.strip()
            ]
        except ValueError:
            raise ValueError("ADMIN_USER_IDS must be a comma-separated list of integers")

        # VIDEO_PREVIEW_SECONDS
        preview_seconds_raw = os.getenv("VIDEO_PREVIEW_SECONDS", "180")
        try:
            self.VIDEO_PREVIEW_SECONDS = int(preview_seconds_raw)
        except ValueError:
            raise ValueError("VIDEO_PREVIEW_SECONDS must be an integer")

        # FULL_VIDEO_PRICE_INR
        price_raw = os.getenv("FULL_VIDEO_PRICE_INR", "299")
        try:
            self.FULL_VIDEO_PRICE_INR = int(price_raw)
        except ValueError:
            raise ValueError("FULL_VIDEO_PRICE_INR must be an integer")

        # WEBHOOK_URL
        self.WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

        # WEBHOOK_SECRET_TOKEN
        self.WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "default_secret_token_123")

        # FULL_VIDEO_FILE_ID
        self.FULL_VIDEO_FILE_ID = os.getenv("FULL_VIDEO_FILE_ID")
        if not self.FULL_VIDEO_FILE_ID:
            raise ValueError("Missing required environment variable: FULL_VIDEO_FILE_ID")

        # RAZORPAY_WEBHOOK_SECRET
        self.RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

        # ALERT_EMAIL (legacy single recipient — kept for backward compatibility)
        self.ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")

        # GMAIL_APP_PASSWORD
        self.GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

        # UPTIMEROBOT_ALERT_CONTACT_ID
        self.UPTIMEROBOT_ALERT_CONTACT_ID = os.getenv("UPTIMEROBOT_ALERT_CONTACT_ID", "")

        # ──────────────────────────────────────────────────────────────────────
        # Phase 0 — Multi-bot ecosystem config.
        # All optional: the existing single bot keeps booting if these are unset.
        # Role → handle mapping (after the name swap):
        #   Sales   → @muthalsamajhbot
        #   Demo    → @lookatthedemobot
        #   Payment → @videovault693bot   (this is the CURRENT live BOT_TOKEN)
        #   File    → (new bot, to be created)
        # ──────────────────────────────────────────────────────────────────────

        # Bot tokens. PAYMENT_BOT_TOKEN falls back to the existing BOT_TOKEN since
        # @videovault693bot (the current token) is now the Payment Bot.
        self.SALES_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
        self.DEMO_BOT_TOKEN = os.getenv("DEMO_BOT_TOKEN", "")
        self.PAYMENT_BOT_TOKEN = os.getenv("PAYMENT_BOT_TOKEN", "") or self.BOT_TOKEN
        self.FILE_BOT_TOKEN = os.getenv("FILE_BOT_TOKEN", "")

        # Bot usernames (without @) — needed to build t.me deep links between bots.
        self.SALES_BOT_USERNAME = os.getenv("SALES_BOT_USERNAME", "muthalsamajhbot").lstrip("@")
        self.DEMO_BOT_USERNAME = os.getenv("DEMO_BOT_USERNAME", "lookatthedemobot").lstrip("@")
        self.PAYMENT_BOT_USERNAME = os.getenv("PAYMENT_BOT_USERNAME", "videovault693bot").lstrip("@")
        self.FILE_BOT_USERNAME = os.getenv("FILE_BOT_USERNAME", "").lstrip("@")

        # Per-bot webhook secret tokens (fall back to the shared WEBHOOK_SECRET_TOKEN).
        self.SALES_WEBHOOK_SECRET = os.getenv("SALES_WEBHOOK_SECRET", self.WEBHOOK_SECRET_TOKEN)
        self.DEMO_WEBHOOK_SECRET = os.getenv("DEMO_WEBHOOK_SECRET", self.WEBHOOK_SECRET_TOKEN)
        self.PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", self.WEBHOOK_SECRET_TOKEN)
        self.FILE_WEBHOOK_SECRET = os.getenv("FILE_WEBHOOK_SECRET", self.WEBHOOK_SECRET_TOKEN)

        # Payment QR / UPI image served by the Payment Bot (Telegram file_id or URL).
        self.PAYMENT_QR_FILE_ID = os.getenv("PAYMENT_QR_FILE_ID", "")

        # Shared storage/library channel id (e.g. -1004297523216). All 4 bots must be
        # admins of it. Content is copied into this channel once; any bot then delivers
        # via copy_message — the portable way to share media across bots.
        try:
            self.STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0"))
        except ValueError:
            self.STORAGE_CHANNEL_ID = 0

        # ── Database backup/restore (free persistence across Render redeploys) ──
        # Private channel where DB snapshots are uploaded + pinned. The Payment bot
        # must be an admin with Post + Pin + Delete rights. Falls back to the
        # storage channel if unset.
        try:
            self.BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
        except ValueError:
            self.BACKUP_CHANNEL_ID = 0
        if not self.BACKUP_CHANNEL_ID:
            self.BACKUP_CHANNEL_ID = self.STORAGE_CHANNEL_ID

        # Periodic backup cadence (hours) — safety net on top of event-driven backups.
        try:
            self.BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))
        except ValueError:
            self.BACKUP_INTERVAL_HOURS = 6

        # Auto-restore from the pinned snapshot on startup when the local DB is empty.
        self.AUTO_RESTORE = os.getenv("AUTO_RESTORE", "true").lower() in ("1", "true", "yes", "on")

        # How many recent snapshots to keep in the backup channel.
        try:
            self.BACKUP_RETENTION = int(os.getenv("BACKUP_RETENTION", "5"))
        except ValueError:
            self.BACKUP_RETENTION = 5

        # Debounce window (seconds) for event-driven backups — coalesces a burst of
        # changes into a single snapshot.
        try:
            self.BACKUP_DEBOUNCE_SECONDS = int(os.getenv("BACKUP_DEBOUNCE_SECONDS", "30"))
        except ValueError:
            self.BACKUP_DEBOUNCE_SECONDS = 30

        # Demo content expiry (seconds) for the Demo Bot.
        try:
            self.DEMO_EXPIRY_SECONDS = int(os.getenv("DEMO_EXPIRY_SECONDS", "200"))
        except ValueError:
            self.DEMO_EXPIRY_SECONDS = 200

        # Multi-recipient alert emails. Comma-separated; falls back to ALERT_EMAIL.
        emails_raw = os.getenv("ALERT_EMAILS", "")
        recipients = [e.strip() for e in emails_raw.split(",") if e.strip()]
        if not recipients and self.ALERT_EMAIL:
            recipients = [self.ALERT_EMAIL]
        self.ALERT_EMAILS = recipients

    # Convenience: map a bot role key → its token (used by the Phase 2 bootstrap).
    def bot_token_for(self, bot_key: str) -> str:
        return {
            "sales": self.SALES_BOT_TOKEN,
            "demo": self.DEMO_BOT_TOKEN,
            "payment": self.PAYMENT_BOT_TOKEN,
            "file": self.FILE_BOT_TOKEN,
        }.get(bot_key, "")

    def bot_username_for(self, bot_key: str) -> str:
        return {
            "sales": self.SALES_BOT_USERNAME,
            "demo": self.DEMO_BOT_USERNAME,
            "payment": self.PAYMENT_BOT_USERNAME,
            "file": self.FILE_BOT_USERNAME,
        }.get(bot_key, "")

    def webhook_secret_for(self, bot_key: str) -> str:
        return {
            "sales": self.SALES_WEBHOOK_SECRET,
            "demo": self.DEMO_WEBHOOK_SECRET,
            "payment": self.PAYMENT_WEBHOOK_SECRET,
            "file": self.FILE_WEBHOOK_SECRET,
        }.get(bot_key, self.WEBHOOK_SECRET_TOKEN)

# Export settings singleton
settings = Settings()


