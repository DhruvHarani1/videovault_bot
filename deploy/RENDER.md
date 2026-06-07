# Render.com Deployment Guide

Follow these steps to deploy **VideoVault Bot** on [Render.com](https://render.com).

## 1. Project Configuration
1. Log in to Render and click **New** -> **Web Service**.
2. Connect your Git repository.
3. Select the following settings:
   - **Runtime**: `Docker`
   - **Instance Type**: Select your plan (the free tier works, but standard plans support persistent disks).

## 2. Environment Variables
Add the following in the **Environment** section of your Web Service configuration:

| Key | Value / Description |
|---|---|
| `BOT_TOKEN` | Your Telegram Bot Token from `@BotFather` |
| `RAZORPAY_KEY_ID` | Your Razorpay API Key ID (test or live) |
| `RAZORPAY_KEY_SECRET` | Your Razorpay API Key Secret |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/videovault.db` |
| `ADMIN_USER_IDS` | Comma-separated list of Telegram admin user IDs |
| `VIDEO_PREVIEW_SECONDS` | `180` |
| `FULL_VIDEO_PRICE_INR` | `299` |
| `WEBHOOK_URL` | Your Render public web service URL (e.g. `https://videovault-bot.onrender.com`) |
| `WEBHOOK_SECRET_TOKEN` | A secure random string to prevent fake updates |
| `RAZORPAY_WEBHOOK_SECRET` | Your Razorpay Webhook Secret for signature verification |
| `ALERT_EMAIL` | Your Gmail address for monitoring alerts |
| `GMAIL_APP_PASSWORD` | Your Gmail App Password |
| `UPTIMEROBOT_ALERT_CONTACT_ID` | Your UptimeRobot Contact ID (optional) |
| `PORT` | `8443` |

## 3. Persistent Disk (SQLite)
Render's filesystem is ephemeral. To persist your database across deployments, you **must** add a persistent disk:

1. In your Web Service settings, navigate to **Disks**.
2. Click **Add Disk**.
3. Configure the disk details:
   - **Name**: `database-storage`
   - **Mount Path**: `/app/data`
   - **Size**: `1 GB` (or as preferred)
4. Save the changes.

The persistent disk will mount at `/app/data`. The database url `sqlite+aiosqlite:///./data/videovault.db` will resolve to `/app/data/videovault.db` inside the container and persist safely.
