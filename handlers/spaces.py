from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
import asyncio
import httpx
import os
import uuid
import tempfile  # Import tempfile module
from gtts import gTTS
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solders.signature import Signature
from handlers.general import find_member_by_username  # Ensure this import is at the top of your file
from utils.retry import with_retry, TransientError, PermanentError, TransactionError
from utils.utils import is_valid_space_url, sanitize_input, process_summary_api_response, api_request
from config import (
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST,
    TRANSACTION_TIMEOUT_MINUTES,
    JOB_CHECK_TIMEOUT_SECONDS,
    MAX_JOB_CHECK_ATTEMPTS,
    RECIPIENT_WALLET,
    SOLANA_RPC_URL,
    SQR_TOKEN_MINT,
    SQR_PURCHASE_LINK,
    MAX_PROMPT_LENGTH
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
        'failed_attempts': 0,
        'signature_attempts': 0
    })

async def check_transaction_status(signature: str, command_start_time: datetime, 
                                    space_url: str = None, request_type: str = 'text') -> Tuple[bool, str]:
    """Check the status of a Solana transaction."""
    try:
        client = AsyncClient(SOLANA_RPC_URL, commitment=Commitment("confirmed"))
        
        # Convert signature string to Signature object
        try:
            signature_obj = Signature.from_string(signature)
        except Exception as original_exception:
            logger.error(f"Error converting signature format: {str(original_exception)}")
            raise TransactionError("Error converting signature format", "SIGNATURE_CONVERSION_ERROR") from original_exception

        # Get transaction details
        tx = await client.get_transaction(
            signature_obj,
            encoding="jsonParsed",  # Use jsonParsed for better token balance parsing
        )

        if not tx or not tx.value:
            raise TransactionError("Could not get transaction details", "TX_NOT_FOUND")
        
        transaction_data = tx.value.transaction
        meta = transaction_data.meta
        
        # Check if transaction was successful
        if not meta:
            raise TransactionError("No meta data found in transaction", "META_NOT_FOUND")
            
        if meta.err:
            raise TransactionError(f"Transaction failed: {meta.err}", "TRANSACTION_FAILED")
            
        # Get block time from transaction
        if not tx.value.block_time:
            logger.error("No block time found in transaction")
            raise TransactionError("No block time found in transaction", "BLOCK_TIME_NOT_FOUND")

        # Convert block time to datetime
        transaction_time = datetime.fromtimestamp(tx.value.block_time)
        
        # Check if transaction was completed within the 30-minute window
        time_diff = transaction_time - command_start_time

        if time_diff < timedelta(0):
            logger.warning("Transaction was completed before command was issued")
            raise TransactionError("Transaction was completed before the command was issued", "TRANSACTION_COMPLETED_BEFORE_COMMAND")
        elif time_diff > timedelta(minutes=TRANSACTION_TIMEOUT_MINUTES):
            minutes_late = int((time_diff - timedelta(minutes=TRANSACTION_TIMEOUT_MINUTES)).total_seconds() / 60)
            logger.warning(f"Transaction was completed {minutes_late} minutes after deadline")
            raise TransactionError(f"Transaction was completed {minutes_late} minutes after the {TRANSACTION_TIMEOUT_MINUTES}-minute window expired", "TRANSACTION_COMPLETED_AFTER_DEADLINE")
            
        # Check token amount using pre and post token balances
        try:
            pre_balances = meta.pre_token_balances
            post_balances = meta.post_token_balances
            
            if not pre_balances or not post_balances:
                logger.error("No token balance information found in transaction")
                raise TransactionError("No token balance information found in transaction", "TOKEN_BALANCE_NOT_FOUND")
            
            # Find the token transfer amount by comparing pre and post balances
            transfer_amount = 0
            target_mint = SQR_TOKEN_MINT
            
            for post_balance in post_balances:
                if str(post_balance.mint) == target_mint:
                    pre_balance = next(
                        (pre for pre in pre_balances 
                         if str(pre.mint) == target_mint and pre.account_index == post_balance.account_index),
                        None
                    )
                    
                    if pre_balance:
                        pre_amount = float(pre_balance.ui_token_amount.ui_amount_string)
                        post_amount = float(post_balance.ui_token_amount.ui_amount_string)
                        transfer_amount = post_amount - pre_amount
                        break
            
            required_amount = AUDIO_SUMMARY_COST if request_type == 'audio' else TEXT_SUMMARY_COST
            
            if transfer_amount <= 0:
                raise TransactionError(f"No valid token transfer found or insufficient amount: {transfer_amount}", "INVALID_TRANSFER")
                
            if transfer_amount < required_amount:
                raise TransactionError(f"Insufficient token amount: {transfer_amount}", "INSUFFICIENT_AMOUNT")
                
        except Exception as e:
            logger.error(f"Error checking token amount: {str(e)}")
            raise TransactionError("Error verifying token amount in transaction", "TOKEN_AMOUNT_VERIFICATION_ERROR") from e
            
        return True, "Transaction confirmed"

    except TransactionError as e:
        logger.error(f"Transaction error: {str(e)} (Code: {e.error_code})")
        return False, f"❌ {str(e)}"
    except Exception as e:
        logger.error(f"Error checking transaction: {str(e)}")
        return False, f"Error checking transaction: {str(e)}"
    finally:
        await client.close()

@with_retry(max_attempts=3)
async def check_job_status(job_id: str, space_url: str) -> Tuple[bool, str]:
    """Check the status of a summarization job with retry logic."""
    try:
        api_key = os.getenv('SQR_FUND_API_KEY')
        if not api_key:
            logger.error("SQR_FUND_API_KEY not found in environment variables")
            raise PermanentError("API key not configured") from None
        logger.debug("API key is present.")

        logger.debug(f"Checking job status for job ID: {job_id}")
        success, data, error = await api_request(
            'get',
            f"https://spaces.sqrfund.ai/api/jobs/{job_id}",
            headers={"X-API-Key": api_key}
        )
        
        if not success:
            logger.error(f"Job status check failed: {error}")
            logger.error(f"Response data: {data}")  # Log the response data for more context
            raise PermanentError("Job status check failed") from None

        # Access the job status from params
        job_status = data.get('job', {}).get('status')
        if job_status is None:
            logger.error("Response does not contain 'job' or 'status' key")
            raise PermanentError("Invalid response format: 'job' or 'status' key missing")

        if job_status == 'completed':
            # Proceed with summarization
            summary_url = "https://spaces.sqrfund.ai/api/summarize-spaces"
            
            # Add a 2-minute buffer before making the API request
            logger.info("Waiting for 2 minutes before summarizing the space...")
            await asyncio.sleep(120)  # Wait for 120 seconds (2 minutes)

            summary_response = await api_request(
                'post',
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
            # Check if the summarization request was successful
            logger.debug(f"Summary response received: {summary_response}")
            
            if summary_response[0]:
                summary_data = summary_response[1]
                summary_text = summary_data.get('summary', '✅ Space summarized successfully!')
                return True, summary_text  # Return as a single part
            else:
                logger.error(f"Failed to summarize space: {summary_response[2]}")
                # Log the entire response for debugging
                logger.error(f"Summary response details: {summary_response}")
                return False, f"❌ Failed to summarize space: {summary_response[2]}"
        elif job_status == 'failed':
            logger.error(f"Job failed: {data.get('job', {}).get('error', 'Unknown error')}")
            raise PermanentError(f"Job failed: {data.get('job', {}).get('error', 'Unknown error')}")
        else:
            logger.warning(f"Job status is still processing: {job_status}")
            raise TransientError("Job still processing")
        
    except httpx.HTTPError as e:
        logger.error(f"HTTP error while checking job status: {str(e)}")
        raise TransientError(f"Failed to check job status: {str(e)}") from e
    except Exception as e:
        logger.error(f"Unexpected error in check_job_status: {str(e)}")
        raise TransientError(f"Unexpected error: {str(e)}") from e

async def convert_text_to_audio(text: str, language: str = 'en') -> Tuple[Optional[str], Optional[str]]:
    """Convert text to audio using Google Text-to-Speech."""
    try:
        # Create a temporary directory if it doesn't exist
        temp_dir = tempfile.gettempdir()  # Use the system's temporary directory
        
        # Generate a unique filename
        filename = f"space_summary_{uuid.uuid4()}.mp3"
        filepath = os.path.join(temp_dir, filename)
        
        # Convert text to speech
        tts = gTTS(text=text, lang=language, slow=False)
        
        # Save the audio file
        tts.save(filepath)  # Direct save to the file path

        return filepath, None
    except Exception as e:
        logger.error(f"Error converting text to audio: {str(e)}")
        return None, f"Error converting text to audio: {str(e)}"

# Create a dictionary to hold locks for each job ID
job_locks = {}

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
    # Create or get the lock for this job_id
    if job_id not in job_locks:
        job_locks[job_id] = asyncio.Lock()
    
    async with job_locks[job_id]:  # Acquire the lock
        start_time = datetime.now()
        attempts = 0
        summarization_initiated = False  # Flag to track if summarization has been initiated
        
        while attempts < max_attempts:
            try:
                success, result = await check_job_status(job_id, space_url)
                
                if success:
                    if request_type == 'audio':
                        # Convert text to audio
                        audio_path, error = await convert_text_to_audio(result)
                        if audio_path and not error:
                            with open(audio_path, 'rb') as audio_file:
                                await context.bot.send_audio(
                                    chat_id=chat_id,
                                    audio=audio_file,
                                    caption="✅ Audio summary completed!",
                                    parse_mode=ParseMode.HTML
                                )
                            # Clean up the temporary audio file
                            try:
                                os.remove(audio_path)
                            except Exception as e:
                                logger.error(f"Error cleaning up audio file: {str(e)}")
                        else:
                            await context.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"❌ Failed to convert summary to audio: {error}",
                                parse_mode=ParseMode.HTML
                            )
                    else:
                        # Handle the summary text
                        summary_text = result  # Use the single result if not a list

                        logger.debug(f"Summary text received: {summary_text[:50]}...")  # Log the first 50 characters
                        # Check if the summary exceeds 4096 characters
                        if len(summary_text) > 4096:
                            # Split summary at sentence or paragraph boundaries when possible
                            parts = []
                            remaining = summary_text
                            max_length = 4096
                            while len(remaining) > max_length:
                                # Try to find a good split point (paragraph, sentence, or word boundary)
                                split_point = remaining[:max_length].rfind('\n\n')  # Try paragraph
                                if split_point < max_length // 2:  # If split point is too early in text
                                    split_point = remaining[:max_length].rfind('. ')  # Try sentence
                                if split_point < max_length // 2:  # If still too early
                                    split_point = remaining[:max_length].rfind(' ')  # Try word boundary
                                if split_point < 0:  # If no good split found
                                    split_point = max_length  # Just split at max length

                                parts.append(remaining[:split_point+1])
                                remaining = remaining[split_point+1:]

                            # Add any remaining text that's within the limit
                            if remaining:
                                parts.append(remaining)

                            # Verify all parts are within the limit
                            for i, part in enumerate(parts):
                                if len(part) > max_length:
                                    logger.warning(f"Part {i+1} exceeds max length ({len(part)} > {max_length})")
                                    # Split the oversized part
                                    while len(part) > max_length:
                                        split_point = part[:max_length].rfind('\n\n')
                                        if split_point < max_length // 2:
                                            split_point = part[:max_length].rfind('. ')
                                        if split_point < max_length // 2:
                                            split_point = part[:max_length].rfind(' ')
                                        if split_point < 0:
                                            split_point = max_length
                                        
                                        parts[i] = part[:split_point+1]
                                        part = part[split_point+1:]
                                        parts.insert(i+1, part)

                            logger.debug(f"Split summary into {len(parts)} parts with lengths: {[len(part) for part in parts]}")

                            # Send each part as a new message
                            for count, part in enumerate(parts, 1):
                                logger.debug(f"Part {count} of {len(parts)}: {part[:50]}..." if len(part) > 50 else part)
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"✅ Summary completed (part {count}/{len(parts)}):\n\n{part}\n\n",
                                    parse_mode=ParseMode.HTML
                                )
                            
                            # Send the edit suggestion message
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="If you would like a shorter version, please use /shorten_summary.\n\n"
                                "Alternatively, if you would like to make suggestions or edits, use the command /edit_summary.",
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            await context.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"✅ Summary completed!\n\n{summary_text}\n\n"
                                     "If you would like a shorter version, please use /shorten_summary.\n\n"
                                     "Alternatively, if you would like to make suggestions or edits, use the command /edit_summary.",
                                parse_mode=ParseMode.HTML
                            )
                    
                    reset_user_data(context)
                    return
                
                # Check for 502 error
                if "502 Bad Gateway" in result:
                    logger.error("Received 502 Bad Gateway Server Error during summarization")
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="❌ The summarization service is temporarily unavailable. Please try again later.\n"
                             "For refund, please contact @DarthCastelian.",   
                        parse_mode=ParseMode.HTML
                    )
                    return  # Exit the function after sending the message
                
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
            text="❌ Timeout: Could not complete summarization in time.\n"
            "For refunds, please contact @DarthCastelian.",
            parse_mode=ParseMode.HTML
        )
        reset_user_data(context)

async def handle_successful_transaction(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    message_text: str,
    space_url: str,
    request_type: str
) -> None:
    """Handle a successful transaction with improved error handling."""
    try:
        processing_msg = await message.reply_text(
            "⏳ Processing your request... This may take up to 5-7 minutes.",
            parse_mode=ParseMode.HTML
        )

        api_key = os.getenv('SQR_FUND_API_KEY')
        if not api_key:
            logger.error("SQR_FUND_API_KEY not found in environment variables")
            raise PermanentError("API key not configured") from None
        
        download_response = await api_request(
            'post',
            "https://spaces.sqrfund.ai/api/async/download-spaces",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key
            },
            json={
                "spacesUrl": space_url
            }
        )

        logger.debug(f"Download response received: {download_response}")

        if not download_response[0]:  # If the request failed
            logger.error(f"Failed to initiate space download: {download_response[2]}")
            raise PermanentError(f"Failed to initiate space download: {download_response[2]}") from None

        job_data = download_response[1]
        job_id = job_data.get('jobId')
        logger.debug(f"Job ID received: {job_id}")

        if not job_id:
            logger.error("No job ID received from download request")
            raise PermanentError("No job ID received from download request") from None

        # Start the periodic job check
        logger.debug(f"Starting periodic job check for job ID: {job_id}")
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
    except TransactionError as e:
        logger.error(f"Transaction error in handle_failed_transaction: {str(e)} (Code: {e.error_code})")
    except Exception as e:
        logger.error(f"Error in handle_failed_transaction: {str(e)}")
    finally:
        reset_user_data(context)

async def process_signature(signature: str, context: ContextTypes.DEFAULT_TYPE, message: Message):
    """Process a transaction signature."""

    command_start_time = context.user_data.get('command_start_time', datetime.now())
    space_url = context.user_data.get('space_url')
    request_type = context.user_data.get('request_type', 'text')
    
    # Initialize or increment the attempt counter
    attempts = context.user_data.get('signature_attempts', 0) + 1
    context.user_data['signature_attempts'] = attempts

    # Log the signature and attempt count
    logger.debug(f"Processing signature: {signature}, Attempt: {attempts}/3")

    # Check transaction status
    success, status_message = await check_transaction_status(
        signature, command_start_time, space_url, request_type
    )
    
    logger.debug(f"Transaction status check result: success={success}, message={status_message}")

    if success:
        await handle_successful_transaction(
            context, message, message.text, space_url, request_type
        )
        # Reset attempts after a successful transaction
        context.user_data['signature_attempts'] = 0
    else:
        logger.warning(f"Signature processing failed: {status_message}")
        if attempts >= 3:
            logger.error("Maximum attempts reached for signature processing. Please try again.")
            await handle_failed_transaction(context, message, message.text, request_type)
            reset_user_data(context)  # Reset user data after 3 failed attempts
        else:
            await message.reply_text(
                f"❌ Attempt {attempts}/3 failed.\n"
                f"Reason: {status_message}.\n"
                "Please try again with a valid signature.",
                parse_mode=ParseMode.HTML
            )

async def edit_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /edit_summary command to allow users to suggest edits."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide the content for the summary edit.",
            parse_mode=ParseMode.HTML
        )
        return

    full_prompt = " ".join(context.args)
    parts = full_prompt.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Please provide both the space URL and the edit prompt.",
            parse_mode=ParseMode.HTML
        )
        return

    space_url = parts[0]
    custom_prompt = parts[1]
    
    # Validate the length of custom_prompt
    if len(custom_prompt) > MAX_PROMPT_LENGTH:
        await update.message.reply_text(
            f"❌ Your edit prompt is too long. Please limit it to {MAX_PROMPT_LENGTH} characters.",
            parse_mode=ParseMode.HTML
        )
        return

    # Sanitize the custom_prompt to remove potentially harmful content
    sanitized_prompt = sanitize_input(custom_prompt)

    if not is_valid_space_url(space_url):
        await update.message.reply_text(
            "❌ Invalid space URL format. Please provide a valid URL.",
            parse_mode=ParseMode.HTML
        )
        return

    api_key = os.getenv('SQR_FUND_API_KEY')
    if not api_key:
        await update.message.reply_text(
            "❌ API key not configured. Please contact support.",
            parse_mode=ParseMode.HTML
        )
        return

    processing_msg = await update.message.reply_text(
        "🔄 Processing your edit request (this may take up to 60 seconds)...",
        parse_mode=ParseMode.HTML
    )

    edit_response = await api_request(
        'post',
        "https://spaces.sqrfund.ai/api/summarize-spaces",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key
        },
        json={
            "spacesUrl": space_url,
            "customPrompt": sanitized_prompt  # Use the sanitized prompt
        }
    )

    # Use the new helper function to process the response
    await process_summary_api_response(context, update, edit_response, processing_msg)

async def shorten_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /shorten_summary command to allow users to shorten the summary."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide the space URL.",
            parse_mode=ParseMode.HTML
        )
        return

    space_url = context.args[0]

    if not is_valid_space_url(space_url):
        await update.message.reply_text(
            "❌ Invalid space URL format. Please provide a valid URL.",
            parse_mode=ParseMode.HTML
        )
        return

    # Create a default prompt to shorten the summary
    custom_prompt = "Please summarize the content in a concise manner while keeping it under 1000 words."

    api_key = os.getenv('SQR_FUND_API_KEY')
    if not api_key:
        await update.message.reply_text(
            "❌ API key not configured. Please contact support.",
            parse_mode=ParseMode.HTML
        )
        return

    processing_msg = await update.message.reply_text(
        "🔄 Processing your shortening request (this may take up to 60 seconds)...",
        parse_mode=ParseMode.HTML
    )

    edit_response = await api_request(
        'post',
        "https://spaces.sqrfund.ai/api/summarize-spaces",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key
        },
        json={
            "spacesUrl": space_url,
            "customPrompt": sanitize_input(custom_prompt)  # Use the sanitized prompt
        }
    )

    # Use the new helper function to process the response
    await process_summary_api_response(context, update, edit_response, processing_msg)

def validate_request_type(request_type: str) -> bool:
    """Validate the request type is either 'text' or 'audio'."""
    return request_type.lower() in ('text', 'audio')

async def summarize_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summarize_space command with improved error handling."""
    try:
        if not context.args:
            await update.message.reply_text(
                "Please provide the X Space URL and the request type (text or audio) after the command.\n\n"
                "Example: `/summarize_space https://x.com/i/spaces/YOUR_SPACE_ID text`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        space_url = context.args[0]
        if not is_valid_space_url(space_url):
            await update.message.reply_text(
                "Please provide the X Space URL and the request type (text or audio) after the command.\n\n"
                "Example: `/summarize_space https://x.com/i/spaces/YOUR_SPACE_ID text`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Determine request type (text or audio)
        if len(context.args) > 1:
            request_type = context.args[1].lower()
            if not validate_request_type(request_type):
                await update.message.reply_text(
                    "❌ Invalid request type. Please specify either 'text' or 'audio'.",
                    parse_mode=ParseMode.HTML
                )
                return
        else:
            request_type = 'text'  # Default to text if not specified

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

        purchase_link = SQR_PURCHASE_LINK
        
        await update.message.reply_text(
            "🔄 <b>Space Summarization Process</b>\n\n"
            f"Request Type: <b>{request_type.upper()}</b>\n"
            f"Required Amount: <b>{cost} $SQR</b>\n\n"
            f"<a href='{purchase_link}'>Buy SQR on Bonkbot</a>\n\n"
            "To proceed with space summarization, please follow these steps:\n\n"
            "1. Send the required $SQR tokens to this address:\n"
            f"<code>{RECIPIENT_WALLET}</code>\n"
            "2. Copy the transaction signature.\n"
            "3. Paste the signature in this chat.\n\n"
            f"⚠️ <i>Note: The transaction must be completed within {TRANSACTION_TIMEOUT_MINUTES} minutes from now.</i>\n"
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