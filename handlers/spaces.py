from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
import asyncio
import httpx
import os
import requests
import uuid
import traceback
from gtts import gTTS
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from utils.retry import with_retry, TransientError, PermanentError
from config import (
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST,
    TRANSACTION_TIMEOUT_MINUTES,
    JOB_CHECK_TIMEOUT_SECONDS,
    MAX_JOB_CHECK_ATTEMPTS,
    ERROR_MESSAGES,
    SUCCESS_MESSAGES,
    RECIPIENT_WALLET,
    TOKEN_PROGRAM_ID,
    SOLANA_RPC_URL
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

@with_retry(max_attempts=3)
async def check_job_status(job_id: str, space_url: str) -> Tuple[bool, str]:
    """Check the status of a summarization job with retry logic."""
    try:
        # First check transaction status
        success, message, _ = await check_transaction_status(job_id)
        if not success:
            return False, message

        api_key = os.getenv('SQR_FUND_API_KEY')
        if not api_key:
            logger.error("SQR_FUND_API_KEY not found in environment variables")
            raise PermanentError("API key not configured")

        # Then download the space
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
            raise PermanentError(f"Failed to initiate space download: {download_response.text}")

        # Get the job ID from the response
        try:
            job_data = download_response.json()
            job_id = job_data.get('jobId')
            if not job_id:
                raise PermanentError("No job ID received from download request")
        except Exception as e:
            raise PermanentError(f"Error parsing download response: {str(e)}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://spaces.sqrfund.ai/api/jobs/{job_id}",
                headers={"X-API-Key": api_key}
            )
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'completed':
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
            elif data['status'] == 'failed':
                raise PermanentError(f"Job failed: {data.get('error', 'Unknown error')}")
            else:
                raise TransientError("Job still processing")
            
    except httpx.HTTPError as e:
        raise TransientError(f"Failed to check job status: {str(e)}")
    except ValueError as e:
        raise PermanentError(f"Invalid response format: {str(e)}")

async def convert_text_to_audio(text: str, language: str = 'en') -> Tuple[Optional[str], Optional[str]]:
    """Convert text to audio using Google Text-to-Speech."""
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
                if request_type == 'audio':
                    # Convert text to audio
                    audio_url = await convert_text_to_audio(result)
                    if audio_url:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=audio_url,
                            caption="✅ Audio summary completed!",
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text="❌ Failed to convert summary to audio",
                            parse_mode=ParseMode.HTML
                        )
                else:
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

async def summarize_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize_space command with improved error handling."""
    try:
        if not context.args:
            raise ValueError("Please provide an X Space URL")
        
        space_url = context.args[0]
        logger.info(f"Space URL: {space_url}")
        if not ('x.com/i/spaces/' in space_url or 'x.com/i/broadcasts/' in space_url):
            raise InvalidSpaceUrlError("Invalid X Space URL")
        
        # Determine request type (text or audio)
        if len(context.args) > 1:
            request_type = 'audio' if context.args[1].lower() == 'audio' else 'text'
        else:
            raise ValueError("Please specify the request type: 'text' or 'audio'.")

        cost = AUDIO_SUMMARY_COST if request_type == 'audio' else TEXT_SUMMARY_COST
        
        # Set user data for transaction
        context.user_data.update({
            'awaiting_signature': True,
            'command_start_time': datetime.now(),
            'space_url': space_url,
            'request_type': request_type,
            'job_id': None,
            'failed_attempts': 0
        })
        
        await update.message.reply_text(
            "🔄 <b>Space Summarization Process</b>\n\n"
            f"Request Type: <b>{request_type.upper()}</b>\n"
            f"Required Amount: <b>{cost} $SQR</b>\n\n"
            "<a href='https://t.me/bonkbot_bot?start=ref_j03ne'>Buy SQR on Bonkbot</a>\n\n"
            "To proceed with space summarization, please follow these steps:\n\n"
            "1. Send the required $SQR tokens to this address:\n"
            "<code>Dt4ansTyBp3ygaDnK1UeR1YVPtyLm5VDqnisqvDR5LM7</code>\n"
            "2. Copy the transaction signature.\n"
            "3. Paste the signature in this chat.\n\n"
            "⚠️ <i>Note: The transaction must be completed within {TRANSACTION_TIMEOUT_MINUTES} minutes from now.</i>\n"
            "If you need to cancel the current transaction, use the /cancel command.\n\n"
            "⏰ Deadline: " + (context.user_data['command_start_time'] + timedelta(minutes=TRANSACTION_TIMEOUT_MINUTES)).strftime("%H:%M:%S"),
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