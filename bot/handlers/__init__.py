from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from bot.handlers.start import start_handler, help_handler, contact_conv
from bot.handlers.video import preview_handler, watch_handler, CallbackRouter
from bot.handlers.payment import pay_handler, payment_callback_handler
from admin.panel import setup_admin_handlers

def register_handlers(application: Application) -> None:
    """Registers all command and callback handlers for the bot."""
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("preview", preview_handler))
    application.add_handler(CommandHandler("watch", watch_handler))
    application.add_handler(CommandHandler("pay", pay_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(contact_conv)
    
    # Callback query router for core video bot actions (including menu and opt-out buttons)
    application.add_handler(CallbackQueryHandler(
        CallbackRouter(),
        pattern="^(start_preview|buy_access|check_payment|watch_full|main_menu|reminders_opt_out)"
    ))
    
    # Callback query handler for payments simulation
    application.add_handler(CallbackQueryHandler(payment_callback_handler, pattern="^pay_sim_"))

def setup_handlers(application: Application) -> Application:
    """Sets up all handlers on the application and returns the configured Application."""
    register_handlers(application)
    setup_admin_handlers(application)
    
    # Register fallback handlers at the very end
    # to avoid intercepting valid user or admin commands/conversations
    from bot.handlers.start import unknown_command_handler, plain_text_handler
    from telegram.ext import MessageHandler, filters
    
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text_handler))
    
    return application

