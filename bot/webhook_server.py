from fastapi import FastAPI, Request, HTTPException, Header, Response
from bot.config import settings
from bot.handlers.payment import verify_webhook_signature
from bot.services.access import grant_user_access
from bot.models import get_db, Payment
from sqlalchemy.future import select
from telegram import Bot
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="VideoVault Webhook Server")

# Initialize Bot instance for sending direct messages
bot = Bot(token=settings.BOT_TOKEN)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}

@app.post("")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(None)):
    """Receives, verifies, and processes incoming Razorpay Webhooks."""
    if not x_razorpay_signature:
        logger.warning("Missing x-razorpay-signature header.")
        raise HTTPException(status_code=400, detail="Missing signature header")

    # 1. Read raw request body
    body_bytes = await request.body()

    # 2. Verify signature
    is_valid = verify_webhook_signature(body_bytes, x_razorpay_signature)
    if not is_valid:
        logger.warning("Invalid webhook signature.")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 3. Parse JSON body
    try:
        event_data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = event_data.get("event")
    logger.info(f"Received Razorpay Webhook Event: {event}")

    # 4. Handle event
    if event == "payment.captured":
        try:
            payload = event_data.get("payload", {})
            payment_entity = payload.get("payment", {}).get("entity", {})
            
            order_id = payment_entity.get("order_id")
            payment_id = payment_entity.get("id")
            
            if not order_id:
                logger.warning("No order_id found in payment.captured payload.")
                return Response(status_code=200)

            # Query database for the payment record to get the telegram_id
            async with get_db() as session:
                result = await session.execute(
                    select(Payment).filter(Payment.razorpay_order_id == order_id)
                )
                db_payment = result.scalars().first()

                if not db_payment:
                    logger.warning(f"Payment for order {order_id} captured but not found in local DB.")
                    return Response(status_code=200)

                telegram_id = db_payment.telegram_id

                if db_payment.status != "paid":
                    # Update local database
                    db_payment.status = "paid"
                    db_payment.razorpay_payment_id = payment_id
                    db_payment.paid_at = datetime.utcnow()
                    
                    # Grant access in database
                    await grant_user_access(telegram_id, session)
                    logger.info(f"Payment successful via Webhook for order {order_id}. User {telegram_id} access granted.")

            # Send confirmation message to the Telegram user
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text="✅ Payment received! Your full access is now active."
                )
                logger.info(f"Sent payment confirmation message to telegram user {telegram_id}.")
            except Exception as tg_err:
                logger.error(f"Failed to send Telegram confirmation message to {telegram_id}: {tg_err}")

        except Exception as err:
            logger.error(f"Error processing webhook payment.captured: {err}", exc_info=True)

    return Response(status_code=200)
