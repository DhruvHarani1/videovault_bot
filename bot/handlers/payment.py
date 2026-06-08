from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import razorpay
from razorpay.errors import BadRequestError
from bot.config import settings
from bot.services import check_user_access, grant_user_access
from bot.services.videos import get_video
from sqlalchemy.future import select
from bot.models import get_db, Payment
import logging

logger = logging.getLogger(__name__)

# Initialize Razorpay Client. We tolerate placeholder/dummy keys so the bot can
# run end-to-end in dev mode and simulate captures.
razorpay_client = None
if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
    if not settings.RAZORPAY_KEY_ID.startswith("rzp_test_dummy"):
        try:
            razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            logger.info("Razorpay client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Razorpay client: {e}")


def _video_id_from_callback(query, default: str = "video_001") -> str:
    if query and query.data and ":" in query.data:
        return query.data.split(":", 1)[1]
    return default


async def handle_buy_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initiates the Razorpay order creation for a specific video."""
    query = update.callback_query
    user_id = update.effective_user.id
    video_id = _video_id_from_callback(query)

    video = await get_video(video_id)
    if not video or not video.is_active:
        text = "❌ That video isn't available for purchase right now."
        if query:
            await query.message.reply_text(text)
        else:
            await update.message.reply_text(text)
        return

    if await check_user_access(user_id, video_id=video_id):
        keyboard = [[InlineKeyboardButton("▶️ Watch Now", callback_data=f"watch_full:{video_id}")]]
        text = f"You already have full access to '{video.title}'!"
        if query:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    price = video.price_inr
    amount_in_paise = price * 100
    order_id = None
    payment_url = None

    if razorpay_client:
        try:
            order = razorpay_client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "telegram_id": str(user_id),
                    "video_id": video_id,
                },
            })
            order_id = order.get("id")
            payment_url = f"https://rzp.io/rzp/{order_id}"
            logger.info(f"Created Razorpay order {order_id} for user {user_id} (video {video_id})")
        except BadRequestError as e:
            logger.error(f"Razorpay BadRequestError: {e}", exc_info=True)
            error_text = "There was an issue creating your payment order. Please contact support."
            if query:
                await query.message.reply_text(error_text)
            else:
                await update.message.reply_text(error_text)
            return
        except Exception as e:
            logger.error(f"Unexpected Razorpay error: {e}", exc_info=True)
            order_id = f"order_mock_{user_id}_{int(datetime.utcnow().timestamp())}"
            payment_url = f"https://rzp.io/rzp/{order_id}"
            logger.info(f"Fallback to mock order {order_id} due to connection error.")
    else:
        order_id = f"order_mock_{user_id}_{int(datetime.utcnow().timestamp())}"
        payment_url = f"https://rzp.io/rzp/{order_id}"
        logger.info(f"Generated mock order {order_id} (simulation mode)")

    # Save pending payment tagged with video_id
    try:
        async with get_db() as session:
            session.add(Payment(
                telegram_id=user_id,
                razorpay_order_id=order_id,
                amount_inr=price,
                video_id=video_id,
                status="pending",
            ))
            logger.info(f"Saved pending payment {order_id} for user {user_id} (video {video_id})")
    except Exception as e:
        logger.error(f"Failed to save payment order to DB: {e}", exc_info=True)
        error_msg = "Database error processing your payment order. Please try again."
        if query:
            await query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return

    keyboard = [
        [InlineKeyboardButton(f"💳 Pay ₹{price} on Razorpay", url=payment_url)],
        [InlineKeyboardButton("✅ I've Paid — Verify", callback_data=f"check_payment:{order_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        f"To unlock '{video.title}', please complete the payment of ₹{price} via Razorpay.\n\n"
        f"Order ID: `{order_id}`\n\n"
        "Once payment is completed, tap the **Verify** button below."
    )

    if query:
        await query.message.reply_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")


async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pay — without arg, opens the library."""
    args = context.args if hasattr(context, "args") else []
    if not args:
        from bot.handlers.library import show_library
        await show_library(update, context)
        return
    # Synthesize a callback-like data so handle_buy_access can pick up the video_id
    class _FakeQuery:
        def __init__(self, data, message):
            self.data = data
            self.message = message
    fake = _FakeQuery(f"buy_access:{args[0]}", update.message)
    update.callback_query = fake  # type: ignore[assignment]
    await handle_buy_access(update, context)


async def payment_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy simulated payment callback (kept so the existing handler registration still works)."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    if data.startswith("pay_sim_"):
        # Legacy: grant access to the default video
        await grant_user_access(user_id, video_id="video_001")
        await query.edit_message_text(
            "Payment Successful! 🎉\n\n"
            "You now have lifetime access. Use /library to start watching!"
        )
        logger.info(f"User {user_id} successfully paid (sim) and was granted access (Data: {data})")


async def handle_check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered by 'Verify' button. Queries Razorpay (or mocks) and grants per-video access."""
    query = update.callback_query
    if not query:
        return

    user_id = query.from_user.id
    data = query.data
    if not data or ":" not in data:
        logger.warning(f"Malformed callback data in check_payment: {data}")
        return

    order_id = data.split(":", 1)[1]
    logger.info(f"Verifying payment for order_id: {order_id}, user: {user_id}")

    async with get_db() as session:
        result = await session.execute(
            select(Payment).filter(Payment.razorpay_order_id == order_id)
        )
        db_payment = result.scalars().first()

        if not db_payment:
            logger.warning(f"Payment order {order_id} not found in DB.")
            await query.message.reply_text("Payment session not found. Please initiate payment again.")
            return

        if db_payment.telegram_id != user_id:
            logger.warning(f"Order {order_id} belongs to user {db_payment.telegram_id}, not requesting user {user_id}.")
            await query.message.reply_text("Access denied: Order verification mismatch.")
            return

        video_id = db_payment.video_id or "video_001"

    # If already marked paid, idempotently grant access and exit
    if db_payment.status == "paid":
        await grant_user_access(user_id, video_id=video_id)
        keyboard = [[InlineKeyboardButton("📺 Watch Full Video", callback_data=f"watch_full:{video_id}")]]
        await query.message.reply_text(
            "🎉 Payment already confirmed! Full video unlocked.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    items = []
    is_mock = order_id.startswith("order_mock_")
    if is_mock or not razorpay_client:
        logger.info(f"Simulating payment capture for mock order: {order_id}")
        items = [{"id": f"pay_mock_{int(datetime.utcnow().timestamp())}", "status": "captured"}]
    else:
        try:
            payments = razorpay_client.order.payments(order_id)
            items = payments.get("items", [])
        except Exception as e:
            logger.error(f"Error fetching payments from Razorpay for order {order_id}: {e}", exc_info=True)
            await query.message.reply_text("Unable to verify payment with Razorpay. Please try again later.")
            return

    payment_status_captured = False
    payment_status_failed = False
    captured_item = None

    for item in items:
        status = item.get("status")
        if status == "captured":
            payment_status_captured = True
            captured_item = item
            break
        elif status == "failed":
            payment_status_failed = True

    if payment_status_captured and captured_item:
        async with get_db() as session:
            result = await session.execute(
                select(Payment).filter(Payment.razorpay_order_id == order_id)
            )
            db_payment = result.scalars().first()
            if db_payment:
                db_payment.status = "paid"
                db_payment.razorpay_payment_id = captured_item["id"]
                db_payment.paid_at = datetime.utcnow()
            await grant_user_access(user_id, video_id=video_id, db=session)

        logger.info(f"Payment {captured_item['id']} captured. Granted user {user_id} access to {video_id}.")

        keyboard = [[InlineKeyboardButton("📺 Watch Full Video", callback_data=f"watch_full:{video_id}")]]
        await query.message.reply_text(
            "🎉 Payment confirmed! Full video unlocked.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif payment_status_failed:
        async with get_db() as session:
            result = await session.execute(
                select(Payment).filter(Payment.razorpay_order_id == order_id)
            )
            db_payment = result.scalars().first()
            if db_payment:
                db_payment.status = "failed"

        logger.info(f"Payment failed for order {order_id} (user {user_id}).")

        from bot.services.monitoring import check_failed_payments_alert
        await check_failed_payments_alert(context.bot)

        keyboard = [[InlineKeyboardButton("💳 Retry Payment", callback_data=f"buy_access:{video_id}")]]
        await query.message.reply_text(
            "❌ Payment failed. Try again?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    else:
        logger.info(f"Payment for order {order_id} (user {user_id}) is still pending/unattempted.")
        payment_url = f"https://rzp.io/rzp/{order_id}"
        video = await get_video(video_id)
        price = video.price_inr if video else 0
        keyboard = [
            [InlineKeyboardButton("✅ Check Again", callback_data=f"check_payment:{order_id}")],
            [InlineKeyboardButton(f"💳 Pay Now (₹{price})", url=payment_url)],
        ]
        await query.message.reply_text(
            "⏳ Payment not received yet. Please complete payment and try again in a minute.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


def verify_webhook_signature(request_body: bytes, signature: str) -> bool:
    """Verifies the signature of a Razorpay webhook payload."""
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        logger.warning("RAZORPAY_WEBHOOK_SECRET is not set. Cannot verify webhook signature.")
        return False

    try:
        if razorpay_client:
            razorpay_client.utility.verify_webhook_signature(
                request_body.decode("utf-8"),
                signature,
                settings.RAZORPAY_WEBHOOK_SECRET,
            )
            return True
        else:
            if settings.RAZORPAY_KEY_ID.startswith("rzp_test_dummy"):
                logger.info("Simulating signature verification (dummy keys detected).")
                return True
            utility = razorpay.Utility(None)
            utility.verify_webhook_signature(
                request_body.decode("utf-8"),
                signature,
                settings.RAZORPAY_WEBHOOK_SECRET,
            )
            return True
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {e}")
        return False
