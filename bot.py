import os
import logging
import traceback
import re
import json
import requests
import trafilatura
import google.generativeai as genai
import sqlite3
import functools
import telegram
import csv
import io
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from googleapiclient.discovery import build
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from datetime import datetime
from typing import List, Tuple
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from spl.token.client import Token
import base58
from solders.keypair import Keypair

# SNS resolution function
async def resolve_sns_domain(domain: str) -> str:
    """Resolve a .sol domain to its wallet address using Bonfida's HTTP API."""
    try:
        # Remove .sol extension if present
        domain = domain.lower().replace('.sol', '')
        
        # Call Bonfida's HTTP API
        response = requests.get(f'https://sns-sdk-proxy.bonfida.workers.dev/resolve/{domain}')
        
        if response.status_code == 200:
            data = response.json()
            if data.get('s') == 'ok' and data.get('result'):
                return data['result']
        
        return None
    except Exception as e:
        logger.error(f"Error resolving SNS domain: {str(e)}")
        return None

# Load environment variables
load_dotenv()

# Configure logging with more detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize empty members lists
AUTHORIZED_MEMBERS = []
MEMBERS = []

# Global variable to store the bot's ID
bot_id = None

# Add these constants after other API configurations
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
solana_client = Client(SOLANA_RPC_URL)

def load_members_from_knowledge():
    """Load regular members from the knowledge base and authorized members from config.json."""
    global AUTHORIZED_MEMBERS, MEMBERS
    try:
        # Check if authorized members are stored in the knowledge base
        authorized_members_knowledge = db.get_knowledge("authorized_members")
        if authorized_members_knowledge:
            AUTHORIZED_MEMBERS = json.loads(authorized_members_knowledge[0][0])
            logger.info(f"Loaded {len(AUTHORIZED_MEMBERS)} authorized members from knowledge base")
        else:
            # Load authorized members from config.json if not found in knowledge base
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    AUTHORIZED_MEMBERS = config.get('authorized_members', [])
                    logger.info(f"Loaded {len(AUTHORIZED_MEMBERS)} authorized members from config.json")
            except Exception as e:
                logger.error(f"Error loading authorized members from config.json: {str(e)}")
                logger.error("Falling back to empty authorized members list")
                AUTHORIZED_MEMBERS = []
        
        # Load regular members from knowledge base
        regular_members = db.get_knowledge("members")
        if regular_members:
            MEMBERS = json.loads(regular_members[0][0])
            logger.info(f"Loaded {len(MEMBERS)} regular members from knowledge base")
        else:
            MEMBERS = []

    except Exception as e:
        logger.error(f"Error loading members: {str(e)}")
        logger.error("Falling back to empty members lists")
        AUTHORIZED_MEMBERS = []
        MEMBERS = []

def save_members_to_knowledge():
    """Save regular members to the knowledge base."""
    try:
        # Save regular members only
        db.store_knowledge("members", json.dumps(MEMBERS))
        logger.info("Successfully saved regular members to knowledge base")
    except Exception as e:
        logger.error(f"Error saving members to knowledge base: {str(e)}")

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bot_memory.db')
        self.cursor = self.conn.cursor()
        self.setup_database()

    def setup_database(self):
        # Create tables for storing conversations and knowledge
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                response TEXT,
                timestamp DATETIME,
                context TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                information TEXT,
                source TEXT,
                timestamp DATETIME
            )
        ''')
        self.conn.commit()

    def store_conversation(self, user_id, message, response, context=None):
        self.cursor.execute('''
            INSERT INTO conversations (user_id, message, response, timestamp, context)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, message, response, datetime.now(), context))
        self.conn.commit()

    def store_knowledge(self, topic, information, source=None):
        self.cursor.execute('''
            INSERT INTO knowledge_base (topic, information, source, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (topic, information, source, datetime.now()))
        self.conn.commit()

    def get_relevant_context(self, user_id, message, limit=5):
        # Simple keyword-based search for now
        keywords = message.lower().split()
        query = '''
            SELECT message, response, context
            FROM conversations
            WHERE user_id = ? AND (
        ''' + ' OR '.join(['lower(message) LIKE ?' for _ in keywords]) + ')'
        params = [user_id] + ['%' + keyword + '%' for keyword in keywords]
        
        self.cursor.execute(query + ' ORDER BY timestamp DESC LIMIT ?', 
                          params + [limit])
        return self.cursor.fetchall()

    def get_knowledge(self, topic):
        self.cursor.execute('''
            SELECT information
            FROM knowledge_base
            WHERE lower(topic) LIKE lower(?)
        ''', ('%' + topic + '%',))
        return self.cursor.fetchall()

# Initialize database and load members
db = Database()

# Store pending member requests
PENDING_REQUESTS = {}

def is_member(func):
    """Check if user is an authorized member."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.username and find_authorized_member_by_username(user.username):
            return await func(update, context)
        else:
            await update.message.reply_text(
                "⚠️ This command is only available to sqrDAO authorized members.\n"
                "Please use /request_member command to request membership.",
                parse_mode=ParseMode.HTML
            )
    return wrapper

def is_any_member(func):
    """Check if user is either an authorized member or regular member."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        logger.info(f"User: {user}")
        if user.username and (find_authorized_member_by_username(user.username) or find_member_by_username(user.username)):
            return await func(update, context)
        else:
            await update.message.reply_text(
                "⚠️ This command is only available to sqrDAO members.\n"
                "Please use /request_member command to request membership.",
                parse_mode=ParseMode.HTML
            )
    return wrapper

async def request_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /request_member command - Request to be added as a member."""
    user = update.effective_user
    user_id = user.id  # Get the user ID

    # Check if the user is already a member
    if find_authorized_member_by_username(user.username) or find_member_by_username(user.username):
        await update.message.reply_text(
            "❌ You are already a member and cannot request membership again.",
            parse_mode=ParseMode.HTML
        )
        return

    # Store the user ID in PENDING_REQUESTS instead of username
    PENDING_REQUESTS[user.username] = {
        'user_id': user_id,
        'username': user.username,
        'timestamp': datetime.now(),
        'status': 'pending'
    }

    # Notify authorized members using user IDs
    for member_id in AUTHORIZED_MEMBERS:  # Ensure AUTHORIZED_MEMBERS contains user IDs
        try:
            await context.bot.send_message(
                chat_id=member_id,
                text=f"🔔 New member request from {user.username}\n\n"
                     f"Use /approve_member {user_id} to approve or\n"
                     f"/reject_member {user_id} to reject"
            )
        except Exception as e:
            logger.error(f"Failed to notify authorized member {member_id}: {str(e)}")
    
    await update.message.reply_text(
        "✅ Your membership request has been submitted!\n"
        "Our team will review it and get back to you soon.",
        parse_mode=ParseMode.HTML
    )

@is_member
async def approve_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /approve_member command - Approve a member request."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to approve.\n"
            "Usage: /approve_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    if username not in PENDING_REQUESTS:
        await update.message.reply_text(
            "❌ No pending request found for this username.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Get user ID from pending requests
    user_id = PENDING_REQUESTS[username]['user_id']
    
    # Add to members list with both username and user_id
    MEMBERS.append({
        'username': username,
        'user_id': user_id
    })
    save_members_to_knowledge()  # Ensure this function is updated to handle the new structure
    logger.info(f"Added member: {username} with user_id: {user_id}")
    # Store the authorized member in the knowledge base
    
    # Remove from pending requests
    del PENDING_REQUESTS[username]
    
    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Congratulations! Your membership request has been approved!\n\n"
                 "You now have access to member-only features. Use /help to see available commands."
        )
    except Exception as e:
        logger.error(f"Failed to notify approved user: {str(e)}")
    
    await update.message.reply_text(
        f"✅ Successfully approved @{username} as a member.",
        parse_mode=ParseMode.HTML
    )

@is_member
async def reject_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reject_member command - Reject a member request."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to reject.\n"
            "Usage: /reject_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    if username not in PENDING_REQUESTS:
        await update.message.reply_text(
            "❌ No pending request found for this username.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Remove from pending requests
    del PENDING_REQUESTS[username]
    
    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=PENDING_REQUESTS[username]['user_id'],
            text="❌ Your membership request has been rejected.\n\n"
                 "If you believe this was a mistake, please use /contact command to contact the team."
        )
    except Exception as e:
        logger.error(f"Failed to notify rejected user: {str(e)}")
    
    await update.message.reply_text(
        f"✅ Successfully rejected @{username}'s membership request.",
        parse_mode=ParseMode.HTML
    )

@is_member
async def list_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_requests command - List pending member requests."""
    if not PENDING_REQUESTS:
        await update.message.reply_text(
            "📝 No pending member requests.",
            parse_mode=ParseMode.HTML
        )
        return
    
    requests_text = "<b>Pending Member Requests:</b>\n\n"
    for user_id, request in PENDING_REQUESTS.items():
        requests_text += f"• @{request['username']} (Requested: {request['timestamp'].strftime('%Y-%m-%d %H:%M:%S')})\n"
    await update.message.reply_text(requests_text, parse_mode=ParseMode.HTML)

# Configure API keys
api_key = os.getenv('GEMINI_API_KEY')
google_api_key = os.getenv('GOOGLE_API_KEY')
google_cse_id = os.getenv('GOOGLE_CSE_ID')

if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables")
if not google_api_key:
    raise ValueError("GOOGLE_API_KEY not found in environment variables")
if not google_cse_id:
    raise ValueError("GOOGLE_CSE_ID not found in environment variables")

# Initialize Google Custom Search API
search_client = build("customsearch", "v1", developerKey=google_api_key)

# Configure Gemini
genai.configure(api_key=api_key)

# Initialize Gemini model with safety settings
try:
    generation_config = {
        "temperature": 0.9,
        "top_p": 1,
        "top_k": 1,
        "max_output_tokens": 2048,
    }

    safety_settings = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
    ]

    model = genai.GenerativeModel(
        model_name='models/gemini-2.0-flash',
        generation_config=generation_config,
        safety_settings=safety_settings
    )
    
    # Test the model with a simple prompt
    test_response = model.generate_content("Hello")
    logger.info("Successfully tested model with 'Hello' prompt")
    logger.debug(f"Test response: {test_response.text if hasattr(test_response, 'text') else 'No text attribute'}")
    
except Exception as e:
    logger.error(f"Error initializing or testing Gemini model: {str(e)}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    raise

def format_response_for_telegram(text):
    """Format the response text to be compatible with Telegram's HTML."""
    # Escape special HTML characters
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # Ensure proper handling of tags
    # Example: Replace **bold** with <b>bold</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)  # Italics
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', text)  # Links

    return text

def extract_urls(text):
    """Extract URLs from text."""
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    return re.findall(url_pattern, text)

def get_webpage_content(url):
    """Get the main content from a webpage."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text:
                return text
        
        # Fallback to basic HTML parsing if trafilatura fails
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text[:5000]  # Limit text length
    except Exception as e:
        logger.error(f"Error fetching webpage content: {str(e)}")
        return None

def search_web(query, num_results=5):
    """Search the web using Google Custom Search API."""
    try:
        results = []
        search_results = search_client.cse().list(q=query, cx=google_cse_id, num=num_results).execute()
        
        if 'items' in search_results:
            for item in search_results['items']:
                results.append({
                    'title': item.get('title', ''),
                    'link': item.get('link', ''),
                    'snippet': item.get('snippet', '')
                })
        
        return results
    except Exception as e:
        logger.error(f"Error searching web: {str(e)}")
        return []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    welcome_message = (
        "👋 <b>Hello!</b> I'm your AI assistant powered by Gemini, developed by sqrFUND. "
        "You can ask me anything, and I'll do my best to help you!\n\n"
        "I can:\n"
        "• Answer your questions about sqrDAO\n"
        "• Provide information about our platform\n"
        "• Help with general inquiries\n"
        "• Assist with platform-related questions\n\n"
        "Just send me a message or use /help to see available commands!"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    user = update.effective_user
    is_authorized = find_authorized_member_by_username(user['username'])
    is_regular_member = find_member_by_username(user['username'])
    
    help_text = """
<b>🤖 sqrAgent Help</b>

I'm your AI assistant for sqrDAO, developed by sqrFUND! Here's what I can do:

<b>Available Commands:</b>
• /start - Start the bot and get welcome message
• /help - Show help and list of available commands
• /about - Learn about sqrDAO
• /website - Get sqrDAO's website
• /contact - Get contact information
• /events - View sqrDAO events
• /balance - Check Solana token balance
• /request_member - Request to become a member
"""

    if is_authorized or is_regular_member:
        help_text += """
<b>Member Commands:</b>
• /resources - Access internal resources
"""

    if is_authorized:
        help_text += """
<b>Authorized Member Commands:</b>
• /learn - Add information to the bot's knowledge base
• /learn_from_url - Learn from a web page by providing a URL
• /bulk_learn - Add multiple entries from CSV file
• /approve_member - Approve a member request
• /reject_member - Reject a member request
• /list_requests - View pending member requests
"""

    help_text += """
<b>Features:</b>
• I remember our conversations and use them for context
• I provide detailed responses using my knowledge base
• I can help you with information about sqrDAO

Just send me a message or use any command to get started!
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

def process_message_with_context(message, context):
    # Prepare context for the model
    context_text = ""
    if context:
        context_text = "Previous relevant conversations:\n"
        for prev_msg, prev_resp, ctx in context:
            context_text += f"User: {prev_msg}\nBot: {prev_resp}\n"
    
    # Get relevant knowledge
    keywords = message.lower().split()
    knowledge_text = ""
    for keyword in keywords:
        knowledge = db.get_knowledge(keyword)
        if knowledge:
            knowledge_text += "\nStored knowledge:\n"
            for info in knowledge:
                knowledge_text += f"• {info[0]}\n"
    
    # Combine context with current message and knowledge
    prompt = f"{context_text}\n{knowledge_text}\nCurrent message: {message}\n\nPlease provide a response that takes into account both the context of previous conversations and the stored knowledge if relevant."
    
    try:
        # Generate response using Gemini
        response = model.generate_content(prompt)
        
        if not hasattr(response, 'text') or not response.text:
            return "I apologize, but I couldn't generate a response. Please try rephrasing your question."
            
        return response.text
        
    except Exception as e:
        logger.error(f"Error generating response: {str(e)}")
        return "I encountered an error while processing your message. Please try again."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and generate responses using Gemini."""
    user_message = update.message.text
    chat_id = update.message.chat_id
    
    # Check if the message is a command
    if user_message.startswith('/'):
        await update.message.reply_text(
            "<i>Please use specific commands like /help, or send a regular message.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Check if the message mentions the bot
    if context.bot.username in user_message:
        # Process the message as a normal user message
        user_message = user_message.replace(f"@{context.bot.username}", "").strip()
        
        if not user_message:
            await update.message.reply_text(
                "<i>I couldn't process an empty message. Please send some text.</i>",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Get relevant context from previous conversations
        relevant_context = db.get_relevant_context(update.effective_user.id, user_message)
        
        # Process the message with context
        response = process_message_with_context(user_message, relevant_context)
        
        # Store the conversation
        db.store_conversation(update.effective_user.id, user_message, response)
        
        # Format and send response with HTML formatting
        formatted_text = format_response_for_telegram(response)
        
        logger.debug(f"Formatted response: {formatted_text}")
        
        await update.message.reply_text(
            formatted_text,
            parse_mode=ParseMode.HTML
        )
        return

    # Check if this is a template request
    if user_message.lower().strip() == "template":
        try:
            with open('template.csv', 'rb') as template_file:
                await update.message.reply_document(
                    document=template_file,
                    filename='sqrdao_knowledge_template.csv',
                    caption="📝 Here's a template CSV file for bulk learning.\n\n"
                           "The file includes:\n"
                           "• Example entries\n"
                           "• Format rules\n"
                           "• Character limits\n"
                           "• Supported delimiters\n\n"
                           "Fill in your entries and send the file back to me!"
                )
            return
        except Exception as e:
            logger.error(f"Error sending template: {str(e)}")
            await update.message.reply_text(
                "❌ Sorry, I couldn't send the template file. Please try again later."
            )
            return
        
    try:
        # Get relevant context from previous conversations
        relevant_context = db.get_relevant_context(update.effective_user.id, user_message)
        
        # Process the message with context
        response = process_message_with_context(user_message, relevant_context)
        
        # Store the conversation
        db.store_conversation(update.effective_user.id, user_message, response)
        
        # Format and send response with HTML formatting
        formatted_text = format_response_for_telegram(response)
        
        logger.debug(f"Formatted response: {formatted_text}")
        
        await update.message.reply_text(
            formatted_text,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        await update.message.reply_text(
            "<i>I encountered an error while processing your message. Please try again.</i>",
            parse_mode=ParseMode.HTML
        )

async def set_bot_commands(application):
    """Set bot commands with descriptions for the command menu."""
    # Basic commands for all users
    basic_commands = [
        ("start", "Start the bot and get welcome message"),
        ("help", "Show help and list of available commands"),
        ("about", "Learn about sqrDAO"),
        ("website", "Get sqrDAO's website"),
        ("contact", "Get contact information"),
        ("events", "View sqrDAO events"),
        ("balance", "Check Solana token balance"),  # Added balance command
        ("request_member", "Request to become a member")
    ]
    
    # Commands for regular members
    member_commands = basic_commands + [
        ("resources", "Access internal resources")
    ]
    
    # Set basic commands for all users
    await application.bot.set_my_commands(basic_commands)
    
    # Set member commands for regular members
    for member in MEMBERS:
        try:
            await application.bot.set_my_commands(
                member_commands,
                scope=telegram.BotCommandScopeChat(member['user_id'])  # Ensure member is a user ID
            )
        except Exception as e:
            logger.error(f"Failed to set commands for member {member['username']}: {str(e)}")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /about command."""
    knowledge = db.get_knowledge("sqrdao")
    about_text = "About sqrDAO:\n\n"
    if knowledge:
        about_text += knowledge[0][0]
    else:
        about_text = "sqrDAO is a decentralized autonomous organization focused on innovative blockchain solutions and research."
    await update.message.reply_text(about_text, parse_mode=ParseMode.HTML)

async def website_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /website command."""
    knowledge = db.get_knowledge("website")
    if knowledge:
        website_text = knowledge[0][0]
    else:
        website_text = "Visit sqrDAO at https://sqrdao.com"
    await update.message.reply_text(website_text, parse_mode=ParseMode.HTML)

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /contact command."""
    contact_text = """
<b>Contact Information</b>

Get in touch with us:
• Email: gm@sqrdao.com
• X (Twitter): @sqrdao
• Website: https://sqrdao.com
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

@is_any_member
async def resources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resources command - Available to all members."""
    resources_text = """
<b>sqrDAO Members' Resources</b>

Here are our internal resources:
• <b>GitHub:</b> https://github.com/sqrdao
• <b>AWS Credits Guide:</b> https://drive.google.com/file/d/12DjM2P5x0T_koLI6o_UMXMo_LUJpYrXL/view?usp=sharing
• <b>AWS Org ID ($10K):</b> 3Ehcy
• <b>Legal Service (20% off):</b> https://teamoutlaw.io/
• <b>SqrDAO Brand Kit:</b> https://sqrdao.notion.site/sqrdao-brand-kit

For access issues, please contact @DarthCastelian.
"""
    await update.message.reply_text(resources_text, parse_mode=ParseMode.HTML)

@is_member
async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /learn command - Available to authorized members only."""
    message = update.message.text.strip()
    
    # Check if the message starts with quotes for the topic
    if not (message.count('"') >= 2 and message.find('"') < message.find('"', message.find('"') + 1)):
        usage_text = """
<b>Usage:</b> /learn "topic" [information]

Examples:
/learn "website" Our new website is live at https://sqrdao.com

The topic must be in quotes. This will store the information in the knowledge base.
"""
        await update.message.reply_text(usage_text, parse_mode=ParseMode.HTML)
        return
    
    # Extract topic (text between first pair of quotes)
    first_quote = message.find('"')
    second_quote = message.find('"', first_quote + 1)
    topic = message[first_quote + 1:second_quote].strip()
    
    # Extract information (everything after the second quote)
    information = message[second_quote + 1:].strip()
    
    if not topic or not information:
        await update.message.reply_text(
            "❌ Please provide both a topic (in quotes) and information.",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        db.store_knowledge(topic, information)
        await update.message.reply_text(
            f"✅ Successfully stored information about <b>{topic}</b>.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error storing knowledge: {str(e)}")
        await update.message.reply_text(
            "❌ Failed to store information. Please try again.",
            parse_mode=ParseMode.HTML
        )

async def bulk_learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bulk_learn command - Bulk add information from CSV file."""
    if not update.message.document:
        # Check if user wants the template
        if update.message.text and "template" in update.message.text.lower():
            try:
                with open('template.csv', 'rb') as template_file:
                    await update.message.reply_document(
                        document=template_file,
                        filename='sqrdao_knowledge_template.csv',
                        caption="📝 Here's a template CSV file for bulk learning.\n\n"
                               "The file includes:\n"
                               "• Example entries\n"
                               "• Format rules\n"
                               "• Character limits\n"
                               "• Supported delimiters\n\n"
                               "Fill in your entries and send the file back to me!"
                    )
                return
            except Exception as e:
                logger.error(f"Error sending template: {str(e)}")
                await update.message.reply_text(
                    "❌ Sorry, I couldn't send the template file. Please try again later."
                )
                return
        
        await update.message.reply_text(
            "Please send a CSV file with the following format:\n"
            "topic,information\n"
            "Example:\n"
            "aws credits,Information about AWS credits\n"
            "legal services,Information about legal services\n\n"
            "Supported formats:\n"
            "• Standard CSV (comma-separated)\n"
            "• Semicolon-separated (SSV)\n"
            "• Tab-separated (TSV)\n"
            "• Pipe-separated (PSV)\n\n"
            "The file should have a header row with 'topic' and 'information' columns.\n\n"
            "Type 'template' to get a template file with examples."
        )
        return

    try:
        # Get the file
        file = await context.bot.get_file(update.message.document.file_id)
        file_content = await file.download_as_bytearray()
        
        # Detect file encoding
        try:
            # Try UTF-8 first
            csv_text = file_content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                # Try UTF-8 with BOM
                csv_text = file_content.decode('utf-8-sig')
            except UnicodeDecodeError:
                # Fallback to latin-1
                csv_text = file_content.decode('latin-1')
        
        # Detect delimiter
        first_line = csv_text.split('\n')[0]
        delimiters = [',', ';', '\t', '|']
        delimiter = max(delimiters, key=lambda d: first_line.count(d))
        
        # Parse CSV content with detected delimiter
        csv_reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
        
        # Get header row
        header = next(csv_reader, None)
        if not header:
            raise ValueError("File is empty")
        
        # Validate header
        header = [col.strip().lower() for col in header]
        required_columns = ['topic', 'information']
        missing_columns = [col for col in required_columns if col not in header]
        
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
        
        # Get column indices
        topic_idx = header.index('topic')
        info_idx = header.index('information')
        
        # Process each row
        success_count = 0
        error_count = 0
        error_messages = []
        skipped_rows = 0
        
        for row_num, row in enumerate(csv_reader, start=2):
            # Skip empty rows
            if not any(cell.strip() for cell in row):
                skipped_rows += 1
                continue
                
            # Validate row length
            if len(row) < max(topic_idx, info_idx) + 1:
                error_count += 1
                error_messages.append(f"Row {row_num}: Insufficient columns")
                continue
            
            # Extract and validate data
            topic = row[topic_idx].strip()
            information = row[info_idx].strip()
            
            # Validate content
            if not topic:
                error_count += 1
                error_messages.append(f"Row {row_num}: Empty topic")
                continue
                
            if not information:
                error_count += 1
                error_messages.append(f"Row {row_num}: Empty information")
                continue
            
            # Validate length limits
            if len(topic) > 255:
                error_count += 1
                error_messages.append(f"Row {row_num}: Topic too long (max 255 characters)")
                continue
                
            if len(information) > 5000:
                error_count += 1
                error_messages.append(f"Row {row_num}: Information too long (max 5000 characters)")
                continue
            
            try:
                # Store in database
                db.store_knowledge(topic, information)
                success_count += 1
            except Exception as e:
                error_count += 1
                error_messages.append(f"Row {row_num} '{topic}': {str(e)}")
        
        # Prepare response
        response = f"✅ Successfully processed {success_count} entries"
        if skipped_rows > 0:
            response += f"\n⏭️ Skipped {skipped_rows} empty rows"
        if error_count > 0:
            response += f"\n❌ Failed to process {error_count} entries"
            if error_messages:
                response += "\n\nErrors:\n" + "\n".join(error_messages[:5])  # Show first 5 errors
                if len(error_messages) > 5:
                    response += f"\n... and {len(error_messages) - 5} more errors"
        
        await update.message.reply_text(response)
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error processing file: {str(e)}\n"
            "Please make sure the file is a valid CSV with 'topic' and 'information' columns.\n"
            "Supported formats: CSV, SSV, TSV, PSV"
        )

@is_member
async def learn_from_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /learn_from_url command - Learn from a web page."""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ Please provide a URL to learn from.\nUsage: /learn_from_url [url]\n"
            "Make sure the URL starts with http:// or https://.",
            parse_mode=ParseMode.HTML
        )
        return

    url = context.args[0]

    # Check if the provided argument is a valid URL
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text(
            "❌ The provided input does not appear to be a valid URL.\n"
            "Please provide a valid URL starting with http:// or https://.\n"
            "Usage: /learn_from_url [url]",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Fetch the content from the URL
    content = get_webpage_content(url)
    
    if content:
        # Store the content in the knowledge base
        topic = url  # You can customize the topic as needed
        try:
            db.store_knowledge(topic, content)
            await update.message.reply_text(
                f"✅ Successfully learned from the URL: <a href='{url}'>{url}</a>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error storing knowledge from URL: {str(e)}")
            await update.message.reply_text(
                "❌ Failed to store information. Please try again.",
                parse_mode=ParseMode.HTML
            )
    else:
        await update.message.reply_text(
            "❌ Failed to fetch content from the provided URL. Please check the URL and try again.",
            parse_mode=ParseMode.HTML
        )

def find_authorized_member_by_username(username):
    """Find an authorized member by username."""
    for member in AUTHORIZED_MEMBERS:
        if member['username'] == username:
            return member  # Return the member object if found
    return None  # Return None if not found

def find_member_by_username(username):
    """Find a regular member by username."""
    for member in MEMBERS:
        if member['username'] == username:
            return member  # Return the member object if found
    return None  # Return None if not found

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command to check SQR token balance for a Solana wallet or .sol domain."""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ Please provide the wallet address or SNS domain.\n"
            "Usage: /balance [wallet_address or sns_domain]\n"
            "Examples:\n"
            "• /balance 5vJRzKtcp4fJxqmR7qzajkGNF9dqQk49TPhWXLDZAJf6\n"
            "• /balance castelian.sol",
            parse_mode=ParseMode.HTML
        )
        return

    input_address = context.args[0]
    token_mint = "CsZmZ4fz9bBjGRcu3Ram4tmLRMmKS6GPWqz4ZVxsxpNX"  # Hardcoded mint address for SQR
    
    # Check if input is an SNS domain
    wallet_address = None
    display_address = input_address
    if input_address.lower().endswith('.sol') or not (input_address.startswith('1') or input_address.startswith('2') or input_address.startswith('3') or input_address.startswith('4') or input_address.startswith('5') or input_address.startswith('6') or input_address.startswith('7') or input_address.startswith('8') or input_address.startswith('9')):
        logger.info(f"Resolving SNS domain: {input_address}")
        resolved_address = await resolve_sns_domain(input_address)
        if resolved_address:
            wallet_address = resolved_address
            display_address = f"{input_address} ({wallet_address[:4]}...{wallet_address[-4:]})"
        else:
            await update.message.reply_text(
                f"❌ Could not resolve SNS domain: {input_address}",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        wallet_address = input_address
        display_address = f"{wallet_address[:4]}...{wallet_address[-4:]}"

    logger.info(f"Checking balance for wallet: {wallet_address} and token: {token_mint}")

    try:
        # Validate addresses
        try:
            wallet_pubkey = Pubkey.from_string(wallet_address)
            # Get SPL Token program ID
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            # Create a dummy keypair as payer since we're only reading data
            dummy_payer = Keypair()
            logger.info("Successfully validated addresses and created token program ID")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid wallet address format.",
                parse_mode=ParseMode.HTML
            )
            return

        # Initialize token client with program ID and payer
        token = Token(
            conn=solana_client,
            pubkey=Pubkey.from_string(token_mint),
            program_id=token_program_id,
            payer=dummy_payer
        )
        logger.info("Successfully initialized Token client")

        # Get token accounts
        token_accounts = token.get_accounts_by_owner_json_parsed(owner=wallet_pubkey)
        logger.info(f"Retrieved token accounts: {token_accounts}")

        if not token_accounts or not token_accounts.value:
            await update.message.reply_text(
                f"No token account found for this token in the wallet {display_address}",
                parse_mode=ParseMode.HTML
            )
            return

        # Get balance from the first account
        account = token_accounts.value[0]
        logger.info(f"First account data: {account}")
        
        # Access the parsed data structure correctly
        token_amount = account.account.data.parsed['info']['tokenAmount']
        logger.info(f"Token amount data: {token_amount}")
        
        balance = int(token_amount['amount'])
        decimals = token_amount['decimals']
        actual_balance = balance / (10 ** decimals)
        logger.info(f"Calculated balance: {actual_balance} (raw: {balance}, decimals: {decimals})")

        # Get token metadata using RPC directly
        try:
            logger.info("Fetching token metadata...")
            # Get token metadata from the RPC
            token_metadata = solana_client.get_account_info_json_parsed(Pubkey.from_string(token_mint))
            logger.info(f"Raw token metadata response: {token_metadata}")
            
            if token_metadata and token_metadata.value:
                logger.info(f"Token metadata value: {token_metadata.value}")
                logger.info(f"Token metadata parsed data: {token_metadata.value.data.parsed}")
                
                mint_data = token_metadata.value.data.parsed
                logger.info(f"Mint data: {mint_data}")
                
                # Find the tokenMetadata extension
                token_metadata_ext = next((ext for ext in mint_data['info']['extensions'] 
                                            if ext['extension'] == 'tokenMetadata'), None)
                
                if token_metadata_ext:
                    token_name = token_metadata_ext['state'].get('name', 'Unknown Token')
                    token_symbol = token_metadata_ext['state'].get('symbol', '???')
                    logger.info(f"Extracted token name: {token_name}, symbol: {token_symbol}")
                else:
                    logger.warning("No tokenMetadata extension found")
                    token_name = 'Unknown Token'
                    token_symbol = '???'
            else:
                logger.warning("No token metadata found in response")
                token_name = 'Unknown Token'
                token_symbol = '???'
        except Exception as e:
            logger.error(f"Error fetching token metadata: {str(e)}")
            logger.error(f"Full error traceback: {traceback.format_exc()}")
            token_name = 'Unknown Token'
            token_symbol = '???'

        await update.message.reply_text(
            f"💰 <b>Token Balance</b>\n\n"
            f"Wallet: {display_address}\n"
            f"Token: {token_name} ({token_symbol})\n"
            f"Balance: {actual_balance:,.{decimals}f} {token_symbol}\n"
            f"Mint: {token_mint[:4]}...{token_mint[-4:]}",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Error checking balance: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        await update.message.reply_text(
            "❌ Error checking balance. Please verify the addresses and try again.",
            parse_mode=ParseMode.HTML
        )

def main():
    """Start the bot."""
    try:
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
            
        # Create the Application with concurrent updates
        application = Application.builder().token(telegram_token).concurrent_updates(True).build()

        # Initialize database
        global db
        db = Database()

        # Load members after the application is created
        load_members_from_knowledge()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("about", about_command))
        application.add_handler(CommandHandler("website", website_command))
        application.add_handler(CommandHandler("contact", contact_command))
        application.add_handler(CommandHandler("events", events_command))
        application.add_handler(CommandHandler("resources", resources_command))
        application.add_handler(CommandHandler("learn", learn_command))
        application.add_handler(CommandHandler("bulk_learn", bulk_learn_command))
        application.add_handler(CommandHandler("request_member", request_member))
        application.add_handler(CommandHandler("approve_member", approve_member))
        application.add_handler(CommandHandler("reject_member", reject_member))
        application.add_handler(CommandHandler("list_requests", list_requests))
        application.add_handler(CommandHandler("learn_from_url", learn_from_url))
        application.add_handler(CommandHandler("balance", check_balance))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Start the Bot
        logger.info("Starting bot...")
        application.post_init = set_bot_commands
        
        # Start polling and set the bot ID after the bot is running
        application.run_polling(allowed_updates=Update.ALL_TYPES)

        # Store the bot ID after the application is created
        global bot_id
        bot_id = application.bot.id  # This should be safe now

    except Exception as e:
        logger.error(f"Fatal error in main(): {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

if __name__ == '__main__':
    import asyncio
    asyncio.run(main()) 