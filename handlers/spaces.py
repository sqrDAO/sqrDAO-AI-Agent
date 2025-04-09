from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
import asyncio
import httpx
from utils.utils import format_response_for_telegram
from utils.retry import with_retry, TransientError, PermanentError
from config import (
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST,
    TRANSACTION_TIMEOUT_MINUTES,
    JOB_CHECK_TIMEOUT_SECONDS,
    MAX_JOB_CHECK_ATTEMPTS,
    ERROR_MESSAGES,
    SUCCESS_MESSAGES,
    RECIPIENT_WALLET
)

logger = logging.getLogger(__name__)

class SpaceSummarizationError(Exception):
    """Base class for space summarization errors."""
    pass

class InvalidSpaceUrlError(SpaceSummarizationError):
    """Raised when the provided space URL is invalid."""
    pass

class TransactionTimeoutError(SpaceSummarizationError):
    """Raised when the transaction timeout is reached."""
    pass

class JobTimeoutError(SpaceSummarizationError):
    """Raised when the job processing timeout is reached."""
    pass

def reset_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all user data related to space summarization."""
    context.user_data.update({
        'awaiting_signature': False,
        'command_start_time': None,
        'space_url': None,
        'request_type': None,
        'job_id': None,
        'failed_attempts': 0
    })

@with_retry(max_attempts=3)
async def check_job_status(job_id: str, space_url: str) -> Tuple[bool, str]:
    """Check the status of a summarization job with retry logic."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"https://api.summarization.service/jobs/{job_id}")
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'completed':
                return True, data['summary']
            elif data['status'] == 'failed':
                raise PermanentError(f"Job failed: {data.get('error', 'Unknown error')}")
            else:
                raise TransientError("Job still processing")
            
    except httpx.HTTPError as e:
        raise TransientError(f"Failed to check job status: {str(e)}")
    except ValueError as e:
        raise PermanentError(f"Invalid response format: {str(e)}")

async def periodic_job_check(
    context: ContextTypes.DEFAULT_TYPE,
    job_id: str,
    space_url: str,
    chat_id: int,
    message_id: int,
    request_type: str = 'text',
    max_attempts: int = MAX_JOB_CHECK_ATTEMPTS,
    check_interval: int = JOB_CHECK_TIMEOUT_SECONDS
) -> None:
    """Periodically check the status of a summarization job with improved error handling."""
    start_time = datetime.now()
    attempts = 0
    
    while attempts < max_attempts:
        try:
            success, result = await check_job_status(job_id, space_url)
            
            if success:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"✅ Summary completed!\n\n{result}",
                    parse_mode=ParseMode.HTML
                )
                reset_user_data(context)
                return
            
            # Check if we've exceeded the total time limit
            if datetime.now() - start_time > timedelta(minutes=TRANSACTION_TIMEOUT_MINUTES):
                raise JobTimeoutError("Job processing timeout reached")
            
            # Update status message
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⏳ Processing... (Attempt {attempts + 1}/{max_attempts})",
                parse_mode=ParseMode.HTML
            )
            
            # Wait before next check
            await asyncio.sleep(check_interval)
            attempts += 1
            
        except TransientError as e:
            logger.warning(f"Transient error in job check: {str(e)}")
            attempts += 1
            await asyncio.sleep(check_interval)
            
        except (PermanentError, JobTimeoutError) as e:
            logger.error(f"Permanent error in job check: {str(e)}")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Error: {str(e)}",
                parse_mode=ParseMode.HTML
            )
            reset_user_data(context)
            return
            
        except Exception as e:
            logger.error(f"Unexpected error in job check: {str(e)}")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ An unexpected error occurred. Please try again later.",
                parse_mode=ParseMode.HTML
            )
            reset_user_data(context)
            return
    
    # Max attempts reached
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="❌ Timeout: Could not complete summarization in time.",
        parse_mode=ParseMode.HTML
    )
    reset_user_data(context)

async def summarize_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize_space command with improved error handling."""
    try:
        if not context.args:
            raise ValueError("Please provide a Twitter Space URL")
        
        space_url = context.args[0]
        if not space_url.startswith(('http://', 'https://')):
            space_url = 'https://' + space_url
        
        if 'twitter.com/i/spaces/' not in space_url:
            raise InvalidSpaceUrlError("Invalid Twitter Space URL")
        
        # Set user data for transaction
        context.user_data.update({
            'awaiting_signature': True,
            'command_start_time': datetime.now(),
            'space_url': space_url,
            'request_type': 'text',
            'job_id': None,
            'failed_attempts': 0
        })
        
        await update.message.reply_text(
            f"🔔 Please send {TEXT_SUMMARY_COST} SQR tokens to {RECIPIENT_WALLET}\n"
            f"and reply with the transaction signature.\n\n"
            f"Timeout: {TRANSACTION_TIMEOUT_MINUTES} minutes",
            parse_mode=ParseMode.HTML
        )
        
    except (ValueError, InvalidSpaceUrlError) as e:
        await update.message.reply_text(
            f"❌ {str(e)}",
            parse_mode=ParseMode.HTML
        )
        reset_user_data(context)
    except Exception as e:
        logger.error(f"Unexpected error in summarize_space: {str(e)}")
        await update.message.reply_text(
            "❌ An unexpected error occurred. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        reset_user_data(context)

async def handle_successful_transaction(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    message_text: str,
    job_id: Optional[str],
    space_url: str,
    request_type: str
) -> None:
    """Handle a successful transaction with improved error handling."""
    try:
        processing_msg = await message.reply_text(
            "⏳ Processing your request...",
            parse_mode=ParseMode.HTML
        )
        
        asyncio.create_task(
            periodic_job_check(
                context, job_id, space_url,
                processing_msg.chat_id, processing_msg.message_id,
                request_type
            )
        )
    except Exception as e:
        logger.error(f"Error in handle_successful_transaction: {str(e)}")
        await message.reply_text(
            "❌ An error occurred while processing your request. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        reset_user_data(context)

async def handle_failed_transaction(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    message_text: str,
    request_type: str
) -> None:
    """Handle a failed transaction with improved error handling."""
    try:
        await message.reply_text(
            "❌ Transaction verification failed. Please try again.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in handle_failed_transaction: {str(e)}")
    finally:
        reset_user_data(context) 