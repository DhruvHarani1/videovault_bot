# VideoVault Bot — Testing Guide

Run these manual testing scenarios before every deploy to verify the integrity and security of the VideoVault bot.

## Scenario 1: Onboarding and Signup Flow
**Goal**: Verify new users are correctly registered and presented with onboarding menus.

1. Delete your user record from the SQLite database:
   ```sql
   DELETE FROM users WHERE telegram_id = YOUR_TELEGRAM_ID;
   ```
2. Open the bot and send `/start`.
3. **Expected Results**:
   - The bot replies with the onboarding welcome menu:
     🎬 *Welcome to VideoVault!*
     Get exclusive video content right here on Telegram.
     👇 Tap below to watch a FREE 3-minute preview.
   - The user record is inserted in the `users` table with `has_full_access` set to `False`.

---

## Scenario 2: Video Preview & Auto-Deletion
**Goal**: Verify that preview delivery, scheduling, and deletion work correctly.

1. Click `▶️ Watch Free Preview` on the onboarding message.
2. **Expected Results**:
   - The bot replies: "🎬 Your 3-minute preview is starting! It will disappear after 3 minutes."
   - The bot sends the preview video with `protect_content: True` and caption: "⏱ Preview ends in 3 minutes. Tap below to unlock full access!".
   - After 3 minutes (configure to `10` seconds in `.env` for fast testing), the preview video is deleted from the chat.
   - The bot sends a purchase follow-up: "⏰ Preview ended! Unlock the full video for just ₹299".

---

## Scenario 3: 24-Hour Preview Limit
**Goal**: Ensure users cannot watch multiple previews in a 24-hour window.

1. Attempt to watch the preview again by sending `/preview` or clicking the watch button.
2. **Expected Results**:
   - The bot replies: "You already watched your free preview. Upgrade to get full access!" and presents the `💳 Buy Full Access` button.
   - No video is delivered.

---

## Scenario 4: Razorpay Payment Flow (Test Mode)
**Goal**: Verify order creation, simulation checks, and access grants.

1. Click `💳 Buy Full Access` or send `/pay`.
2. **Expected Results**:
   - The bot replies with a payment details message and two buttons:
     - `💳 Pay ₹299 on Razorpay` (redirects to sandbox URL)
     - `✅ I've Paid — Verify`
   - A pending transaction is logged in the `payments` table with `status = 'pending'`.
3. Complete the mock payment on the sandbox page.
4. Click `✅ I've Paid — Verify`.
5. **Expected Results**:
   - The bot verifies the order, grants access, and displays: "🎉 Payment confirmed! Full video unlocked." with a `📺 Watch Full Video` button.
   - The payment record status changes to `paid`.
   - The user's `has_full_access` in the DB becomes `True`.

---

## Scenario 5: Full Video Access & Security
**Goal**: Verify that paid users can access the full video and forwarding is disabled.

1. Click `📺 Watch Full Video` or send `/watch`.
2. **Expected Results**:
   - The bot sends the full video with `protect_content: True` (forwarding/saving disabled).
   - An access entry is logged in `video_views`.

---

## Scenario 6: Contact Support inquiry
**Goal**: Verify that support questions are forwarded to admins.

1. Send `/contact`.
2. Send a text message: "Hello support, I have a question about the video."
3. **Expected Results**:
   - The bot forwards the inquiry to all `ADMIN_USER_IDS` with a user header:
     💬 *Support Inquiry*
     👤 User: John @johndoe (123456789)
     Message: Hello support...
   - The bot replies to the user: "✅ Message sent! We'll reply within 24 hours." and displays a back-to-menu button.
