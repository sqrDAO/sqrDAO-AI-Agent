from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from utils.utils import format_response_for_telegram, get_announcement_prefix, parse_mass_message_input, escape_markdown_v2
from config import ERROR_MESSAGES, SUCCESS_MESSAGES

logger = logging.getLogger(__name__)

async def mass_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mass_message command - Send a message to all users and groups."""
    if not context.args:
        help_text = (
            "❌ Please provide the message to send.\n"
            "Usage: /mass_message &lt;group_type&gt; &lt;message&gt;\n\n"
            "Group types:\n"
            "• sqrdao - Send to sqrDAO groups only\n"
            "• sqrfund - Send to sqrFUND groups only\n"
            "• summit - Send to Summit groups only\n"
            "• both - Send to both sqrDAO and sqrFUND groups\n"
            "• all - Send to all groups and members"
        )
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.HTML
        )
        return

    # Parse the input to get group type and message
    message, group_type = parse_mass_message_input(' '.join(context.args))
    if not message:
        await update.message.reply_text(
            "❌ Please provide a valid message to send.",
            parse_mode=ParseMode.HTML
        )
        return

    # Get the appropriate prefix based on group type
    prefix = get_announcement_prefix(group_type)
    full_message = f"{prefix}{message}"

    # Initialize counters
    success_count = 0
    fail_count = 0
    failed_targets = []

    # Send to groups based on type
    for group in context.bot_data.get('group_members', []):
        group_id = group.get('id')
        group_name = group.get('title', 'Unknown Group')
        
        # Filter groups based on type
        if group_type == 'sqrdao' and 'sqrdao' not in group_name.lower():
            continue
        elif group_type == 'sqrfund' and 'sqrfund' not in group_name.lower():
            continue
        elif group_type == 'summit' and 'summit' not in group_name.lower():
            continue
        elif group_type == 'both' and not ('sqrdao' in group_name.lower() or 'sqrfund' in group_name.lower()):
            continue

        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=format_response_for_telegram(full_message),
                parse_mode=ParseMode.HTML
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send message to group {group_name}: {str(e)}")
            fail_count += 1
            failed_targets.append(f"Group: {group_name}")

    # Send to regular members (excluding authorized members)
    if group_type in ['all', 'both']:
        authorized_usernames = {member['username'] for member in context.bot_data.get('authorized_members', [])}
        for member in context.bot_data.get('members', []):
            if member['username'] in authorized_usernames:
                continue

            try:
                await context.bot.send_message(
                    chat_id=member['user_id'],
                    text=format_response_for_telegram(full_message),
                    parse_mode=ParseMode.HTML
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send message to member {member['username']}: {str(e)}")
                fail_count += 1
                failed_targets.append(f"Member: @{member['username']}")

    # Prepare response message
    response = f"✅ Mass message sent!\n\n"
    response += f"Successfully sent to: {success_count} targets\n"
    response += f"Failed to send to: {fail_count} targets\n"

    if failed_targets:
        response += "\nFailed targets:\n"
        response += "\n".join(f"• {target}" for target in failed_targets)

    await update.message.reply_text(
        format_response_for_telegram(response),
        parse_mode=ParseMode.HTML
    ) 