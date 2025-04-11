from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from spl.token.client import Token
from utils.utils import get_sqr_info, resolve_sns_domain
from base58 import b58decode
from config import (
    SOLANA_RPC_URL,
    SQR_TOKEN_MINT,
    TOKEN_PROGRAM_ID,
    TEXT_SUMMARY_COST,
    AUDIO_SUMMARY_COST
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
            
            resolved_address = await resolve_sns_domain(domain)
            if resolved_address:
                wallet_address = resolved_address
                display_address = f"{input_address} ({wallet_address[:4]}...{wallet_address[-4:]})"
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
                await update.message.reply_text(
                    f"❌ Error resolving SNS domain: {input_address}\n"
                    f"Please try again later or use a wallet address instead.",
                    parse_mode=ParseMode.HTML
                )
            return
        except Exception:
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
            # Decode base58 addresses to bytes
            wallet_bytes = b58decode(wallet_address)
            token_program_bytes = b58decode(TOKEN_PROGRAM_ID)
            token_mint_bytes = b58decode(token_mint)
            
            wallet_pubkey = Pubkey.from_bytes(wallet_bytes)
            
            token_program_id = Pubkey.from_bytes(token_program_bytes)
            
            # Create a dummy keypair as payer since we're only reading data
            dummy_payer = Keypair()
            
            # Initialize token client with program ID and payer
            token = Token(
                conn=client,
                pubkey=Pubkey.from_bytes(token_mint_bytes),
                program_id=token_program_id,
                payer=dummy_payer
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid wallet address format.",
                parse_mode=ParseMode.HTML
            )
            return
        except Exception:
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
        except Exception:
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

    except Exception:
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
        info_text = "<b>💰 SQR Token Information</b>\n\n"
        info_text += f"• Price: ${data.get('price_usd', 'N/A')}\n"
        info_text += f"• 24h Change: {data.get('price_change_percentage', {}).get('h24', 'N/A')}%\n"
        info_text += f"• Market Cap: ${data.get('market_cap_usd', 'N/A')}\n"
        info_text += f"• 24h Volume: ${data.get('volume_usd', {}).get('h24', 'N/A')}\n"
        info_text += f"• Holders: {data.get('holders', 'N/A')}\n"
        info_text += f"• Total Supply: {data.get('total_supply', 'N/A')} SQR\n"
        info_text += f"• Circulating Supply: {data.get('circulating_supply', data.get('total_supply', 'N/A'))} SQR\n\n"
        info_text += f"<b>Token Address:</b>\n{SQR_TOKEN_MINT}\n\n"
        info_text += "<b>Use Cases:</b>\n"
        info_text += f"• X Space Text summary: {TEXT_SUMMARY_COST} SQR\n"
        info_text += f"• X Space Audio summary: {AUDIO_SUMMARY_COST} SQR"
        
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)
        
    except Exception:
        await update.message.reply_text(
            "❌ Error fetching SQR token information. Please try again later.",
            parse_mode=ParseMode.HTML
        )