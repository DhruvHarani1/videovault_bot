from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, ConversationHandler, filters, Application
from telegram.constants import ParseMode
from sqlalchemy.future import select
from bot.config import settings
from bot.models import get_db, User
from bot.services import get_video_price
from bot.services.videos import list_active_videos
from bot.handlers.library import show_library
import logging
import re
from bot.services.monitoring import update_last_message_time

logger = logging.getLogger(__name__)

# State for the contact ConversationHandler
WAITING_FOR_CONTACT_MSG = 1

def sanitize_input(text: str) -> str:
    """Strips HTML tags and MarkdownV2 special characters from user input."""
    if not text:
        return text
    # Strip HTML tags
    text = re.sub(r"<[^>]*>", "", text)
    # Strip MarkdownV2 special characters
    text = re.sub(r"[_*\[\]()~`#+\-=|{}.!]", "", text)
    return text.strip()

async def show_onboarding_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Onboarding entry: show a welcome message then open the catalog.

    With multiple videos there's no single 'watch full' button to offer; users always
    flow through the catalog → per-video detail → preview/buy/watch.
    """
    query = update.callback_query
    first_name = update.effective_user.first_name or "there"

    videos = await list_active_videos()
    safe_name = re.sub(r"[_*\[\]()~`#+\-=|{}.!\\]", "", first_name)

    if not videos:
        welcome_text = (
            f"👋 Hi {safe_name}\\! Welcome to *VideoVault*\\.\n\n"
            "No videos are available right now\\. Please check back soon\\!"
        )
        if query:
            await query.message.edit_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    welcome_text = (
        f"👋 Hi {safe_name}\\! Welcome to *VideoVault*\\.\n\n"
        "Browse the catalog below to watch a free preview or unlock the full video\\."
    )
    keyboard = [[InlineKeyboardButton("📚 Browse Library", callback_data="library")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command. Registers/updates the user and displays onboarding options."""
    update_last_message_time()
    tg_user = update.effective_user
    if not tg_user:
        logger.warning("Received start command with no effective user.")
        return

    logger.info(f"User {tg_user.id} (@{tg_user.username}) triggered /start")
    
    clean_username = sanitize_input(tg_user.username)
    clean_first_name = sanitize_input(tg_user.first_name)

    # Upsert user by telegram_id
    async with get_db() as session:
        result = await session.execute(select(User).filter(User.telegram_id == tg_user.id))
        user = result.scalars().first()
        
        if user:
            logger.info(f"Existing user returned: {user}")
            user.username = clean_username
            user.first_name = clean_first_name
            # Ensure user is marked active when restarting the bot
            user.is_active = True
        else:
            logger.info(f"Creating new user for telegram_id: {tg_user.id}")
            user = User(
                telegram_id=tg_user.id,
                username=clean_username,
                first_name=clean_first_name,
                has_full_access=False,
                is_active=True
            )
            session.add(user)


    await show_onboarding_menu(update, context)

# Prompt 15: /help command handler
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays instructions on how the bot works."""
    update_last_message_time()
    help_text = (
        "🆘 *How VideoVault works*\n\n"
        "1️⃣ Use /library or tap \"Browse Library\" to see all videos\n"
        "2️⃣ Pick a video and tap \"Watch Free Preview\" for a 3\\-min clip\n"
        "3️⃣ The preview auto\\-deletes after 3 minutes\n"
        "4️⃣ Tap \"Buy Full Access\" to pay via Razorpay and unlock the full video\n\n"
        "Questions? Use /contact to message support\\."
    )

    keyboard = [
        [InlineKeyboardButton("📚 Browse Library", callback_data="library")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=reply_markup
    )

# Prompt 15: /contact flow (ConversationHandler)
async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiates contact support inquiry flow."""
    await update.message.reply_text(
        "✉️ *Contact Support*\n\n"
        "What is your question? Send your message below, or type `/cancel` to cancel.",
        parse_mode=ParseMode.MARKDOWN
    )
    return WAITING_FOR_CONTACT_MSG

async def contact_message_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Forwards support inquiries directly to administrators."""
    update_last_message_time()
    msg = update.message
    user = update.effective_user
    
    admin_header = (
        f"💬 *Support Inquiry*\n"
        f"👤 User: {user.first_name} @{user.username or 'N/A'} (`{user.id}`)\n\n"
        f"📝 *Message:*\n{msg.text}"
    )
    
    # Forward message to all administrators
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_header,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to forward support query to admin {admin_id}: {e}")

    keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "✅ Message sent! We'll reply within 24 hours.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def contact_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the support inquiry flow."""
    keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Contact request cancelled.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

# Prompt 15: Fallback unknown commands
async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Replies to unknown command keywords."""
    await update.message.reply_text("Unknown command. Use /help to see what I can do.")

# Prompt 15: Fallback plain text menu rendering
async def plain_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays main menu when receiving non-command messages outside conversation."""
    update_last_message_time()
    await show_onboarding_menu(update, context)

# ConversationHandler for /contact support inquiries
contact_conv = ConversationHandler(
    entry_points=[CommandHandler("contact", contact_command)],
    states={
        WAITING_FOR_CONTACT_MSG: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, contact_message_receiver)
        ]
    },
    fallbacks=[CommandHandler("cancel", contact_cancel)]
)

