import os
import json
import logging
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import Update, Message
from telegram.constants import ParseMode

# Import handlers from other modules
from handlers.general import (
    start, help_command, about_command, website_command,
    contact_command, events_command
)
from handlers.member import (
    request_member, approve_member, reject_member,
    list_requests, list_members, resources_command,
    list_groups, add_group, remove_group
)
from handlers.knowledge import (
    learn_command, bulk_learn_command, learn_from_url
)
from handlers.solana import (
    check_balance, process_signature, sqr_info
)
from handlers.spaces import (
    summarize_space, handle_successful_transaction,
    handle_failed_transaction
)
from handlers.mass_message import mass_message

# Import database and utils
from db import Database
from utils.utils import (
    format_response_for_telegram, extract_urls,
    get_webpage_content, escape_markdown_v2,
    get_announcement_prefix, parse_mass_message_input,
    get_error_message, get_success_message,
    load_authorized_members
)

# Import config
from config import (
    TELEGRAM_BOT_TOKEN, ERROR_MESSAGES, SUCCESS_MESSAGES
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize database
db = Database()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    try:
        message = update.message
        if not message:
            return

        # Check if we're waiting for a transaction signature
        if context.user_data.get('awaiting_signature'):
            await process_signature(message.text, context, message)
            return

        # Process the message based on chat type
        if message.chat.type == 'private':
            await handle_private_message(message, context)
        else:
            await handle_group_message(message, context)

    except Exception as e:
        logger.error(f"Error handling message: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )

async def handle_private_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Handle private messages."""
    try:
        # Process message with context
        response = await process_message_with_context_and_reply(message, context)
        if response:
            await message.reply_text(
                format_response_for_telegram(response),
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error handling private message: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )

async def handle_group_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Handle group messages."""
    try:
        # Check if message is from a group where bot is a member
        if message.chat.id not in [group['id'] for group in context.bot_data['group_members']]:
            return

        # Process message with context
        response = await process_message_with_context_and_reply(message, context)
        if response:
            await message.reply_text(
                format_response_for_telegram(response),
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error handling group message: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )

async def process_message_with_context_and_reply(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Process message with context and prepare response."""
    try:
        # Get relevant context from previous conversations
        context_messages = db.get_relevant_context(message.from_user.id, message.text)
        
        # Prepare context for the model
        context_text = "\n".join([f"Previous: {msg[0]}\nResponse: {msg[1]}" for msg in context_messages])
        
        # Process message with context
        response = await process_message_with_context(message.text, context_text)
        
        # Store conversation
        db.store_conversation(
            message.from_user.id,
            message.text,
            response,
            context=context_text
        )
        
        return response

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        return get_error_message('processing_error')

async def process_message_with_context(message: str, context: str) -> str:
    """Process message with context using the AI model."""
    try:
        # TODO: Implement AI model processing
        # This is a placeholder for the actual AI processing logic
        return "This is a placeholder response. AI processing not implemented yet."

    except Exception as e:
        logger.error(f"Error in AI processing: {str(e)}")
        return get_error_message('ai_processing_error')

def main():
    """Main function to run the bot."""
    try:
        # Create application
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Initialize bot data
        application.bot_data['db'] = Database()  # Initialize database
        application.bot_data['authorized_members'] = load_authorized_members(application.bot_data['db'])  # Load authorized members
        application.bot_data['members'] = []  # List of regular members
        application.bot_data['group_members'] = []  # List of tracked groups
        application.bot_data['pending_requests'] = {}  # Dictionary of pending member requests

        # Load initial data from database if available
        try:
            # Load members from database
            members_data = application.bot_data['db'].get_knowledge("members")
            if members_data and members_data[0]:  # Check if we have any data
                application.bot_data['members'] = members_data[0]  # Use first result since it's the latest
            else:
                application.bot_data['members'] = []

            # Load groups from database
            groups_data = application.bot_data['db'].get_knowledge("groups")
            if groups_data and groups_data[0]:
                application.bot_data['group_members'] = groups_data[0]
            else:
                application.bot_data['group_members'] = []
        except Exception as e:
            logger.error(f"Error loading initial data: {str(e)}")

        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("about", about_command))
        application.add_handler(CommandHandler("website", website_command))
        application.add_handler(CommandHandler("contact", contact_command))
        application.add_handler(CommandHandler("events", events_command))
        application.add_handler(CommandHandler("request_member", request_member))
        application.add_handler(CommandHandler("resources", resources_command))
        application.add_handler(CommandHandler("learn", learn_command))
        application.add_handler(CommandHandler("bulk_learn", bulk_learn_command))
        application.add_handler(CommandHandler("learn_from_url", learn_from_url))
        application.add_handler(CommandHandler("balance", check_balance))
        application.add_handler(CommandHandler("sqr_info", sqr_info))
        application.add_handler(CommandHandler("summarize_space", summarize_space))
        application.add_handler(CommandHandler("list_members", list_members))
        application.add_handler(CommandHandler("approve_member", approve_member))
        application.add_handler(CommandHandler("reject_member", reject_member))
        application.add_handler(CommandHandler("list_requests", list_requests))
        application.add_handler(CommandHandler("list_groups", list_groups))
        application.add_handler(CommandHandler("add_group", add_group))
        application.add_handler(CommandHandler("remove_group", remove_group))
        application.add_handler(CommandHandler("mass_message", mass_message))

        # Add message handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Set bot commands
        commands = [
            ("start", "Start the bot"),
            ("help", "Show help message"),
            ("about", "About sqrDAO and sqrFUND"),
            ("website", "Get website links"),
            ("contact", "Get contact information"),
            ("events", "Get upcoming events"),
            ("request_member", "Request member access"),
            ("resources", "Access member resources"),
            ("balance", "Check SQR token balance"),
            ("sqr_info", "Get SQR token information"),
            ("summarize_space", "Summarize a Twitter Space")
        ]
        
        application.bot.set_my_commands(commands)
            
        # Run the bot
        application.run_polling()

    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == '__main__':
    main() 