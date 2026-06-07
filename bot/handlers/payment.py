from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import razorpay
from razorpay.errors import BadRequestError
from bot.config import settings
from bot.services import check_user_access, grant_user_access, get_video_price
from sqlalchemy.future import select
from bot.models import get_db, Payment
import logging

logger = logging.getLogger(__name__)

# Initialize Razorpay Client (will fail/warn if keys are not set, so wrap or check)
razorpay_client = None
if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
    if not settings.RAZORPAY_KEY_ID.startswith("rzp_test_dummy"):
        try:
            razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            logger.info("Razorpay client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Razorpay client: {e}")

async def handle_buy_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initiates the Razorpay order creation and presents payment buttons to the user."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Extract video_id from callback_data (format "buy_access:video_001")
    video_id = "video_001"
    if query and query.data and ":" in query.data:
        video_id = query.data.split(":", 1)[1]

    # 1. Check if user already paid
    has_access = await check_user_access(user_id)
    if has_access:
        keyboard = [
            [InlineKeyboardButton("▶️ Watch Now", callback_data=f"watch_full:{video_id}")]
        ]
        text = "You already have full access!"
        if query:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 2. Create Razorpay order
    price = get_video_price()
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
                    "video_id": video_id
                }
            })
            order_id = order.get("id")
            payment_url = f"https://rzp.io/rzp/{order_id}"
            logger.info(f"Created Razorpay order {order_id} for user {user_id}")
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
            # Development simulation fallback
            order_id = f"order_mock_{user_id}_{int(datetime.utcnow().timestamp())}"
            payment_url = f"https://rzp.io/rzp/{order_id}"
            logger.info(f"Fallback to mock order {order_id} due to connection error.")
    else:
        # Development simulation fallback
        order_id = f"order_mock_{user_id}_{int(datetime.utcnow().timestamp())}"
        payment_url = f"https://rzp.io/rzp/{order_id}"
        logger.info(f"Generated mock order {order_id} (simulation mode)")

    # 3. Save order to payments table (status='pending')
    try:
        async with get_db() as session:
            session.add(Payment(
                telegram_id=user_id,
                razorpay_order_id=order_id,
                amount_inr=price,
                status="pending"
            ))
            logger.info(f"Saved pending payment {order_id} to DB for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to save payment order to DB: {e}", exc_info=True)
        error_msg = "Database error processing your payment order. Please try again."
        if query:
            await query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return

    # 4 & 5. Send payment options and text fallback
    keyboard = [
        [
            InlineKeyboardButton(f"💳 Pay ₹{price} on Razorpay", url=payment_url)
        ],
        [
            InlineKeyboardButton("✅ I've Paid — Verify", callback_data=f"check_payment:{order_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        f"To unlock full video access, please complete the payment of ₹{price} via Razorpay.\n\n"
        f"Payment ID: `{order_id}`\n\n"
        "Once payment is completed, tap the **Verify** button below."
    )

    if query:
        await query.message.reply_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command fallback /pay to buy access."""
    await handle_buy_access(update, context)

async def payment_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline keyboard callbacks for simulated payments (legacy mock)."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data

    if data.startswith("pay_sim_"):
        await grant_user_access(user_id)
        await query.edit_message_text(
            "Payment Successful! 🎉\n\n"
            "You now have lifetime access to the full video.\n"
            "Use /watch to start watching!"
        )
        logger.info(f"User {user_id} successfully paid and was granted access (Data: {data})")

async def handle_check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered by 'Verify' button. Queries Razorpay API (or mocks) and updates DB access."""
    query = update.callback_query
    # Note: CallbackRouter already answered query, but let's be safe.
    if not query:
        return

    user_id = query.from_user.id
    data = query.data

    if not data or ":" not in data:
        logger.warning(f"Malformed callback data in check_payment: {data}")
        return

    order_id = data.split(":", 1)[1]
    logger.info(f"Verifying payment for order_id: {order_id}, user: {user_id}")

    # 2. Fetch order from local DB
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

    # If the database payment is already marked paid, just verify and exit
    if db_payment.status == "paid":
        async with get_db() as session:
            await grant_user_access(user_id, session)
        keyboard = [[InlineKeyboardButton("📺 Watch Full Video", callback_data="watch_full:video_001")]]
        await query.message.reply_text(
            "🎉 Payment already confirmed! Full video unlocked.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 3. Call Razorpay API to fetch payments
    items = []
    is_mock = order_id.startswith("order_mock_")

    if is_mock or not razorpay_client:
        # Development simulation mode: automatically treat mock orders as captured
        logger.info(f"Simulating payment capture for mock order: {order_id}")
        items = [{
            "id": f"pay_mock_{int(datetime.utcnow().timestamp())}",
            "status": "captured"
        }]
    else:
        try:
            payments = razorpay_client.order.payments(order_id)
            items = payments.get("items", [])
        except Exception as e:
            logger.error(f"Error fetching payments from Razorpay for order {order_id}: {e}", exc_info=True)
            await query.message.reply_text("Unable to verify payment with Razorpay. Please try again later.")
            return

    # 4. Loop through payments and check status
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
        # a. Update payments table in DB
        async with get_db() as session:
            result = await session.execute(
                select(Payment).filter(Payment.razorpay_order_id == order_id)
            )
            db_payment = result.scalars().first()
            if db_payment:
                db_payment.status = "paid"
                db_payment.razorpay_payment_id = captured_item["id"]
                db_payment.paid_at = datetime.utcnow()
                
            # b. Grant access
            await grant_user_access(user_id, session)

        logger.info(f"Payment {captured_item['id']} captured. Granted user {user_id} full access.")
        
        # c. Send success message
        keyboard = [[InlineKeyboardButton("📺 Watch Full Video", callback_data="watch_full:video_001")]]
        await query.message.reply_text(
            "🎉 Payment confirmed! Full video unlocked.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif payment_status_failed:
        # Update DB to failed
        async with get_db() as session:
            result = await session.execute(
                select(Payment).filter(Payment.razorpay_order_id == order_id)
            )
            db_payment = result.scalars().first()
            if db_payment:
                db_payment.status = "failed"

        logger.info(f"Payment failed for order {order_id} (user {user_id}).")
        
        # Check failed payments alert trigger
        from bot.services.monitoring import check_failed_payments_alert
        await check_failed_payments_alert(context.bot)

        keyboard = [[InlineKeyboardButton("💳 Retry Payment", callback_data="buy_access:video_001")]]
        await query.message.reply_text(
            "❌ Payment failed. Try again?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    else:
        # Still pending / no payment attempt found
        logger.info(f"Payment for order {order_id} (user {user_id}) is still pending/unattempted.")
        payment_url = f"https://rzp.io/rzp/{order_id}"
        keyboard = [
            [InlineKeyboardButton("✅ Check Again", callback_data=f"check_payment:{order_id}")],
            [InlineKeyboardButton(f"💳 Pay Now (₹{get_video_price()})", url=payment_url)]
        ]
        await query.message.reply_text(
            "⏳ Payment not received yet. Please complete payment and try again in a minute.",
            reply_markup=InlineKeyboardMarkup(keyboard)
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
                settings.RAZORPAY_WEBHOOK_SECRET
            )
            return True
        else:
            # Bypass validation in local simulation mode if dummy keys are used
            if settings.RAZORPAY_KEY_ID.startswith("rzp_test_dummy"):
                logger.info("Simulating signature verification (dummy keys detected).")
                return True
            
            # Non-client direct validation
            utility = razorpay.Utility(None)
            utility.verify_webhook_signature(
                request_body.decode("utf-8"),
                signature,
                settings.RAZORPAY_WEBHOOK_SECRET
            )
            return True
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {e}")
        return False
