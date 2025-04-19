import httpx
import logging
from typing import Optional, List, Tuple
import re
from bs4 import BeautifulSoup
from trafilatura import extract
from utils.retry import with_retry, TransientError
from config import ERROR_MESSAGES, SUCCESS_MESSAGES, SQR_TOKEN_MINT
import json
import bleach  # Add this import
from telegram.constants import ParseMode  # Import ParseMode

logger = logging.getLogger(__name__)

def is_valid_space_url(url: str) -> bool:
    """Check if the provided URL is a valid X Space URL."""
    return 'x.com/i/spaces/' in url or 'x.com/i/broadcasts/' in url

def format_response_for_telegram(text: str, parse_mode: str = 'HTML') -> str:
    """Format text to be compatible with Telegram's HTML formatting."""
    logger.debug(f"Formatting response for Telegram with parse_mode: {parse_mode}")  # Log parse mode
    logger.debug(f"Input text: {text}")  # Log input text

    if parse_mode == 'HTML':
        # Use regex to handle markdown-style formatting
        # Handle bold text
        text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        # Handle italic text
        text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
        # Handle underlined text
        text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)
        # Handle code blocks
        text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)
        # Handle inline code
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        
        # Handle bullet points
        text = re.sub(r'^\*\s*(.*)', r'<li>\1</li>', text, flags=re.MULTILINE)
        # Only wrap <li> elements in <ul> tags if they exist
        if '<li>' in text:
            text = re.sub(r'(<li>.*?</li>(\n|$))+', r'<ul>\g<0></ul>', text, flags=re.DOTALL)
        
        # Define allowed tags for bleach
        # Ensure 'a' is included for hyperlinks
        # And that it allows 'href' attribute
        allowed_tags = ['b', 'i', 'u', 'pre', 'code', 'a']
        allowed_attributes = {'a': ['href']}

        # Format hyperlinks correctly
        # Convert Markdown links to HTML links with more robust regex
        # Handles URLs with special characters and prevents partial matches
        text = re.sub(
            r'\[([^\]]+)\]\((https?://[^\s<>()]+(?:\([^\s<>()]*\)|[^\s<>()]*)*)\)',
            r'<a href="\2">\1</a>',
            text
        )
        
        # Sanitize the HTML to remove unsupported tags and fix any issues
        text = bleach.clean(text, tags=allowed_tags, attributes=allowed_attributes, strip=True)

        # Re-escape & to &amp; after bleach sanitization
        text = text.replace('&', '&amp;').replace('&amp;', '&')  # Ensure &amp; is not double-escaped

        logger.debug(f"Formatted text: {text}")  # Log the formatted text
        return text
    return text  # Return unmodified text if parse mode is not recognized

def extract_urls(text: str) -> List[str]:
    """Extract URLs from text using regex."""
    url_pattern = r'https?://\S+'
    return re.findall(url_pattern, text)

@with_retry(max_attempts=3)
async def get_webpage_content(url: str, max_length: int = 10000) -> Optional[str]:
    """Fetch main content from a webpage using httpx.
    
    Args:
        url: The URL of the webpage to fetch.
        max_length: The maximum length of the content to return.
        
    Returns:
        The main content of the webpage, limited to max_length characters.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Try trafilatura first
            content = extract(response.text)
            if content:
                return content[:max_length]  # Limit to max_length chars
            
            # Fallback to BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            return text[:max_length] if text else None
            
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching {url}: {str(e)}")
        raise TransientError(f"HTTP error: {str(e)}") from e
    except Exception as e:
        logger.error(f"Error fetching {url}: {str(e)}")
        raise TransientError(f"Error fetching content: {str(e)}") from e

@with_retry(max_attempts=3)
async def resolve_sns_domain(domain: str) -> Optional[str]:
    """Resolve SNS domain to wallet address using httpx."""
    try:
        # Remove .sol extension if present
        domain = domain.lower().replace('.sol', '')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"https://sns-sdk-proxy.bonfida.workers.dev/resolve/{domain}"
            
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Get the result field which contains the wallet address
            result = data.get('result')
            if not result:
                logger.warning(f"No result found in response for domain {domain}")
            
            return result
    except httpx.HTTPError as e:
        logger.error(f"HTTP error resolving SNS domain {domain}: {str(e)}")
        raise TransientError(f"HTTP error: {str(e)}") from e
    except Exception as e:
        logger.error(f"Error resolving SNS domain {domain}: {str(e)}")
        raise TransientError(f"Error resolving domain: {str(e)}") from e

@with_retry(max_attempts=3)
async def get_sqr_info() -> Optional[dict]:
    """Fetch SQR token info from GeckoTerminal API using httpx."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{SQR_TOKEN_MINT}",
                headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching SQR info: {str(e)}")
        raise TransientError(f"HTTP error: {str(e)}") from e
    except Exception as e:
        logger.error(f"Error fetching SQR info: {str(e)}")
        raise TransientError(f"Error fetching token info: {str(e)}") from e

def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram's Markdown V2."""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def get_announcement_prefix(grouptype: Optional[str]) -> str:
    """Get announcement prefix based on group type."""
    if not grouptype:
        return '🔔 <b>Announcement:</b>'
        
    prefixes = {
        'sqrdao': '🔔 <b>sqrDAO Announcement:</b>',
        'sqrfund': '🔔 <b>sqrFUND Announcement:</b>',
        'both': '🔔 <b>sqrDAO & sqrFUND Announcement:</b>',
        'summit': '🔔 <b>Web3 Builders\' Summit Announcement:</b>'
    }
    return prefixes.get(grouptype.lower(), '🔔 <b>Announcement:</b>')

def parse_mass_message_input(raw_input: str) -> tuple[str, Optional[str]]:
    """Parse input for mass messages.
    
    Args:
        raw_input: The input string to parse, expected format: "message | grouptype"
        
    Returns:
        tuple[str, Optional[str]]: A tuple containing (message, grouptype)
    """
    # Split by the pipe character to separate message and grouptype
    parts = raw_input.split('|', 1)
    
    if len(parts) == 2:
        # If we have both parts, strip whitespace and return
        message = parts[0].strip()
        grouptype = parts[1].strip().lower()
        # Validate grouptype
        if grouptype not in ['sqrdao', 'sqrfund', 'summit', 'both']:
            grouptype = None
        return message, grouptype
    else:
        # If no pipe found, return the whole input as message with no grouptype
        return raw_input.strip(), None

def get_error_message(key: str) -> str:
    """Get formatted error message."""
    return ERROR_MESSAGES.get(key, "An error occurred. Please try again later.")

def get_success_message(key: str) -> str:
    """Get formatted success message."""
    return SUCCESS_MESSAGES.get(key, "Operation completed successfully.") 

def load_authorized_members(db):
    """Load authorized members from config.json if not found in database."""
    try:
        # # Try to load from database first
        # authorized_data = db.get_knowledge("authorized_members")
        # if authorized_data and authorized_data[0]:
        #     return authorized_data[0]
        
        # If not in database, load from config.json
        with open('config.json', 'r') as f:
            config = json.load(f)
            authorized_members = config.get('authorized_members', [])
            
            # Store in database for future use
            if authorized_members:
                db.store_knowledge("authorized_members", json.dumps(authorized_members))
            
            return authorized_members
    except Exception as e:
        logger.error(f"Error loading authorized members: {str(e)}")
        return []

def sanitize_input(input_text: str) -> str:
    """Sanitize input to remove potentially harmful content."""
    # Implement sanitization logic here (e.g., remove HTML tags, escape special characters)
    # For simplicity, let's just strip leading/trailing whitespace
    return input_text.strip() 

async def api_request(method: str, url: str, headers: dict = None, json: dict = None) -> Tuple[bool, Optional[dict], Optional[str]]:
    """Reusable function to perform an HTTP request with error handling."""
    response = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            if method.lower() == 'post':
                response = await client.post(url, headers=headers, json=json)
            elif method.lower() == 'get':
                response = await client.get(url, headers=headers)
            else:
                raise ValueError("Unsupported HTTP method")

            response.raise_for_status()  # Raise an error for bad responses
            try:
                data = response.json()
            except ValueError as ve:
                logger.error(f"Failed to parse JSON: {str(ve)}")
                return False, None, "Invalid JSON response from server"
            return True, data, None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error during {method} request: {str(e)}")
        return False, None, str(e)
    except Exception as e:
        logger.error(f"Unexpected error during {method} request: {str(e)}")
        return False, None, str(e)

async def process_summary_api_response(context, update, edit_response, processing_msg):
    """Handle API response for summary operations."""
    if not edit_response[0]:  # If the request failed
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg.message_id,
            text=f"❌ Failed to process summary: {edit_response[2]}",
            parse_mode=ParseMode.HTML
        )
        return False

    summary_data = edit_response[1]
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=processing_msg.message_id,
        text=f"✅ Processed Summary:\n\n{summary_data.get('summary', 'No summary returned.')}",
        parse_mode=ParseMode.HTML
    )
    return True


def extract_keywords(message):
    """Extract meaningful keywords by removing stop words and short words."""
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                   'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall', 'should', 'may',
                   'might', 'must', 'can', 'could', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my',
                   'your', 'his', 'her', 'its', 'our', 'their', 'this', 'that', 'these', 'those', 'what',
                   'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how', 'at', 'by',
                   'for', 'with', 'about', 'against', 'between', 'into', 'through', 'during', 'before',
                   'after', 'above', 'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over',
                   'under', 'again', 'further', 'then', 'once', 'here', 'there', 'all', 'any', 'both', 
                   'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 
                   'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'just', 'don', 'now'}

    words = message.lower().split()
    # Use set comprehension directly for better performance
    return {re.sub(r'\W+', '', word) for word in words if len(word) > 2 and word not in stop_words}

async def retrieve_knowledge(db, keywords):
    """Retrieve knowledge for each keyword and aggregate results."""
    knowledge_text = ""
    if keywords:
        knowledge_text = "\nStored knowledge:\n"
        for keyword in set(keywords):
            try:
                knowledge = db.get_knowledge(keyword)
                if knowledge:
                    for info in knowledge:
                        knowledge_text += f"• {info}\n"
            except Exception as e:
                logger.error(f"Error retrieving knowledge for keyword '{keyword}': {str(e)}")
    return knowledge_text

def format_context(context):
    """Format the context for the AI model."""
    context_text = ""
    if context:
        context_text = "Previous relevant conversations:\n"
        for entry in context:
            if len(entry) >= 2:
                prev_msg, prev_resp = entry[:2]
                # Sanitize user input to prevent injection
                prev_msg = sanitize_input(prev_msg)
                prev_resp = sanitize_input(prev_resp)
                context_text += f"User: {prev_msg}\nBot: {prev_resp}\n"
            else:
                logger.warning(f"Unexpected context format: {entry}")
    return context_text
