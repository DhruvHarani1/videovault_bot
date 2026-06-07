# VideoVault Bot — Go-Live Launch Checklist

Follow this checklist to verify and harden your deployment before exposing the bot to production users.

## Security Hardening
- [ ] **Environment Isolation**: Double check that `.env` is listed in your `.dockerignore` and `.gitignore` and has NOT been committed to Git.
- [ ] **Credentials Review**: Ensure that you are not using default or test values for `BOT_TOKEN`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET`.
- [ ] **Admin Authorization**: Set `ADMIN_USER_IDS` to your real personal Telegram User ID (and other authorized team members).
- [ ] **Paid Content Security**: Verify that `protect_content=True` is enabled on all calls to `send_video()` that deliver the preview and the full premium video.
- [ ] **Webhook Protection**: Ensure `WEBHOOK_SECRET_TOKEN` is configured in `.env` and matches the secret token used by Telegram.
- [ ] **Email Alerts**: Configure `ALERT_EMAIL` and `GMAIL_APP_PASSWORD` for monitoring downtime/crash notifications.

## Functional Checks
- [ ] **User Signup Flow**: Send `/start` from a new Telegram account and verify it registers the user in the database.
- [ ] **Free Preview Flow**: Verify that tapping "Watch Free Preview" delivers the preview clip and that the video message is deleted exactly after 3 minutes.
- [ ] **Razorpay Sandbox Integration**: Run an order creation and complete payment verification in Razorpay TEST mode.
- [ ] **Webhook confirmations**: Confirm that the FastAPI `/razorpay-webhook` receives payment capture events and successfully updates users' access in the DB.
- [ ] **Content Delivery**: Confirm that the full video is sent immediately after successful payment verification.
- [ ] **Admin Stats**: Execute `/stats` as an admin and ensure total users, paid count, and revenue metrics are correct.
- [ ] **Set Commands Menu**: Call the `/setcommands` admin command to register command shortcuts in users' clients.
- [ ] **Admin Broadcasts**: Send a test broadcast and ensure it reaches user devices with appropriate delays (`0.05s` sleep).
- [ ] **Daily report**: Confirm that the daily report task runs at 9:00 AM IST.
- [ ] **Backups**: Confirm the cron schedule `0 2 * * * /app/backup/backup.sh` is active and database files are copied and cleaned up.
- [ ] **Uptime Monitoring**: Add a HTTPS monitor in UptimeRobot pointing to `GET https://{your-domain.com}/health` scheduled every 5 minutes.

## Switch to Production Keys
- [ ] Replace Razorpay TEST keys with **LIVE keys** (`rzp_live_*`) in `.env`.
- [ ] Test the payment flow with a **real ₹1 payment** to verify end-to-end routing.
- [ ] Set up the official bot name, description, and profile pictures using `@BotFather`.
