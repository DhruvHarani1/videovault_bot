# Railway.app Deployment Guide

Follow these steps to deploy **VideoVault Bot** on [Railway.app](https://railway.app).

## 1. Project Configuration
Railway will automatically build your project using the `Dockerfile` at the root of the repository.

- **Start Command**: `python main.py` (automatically configured by the `CMD` in the `Dockerfile`, but can be explicitly set in Railway as `python main.py` if needed).
- **Port**: Set `PORT` variable to `8443` (Railway will route incoming traffic to this port).

## 2. Environment Variables
Configure the following variables in the **Variables** tab of your Railway service:

| Variable | Value / Description |
|---|---|
| `BOT_TOKEN` | Your Telegram Bot Token from `@BotFather` |
| `RAZORPAY_KEY_ID` | Your Razorpay API Key ID (test or live) |
| `RAZORPAY_KEY_SECRET` | Your Razorpay API Key Secret |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/videovault.db` |
| `ADMIN_USER_IDS` | Comma-separated list of Telegram admin user IDs |
| `VIDEO_PREVIEW_SECONDS` | `180` |
| `FULL_VIDEO_PRICE_INR` | `299` |
| `WEBHOOK_URL` | Your Railway public domain URL (e.g. `https://your-bot.up.railway.app`) |
| `WEBHOOK_SECRET_TOKEN` | A secure random string to prevent fake updates |
| `RAZORPAY_WEBHOOK_SECRET` | Your Razorpay Webhook Secret for signature verification |
| `ALERT_EMAIL` | Your Gmail address for monitoring alerts |
| `GMAIL_APP_PASSWORD` | Your Gmail App Password |
| `UPTIMEROBOT_ALERT_CONTACT_ID` | Your UptimeRobot Contact ID (optional) |

## 3. Persistent Volume (SQLite)
Since SQLite stores data in a local file, you **must** attach a persistent disk to prevent data loss when Railway redeploys or restarts the container.

1. Go to your project canvas.
2. Click **New** -> **Volume**.
3. Set the **Mount Path** of the volume to `/app/data`.
4. Connect this volume to your `bot` service.
5. In the service settings, ensure the database URL points to `sqlite+aiosqlite:///./data/videovault.db`.

This mounts your database under the persistent `/app/data` volume, preventing it from being deleted when the container is replaced.
