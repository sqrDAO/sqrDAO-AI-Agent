import os
from dotenv import load_dotenv

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

# Token Costs
TEXT_SUMMARY_COST = 1000
AUDIO_SUMMARY_COST = 2000

# Timeouts
TRANSACTION_TIMEOUT_MINUTES = 30
JOB_CHECK_TIMEOUT_SECONDS = 180
MAX_JOB_CHECK_ATTEMPTS = 30

# Database
DATABASE_FILE = 'bot_memory.db'

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
}

# Success Messages
SUCCESS_MESSAGES = {
    'transaction_verified': "✅ Transaction verified successfully!",
    'space_download_started': "🔄 Space download initiated. This may take a few minutes.",
    'space_summarized': "✅ Space summarized successfully!",
}

# Announcement Prefixes
ANNOUNCEMENT_PREFIXES = {
    'sqrdao': "📢 <b>Announcement from sqrDAO:</b>",
    'summit': "📢 <b>Announcement from sqrDAO:</b>",
    'sqrfund': "📢 <b>Announcement from sqrFUND:</b>",
    'default': "📢 <b>Announcement:</b>"
} 