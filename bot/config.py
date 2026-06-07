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

        # ALERT_EMAIL
        self.ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")

        # GMAIL_APP_PASSWORD
        self.GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

        # UPTIMEROBOT_ALERT_CONTACT_ID
        self.UPTIMEROBOT_ALERT_CONTACT_ID = os.getenv("UPTIMEROBOT_ALERT_CONTACT_ID", "")

# Export settings singleton
settings = Settings()


