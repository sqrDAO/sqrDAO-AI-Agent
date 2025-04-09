from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from typing import Optional, Tuple
from datetime import datetime
import asyncio
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TokenAccountOpts
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address
from utils.utils import format_response_for_telegram
from handlers.spaces import handle_successful_transaction, handle_failed_transaction
from config import (
    SOLANA_RPC_URL,
    SQR_TOKEN_MINT,
    RECIPIENT_WALLET,
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST,
    TRANSACTION_TIMEOUT_MINUTES,
    ERROR_MESSAGES,
    SUCCESS_MESSAGES
)

logger = logging.getLogger(__name__)

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command - Check user's SQR token balance."""
    user = update.effective_user
    user_id = user.id
    
    try:
        # Initialize Solana client
        client = AsyncClient(SOLANA_RPC_URL, commitment=Commitment("confirmed"))
        
        # Get associated token account
        ata = get_associated_token_address(user_id, SQR_TOKEN_MINT)
        
        # Get token accounts
        response = await client.get_token_accounts_by_owner(
            user_id,
            TokenAccountOpts(program_id=TOKEN_PROGRAM_ID)
        )
        
        # Find SQR token account
        sqr_balance = 0
        for account in response.value:
            if account.account.data.parsed['info']['mint'] == SQR_TOKEN_MINT:
                sqr_balance = account.account.data.parsed['info']['tokenAmount']['uiAmount']
                break
        
        # Format balance message
        balance_text = f"💰 Your SQR token balance: {sqr_balance} SQR\n\n"
        balance_text += f"Costs:\n"
        balance_text += f"• Text summary: {TEXT_SUMMARY_COST} SQR\n"
        balance_text += f"• Audio summary: {AUDIO_SUMMARY_COST} SQR"
        
        await update.message.reply_text(balance_text, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Error checking balance: {str(e)}")
        await update.message.reply_text(
            "❌ Error checking your balance. Please try again later.",
            parse_mode=ParseMode.HTML
        )
    finally:
        await client.close()

async def check_transaction_status(signature: str, command_start_time: datetime, 
                                 space_url: str = None, request_type: str = 'text') -> Tuple[bool, str, Optional[str]]:
    """Check the status of a Solana transaction."""
    try:
        client = AsyncClient(SOLANA_RPC_URL, commitment=Commitment("confirmed"))
        
        # Check if transaction is confirmed
        response = await client.get_signature_statuses([signature])
        if not response.value[0]:
            return False, "Transaction not found", None
        
        status = response.value[0]
        if status.err:
            return False, f"Transaction failed: {status.err}", None
        
        # Get transaction details
        tx = await client.get_transaction(signature)
        if not tx:
            return False, "Could not get transaction details", None
        
        # Check if transaction is to the correct recipient
        for instruction in tx.transaction.message.instructions:
            if instruction.program_id == TOKEN_PROGRAM_ID:
                for account in instruction.accounts:
                    if account.pubkey == RECIPIENT_WALLET:
                        # Transaction is confirmed and correct
                        return True, "Transaction confirmed", None
        
        return False, "Transaction not to correct recipient", None
        
    except Exception as e:
        logger.error(f"Error checking transaction: {str(e)}")
        return False, f"Error checking transaction: {str(e)}", None
    finally:
        await client.close()

async def process_signature(signature: str, context: ContextTypes.DEFAULT_TYPE, message: Message):
    """Process a transaction signature."""
    command_start_time = datetime.now()
    space_url = context.user_data.get('space_url')
    request_type = context.user_data.get('request_type', 'text')
    
    # Reset user data
    context.user_data['awaiting_signature'] = False
    context.user_data['command_start_time'] = None
    context.user_data['space_url'] = None
    context.user_data['request_type'] = None
    
    # Check transaction status
    success, status_message, job_id = await check_transaction_status(
        signature, command_start_time, space_url, request_type
    )
    
    if success:
        await handle_successful_transaction(
            context, message, message.text, job_id, space_url, request_type
        )
    else:
        await handle_failed_transaction(context, message, message.text, request_type) 