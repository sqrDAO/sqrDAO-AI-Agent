from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
import csv
import io
from utils.utils import get_webpage_content
from handlers.general import find_authorized_member_by_username
from config import ERROR_MESSAGES as _UNUSED_ERROR_MESSAGES, SUCCESS_MESSAGES as _UNUSED_SUCCESS_MESSAGES

logger = logging.getLogger(__name__)

async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /learn command - Add information to the bot's knowledge base."""

    if not find_authorized_member_by_username(update.effective_user['username'], context):
        logger.warning("Unauthorized access attempt by user: %s", update.effective_user['username'])
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Please provide the information to learn.\n"
            "Usage: /learn [topic] | [information]",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Join all arguments and split by '|'
    input_text = ' '.join(context.args)
    logger.debug("Input text received: %s", input_text)

    if '|' not in input_text:
        await update.message.reply_text(
            "❌ Please separate topic and information with '|'.\n"
            "Usage: /learn [topic] | [information]",
            parse_mode=ParseMode.HTML
        )
        return
    
    topic, information = input_text.split('|', 1)
    topic = topic.strip()
    information = information.strip()

    if not topic or not information:
        logger.error("Both topic and information are required but one or both are missing.")
        await update.message.reply_text(
            "❌ Both topic and information are required.\n"
            "Usage: /learn [topic] | [information]",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Store in knowledge base
    try:
        context.bot_data['db'].store_knowledge(topic, information)
    except Exception as e:
        logger.error("Error storing knowledge for topic '%s': %s", topic, str(e))
        await update.message.reply_text(
            "❌ An error occurred while storing the information. Please try again.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await update.message.reply_text(
        f"✅ Successfully learned about '{topic}'.",
        parse_mode=ParseMode.HTML
    )

async def bulk_learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bulk_learn command - Add multiple entries from CSV file."""
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not update.message.document:
        await update.message.reply_text(
            "❌ Please send a CSV file with the following format:\n"
            "topic,information\n"
            "Example:\n"
            "sqrdao,sqrDAO is a Web3 builders-driven community\n"
            "sqrfund,sqrFUND is a Web3 + AI development DAO",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        # Get the file
        file = await context.bot.get_file(update.message.document.file_id)
        file_content = await file.download_as_bytearray()
        
        # Read CSV content
        csv_file = io.StringIO(file_content.decode('utf-8'))
        reader = csv.reader(csv_file)
        
        # Skip header if exists
        header = next(reader, None)
        if header and len(header) != 2:
            await update.message.reply_text(
                "❌ Invalid CSV format. Please use: topic,information",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Process each row
        success_count = 0
        error_count = 0
        for row in reader:
            if len(row) != 2:
                error_count += 1
                continue
            
            topic, information = row
            if topic and information:
                context.bot_data['db'].store_knowledge(topic.strip(), information.strip())
                success_count += 1
            else:
                error_count += 1
        
        await update.message.reply_text(
            f"✅ Bulk learning completed!\n"
            f"Successfully added: {success_count}\n"
            f"Failed to add: {error_count}",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error in bulk_learn_command: {str(e)}")
        await update.message.reply_text(
            "❌ Error processing the CSV file. Please check the format and try again.",
            parse_mode=ParseMode.HTML
        )

async def learn_from_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /learn_from_url command - Learn from a web page."""
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a URL.\n"
            "Usage: /learn_from_url [url]",
            parse_mode=ParseMode.HTML
        )
        return
    
    url = context.args[0]
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        # Get webpage content - properly await the async function
        content = await get_webpage_content(url)
        if not content:
            await update.message.reply_text(
                "❌ Could not fetch content from the URL. Please try again.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Store knowledge
        context.bot_data['db'].store_knowledge("webpage", content, source=url)
        
        await update.message.reply_text(
            f"✅ Successfully learned from <a href='{url}'>webpage</a>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error learning from URL: {str(e)}")
        await update.message.reply_text(
            "❌ Error learning from URL. Please try again.",
            parse_mode=ParseMode.HTML
        ) 