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
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solders.rpc.responses import GetTransactionResp
from solana.rpc.commitment import Commitment
from spl.token.client import Token
import base58
from solders.keypair import Keypair
from telegram.ext import ChatMemberHandler
from solders.signature import Signature
import asyncio
from gtts import gTTS
import uuid

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

# Initialize empty lists
AUTHORIZED_MEMBERS = []
MEMBERS = []
GROUP_MEMBERS = []  # Store groups where the bot is a member

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
        else:
            # Load authorized members from config.json if not found in knowledge base
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    AUTHORIZED_MEMBERS = config.get('authorized_members', [])
            except Exception as e:
                logger.error(f"Error loading authorized members from config.json: {str(e)}")
                logger.error("Falling back to empty authorized members list")
                AUTHORIZED_MEMBERS = []
        
        # Load regular members from knowledge base
        regular_members = db.get_knowledge("members")
        
        # Initialize empty set to track unique members
        unique_members = set()
        MEMBERS = []
        
        if regular_members:
            for entry in regular_members:
                try:
                    members_list = json.loads(entry[0])
                    for member in members_list:
                        # Create a unique key for each member
                        member_key = f"{member['username']}_{member['user_id']}"
                        if member_key not in unique_members:
                            unique_members.add(member_key)
                            MEMBERS.append(member)
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON for members entry: {str(e)}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing members entry: {str(e)}")
                    continue
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
        # Create a dictionary to filter out duplicates by user_id
        unique_members = {member['user_id']: member for member in MEMBERS}.values()
        
        # Save unique members
        db.store_knowledge("members", json.dumps(list(unique_members)))
    except Exception as e:
        logger.error(f"Error saving members to knowledge base: {str(e)}")

def load_groups_from_knowledge():
    """Load groups from the knowledge base."""
    global GROUP_MEMBERS
    try:
        # Check if groups are stored in the knowledge base
        groups_knowledge = db.get_knowledge("bot_groups")
        
        if groups_knowledge:
            # Initialize empty set to track unique groups by ID
            unique_groups = {}
            
            # Process each entry in the knowledge base
            for entry in groups_knowledge:
                try:
                    groups_list = json.loads(entry[0])
                    for group in groups_list:
                        # Use group ID as key to ensure uniqueness
                        if group['id'] not in unique_groups:
                            unique_groups[group['id']] = group
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON for groups entry: {str(e)}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing groups entry: {str(e)}")
                    continue
            
            # Convert unique groups dictionary to list
            GROUP_MEMBERS = list(unique_groups.values())
        else:
            GROUP_MEMBERS = []
    except Exception as e:
        logger.error(f"Error loading groups: {str(e)}")
        logger.error("Falling back to empty groups list")
        GROUP_MEMBERS = []

def save_groups_to_knowledge():
    global GROUP_MEMBERS
    """Save groups to the knowledge base."""
    try:
        # Create a dictionary to filter out duplicates by ID
        unique_groups = {group['id']: group for group in GROUP_MEMBERS}.values()
        
        # Save unique groups
        db.store_knowledge("bot_groups", json.dumps(list(unique_groups)))
    except Exception as e:
        logger.error(f"Error saving groups to knowledge base: {str(e)}")

def delete_groups_from_knowledge():
    global GROUP_MEMBERS
    """Delete all groups from the knowledge base and save the current GROUP_MEMBERS list."""
    try:
        # First, delete all existing bot_groups entries
        db.cursor.execute('''
            DELETE FROM knowledge_base
            WHERE topic = 'bot_groups'
        ''')
        db.conn.commit()
        
        # Now save the current GROUP_MEMBERS list (which already has the group removed)
        # Create a dictionary to filter out duplicates by ID
        unique_groups = {group['id']: group for group in GROUP_MEMBERS}.values()
        
        # Save unique groups
        db.store_knowledge("bot_groups", json.dumps(list(unique_groups)))
    except Exception as e:
        logger.error(f"Error updating groups in knowledge base: {str(e)}")

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
    
    # Reset user data when /start is issued
    context.user_data['awaiting_signature'] = False
    context.user_data['command_start_time'] = None
    context.user_data['space_url'] = None
    context.user_data['request_type'] = None
    context.user_data['job_id'] = None
    context.user_data['failed_attempts'] = 0

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
    is_authorized = find_authorized_member_by_username(user['username'])
    is_regular_member = find_member_by_username(user['username'])
    
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

async def check_transaction_status(signature: str, command_start_time: datetime, space_url: str = None, request_type: str = 'text') -> Tuple[bool, str, Optional[str]]:
    """Check if a Solana transaction was successful, completed within the deadline, and has correct amount.
    
    Args:
        signature (str): The transaction signature to check
        command_start_time (datetime): When the command was initiated
        space_url (str): The Twitter Space URL to summarize
        request_type (str): The type of request ('text' or 'audio')
        
    Returns:
        Tuple[bool, str, Optional[str]]: (True if all checks pass, error message if any check fails, job_id if space download was initiated)
    """
    try:
        # Validate signature format
        if not signature or len(signature) < 32:
            logger.warning("Invalid signature format: too short or empty")
            return False, "❌ Invalid transaction signature format", None
            
        # Convert signature string to Signature object
        try:
            signature_obj = Signature.from_string(signature)
        except Exception as e:
            logger.error(f"Error converting signature format: {str(e)}")
            return False, f"❌ Error converting signature format: {str(e)}", None
            
        # Get transaction details according to Solana RPC spec
        response = solana_client.get_transaction(
            signature_obj,
            encoding="jsonParsed",  # Use jsonParsed for better token balance parsing
        )
        
        if not response or not response.value:
            logger.error("No transaction data found in response")
            return False, "❌ No transaction data found in response", None
            
        # Access the transaction data structure correctly
        transaction_data = response.value.transaction
        meta = transaction_data.meta
        
        # Check if transaction was successful
        if not meta:
            logger.error("No meta data found in transaction")
            return False, "❌ No meta data found in transaction", None
            
        if meta.err:
            logger.error(f"Transaction failed with error: {meta.err}")
            return False, f"❌ Transaction failed: {meta.err}", None
            
        # Get block time from transaction
        if not response.value.block_time:
            logger.error("No block time found in transaction")
            return False, "❌ No block time found in transaction", None
            
        # Convert block time to datetime
        transaction_time = datetime.fromtimestamp(response.value.block_time)
        
        # Check if transaction was completed within the 30-minute window
        time_diff = transaction_time - command_start_time
        
        if time_diff < timedelta(0):
            logger.warning("Transaction was completed before command was issued")
            return False, "❌ Transaction was completed before the command was issued", None
        elif time_diff > timedelta(minutes=30):
            minutes_late = int((time_diff - timedelta(minutes=30)).total_seconds() / 60)
            logger.warning(f"Transaction was completed {minutes_late} minutes after deadline")
            return False, f"❌ Transaction was completed {minutes_late} minutes after the 30-minute window expired", None
            
        # Check token amount using pre and post token balances
        try:
            # Get pre and post token balances
            pre_balances = meta.pre_token_balances
            post_balances = meta.post_token_balances
            
            if not pre_balances or not post_balances:
                logger.error("No token balance information found in transaction")
                return False, "❌ No token balance information found in transaction", None
            
            # Find the token transfer amount by comparing pre and post balances
            transfer_amount = 0
            target_mint = "CsZmZ4fz9bBjGRcu3Ram4tmLRMmKS6GPWqz4ZVxsxpNX"
            
            # First find the token account that received the tokens
            for post_balance in post_balances:
                if str(post_balance.mint) == target_mint:
                    # Find the corresponding pre-balance for this account
                    pre_balance = next(
                        (pre for pre in pre_balances 
                         if str(pre.mint) == target_mint and pre.account_index == post_balance.account_index),
                        None
                    )
                    
                    if pre_balance:
                        # Calculate the actual transfer amount (post - pre)
                        pre_amount = float(pre_balance.ui_token_amount.ui_amount_string)
                        post_amount = float(post_balance.ui_token_amount.ui_amount_string)
                        transfer_amount = post_amount - pre_amount
                        break
            
            # Set required amount based on request type
            required_amount = 2000 if request_type == 'audio' else 1000
            
            if transfer_amount <= 0:
                logger.warning(f"Invalid transfer amount: {transfer_amount}")
                return False, f"❌ No valid token transfer found or insufficient amount: {transfer_amount} (minimum required: {required_amount})", None
                
            if transfer_amount < required_amount:
                logger.warning(f"Insufficient transfer amount: {transfer_amount}")
                return False, f"❌ Insufficient token amount: {transfer_amount} (minimum required: {required_amount})", None
                
        except Exception as e:
            logger.error(f"Error checking token amount: {str(e)}")
            logger.error(f"Full error traceback: {traceback.format_exc()}")
            return False, "❌ Error verifying token amount in transaction", None
            
        # If we have a space URL and all checks passed, process the space summarization
        if space_url:
            try:
                # Get API key from environment variables
                api_key = os.getenv('SQR_FUND_API_KEY')
                if not api_key:
                    logger.error("SQR_FUND_API_KEY not found in environment variables")
                    return False, "❌ API key not configured", None

                # First, download the space
                download_response = requests.post(
                    "https://spaces.sqrfund.ai/api/async/download-spaces",
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": api_key
                    },
                    json={
                        "spacesUrl": space_url
                    }
                )

                if download_response.status_code != 202:
                    logger.error(f"Failed to initiate space download: {download_response.text}")
                    return False, f"❌ Failed to initiate space download: {download_response.text}", None

                # Get the job ID from the response
                try:
                    job_data = download_response.json()
                    job_id = job_data.get('jobId')
                    if not job_id:
                        logger.error("No job ID received from download request")
                        return False, "❌ No job ID received from download request", None
                except Exception as e:
                    logger.error(f"Error parsing download response: {str(e)}")
                    return False, f"❌ Error parsing download response: {str(e)}", None

                # Return message about download in progress
                return True, (
                    "✅ Transaction verified successfully!\n\n"
                    "🔄 Space download initiated. This may take a few minutes.\n"
                    "Please wait while we process your request..."
                ), job_id
                    
            except Exception as e:
                logger.error(f"Error processing space: {str(e)}")
                logger.error(f"Full error traceback: {traceback.format_exc()}")
                return False, f"❌ Error processing space: {str(e)}", None
            
        return True, "✅ Transaction verified successfully!", None
        
    except Exception as e:
        logger.error(f"Error checking transaction status: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        return False, f"❌ Error checking transaction status: {str(e)}", None

async def check_job_status(job_id: str, space_url: str) -> Tuple[bool, str]:
    """Check the status of a space download job and proceed with summarization if complete.
    
    Args:
        job_id (str): The ID of the download job to check
        space_url (str): The Twitter Space URL to summarize
        
    Returns:
        Tuple[bool, str]: (True if job is complete and summary is ready, error message if any step fails)
    """
    try:
        logger.info(f"Checking job status for job_id: {job_id}")
        api_key = os.getenv('SQR_FUND_API_KEY')
        if not api_key:
            logger.error("SQR_FUND_API_KEY not found in environment variables")
            return False, "❌ API key not configured"

        # Check job status
        status_url = f"https://spaces.sqrfund.ai/api/jobs/{job_id}"
        
        logger.info(f"Sending request to check job status at {status_url}")
        status_response = requests.get(
            status_url,
            headers={
                "X-API-Key": api_key
            }
        )

        logger.info(f"Received response with status code: {status_response.status_code}")
        
        if status_response.status_code == 502:
            logger.error("Received 502 Server Error from API")
            return False, (
                "⚠️ <b>Service Temporarily Unavailable</b>\n\n"
                "We're experiencing high demand or temporary service issues.\n"
                "Please wait a few minutes and try again.\n\n"
                "If the issue persists, you can:\n"
                "• Try again later\n"
                "• Contact support at dev@sqrfund.ai"
            )

        if status_response.status_code != 200:
            logger.error(f"Failed to check job status. Status code: {status_response.status_code}, Response: {status_response.text}")
            return False, f"❌ Failed to check job status: {status_response.text}"

        job_status = status_response.json()
        logger.info(f"Job status response: {job_status}")
        
        # Access the nested status field from the job object
        status = job_status.get('job', {}).get('status')

        if status == 'completed':
            logger.info("Job completed successfully, proceeding with summarization")
            # Proceed with summarization
            summary_url = "https://spaces.sqrfund.ai/api/summarize-spaces"
            
            summary_response = requests.post(
                summary_url,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key
                },
                json={
                    "spacesUrl": space_url,
                    "promptType": "formatted"
                }
            )

            logger.info(f"Received response for summarization request: {summary_response.status_code}")
            
            if summary_response.status_code == 502:
                logger.error("Received 502 Server Error during summarization")
                return False, (
                    "⚠️ <b>Summarization Service Temporarily Unavailable</b>\n\n"
                    "We're experiencing high demand or temporary service issues.\n"
                    "Please wait a few minutes and try again.\n\n"
                    "If the issue persists, you can:\n"
                    "• Try again later\n"
                    "• Contact support at dev@sqrfund.ai"
                )

            if summary_response.status_code == 200:
                summary_data = summary_response.json()
                logger.info(f"Summarization response: {summary_data}")
                return True, summary_data.get('summary', '✅ Space summarized successfully!')
            else:
                logger.error(f"Failed to summarize space. Status code: {summary_response.status_code}, Response: {summary_response.text}")
                return False, f"❌ Failed to summarize space: {summary_response.text}"
        elif status == 'failed':
            error_msg = job_status.get('job', {}).get('error', 'Unknown error')
            logger.error(f"Space download failed with error: {error_msg}")
            
            # Provide more user-friendly error messages for common issues
            if "yt-dlp process exited with code 1" in error_msg:
                return False, (
                    "❌ Failed to download the Space. This could be due to:\n"
                    "• The Space URL is invalid or no longer available\n"
                    "• The Space is private or restricted\n"
                    "• The Space has been deleted\n"
                    "• Technical issues with the Space download\n\n"
                    "Please verify the Space URL and try again."
                )
            else:
                return False, f"❌ Space download failed: {error_msg}"
        elif status == 'processing':
            logger.info("Job is still processing, will check again later")
            await asyncio.sleep(180)  # Wait for 180 seconds
            return await check_job_status(job_id, space_url)  # Recursive call to check again
        else:
            logger.warning(f"Unexpected job status: {status}")
            return False, "🔄 Space download is still in progress. Please wait..."

    except Exception as e:
        logger.error(f"Error checking job status: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        return False, f"❌ Error checking job status: {str(e)}"

async def text_to_audio(text: str, language: str = 'en') -> Tuple[Optional[str], Optional[str]]:
    """Convert text to audio using Google Text-to-Speech.
    
    Args:
        text (str): The text to convert to audio
        language (str): The language code (default: 'en' for English)
        
    Returns:
        Tuple[Optional[str], Optional[str]]: (audio file path, error message if any)
    """
    try:
        # Create a temporary directory if it doesn't exist
        temp_dir = os.path.join(os.getcwd(), 'temp_audio')
        os.makedirs(temp_dir, exist_ok=True)
        
        # Generate a unique filename
        filename = f"space_summary_{uuid.uuid4()}.mp3"
        filepath = os.path.join(temp_dir, filename)
        
        # Convert text to speech
        tts = gTTS(text=text, lang=language, slow=False)
        tts.save(filepath)
        
        return filepath, None
    except Exception as e:
        logger.error(f"Error converting text to audio: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        return None, f"Error converting text to audio: {str(e)}"

def escape_markdown_v2(text):
    """Escape special characters for Telegram's Markdown V2 format and handle bold and bullet points."""
    if text is None:
        return ""
    
    # Handle bold text
    # Replace *text* with <b>text</b>
    text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)

    # Handle bullet points
    # Replace lines starting with * and ending with a line break
    text = re.sub(r'^\*\s*(.*)', r'• \1', text, flags=re.MULTILINE)

    # Escape other special characters
    chars_to_escape = ['_', '[', ']', '(', ')', '~', '`', '>', '#', '+', 
                       '-', '=', '|', '{', '}', '.', '!']
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    return text

async def generate_and_send_audio(context, chat_id, message, request_type):
    if request_type == 'audio':
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎧 Audio version is being generated and will be sent shortly...",
            parse_mode=ParseMode.HTML
        )
        
        # Add timeout to prevent indefinite waiting
        try:
            task = asyncio.create_task(generate_audio_and_notify(context, chat_id, message))
            # Set a reasonable timeout (e.g., 10 minutes)
            await asyncio.wait_for(task, timeout=600)
        except asyncio.TimeoutError:
            logger.error("Audio generation timed out after 10 minutes")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Audio generation is taking longer than expected. You'll receive it when ready.",
                parse_mode=ParseMode.HTML
            )

async def generate_audio_and_notify(context, chat_id, message):
    # More robust markdown/formatting removal
    plain_text = re.sub(r'\*\*?|__|`|~~|\[.*?\]\(.*?\)', '', message)
    # Remove remaining special characters that might affect speech synthesis
    plain_text = re.sub(r'[^\w\s.,?!;:()\-"\']+', ' ', plain_text)
    
    # Define a reasonable chunk size for audio files
    max_chunk_size = 10000
    if len(plain_text) > max_chunk_size:
        logger.warning(f"Text split into multiple parts for audio generation: {len(plain_text)} characters")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"ℹ️ Text is quite long and will be split into {(len(plain_text) // max_chunk_size) + 1} audio files.",
            parse_mode=ParseMode.HTML
        )
     
        # Split text into chunks and generate audio for each
        text_chunks = [plain_text[i:i+max_chunk_size] for i in range(0, len(plain_text), max_chunk_size)]
        for i, chunk in enumerate(text_chunks):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎧 Generating audio part {i+1}/{len(text_chunks)}...",
                parse_mode=ParseMode.HTML
            )
            audio_filepath, error = await text_to_audio(chunk)
         
            if audio_filepath and not error:
                try:
                    with open(audio_filepath, 'rb') as audio_file:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=audio_file,
                            caption=f"🎧 Audio part {i+1}/{len(text_chunks)} of the Space summary",
                            title=f"Space Summary Part {i+1}",
                            performer="sqrAI"
                        )
                except Exception as e:
                    logger.error(f"Failed to send audio file: {str(e)}")
                    logger.error(f"Full error traceback: {traceback.format_exc()}")
                finally:
                    try:
                        os.remove(audio_filepath)
                        logger.info("Cleaned up temporary audio file")
                    except Exception as e:
                        logger.error(f"Failed to remove temporary audio file: {str(e)}")
        return

async def periodic_job_check(context: ContextTypes.DEFAULT_TYPE, job_id: str, space_url: str, chat_id: int, message_id: int, request_type: str = 'text', max_attempts: int = 30):
    """Periodically check job status and update the user.
    
    Args:
        context: The context object from the application
        job_id: The ID of the download job to check
        space_url: The Twitter Space URL to summarize
        chat_id: The chat ID to send updates to
        message_id: The message ID to update
        request_type: The type of request ('text' or 'audio')
        max_attempts: Maximum number of attempts (default 30 = 5 minutes)
    """
    logger.info(f"Starting periodic job check for job_id: {job_id}, space_url: {space_url}, request_type: {request_type}")
    attempt = 0
    while attempt < max_attempts:
        logger.info(f"Checking job status - Attempt {attempt + 1}/{max_attempts}")
        is_complete, message = await check_job_status(job_id, space_url)
        
        if is_complete:
            logger.info("Job completed successfully, preparing to send response")
            try:
                # Split long messages into chunks of 4000 characters (Telegram's limit is 4096)
                message_chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
                logger.info(f"Split message into {len(message_chunks)} chunks")
                
                # Send each chunk
                for i, chunk in enumerate(message_chunks):
                    logger.info(f"Sending chunk {i + 1}/{len(message_chunks)}")
                    # Escape special characters for Markdown V2
                    escaped_chunk = chunk.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')
                    
                    try:
                        if i == 0:
                            # First chunk updates the original message
                            logger.info("Updating original message with first chunk")
                            await context.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=escaped_chunk,
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        else:
                            # Additional chunks as new messages
                            logger.info("Sending additional chunk as new message")
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=escaped_chunk,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_to_message_id=message_id  # Link to the original message
                            )
                    except telegram.error.BadRequest as e:
                        # Handle specific Telegram API errors
                        logger.error(f"Error sending message chunk {i+1}: {str(e)}")
                        # Try sending without markdown if parsing fails
                        if "can't parse entities" in str(e).lower():
                            plain_chunk = chunk  # Use non-escaped version
                            logger.info("Retrying without markdown parsing")
                            if i == 0:
                                await context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    text=plain_chunk
                                )
                            else:
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=plain_chunk,
                                    reply_to_message_id=message_id
                                )
                
                # Start audio generation in background if requested
                if request_type == 'audio':
                    await generate_and_send_audio(context, chat_id, message, request_type)
                
                logger.info("Successfully completed periodic job check")
                return True
            except Exception as e:
                logger.error(f"Failed to send summary message: {str(e)}")
                logger.error(f"Full error traceback: {traceback.format_exc()}")
                return False
        
        # Update the status message
        try:
            logger.info(f"Updating status message: {message}")
            # Escape special characters for Markdown V2
            escaped_message = escape_markdown_v2(message)
            
            # Split long messages into chunks
            status_message = f"{escaped_message}\n\n⏳ Checking again in 180 seconds..."
            if len(status_message) > 4000:
                status_message = status_message[:3997] + "..."
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=status_message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error updating status message: {str(e)}")
            logger.error(f"Full error traceback: {traceback.format_exc()}")
        
        # Wait for 180 seconds before next check
        logger.info("Waiting 180 seconds before next check")
        await asyncio.sleep(180)
        attempt += 1
    
    # If we've reached max attempts, send a timeout message
    logger.warning(f"Reached maximum attempts ({max_attempts}) without completion")
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=escape_markdown_v2("❌ Timeout: Space processing took too long. Please try again later."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Failed to send timeout message: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    try:
        message = update.message
        if not message or not message.text:
            return

        # First check if we're awaiting a signature for space summarization
        if context.user_data.get('awaiting_signature'):
            # Only process summarize_space command status in private chats
            if update.message.chat.type != 'private':
                await update.message.reply_text(
                    "⚠️ This command is only available in private chats.",
                    parse_mode=ParseMode.HTML
                )
                return

            command_start_time = context.user_data.get('command_start_time')
            space_url = context.user_data.get('space_url')
            request_type = context.user_data.get('request_type', 'text')  # Default to 'text' if not set
            
            if not command_start_time or (datetime.now() - command_start_time) > timedelta(minutes=30):
                await message.reply_text(
                    "❌ Time limit expired!\n"
                    "The 30-minute window for completing the transaction has passed.\n"
                    "Please use /summarize_space command again to start a new transaction.",
                    parse_mode=ParseMode.HTML
                )
                context.user_data['awaiting_signature'] = False
                context.user_data['command_start_time'] = None
                context.user_data['space_url'] = None
                context.user_data['request_type'] = None
                context.user_data['job_id'] = None
                context.user_data['failed_attempts'] = 0
                return

            signature = message.text.strip()
            
            is_successful, message_text, job_id = await check_transaction_status(signature, command_start_time, space_url, request_type)
            
            if is_successful:
                # Send initial status message
                status_message = await message.reply_text(
                    "✅ Transaction verified successfully!\n"
                    "Processing your request...\n"
                    "This can take up to 5-10 minutes.",
                    parse_mode=ParseMode.HTML
                )
                
                # If we have a job ID, start periodic checking
                if job_id:
                    # Store the job_id in user_data
                    context.user_data['job_id'] = job_id
                    # Start the periodic check in the background
                    asyncio.create_task(periodic_job_check(
                        context=context,
                        job_id=job_id,
                        space_url=space_url,
                        chat_id=message.chat_id,
                        message_id=status_message.message_id,
                        request_type=request_type
                    ))
                else:
                    await message.reply_text(message_text, parse_mode=ParseMode.HTML)
            else:
                # Increment failed attempts counter
                failed_attempts = context.user_data.get('failed_attempts', 0) + 1
                context.user_data['failed_attempts'] = failed_attempts
                
                if failed_attempts >= 3:
                    await message.reply_text(
                        "❌ Maximum number of failed attempts reached.\n"
                        "Please use /summarize_space command again to start a new transaction.",
                        parse_mode=ParseMode.HTML
                    )
                    context.user_data['awaiting_signature'] = False
                    context.user_data['command_start_time'] = None
                    context.user_data['space_url'] = None
                    context.user_data['request_type'] = None
                    context.user_data['job_id'] = None
                    context.user_data['failed_attempts'] = 0
                else:
                    remaining_attempts = 3 - failed_attempts
                    required_amount = 2000 if request_type == 'audio' else 1000
                    await message.reply_text(
                        f"{message_text}\n\n"
                        f"Please ensure you:\n"
                        f"1. Send exactly {required_amount} $SQR tokens\n"
                        f"2. Complete the transaction within 30 minutes\n"
                        f"3. Send the correct transaction signature\n\n"
                        f"⚠️ You have {remaining_attempts} attempt{'s' if remaining_attempts > 1 else ''} remaining.",
                        parse_mode=ParseMode.HTML
                    )
            return

        # Only proceed with other message handling if we're not awaiting a signature
        # Check for scammer accusations in any chat
        if "scammer" in message.text.lower():
            await message.reply_text("🚫 Rome wasn't built in one day! Building something meaningful takes time and dedication. Let's support our founders who are working hard to create value! 💪")
            return

        # Check if the message is a command
        if message.text.startswith('/'):
            await message.reply_text(
                "<i>Please use specific commands like /help, or send a regular message.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        # For group chats, ignore all other messages
        if message.chat.type != 'private':
            return

        # Only process conversational features in private chats
        # Check if the message mentions the bot
        if context.bot.username in message.text:
            # Process the message as a normal user message
            message.text = message.text.replace(f"@{context.bot.username}", "").strip()
            
            if not message.text:
                await message.reply_text(
                    "<i>I couldn't process an empty message. Please send some text.</i>",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Get relevant context from previous conversations
            relevant_context = db.get_relevant_context(update.effective_user.id, message.text)
            
            # Process the message with context
            response = process_message_with_context(message.text, relevant_context)
            
            # Store the conversation
            db.store_conversation(update.effective_user.id, message.text, response)
            
            # Format and send response with HTML formatting
            formatted_text = format_response_for_telegram(response)
            
            await message.reply_text(
                formatted_text,
                parse_mode=ParseMode.HTML
            )
            return

        # Check if this is a template request
        if message.text.lower().strip() == "template":
            try:
                with open('template.csv', 'rb') as template_file:
                    await message.reply_document(
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
                await message.reply_text(
                    "❌ Sorry, I couldn't send the template file. Please try again later."
                )
                return
        
        try:
            # Get relevant context from previous conversations
            relevant_context = db.get_relevant_context(update.effective_user.id, message.text)
            
            # Process the message with context
            response = process_message_with_context(message.text, relevant_context)
            
            # Store the conversation
            db.store_conversation(update.effective_user.id, message.text, response)
            
            # Format and send response with HTML formatting
            formatted_text = format_response_for_telegram(response)
            
            await message.reply_text(
                formatted_text,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await message.reply_text(
                "<i>I encountered an error while processing your message. Please try again.</i>",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error handling message: {str(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        if message.chat.type == 'private':  # Only send error messages in private chats
            await message.reply_text(
                "<i>I encountered an error while processing your message. Please try again.</i>",
                parse_mode=ParseMode.HTML
            )

async def set_bot_commands(application):
    """Set bot commands with descriptions for the command menu."""
    # Basic commands for all users
    basic_commands = [
        ("start", "Start the bot and get welcome message"),
        ("help", "Show help and list of available commands"),
        ("about", "Learn about sqrDAO and sqrFUND"),
        ("website", "Get sqrDAO's and sqrFUND's website"),
        ("contact", "Get contact information"),
        ("events", "View sqrDAO events"),
        ("balance", "Check $SQR token balance"),  # Added balance command
        ("sqr_info", "Get information about $SQR token"),
        ("request_member", "Request to become a member")
    ]
    
    # Commands for regular members
    member_commands = basic_commands + [
        ("resources", "Access internal resources for sqrDAO Members and sqrFUND Chads")
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
    about_text = "<b>About sqrDAO:</b>\n\n"
    if knowledge:
        about_text += knowledge[0][0]
    else:
        about_text = "sqrDAO is a Web3 builders-driven community in Vietnam and Southeast Asia, created by and for crypto builders. We connect and empower both technical and non-technical builders to collaborate, explore new ideas, and BUIDL together."
    
    about_text += "\n\n<b>About sqrFUND:</b>\n\n"
    knowledge = db.get_knowledge("sqrfund")
    if knowledge:
        about_text += knowledge[0][0]
    else:
        about_text += "sqrFUND, incubated by sqrDAO, is a Web3 + AI development DAO that combines Web3 builders' expertise with AI-powered data analytics to create intelligent DeFAI trading and market analysis agents."
    await update.message.reply_text(about_text, parse_mode=ParseMode.HTML)

async def website_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /website command."""
    knowledge = db.get_knowledge("website")
    if knowledge:
        website_text = knowledge[0][0]
    else:
        website_text = "Visit sqrDAO at https://sqrdao.com"

    knowledge = db.get_knowledge("sqrfund")
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

async def get_sqr_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get information about SQR token including prices from GeckoTerminal."""
    try:
        # SQR token address on Solana
        token_address = "CsZmZ4fz9bBjGRcu3Ram4tmLRMmKS6GPWqz4ZVxsxpNX" # Can be changed to other token address
        
        # GeckoTerminal API endpoint for token info
        url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{token_address}"
        
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            token_data = data.get('data', {}).get('attributes', {})
            
            # Extract relevant information
            price_usd = token_data.get('price_usd', 'N/A')
            price_change_24h = token_data.get('price_change_24h', 'N/A')
            volume_24h = token_data.get('volume_24h', 'N/A')
            market_cap = token_data.get('market_cap_usd', 'N/A')
            
            # Format numeric values if they exist
            try:
                volume_24h = f"${float(volume_24h):,.2f}" if volume_24h != 'N/A' else 'N/A'
            except (ValueError, TypeError):
                volume_24h = 'N/A'
                
            try:
                market_cap = f"${float(market_cap):,.2f}" if market_cap != 'N/A' else 'N/A'
            except (ValueError, TypeError):
                market_cap = 'N/A'
            
            # Format the message
            message = (
                "🪙 <b>SQR Token Information</b>\n\n"
                f"💰 Price: ${price_usd}\n"
                f"📈 24h Change: {price_change_24h}%\n"
                f"📊 24h Volume: {volume_24h}\n"
                f"💎 Market Cap: {market_cap}\n\n"
                "Data provided by GeckoTerminal\n\n"
                "<a href='https://t.me/bonkbot_bot?start=ref_j03ne'>Buy SQR on Bonkbot</a>\n"
            )
            
            await update.message.reply_text(message, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("❌ Sorry, I couldn't fetch SQR token information at the moment. Please try again later.")
            
    except Exception as e:
        logging.error(f"Error fetching SQR info: {str(e)}")
        await update.message.reply_text("❌ An error occurred while fetching SQR token information. Please try again later.")

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
<b>sqrDAO Members' and sqrFUND Chads' Resources</b>

Here are our internal resources:
• <b>GitHub:</b> https://github.com/sqrdao
• <b>AWS Credits Guide:</b> https://drive.google.com/file/d/12DjM2P5x0T_koLI6o_UMXMo_LUJpYrXL/view?usp=sharing
• <b>AWS Org ID ($10K):</b> 3Ehcy
• <b>Legal Service (20% off):</b> https://teamoutlaw.io/
• <b>sqrDAO & sqrFUND Brand Kit:</b> https://sqrdao.notion.site/sqrdao-brand-kit
• <b>$SQR CHADS TG group:</b> https://t.me/+Yh6VkC81BdljZDg1

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
            "• /balance 2uWgfTebL5xfhFPJwguVuRfidgUAvjUX1vZRepZgZym9\n"
            "• /balance castelian.sol",
            parse_mode=ParseMode.HTML
        )
        return

    input_address = context.args[0]
    token_mint = "CsZmZ4fz9bBjGRcu3Ram4tmLRMmKS6GPWqz4ZVxsxpNX"  # Hardcoded mint address for $SQR token
    
    # Check if input is an SNS domain
    wallet_address = None
    display_address = input_address
    if input_address.lower().endswith('.sol') or not (input_address.startswith('1') or input_address.startswith('2') or input_address.startswith('3') or input_address.startswith('4') or input_address.startswith('5') or input_address.startswith('6') or input_address.startswith('7') or input_address.startswith('8') or input_address.startswith('9')):
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

    try:
        # Validate addresses
        try:
            wallet_pubkey = Pubkey.from_string(wallet_address)
            # Get SPL Token program ID
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            # Create a dummy keypair as payer since we're only reading data
            dummy_payer = Keypair()
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

        # Get token accounts
        token_accounts = token.get_accounts_by_owner_json_parsed(owner=wallet_pubkey)
        
        if not token_accounts or not token_accounts.value:
            await update.message.reply_text(
                f"No token account found for this token in the wallet {display_address}",
                parse_mode=ParseMode.HTML
            )
            return

        # Get balance from the first account
        account = token_accounts.value[0]
        
        # Access the parsed data structure correctly
        token_amount = account.account.data.parsed['info']['tokenAmount']
        
        balance = int(token_amount['amount'])
        decimals = token_amount['decimals']
        actual_balance = balance / (10 ** decimals)

        # Get token metadata using RPC directly
        try:
            # Get token metadata from the RPC
            token_metadata = solana_client.get_account_info_json_parsed(Pubkey.from_string(token_mint))
            
            if token_metadata and token_metadata.value:
                mint_data = token_metadata.value.data.parsed
                
                # Find the tokenMetadata extension
                token_metadata_ext = next((ext for ext in mint_data['info']['extensions'] 
                                            if ext['extension'] == 'tokenMetadata'), None)
                
                if token_metadata_ext:
                    token_name = token_metadata_ext['state'].get('name', 'Unknown Token')
                    token_symbol = token_metadata_ext['state'].get('symbol', '???')
                else:
                    token_name = 'Unknown Token'
                    token_symbol = '???'
            else:
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

@is_member
async def list_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_members command - List all members."""
    if not MEMBERS:
        await update.message.reply_text(
            "📝 No members found.",
            parse_mode=ParseMode.HTML
        )
        return
    
    members_text = "<b>Current Members:</b>\n\n"
    for member in MEMBERS:
        members_text += f"• @{member['username']} (User ID: {member['user_id']})\n"
    
    await update.message.reply_text(members_text, parse_mode=ParseMode.HTML)

# Add this handler to detect when bot is added to or removed from groups
async def handle_group_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added to or removed from a group or channel, or when a group is migrated to a supergroup."""
    global GROUP_MEMBERS  # Add global declaration
    
    # Check for my_chat_member updates
    if update.my_chat_member and update.my_chat_member.chat.type in ['group', 'supergroup', 'channel']:
        chat = update.my_chat_member.chat
        new_status = update.my_chat_member.new_chat_member.status if update.my_chat_member.new_chat_member else None
        
        logger.debug(f"Group/Channel status update: {chat.title} (ID: {chat.id}) - New Status: {new_status}")

        # Bot was added to a group or channel
        if new_status in ['member', 'administrator']:
            if not any(g['id'] == chat.id for g in GROUP_MEMBERS):
                GROUP_MEMBERS.append({
                    'id': chat.id,
                    'title': chat.title,
                    'type': chat.type,  # Ensure type is captured
                    'added_at': datetime.now().isoformat()
                })
                save_groups_to_knowledge()
        
        # Bot was removed from a group or channel
        elif new_status in ['left', 'kicked']:
            # Remove the group from GROUP_MEMBERS
            GROUP_MEMBERS = [g for g in GROUP_MEMBERS if g['id'] != chat.id]
            logger.debug(f"Successfully removed group/channel: {chat.title} (ID: {chat.id}) from GROUP_MEMBERS.")
            
            # Delete all groups from knowledge base and save the updated GROUP_MEMBERS
            delete_groups_from_knowledge()
            
            # Reload groups from knowledge base to ensure consistency
            # load_groups_from_knowledge()

# Replace the get_bot_groups function with this simpler version
async def get_bot_groups(context: ContextTypes.DEFAULT_TYPE) -> List[dict]:
    """Get all groups where the bot is a member."""
    # First check if the current chat is a group and not in our list
    try:
        if hasattr(context, 'message') and context.message and context.message.chat:
            chat = context.message.chat
            if chat.type in ['group', 'supergroup'] and not any(g['id'] == chat.id for g in GROUP_MEMBERS):
                GROUP_MEMBERS.append({
                    'id': chat.id,
                    'title': chat.title,
                    'type': chat.type,
                    'added_at': datetime.now().isoformat()
                })
                save_groups_to_knowledge()
    except Exception as e:
        logger.error(f"Error checking current chat: {str(e)}")
    
    return GROUP_MEMBERS

# Add this command to manually add a group
@is_member
async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_group command - Manually add a group ID."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a group ID to add.\n"
            "Usage: /add_group [group_id] [group_name]\n"
            "Example: /add_group -1001234567890 My Group\n\n"
            "<b>How to find a group ID:</b>\n"
            "1. Add @username_to_id_bot to your group\n"
            "2. Send /id in the group\n"
            "3. The bot will reply with the group ID\n"
            "4. Use that ID with this command",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        group_id = int(context.args[0])
        group_name = " ".join(context.args[1:]) if len(context.args) > 1 else f"Group {group_id}"
        
        # Check if group already exists
        if any(g['id'] == group_id for g in GROUP_MEMBERS):
            await update.message.reply_text(
                f"⚠️ Group with ID {group_id} is already in the list.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Add the group
        GROUP_MEMBERS.append({
            'id': group_id,
            'title': group_name,
            'type': 'group',  # Default to group, we don't know if it's a supergroup
            'added_at': datetime.now().isoformat(),
            'added_by': update.effective_user.username
        })
        save_groups_to_knowledge()
        
        await update.message.reply_text(
            f"✅ Successfully added group: {group_name} ({group_id})",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid group ID. Please provide a valid numerical ID.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error adding group: {str(e)}")
        await update.message.reply_text(
            f"❌ Error adding group: {str(e)}",
            parse_mode=ParseMode.HTML
        )

# Add this command to list all groups
@is_member
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_groups command - List all tracked groups and channels."""
    global GROUP_MEMBERS

    if not GROUP_MEMBERS:
        await update.message.reply_text(
            "📝 No groups or channels found.",
            parse_mode=ParseMode.HTML
        )
        return

    groups_text = "<b>Current Groups and Channels:</b>\n\n"
    for group in GROUP_MEMBERS:
        # Escape special characters in title
        title = group['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        group_id = group['id']
        group_type = group['type']
        added_by = group.get('added_by', 'system')
        
        # Only add the "by @username" part if there's a username
        added_by_text = f" (by @{added_by})" if added_by != 'system' else ""
        
        # Build the line with proper HTML escaping
        groups_text += f"• {title} ({group_id}) - {group_type}{added_by_text}\n"
    
    try:
        await update.message.reply_text(groups_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error sending groups list: {str(e)}")
        # Fallback to plain text if HTML parsing fails
        plain_text = groups_text.replace('<b>', '').replace('</b>', '')
        await update.message.reply_text(plain_text)

# Add this command to remove a group
@is_member
async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove_group command - Remove a group ID."""
    global GROUP_MEMBERS  # Add global declaration
    
    if not context.args:
        await update.message.reply_text(
            "<b>❌ Please provide a group ID to remove.</b>\n"
            "Usage: /remove_group [group_id]\n"
            "Example: /remove_group -1001234567890\n\n"
            "Use /list_groups to see all tracked groups.",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        group_id = int(context.args[0])
        
        # Remove all groups with the specified ID
        initial_count = len(GROUP_MEMBERS)
        GROUP_MEMBERS = [g for g in GROUP_MEMBERS if g['id'] != group_id]
        removed_count = initial_count - len(GROUP_MEMBERS)

        if removed_count > 0:
            # Update the knowledge base with the new GROUP_MEMBERS list
            delete_groups_from_knowledge()
            await update.message.reply_text(
                f"<b>✅ Successfully removed {removed_count} group(s) with ID:</b> {group_id}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"<b>⚠️ No group found with ID {group_id}.</b>",
                parse_mode=ParseMode.HTML
            )
    except ValueError:
        await update.message.reply_text(
            "<b>❌ Invalid group ID. Please provide a valid numerical ID.</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error removing group: {str(e)}")
        await update.message.reply_text(
            f"<b>❌ Error removing group:</b> {str(e)}",
            parse_mode=ParseMode.HTML
        )

@is_member
async def mass_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mass_message command - Send a message with optional image to all users and groups."""
    # Check if there's an image attached
    photo = None
    caption = None
    grouptype = None
    
    # Check if there are enough arguments
    if len(context.args) < 1:  # At least a message and a grouptype
        await update.message.reply_text(
            "❌ Please provide a message and an optional grouptype.\n"
            "Usage:\n"
            "• /mass_message [message] | [grouptype]\n"
            "• Example: /mass_message Hello everyone | sqrdao\n"
            "If grouptype is 'sqrdao', the message will only be sent to groups/channels with 'sqrdao' in their title.",
            parse_mode=ParseMode.HTML
        )
        return

    # Check if the separator is present in the arguments
    if "|" in context.args:
        # Split the arguments into message parts and grouptype
        separator_index = context.args.index("|")
        message_parts = context.args[:separator_index]  # All arguments before the separator
        grouptype = context.args[separator_index + 1].strip().lower() if separator_index + 1 < len(context.args) else None
        
        # Join the message parts into a single string
        message = " ".join(message_parts).strip()
    else:
        message = " ".join(context.args)  # If no separator, treat all as message

    if update.message.photo:
        # Get the largest photo size
        photo = update.message.photo[-1].file_id
        caption = update.message.caption if update.message.caption else ""
    elif not message:
        await update.message.reply_text(
            "❌ Please provide a message or image to send.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Get all regular users (excluding authorized members)
    valid_users = [user for user in MEMBERS if user.get('user_id')]
    
    # Get all groups and channels where the bot is a member
    all_groups = await get_bot_groups(context)

    # Filter groups based on grouptype if specified
    if grouptype == "sqrdao":
        filtered_groups = [g for g in all_groups if "sqrdao" in g['title'].lower()]
    elif grouptype == "summit":
        filtered_groups = [g for g in all_groups if "summit" in g['title'].lower()]
    else:
        filtered_groups = all_groups

    if not valid_users and not filtered_groups:
        await update.message.reply_text(
            "❌ No valid users or groups/channels found to send the message to.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Send confirmation to the sender
    group_type_msg = " (sqrDAO groups only)" if grouptype == "sqrdao" else " (Summit groups only)" if grouptype == "summit" else ""
    await update.message.reply_text(
        f"📤 Starting to send {'image' if photo else 'message'} to {len(valid_users)} users and {len(filtered_groups)} groups/channels{group_type_msg}...",
        parse_mode=ParseMode.HTML
    )
    
    # Track success and failure counts
    user_success_count = 0
    user_failure_count = 0
    group_success_count = 0
    group_failure_count = 0
    failed_users = []
    failed_groups = []

    for group in filtered_groups:
        try:
            if photo:
                # Determine announcement format based on grouptype
                if grouptype in ["sqrdao", "summit"]:
                    announcement_prefix = "📢 <b>Announcement from sqrDAO:</b>"
                else:
                    announcement_prefix = "📢 <b>Announcement from sqrFUND:</b>"
                
                # Send photo with caption, stripping the command if present
                formatted_caption = f"{announcement_prefix}\n\n{caption.replace('/mass_message', '').strip()}" if caption else None
                await context.bot.send_photo(
                    chat_id=group['id'],
                    photo=photo,
                    caption=formatted_caption,
                    parse_mode=ParseMode.HTML if formatted_caption else None
                )
            else:
                # Determine announcement format based on grouptype
                if grouptype in ["sqrdao", "summit"]:
                    announcement_prefix = "📢 <b>Announcement from sqrDAO:</b>"
                else:
                    announcement_prefix = "📢 <b>Announcement from sqrFUND:</b>"
                
                # Send text message without the command
                await context.bot.send_message(
                    chat_id=group['id'],
                    text=f"{announcement_prefix}\n\n{message}",
                    parse_mode=ParseMode.HTML
                )
            group_success_count += 1
            
        except Exception as e:
            group_failure_count += 1
            failed_groups.append(f"{group['title']} ({group['type']})")
            logger.error(f"Failed to send to group/channel {group['title']} (ID: {group['id']}): {str(e)}")
    
    # Send summary to the sender
    summary = f"✅ {'Image' if photo else 'Message'} delivery complete!\n\n"
    
    if grouptype == "sqrdao":
        summary += "📝 Message was sent to sqrDAO groups only\n\n"
    elif grouptype == "summit":
        summary += "📝 Message was sent to Summit groups only\n\n"
    
    if failed_users:
        summary += f"❌ Failed to send to users:\n"
        summary += "\n".join(f"• {user}" for user in failed_users[:5])
        if len(failed_users) > 5:
            summary += f"\n... and {len(failed_users) - 5} more users"
    
    summary += f"\n\n📊 User Statistics:\n"
    summary += f"• Successfully sent: {user_success_count}\n"
    summary += f"• Failed to send: {user_failure_count}\n"
    
    summary += "\n\n📊 Group/Channel Statistics:\n"
    summary += f"• Successfully sent: {group_success_count}\n"
    summary += f"• Failed to send: {group_failure_count}\n"
    
    await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

async def summarize_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize_space command - Process SQR token transfer and verify transaction."""
    # Check if there's already an active transaction window
    if context.user_data.get('awaiting_signature'):
        command_start_time = context.user_data.get('command_start_time')
        if command_start_time and (datetime.now() - command_start_time) <= timedelta(minutes=30):
            # Calculate remaining time
            remaining_time = timedelta(minutes=30) - (datetime.now() - command_start_time)
            minutes = int(remaining_time.total_seconds() // 60)
            seconds = int(remaining_time.total_seconds() % 60)
            
            await update.message.reply_text(
                f"⚠️ <b>Active Transaction Window</b>\n\n"
                f"You already have an active transaction window with {minutes}m {seconds}s remaining.\n"
                f"Please complete the current transaction or wait for the window to expire before starting a new one.\n\n"
                f"If you need to cancel the current transaction, use the /cancel command.",
                parse_mode=ParseMode.HTML
            )
            return

    # Check if both request type and space URL were provided
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Please provide both request type and X Space URL.\n"
            "Usage: /summarize_space [request_type] [space_url]\n"
            "Request types:\n"
            "• text - Get text summary (1000 $SQR)\n"
            "• audio - Get text + audio summary (2000 $SQR)\n"
            "Example: /summarize_space text https://x.com/i/spaces/1234567890",
            parse_mode=ParseMode.HTML
        )
        return

    # Validate request type
    request_type = context.args[0].lower()
    if request_type not in ['text', 'audio']:
        await update.message.reply_text(
            "❌ Invalid request type.\n"
            "Please use either 'text' or 'audio'.\n"
            "Example: /summarize_space text https://x.com/i/spaces/1234567890",
            parse_mode=ParseMode.HTML
        )
        return

    # Validate the space URL
    space_url = context.args[1]
    if not space_url.startswith("https://x.com/i/spaces/") and not space_url.startswith("https://x.com/i/broadcasts/"):
        await update.message.reply_text(
            "❌ Invalid X Space URL format.\n"
            "Please provide a valid URL starting with 'https://x.com/i/spaces/' or 'https://x.com/i/broadcasts/'",
            parse_mode=ParseMode.HTML
        )
        return

    # Store the user's state in context with timestamp, space URL, and request type
    context.user_data['awaiting_signature'] = True
    context.user_data['command_start_time'] = datetime.now()
    context.user_data['space_url'] = space_url
    context.user_data['request_type'] = request_type
    
    # Set required token amount based on request type
    required_amount = 2000 if request_type == 'audio' else 1000
    
    # Send instructions to the user
    instructions = (
        "🔄 <b>Space Summarization Process</b>\n\n"
        f"Request Type: <b>{request_type.upper()}</b>\n"
        f"Required Amount: <b>{required_amount} $SQR</b>\n\n"
        "To proceed with space summarization, please follow these steps:\n\n"
        "1. Send the required $SQR tokens to this address:\n"
        "<code>Dt4ansTyBp3ygaDnK1UeR1YVPtyLm5VDqnisqvDR5LM7</code>\n"
        "<a href='https://t.me/bonkbot_bot?start=ref_j03ne'>Buy SQR on Bonkbot</a>\n"
        "2. Copy the transaction signature\n"
        "3. Paste the signature in this chat\n\n"
        "⚠️ <i>Note: The transaction must be completed within 30 minutes from now.</i>\n"
        "If you need to cancel the current transaction, use the /cancel command."
        "⏰ Deadline: " + (context.user_data['command_start_time'] + timedelta(minutes=30)).strftime("%H:%M:%S")
    )
    
    await update.message.reply_text(instructions, parse_mode=ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command - Cancel the current transaction."""
    if context.user_data.get('awaiting_signature'):
        context.user_data['awaiting_signature'] = False
        context.user_data['command_start_time'] = None
        context.user_data['space_url'] = None
        context.user_data['request_type'] = None
        context.user_data['job_id'] = None
        context.user_data['failed_attempts'] = 0
        
        await update.message.reply_text(
            "✅ Your current transaction has been cancelled.\n\n"
            "For refund, please contact @DarthCastelian.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "❌ No active transaction to cancel.",
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
        load_groups_from_knowledge()

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
        application.add_handler(CommandHandler("list_members", list_members))
        application.add_handler(CommandHandler("sqr_info", get_sqr_info_command))
        application.add_handler(CommandHandler("mass_message", mass_message))
        application.add_handler(CommandHandler("add_group", add_group))
        application.add_handler(CommandHandler("list_groups", list_groups))
        application.add_handler(CommandHandler("remove_group", remove_group))
        application.add_handler(CommandHandler("summarize_space", summarize_space))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group_status))
        application.add_handler(ChatMemberHandler(handle_group_status))
        # Add handler for photos with mass_message command in caption
        application.add_handler(MessageHandler(
            filters.PHOTO & filters.CaptionRegex(r'^/mass_message'), mass_message
        ))

        # Start the Bot
        logger.debug("Starting bot...")  # This can be kept for clarity
        application.post_init = set_bot_commands
        
        # Start polling and set the bot ID after the bot is running
        application.run_polling(allowed_updates=Update.ALL_TYPES)

        # Store the bot ID after the application is created
        global bot_id
        bot_id = application.bot.id

    except Exception as e:
        logger.error(f"Fatal error in main(): {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

if __name__ == '__main__':
    import asyncio
    asyncio.run(main()) 