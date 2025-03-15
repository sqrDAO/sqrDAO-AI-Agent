import os
import logging
import traceback
import re
import json
from urllib.parse import urlparse
import requests
import trafilatura
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import google.generativeai as genai
from googleapiclient.discovery import build
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure logging with more detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

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
        model_name='models/gemini-1.5-pro-latest',
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
    # First, escape special HTML characters in the text, but not in code blocks
    chunks = []
    is_code = False
    current_chunk = ""
    
    for line in text.split('\n'):
        if line.strip().startswith('```') and line.strip().endswith('```'):
            # Single-line code block
            code = line.strip()[3:-3]
            chunks.append(f"<code>{code}</code>")
            continue
        elif line.strip().startswith('```'):
            is_code = True
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line.replace('```', '')
            continue
        elif line.strip().endswith('```') and is_code:
            is_code = False
            current_chunk += '\n' + line.replace('```', '')
            chunks.append(f"<pre>{current_chunk}</pre>")
            current_chunk = ""
            continue
            
        if is_code:
            current_chunk += '\n' + line
        else:
            # First escape HTML special characters
            line = line.replace('&', '&amp;')
            line = line.replace('<', '&lt;')
            line = line.replace('>', '&gt;')
            
            # Handle headers before other formatting
            if line.strip().startswith('## '):
                line = f"<b>{line.strip()[3:]}</b>"
            elif line.strip().startswith('# '):
                line = f"<b><u>{line.strip()[2:]}</u></b>"
            elif line.strip().startswith('### '):
                line = f"<b><i>{line.strip()[4:]}</i></b>"
            else:
                # Convert markdown to HTML, being careful with nested tags
                # Bold - replace ** with temporary markers
                line = re.sub(r'\*\*(.*?)\*\*', '[[BOLD]]\\1[[/BOLD]]', line)
                # Italic - replace * with temporary markers
                line = re.sub(r'\*(.*?)\*', '[[ITALIC]]\\1[[/ITALIC]]', line)
                # Code - replace ` with temporary markers
                line = re.sub(r'`(.*?)`', '[[CODE]]\\1[[/CODE]]', line)
                # Links - replace with temporary markers
                line = re.sub(r'\[(.*?)\]\((.*?)\)', '[[LINK]]\\1[[URL]]\\2[[/LINK]]', line)
                
                # Now replace the markers with actual HTML tags
                line = line.replace('[[BOLD]]', '<b>').replace('[[/BOLD]]', '</b>')
                line = line.replace('[[ITALIC]]', '<i>').replace('[[/ITALIC]]', '</i>')
                line = line.replace('[[CODE]]', '<code>').replace('[[/CODE]]', '</code>')
                line = re.sub(r'\[\[LINK\]\](.*?)\[\[URL\]\](.*?)\[\[/LINK\]\]', r'<a href="\2">\1</a>', line)
                
                # Handle bullet points last
                if line.strip().startswith('* '):
                    line = '• ' + line[2:]
            
            if current_chunk:
                current_chunk += '\n' + line
            else:
                current_chunk = line
    
    if current_chunk:
        chunks.append(current_chunk)
    
    result = '\n'.join(chunks)
    
    # Add debug logging
    logger.debug(f"Formatted text before sending: {result}")
    
    return result

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
        "👋 <b>Hello!</b> I'm your AI assistant powered by Gemini. "
        "You can ask me anything, and I'll do my best to help you!\n\n"
        "I can:\n"
        "• Answer your questions\n"
        "• Search the web for information\n"
        "• Read and analyze web pages\n"
        "• Help with various tasks\n\n"
        "Just send me a message or a URL to get started!"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = """
<b>🤖 sqrDAO Bot Help</b>

I'm your AI assistant! Here's what I can do:

<b>Commands:</b>
• /start - Start the bot
• /help - Show this help message
• /learn - Teach me new information
  Format: /learn topic | information | source(optional)
  Example: /learn crypto | Bitcoin is a cryptocurrency | bitcoin.org

<b>Features:</b>
• I remember our conversations and use them for context
• I can learn new information that you teach me
• I provide detailed responses using my knowledge base

Just send me a message and I'll do my best to help you!
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /learn command to store new knowledge."""
    try:
        # Expected format: /learn topic | information | source(optional)
        args = ' '.join(context.args)
        if not args or '|' not in args:
            await update.message.reply_text(
                "<i>Please use the format: /learn topic | information | source(optional)</i>",
                parse_mode=ParseMode.HTML
            )
            return

        parts = [part.strip() for part in args.split('|')]
        topic = parts[0]
        information = parts[1]
        source = parts[2] if len(parts) > 2 else None

        # Store the knowledge
        db.store_knowledge(topic, information, source)
        
        await update.message.reply_text(
            f"<i>✅ I've learned new information about {topic}!</i>",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Error in learn_command: {str(e)}")
        await update.message.reply_text(
            "<i>I encountered an error while learning. Please try again.</i>",
            parse_mode=ParseMode.HTML
        )

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

# Initialize database
db = Database()

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
    
    if not user_message:
        await update.message.reply_text(
            "<i>I couldn't process an empty message. Please send some text.</i>",
            parse_mode=ParseMode.HTML
        )
        return
        
    logger.debug(f"Received message: {user_message}")
    
    # Check if the message is a command
    if user_message.startswith('/'):
        await update.message.reply_text(
            "<i>Please use specific commands like /help, or send a regular message.</i>",
            parse_mode=ParseMode.HTML
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
        logger.debug(f"Formatted text: {formatted_text}")  # Add debug logging
        
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

def store_new_knowledge(topic, information, source=None):
    db.store_knowledge(topic, information, source)

def main():
    """Start the bot."""
    try:
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
            
        # Create the Application and pass it your bot's token
        application = Application.builder().token(telegram_token).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("learn", learn_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Start the Bot
        logger.info("Starting bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Fatal error in main(): {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

if __name__ == '__main__':
    main() 