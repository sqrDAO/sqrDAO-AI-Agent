from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from typing import Optional
import logging
from config import ERROR_MESSAGES, SUCCESS_MESSAGES

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    # Reset user data when /start is issued
    context.user_data['awaiting_signature'] = False
    context.user_data['command_start_time'] = None
    context.user_data['space_url'] = None
    context.user_data['request_type'] = None
    context.user_data['job_id'] = None
    context.user_data['failed_attempts'] = 0
    context.user_data['signature_attempts'] = 0
    
    welcome_message = (
        "👋 <b>Hello!</b> I'm your AI assistant powered by Gemini, developed by sqrFUND. "
        "You can ask me anything, and I'll do my best to help you!\n\n"
        "I can:\n"
        "• Answer your questions about sqrDAO and sqrFUND\n"
        "• Provide information about us\n"
        "• Help with general inquiries\n"
        "• Assist with sqrDAO- and sqrFUND-related questions\n\n"
        "Just send me a message or use /help to see available commands!"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    user = update.effective_user
    is_authorized = find_authorized_member_by_username(user['username'], context)
    is_regular_member = find_member_by_username(user['username'], context)
    
    help_text = """
<b>🤖 sqrAgent Help</b>

I'm your AI assistant for sqrDAO, developed by sqrFUND! Here's what I can do:

<b>Available Commands:</b>
• /start - Start the bot and get welcome message
• /help - Show help and list of available commands
• /about - Learn about sqrDAO and sqrFUND
• /website - Get sqrDAO's and sqrFUND's website
• /contact - Get contact information
• /events - View sqrDAO events
• /balance - Check $SQR token balance
• /sqr_info - Get information about $SQR token
• /request_member - Request to become a member
• /summarize_space - Summarize an X space
"""

    if is_authorized or is_regular_member:
        help_text += """
<b>Member Commands:</b>
• /resources - Access internal resources for sqrDAO Members and sqrFUND Chads
"""

    if is_authorized:
        help_text += """
<b>Authorized Member Commands:</b>
• /learn - Add information to the bot's knowledge base
• /learn_from_url - Learn from a web page by providing a URL
• /bulk_learn - Add multiple entries from CSV file
• /mass_message - Send a message to all users and groups
• /approve_member - Approve a member request
• /reject_member - Reject a member request
• /list_requests - View pending member requests
• /list_members - List all current members
• /list_groups - List all tracked groups
• /add_group - Add a group to the bot's tracking list
• /remove_group - Remove a group from the bot's tracking list
"""

    help_text += """
<b>Features:</b>
• I remember our conversations and use them for context
• I provide detailed responses using my knowledge base
• I can help you with information about sqrDAO and sqrFUND

Just send me a message or use any command to get started!
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /about command."""
    knowledge = context.bot_data['db'].get_knowledge("sqrdao")
    about_text = "<b>About sqrDAO:</b>\n\n"
    if knowledge:
        about_text += knowledge[0][0]
    else:
        about_text = "sqrDAO is a Web3 builders-driven community in Vietnam and Southeast Asia, created by and for crypto builders. We connect and empower both technical and non-technical builders to collaborate, explore new ideas, and BUIDL together."
    
    about_text += "\n\n<b>About sqrFUND:</b>\n\n"
    knowledge = context.bot_data['db'].get_knowledge("sqrfund")
    if knowledge:
        about_text += knowledge[0][0]
    else:
        about_text += "sqrFUND, incubated by sqrDAO, is a Web3 + AI development DAO that combines Web3 builders' expertise with AI-powered data analytics to create intelligent DeFAI trading and market analysis agents."
    await update.message.reply_text(about_text, parse_mode=ParseMode.HTML)

async def website_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /website command."""
    knowledge = context.bot_data['db'].get_knowledge("website")
    if knowledge:
        website_text = knowledge[0][0]
    else:
        website_text = "Visit sqrDAO at https://sqrdao.com"

    knowledge = context.bot_data['db'].get_knowledge("sqrfund")
    if knowledge:
        website_text += "\n\n" + knowledge[0][0]
    else:
        website_text += "\n\nVisit sqrFUND at https://sqrfund.ai"

    await update.message.reply_text(website_text, parse_mode=ParseMode.HTML)

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /contact command."""
    contact_text = """
<b>Contact Information</b>

Get in touch with sqrDAO:
• Email: gm@sqrdao.com
• X (Twitter): @sqrdao
• Website: https://sqrdao.com

Get in touch with sqrFUND:
• Email: dev@sqrfund.ai
• X (Twitter): @sqrfund_ai
• Website: https://sqrfund.ai
"""
    await update.message.reply_text(contact_text, parse_mode=ParseMode.HTML)

async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /events command."""
    events_text = """
<b>sqrDAO Events Calendar</b>

View and register for our upcoming events on Luma:
• https://lu.ma/sqrdao-events

Stay updated with our latest events, workshops, and community gatherings!
"""
    await update.message.reply_text(events_text, parse_mode=ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command - Cancel the current transaction."""
    if context.user_data.get('awaiting_signature'):
        context.user_data['awaiting_signature'] = False
        context.user_data['command_start_time'] = None
        context.user_data['space_url'] = None
        context.user_data['request_type'] = None
        context.user_data['job_id'] = None
        context.user_data['failed_attempts'] = 0
        context.user_data['signature_attempts'] = 0
        await update.message.reply_text(
            "✅ Your current transaction has been cancelled.\n\n"
            "For refund (if any), please contact @DarthCastelian.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "❌ No active transaction to cancel.",
            parse_mode=ParseMode.HTML
        )

def find_authorized_member_by_username(username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Find an authorized member by username."""
    logger.info(f"Searching for authorized member: {username}")
    
    authorized_members = context.bot_data.get('authorized_members', [])
    logger.info(f"Authorized members loaded: {authorized_members}")

    for member in authorized_members:
        logger.info(f"Checking member: {member['username']}")
        if member['username'] == username:
            logger.info(f"Authorized member found: {member}")
            return member
    
    logger.warning(f"Authorized member not found for username: {username}")
    return None

def find_member_by_username(username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Find a regular member by username."""
    for member in context.bot_data['members']:
        if member['username'] == username:
            return member
    return None 