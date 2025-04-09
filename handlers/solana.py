from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from typing import Optional, Tuple
from datetime import datetime
import asyncio
import traceback
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TokenAccountOpts
from spl.token.instructions import get_associated_token_address
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from spl.token.client import Token
from utils.utils import format_response_for_telegram, get_sqr_info, resolve_sns_domain
from base58 import b58decode
from handlers.spaces import handle_successful_transaction, handle_failed_transaction
from config import (
    SOLANA_RPC_URL,
    SQR_TOKEN_MINT,
    RECIPIENT_WALLET,
    TOKEN_PROGRAM_ID,
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST,
    TRANSACTION_TIMEOUT_MINUTES,
    ERROR_MESSAGES,
    SUCCESS_MESSAGES
)
import httpx

logger = logging.getLogger(__name__)

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
    token_mint = SQR_TOKEN_MINT
    
    # Check if input is an SNS domain
    wallet_address = None
    display_address = input_address
    if input_address.lower().endswith('.sol') or not (input_address.startswith('1') or input_address.startswith('2') or input_address.startswith('3') or input_address.startswith('4') or input_address.startswith('5') or input_address.startswith('6') or input_address.startswith('7') or input_address.startswith('8') or input_address.startswith('9')):
        try:
            # Remove .sol extension if present for the API call
            domain = input_address.lower().replace('.sol', '')
            logger.info(f"Attempting to resolve SNS domain: {domain}")
            
            resolved_address = await resolve_sns_domain(domain)
            if resolved_address:
                wallet_address = resolved_address
                display_address = f"{input_address} ({wallet_address[:4]}...{wallet_address[-4:]})"
                logger.info(f"Successfully resolved domain to address: {wallet_address}")
            else:
                logger.warning(f"SNS domain not found: {input_address}")
                await update.message.reply_text(
                    f"❌ SNS domain not found: {input_address}\n"
                    f"Please verify the domain exists or try using a wallet address instead.",
                    parse_mode=ParseMode.HTML
                )
                return
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"SNS domain not found: {input_address}")
                await update.message.reply_text(
                    f"❌ SNS domain not found: {input_address}\n"
                    f"Please verify the domain exists or try using a wallet address instead.",
                    parse_mode=ParseMode.HTML
                )
            else:
                logger.error(f"HTTP error resolving SNS domain {input_address}: {str(e)}")
                await update.message.reply_text(
                    f"❌ Error resolving SNS domain: {input_address}\n"
                    f"Please try again later or use a wallet address instead.",
                    parse_mode=ParseMode.HTML
                )
            return
        except Exception as e:
            logger.error(f"Error resolving SNS domain: {str(e)}")
            logger.error(f"Full error traceback: {traceback.format_exc()}")
            await update.message.reply_text(
                f"❌ Error resolving SNS domain: {input_address}\n"
                f"Please try again later or use a wallet address instead.",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        wallet_address = input_address
        display_address = f"{wallet_address[:4]}...{wallet_address[-4:]}"

    try:
        # Initialize Solana client
        client = AsyncClient(SOLANA_RPC_URL, commitment=Commitment("confirmed"))
        
        # Validate addresses
        try:
            logger.info(f"Attempting to validate wallet address: {wallet_address}")
            logger.info(f"Attempting to validate token program ID: {TOKEN_PROGRAM_ID}")
            logger.info(f"Attempting to validate token mint: {token_mint}")
            
            # Decode base58 addresses to bytes
            wallet_bytes = b58decode(wallet_address)
            token_program_bytes = b58decode(TOKEN_PROGRAM_ID)
            token_mint_bytes = b58decode(token_mint)
            
            wallet_pubkey = Pubkey.from_bytes(wallet_bytes)
            logger.info(f"Successfully created wallet pubkey: {wallet_pubkey}")
            
            token_program_id = Pubkey.from_bytes(token_program_bytes)
            logger.info(f"Successfully created token program pubkey: {token_program_id}")
            
            # Create a dummy keypair as payer since we're only reading data
            dummy_payer = Keypair()
            logger.info("Created dummy payer keypair")
            
            # Initialize token client with program ID and payer
            token = Token(
                conn=client,
                pubkey=Pubkey.from_bytes(token_mint_bytes),
                program_id=token_program_id,
                payer=dummy_payer
            )
        except ValueError as ve:
            logger.error(f"ValueError during address validation: {str(ve)}")
            logger.error(f"Input wallet address: {wallet_address}")
            logger.error(f"Input token program ID: {TOKEN_PROGRAM_ID}")
            await update.message.reply_text(
                "❌ Invalid wallet address format.",
                parse_mode=ParseMode.HTML
            )
            return
        except Exception as e:
            logger.error(f"Unexpected error during address validation: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Input wallet address: {wallet_address}")
            logger.error(f"Input token program ID: {TOKEN_PROGRAM_ID}")
            await update.message.reply_text(
                "❌ Error validating wallet address.",
                parse_mode=ParseMode.HTML
            )
            return

        # Get token accounts
        token_accounts = await token.get_accounts_by_owner_json_parsed(owner=wallet_pubkey)
        
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
            token_metadata = await client.get_account_info_json_parsed(Pubkey.from_bytes(token_mint_bytes))
            
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
    finally:
        await client.close()

async def sqr_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sqr_info command - Get information about SQR token."""
    try:
        # Get SQR token info from GeckoTerminal
        token_info = await get_sqr_info()
        
        if not token_info:
            await update.message.reply_text(
                "❌ Could not fetch SQR token information. Please try again later.",
                parse_mode=ParseMode.HTML
            )
            return
            
        # Extract relevant information
        data = token_info.get('data', {}).get('attributes', {})
        
        # Format the response
        info_text = f"<b>💰 SQR Token Information</b>\n\n"
        info_text += f"• Price: ${data.get('price_usd', 'N/A')}\n"
        info_text += f"• 24h Change: {data.get('price_change_percentage', {}).get('h24', 'N/A')}%\n"
        info_text += f"• Market Cap: ${data.get('market_cap_usd', 'N/A')}\n"
        info_text += f"• 24h Volume: ${data.get('volume_usd', {}).get('h24', 'N/A')}\n"
        info_text += f"• Holders: {data.get('holders', 'N/A')}\n"
        info_text += f"• Total Supply: {data.get('total_supply', 'N/A')} SQR\n"
        info_text += f"• Circulating Supply: {data.get('circulating_supply', 'N/A')} SQR\n\n"
        info_text += f"<b>Token Address:</b>\n{SQR_TOKEN_MINT}\n\n"
        info_text += f"<b>Use Cases:</b>\n"
        info_text += f"• Text summary: {TEXT_SUMMARY_COST} SQR\n"
        info_text += f"• Audio summary: {AUDIO_SUMMARY_COST} SQR"
        
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Error getting SQR info: {str(e)}")
        await update.message.reply_text(
            "❌ Error fetching SQR token information. Please try again later.",
            parse_mode=ParseMode.HTML
        )

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