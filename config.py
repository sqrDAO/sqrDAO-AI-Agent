import os
from dotenv import load_dotenv
from telegram.ext.filters import BaseFilter

# Load environment variables
load_dotenv()

# API Keys
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
SQR_FUND_API_KEY = os.getenv('SQR_FUND_API_KEY')

# Solana Configuration
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
SQR_TOKEN_MINT = "CsZmZ4fz9bBjGRcu3Ram4tmLRMmKS6GPWqz4ZVxsxpNX"
RECIPIENT_WALLET = "Dt4ansTyBp3ygaDnK1UeR1YVPtyLm5VDqnisqvDR5LM7"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SQR_PURCHASE_LINK = "https://t.me/bonkbot_bot?start=ref_j03ne"

# Token Costs
TEXT_SUMMARY_COST = 1000
AUDIO_SUMMARY_COST = 2000

# Timeouts
TRANSACTION_TIMEOUT_MINUTES = 30
JOB_CHECK_TIMEOUT_SECONDS = 60
MAX_JOB_CHECK_ATTEMPTS = 6

# Database
DATABASE_FILE = os.getenv('DATABASE_FILE', 'bot_memory.db')

# Message Limits
MAX_MESSAGE_LENGTH = 4000
MAX_AUDIO_CHUNK_SIZE = 10000

# Error Messages
ERROR_MESSAGES = {
    'api_key_missing': "❌ API key not configured",
    'invalid_signature': "❌ Invalid transaction signature format",
    'transaction_failed': "❌ Transaction failed",
    'timeout': "❌ Time limit expired!",
    'space_download_failed': "❌ Failed to download the Space",
    'space_summarization_failed': "❌ Failed to summarize space",
    'general_error': "❌ An error occurred. Please try again later.",
    'processing_error': "❌ Error processing your request.",
    'ai_processing_error': "❌ Error in AI processing.",
    'unauthorized': "❌ You are not authorized to use this command.",
    'invalid_request': "❌ Invalid request format.",
    'member_not_found': "❌ Member not found.",
    'group_not_found': "❌ Group not found.",
}

# Success Messages
SUCCESS_MESSAGES = {
    'transaction_verified': "✅ Transaction verified successfully!",
    'space_download_started': "🔄 Space download initiated. This may take a few minutes.",
    'space_summarized': "✅ Space summarized successfully!",
    'member_approved': "✅ Member approved successfully!",
    'member_rejected': "✅ Member rejected.",
    'group_added': "✅ Group added successfully!",
    'group_removed': "✅ Group removed successfully!",
    'knowledge_stored': "✅ Knowledge stored successfully!",
    'request_submitted': "✅ Request submitted successfully!",
}

# Announcement Prefixes
ANNOUNCEMENT_PREFIXES = {
    'sqrdao': "📢 <b>Announcement from sqrDAO:</b>",
    'summit': "📢 <b>Announcement from sqrDAO:</b>",
    'sqrfund': "📢 <b>Announcement from sqrFUND:</b>",
    'default': "📢 <b>Announcement:</b>"
}

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

class DocumentWithMassMessageCaption(BaseFilter):
    def filter(self, message):
        # Check if it's a document AND has a caption starting with /mass_message
        return bool(
            message.document and
            message.caption and
            message.caption.startswith('/mass_message')
        )