import httpx
import logging
from typing import Optional, List
import re
from bs4 import BeautifulSoup
from trafilatura import extract
from utils.retry import with_retry, TransientError
from config import ERROR_MESSAGES, SUCCESS_MESSAGES, SQR_TOKEN_MINT
import json
import traceback

logger = logging.getLogger(__name__)

def format_response_for_telegram(text: str, parse_mode: str = 'HTML') -> str:
    """Format text to be compatible with Telegram's HTML or Markdown."""
    if parse_mode == 'HTML':
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    elif parse_mode == 'MARKDOWN_V2':
        # Escape Markdown special characters
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text
    return text  # Return unmodified text if parse mode is not recognized

def extract_urls(text: str) -> List[str]:
    """Extract URLs from text using regex."""
    url_pattern = r'https?://\S+'
    return re.findall(url_pattern, text)

@with_retry(max_attempts=3)
async def get_webpage_content(url: str) -> Optional[str]:
    """Fetch main content from a webpage using httpx."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Try trafilatura first
            content = extract(response.text)
            if content:
                return content[:5000]  # Limit to 5000 chars
            
            # Fallback to BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            return text[:5000] if text else None
            
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching {url}: {str(e)}")
        raise TransientError(f"HTTP error: {str(e)}")
    except Exception as e:
        logger.error(f"Error fetching {url}: {str(e)}")
        raise TransientError(f"Error fetching content: {str(e)}")

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
        logger.error(f"Response status code: {e.response.status_code if hasattr(e, 'response') else 'N/A'}")
        logger.error(f"Response headers: {e.response.headers if hasattr(e, 'response') else 'N/A'}")
        logger.error(f"Response body: {e.response.text if hasattr(e, 'response') else 'N/A'}")
        raise TransientError(f"HTTP error: {str(e)}")
    except Exception as e:
        logger.error(f"Error resolving SNS domain {domain}: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Full error traceback: {traceback.format_exc()}")
        raise TransientError(f"Error resolving domain: {str(e)}")

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
        raise TransientError(f"HTTP error: {str(e)}")
    except Exception as e:
        logger.error(f"Error fetching SQR info: {str(e)}")
        raise TransientError(f"Error fetching token info: {str(e)}")

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
        'both': '🔔 <b>sqrDAO & sqrFUND Announcement:</b>'
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