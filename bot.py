import os
import json
import logging
import google.generativeai as genai
import traceback
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
from telegram import Update, Message
from telegram.constants import ParseMode
import re
import telegram

# Import handlers from other modules
from handlers.general import (
    start, help_command, about_command, website_command,
    contact_command, events_command, cancel_command
)
from handlers.member import (
    request_member, approve_member, reject_member,
    list_requests, list_members, resources_command,
    list_groups
)
from handlers.knowledge import (
    learn_command, bulk_learn_command, learn_from_url
)
from handlers.solana import (
    check_balance, sqr_info
)
from handlers.spaces import (
    summarize_space, edit_summary, shorten_summary
)
from handlers.mass_message import mass_message
from handlers.spaces import process_signature

# Import database and utils
from db import Database
from utils.utils import (
    format_response_for_telegram, extract_urls,
    get_webpage_content, escape_markdown_v2,
    get_announcement_prefix, parse_mass_message_input,
    get_error_message, get_success_message,
    load_authorized_members, extract_keywords, retrieve_knowledge, format_context
)

# Import config
from config import (
    TELEGRAM_BOT_TOKEN, ERROR_MESSAGES, SUCCESS_MESSAGES,
    DocumentWithMassMessageCaption, generation_config, safety_settings
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

# Initialize Gemini model with safety settings
try:

    model = genai.GenerativeModel(
        model_name='models/gemini-2.0-flash',
        generation_config=generation_config,
        safety_settings=safety_settings
    )
    
except Exception as e:
    logger.error(f"Error initializing or testing Gemini model: {str(e)}")
    raise

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    try:
        message = update.message
        if not message:
            logger.warning("Received an empty message.")
            return

        # If we're in the middle of a space summarization process, ignore other messages
        if context.user_data.get('space_url') and not context.user_data.get('awaiting_signature'):
            logger.debug("Ignoring message during space summarization process.")
            return

        # Check if we're in a group chat
        if update.message.chat.type in ['group', 'supergroup']:
            # If awaiting signature, silently ignore the message in groups
            if context.user_data.get('awaiting_signature'):
                return
            # Otherwise process group message normally
            await handle_group_message(update.message, context)
        elif update.message.chat.type == 'private':
            # If awaiting signature, , process the transaction signature in private chats
            if context.user_data.get('awaiting_signature'):
                await process_signature(update.message.text, context, update.message)
                return
            # Otherwise process private message normally
            await handle_private_message(message, context)
        else:
            logger.warning(f"Unhandled chat type: {message.chat.type}")

    except Exception as e:
        logger.error(f"Error in handle_message: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )

async def handle_private_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Handle private messages."""
    try:
        # Process message with context
        response = await process_message_with_context_and_reply(message, context)
        logger.debug(f"Response generated for {message.from_user.username}: {response}")  # Log the response
        
        if response:
            await message.reply_text(
                format_response_for_telegram(response, 'HTML'),
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.debug(f"Error processing private message from {message.from_user.username}: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )

async def handle_group_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Handle group messages."""
    try:
        # Log the entire message object for debugging
        logger.debug(f"Received group message from {message.from_user.username} in chat {message.chat.id}")

        # Initialize group_members if not exists
        if 'group_members' not in context.bot_data:
            context.bot_data['group_members'] = []
            logger.warning("group_members not initialized, using empty list")

        # Handle different formats of group_members
        group_members = context.bot_data['group_members']
        if isinstance(group_members, dict):
            # Convert dictionary to list of groups
            group_members = [{'id': chat_id, 'title': title} for chat_id, title in group_members.items()]
            context.bot_data['group_members'] = group_members
            logger.debug("Converted group_members from dict to list format")
        elif not isinstance(group_members, list):
            logger.error(f"group_members is not a list or dict: {type(group_members)}")
            context.bot_data['group_members'] = []

        # Check if message is from a group where bot is a member
        if message.chat.id not in [group['id'] for group in context.bot_data['group_members']]:
            logger.warning(f"Message from non-member group: {message.chat.id}. Ignoring.")
            return

        # Check if the bot is mentioned in the message
        bot_username_with_at = f"@{context.bot.username}"
        bot_mentioned = False
        for entity in message.entities:
        # Check if it's a mention entity
            if entity.type == telegram.constants.MessageEntityType.MENTION:
            # Extract the actual text corresponding to the entity
                start = entity.offset
                end = start + entity.length
                mentioned_text = message.text[start:end]

                logger.debug(f"Checking mention entity: Type={entity.type}, Offset={start}, Length={entity.length}, Text='{mentioned_text}'")

                # Compare the extracted text directly with the bot's username
                if mentioned_text == bot_username_with_at:
                    bot_mentioned = True
                    break # Exit loop once mention is found
        
        # Log the result of the mention check
        if bot_mentioned:
            logger.debug(f"Bot mentioned in message: {message.text}")
        else:
            logger.warning(f"Bot not mentioned in message: {message.text}")

        if not bot_mentioned:
            logger.debug("Bot not mentioned in the message. Ignoring.")
            return

        # Process message with context
        response = await process_message_with_context_and_reply(message, context)
        if response:
            await message.reply_text(
                format_response_for_telegram(response, 'HTML'),
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error in handle_group_message: {str(e)}")
        await message.reply_text(
            get_error_message('general_error'),
            parse_mode=ParseMode.HTML
        )
    except telegram.error.Forbidden as e:
        logger.error(f"Forbidden error: {str(e)} - The bot may have been removed from the group.")

async def process_message_with_context_and_reply(message: Message, context: ContextTypes.DEFAULT_TYPE):
    """Process message with context and prepare response."""
    try:
        # Get relevant context from previous conversations
        context_messages = db.get_relevant_context(message.from_user.id, message.text)

        # Log the context_messages
        logger.debug(f"context_messages: {context_messages}, type: {type(context_messages)}")

        # Prepare context for the model
        context_text = " ".join(
            [f"Previous: {msg[0]} Response: {msg[1]}" for msg in context_messages if len(msg) >= 2 and msg[0] and msg[1]]
        )

        # Log the context_text
        logger.debug(f"context_text: {context_text}, type: {type(context_text)}")

        # Process message with context
        response = await process_message_with_context(message.text, context_text)

        logger.debug(f"response: {response}, type: {type(response)}")
        
        # Store conversation
        db.store_conversation(
            message.from_user.id,
            message.text,
            response,
            context=context_text
        )
        
        return response

    except Exception as e:
        logger.debug(f"Error in process_message_with_context_and_reply: {str(e)}")
        return get_error_message('processing_error')

async def process_message_with_context(message, context):
    """Process the message with context and prepare the response."""
    logger.debug(f"Processing message with context: {message}, type: {type(message)}")

    # Check if message is a valid string
    if not isinstance(message, str) or not message.strip():
        logger.error("Invalid message input. Must be a non-empty string.")
        return "Invalid message input."

    # Step 1: Extract meaningful keywords
    keywords = extract_keywords(message)
    
    logger.debug(f"Keywords: {keywords}, type: {type(keywords)}")
    
    # Step 2: Retrieve relevant knowledge
    knowledge_text = await retrieve_knowledge(db, keywords)
    
    # Log the types of knowledge_text and message
    logger.debug(f"knowledge_text: {knowledge_text}, type: {type(knowledge_text)}")
    logger.debug(f"message: {message}, type: {type(message)}")

    # Ensure knowledge_text is a string
    if not isinstance(knowledge_text, str):
        logger.error(f"Expected knowledge_text to be a string, got {type(knowledge_text)}")
        knowledge_text = str(knowledge_text)  # Convert to string if necessary

    # Step 3: Format context
    context_text = format_context(context)
    
    # Log the type of context_text
    logger.debug(f"context_text: {context_text}, type: {type(context_text)}")

    # Step 4: Combine context with current message and knowledge
    prompt = f"{context_text}\n{knowledge_text}\nCurrent message: {message}\n\nPlease provide a response that takes into account both the context of previous conversations and the stored knowledge if relevant."
    
    # Log the prompt
    logger.debug(f"prompt: {prompt}, type: {type(prompt)}")

    try:
        # Generate response using Gemini
        response = model.generate_content(prompt)
        
        if not hasattr(response, 'text') or not response.text:
            return "I apologize, but I couldn't generate a response. Please try rephrasing your question."
            
        return response.text
        
    except Exception as e:
        logger.error(f"Error processing message with context: {str(e)}")
        return "I encountered an error while processing your message. Please try again."

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle updates to chat member status."""
    try:
        # Log the received update for debugging
        logger.debug(f"Received chat member update for chat: {update.my_chat_member.chat.id if update.my_chat_member else 'unknown'}")

        # Check if the update contains my_chat_member
        if not update.my_chat_member:
            logger.warning("Received update does not contain my_chat_member.")
            return

        chat_member = update.my_chat_member
        chat_id = chat_member.chat.id

        # Log the new membership status
        logger.debug(f"Chat ID: {chat_id}, New Status: {chat_member.new_chat_member.status}")

        # Check the bot's new membership status
        if chat_member.new_chat_member.status == "member":
            # Bot was added to the group
            logger.debug(f"Bot added to group: {chat_member.chat.title}")
            db.add_group(chat_id, chat_member.chat.title, context.bot_data)

        elif chat_member.new_chat_member.status == "left":
            # Bot was removed from the group
            logger.debug(f"Bot removed from group: {chat_member.chat.title}")
            db.remove_group(chat_id, context.bot_data)

    except Exception as e:
        logger.error(f"Error handling chat member update: {str(e)}")

def main():
    """Main function to run the bot."""
    try:
        # Create application
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Initialize database
        application.bot_data['db'] = Database()  # Initialize database
        application.bot_data['authorized_members'] = load_authorized_members(application.bot_data['db'])  # Load authorized members

        # Load initial data from database
        application.bot_data['members'] = application.bot_data['db'].load_members()
        logger.debug(f"Loaded members: {application.bot_data['members']}")
        application.bot_data['group_members'] = application.bot_data['db'].load_groups()
        logger.debug(f"Loaded groups: {application.bot_data['group_members']}")

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
        application.add_handler(CommandHandler("summarize_space", summarize_space, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("list_members", list_members, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("approve_member", approve_member, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("reject_member", reject_member, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("list_requests", list_requests, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("list_groups", list_groups, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("mass_message", mass_message))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(CommandHandler("edit_summary", edit_summary, filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("shorten_summary", shorten_summary, filters.ChatType.PRIVATE))
        # Add message handler
        application.add_handler(MessageHandler(filters.TEXT, handle_message))  # Removed command filter

        # Add handler for documents with mass_message command in caption using custom filter
        application.add_handler(MessageHandler(
            DocumentWithMassMessageCaption() & filters.ChatType.PRIVATE, mass_message
        ))

        # Add handler for photos with mass_message command in caption
        application.add_handler(MessageHandler(
            filters.PHOTO & filters.CaptionRegex(r'^/mass_message') & filters.ChatType.PRIVATE, mass_message
        ))

        # Add handler for videos with mass_message command in caption
        application.add_handler(MessageHandler(
            filters.VIDEO & filters.CaptionRegex(r'^/mass_message') & filters.ChatType.PRIVATE, mass_message
        ))

        # Add chat member update handler
        application.add_handler(ChatMemberHandler(handle_chat_member_update))

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
            ("sqr_info", "Get SQR token information")
        ]
        
        application.bot.set_my_commands(commands)
            
        # Run the bot
        application.run_polling()

    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == '__main__':
    main() 
