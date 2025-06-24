import telegram
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from utils.utils import parse_mass_message_input, get_error_message
from handlers.general import find_authorized_member_by_username

logger = logging.getLogger(__name__)

async def mass_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mass_message command - Send a message with optional image, video, or document to all users and groups."""
    if update.message.chat.type in ['group', 'supergroup']:
        logger.warning("Mass message command is not allowed in groups.")
        return  # Exit if the command is invoked in a group chat

    try:
        # Log the received message for debugging
        logger.debug(f"Received mass message with caption: {update.message.caption}")

        # Check if effective_user is None
        if update.effective_user is None:
            await update.message.reply_text("❌ This command can only be used by users.")
            return

        if not find_authorized_member_by_username(update.effective_user['username'], context):
            await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
            return

        # Initialize variables
        media = None
        caption = None
        grouptype = None
        message = None

        # Check if there's an image, video, or document with caption
        if update.message.photo or update.message.video or update.message.document:
            if update.message.photo:
                media = update.message.photo[-1].file_id
            elif update.message.video:
                media = update.message.video.file_id
            elif update.message.document:
                media = update.message.document.file_id
            
            caption = update.message.caption if update.message.caption else ""
            
            # Extract message and grouptype from caption if it contains the command
            if caption and caption.startswith('/mass_message'):
                message, grouptype = parse_mass_message_input(caption.replace('/mass_message', '', 1))
            else:
                message = caption
        else:
            # Handle text-only message
            if not context.args:
                help_text = (
                    "❌ Please provide a message and an optional grouptype.\n"
                    "Usage:\n"
                    "• /mass_message [message] | [grouptype]\n"
                    "• Example: /mass_message Hello|n|World | sqrdao\n"
                    "If grouptype is 'sqrdao', 'sqrfund', 'summit', or 'web3', the message will only be sent to groups/channels with that keyword in their title.\n\n"
                    "<b>Tip:</b> For multi-line messages, use <code>|n|</code> to insert a line break. Example: <code>/mass_message Line 1|n|Line 2|n|Line 3</code>"
                )
                try:
                    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
                except telegram.error.BadRequest as e:
                    logger.error(f"Failed to send message: {str(e)}. The chat may be closed or invalid.")
                return
            
            # Parse the message and grouptype from arguments
            raw_line = " ".join(context.args)
            message, grouptype = parse_mass_message_input(raw_line)

        # Replace custom line break token with actual line breaks
        if message:
            message = message.replace('|n|', '\n')  # Allow users to specify line breaks in text-only messages

        if not message and not media:
            await update.message.reply_text(
                "❌ Please provide a message or media to send.",
                parse_mode=ParseMode.HTML
            )
            return

        # Get all groups and channels where the bot is a member
        all_groups = context.bot_data.get('group_members', [])

        # Filter groups based on grouptype if specified
        if grouptype == "sqrdao":
            filtered_groups = [g for g in all_groups if "sqrdao" in g['title'].lower()]
        elif grouptype == "summit":
            filtered_groups = [g for g in all_groups if "summit" in g['title'].lower()]
        elif grouptype == "sqrfund":
            filtered_groups = [g for g in all_groups if "sqrfund" in g['title'].lower()]
        elif grouptype == "web3":
            filtered_groups = [g for g in all_groups if "web3" in g['title'].lower()]
        elif grouptype == "both":
            filtered_groups = [g for g in all_groups if "sqrdao" in g['title'].lower() or "summit" in g['title'].lower() or "sqrfund" in g['title'].lower()]
        else:
            filtered_groups = all_groups

        # Get all regular users (excluding authorized members)
        authorized_usernames = {member['username'] for member in context.bot_data.get('authorized_members', [])}
        valid_users = [user for user in context.bot_data.get('members', []) if user.get('user_id') and user['username'] not in authorized_usernames]

        if not valid_users and not filtered_groups:
            await update.message.reply_text(
                "❌ No valid users or groups/channels found to send the message to.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Send confirmation to the sender
        group_type_msg = " (sqrDAO groups only)" if grouptype == "sqrdao" else " (Summit groups only)" if grouptype == "summit" else " (sqrFUND groups only)" if grouptype == "sqrfund" else " (All groups)"
        await update.message.reply_text(
            f"📤 Starting to send {'image' if media and update.message.photo else 'video' if media and update.message.video else 'document' if media and update.message.document else 'message'} to {len(valid_users) if not grouptype else 0} users and {len(filtered_groups)} groups/channels{group_type_msg}...",
            parse_mode=ParseMode.HTML
        )
        
        # Track success and failure counts
        user_success_count = 0
        user_failure_count = 0
        group_success_count = 0
        group_failure_count = 0
        failed_users = []
        failed_groups = []

        # Only send to users if no grouptype is specified
        if not grouptype:
            for user in valid_users:
                try:
                    if media:
                        # Send media (image, video, or document) with caption
                        formatted_caption = f"{message}" if message else None
                        logger.debug(f"Sending media to user {user['user_id']}")
                        if update.message.photo:
                            await context.bot.send_photo(
                                chat_id=user['user_id'],
                                photo=media,
                                caption=formatted_caption,
                                parse_mode=ParseMode.HTML if formatted_caption else None
                            )
                        elif update.message.video:
                            await context.bot.send_video(
                                chat_id=user['user_id'],
                                video=media,
                                caption=formatted_caption,
                                parse_mode=ParseMode.HTML if formatted_caption else None
                            )
                        elif update.message.document:
                            await context.bot.send_document(
                                chat_id=user['user_id'],
                                document=media,
                                caption=formatted_caption,
                                parse_mode=ParseMode.HTML if formatted_caption else None
                            )
                    else:
                        # Send text message
                        await context.bot.send_message(
                            chat_id=user['user_id'],
                            text=message,
                            parse_mode=ParseMode.HTML
                        )
                    user_success_count += 1
                    
                except Exception as e:
                    user_failure_count += 1
                    failed_users.append(f"@{user['username']}")
                    logger.error(f"Failed to send to user @{user['username']}: {str(e)}")

        for group in filtered_groups:
            try:
                if media:
                    # Send media (image, video, or document) with caption
                    formatted_caption = f"{message}" if message else None
                    if update.message.photo:
                        await context.bot.send_photo(
                            chat_id=group['id'],
                            photo=media,
                            caption=formatted_caption,
                            parse_mode=ParseMode.HTML if formatted_caption else None
                        )
                    elif update.message.video:
                        await context.bot.send_video(
                            chat_id=group['id'],
                            video=media,
                            caption=formatted_caption,
                            parse_mode=ParseMode.HTML if formatted_caption else None
                        )
                    elif update.message.document:
                        await context.bot.send_document(
                            chat_id=group['id'],
                            document=media,
                            caption=formatted_caption,
                            parse_mode=ParseMode.HTML if formatted_caption else None
                        )
                else:
                    # Send text message
                    await context.bot.send_message(
                        chat_id=group['id'],
                        text=f"{message}",
                        parse_mode=ParseMode.HTML
                    )
                group_success_count += 1
                
            except Exception as e:
                group_failure_count += 1
                failed_groups.append(f"{group['title']} ({group['type']})")
                logger.error(f"Failed to send to group/channel {group['title']} (ID: {group['id']}): {str(e)}")
        
        # Send summary to the sender
        summary = f"✅ {'Image' if media and update.message.photo else 'Video' if media and update.message.video else 'Document' if media and update.message.document else 'Message'} delivery complete!\n\n"
        
        if grouptype == "sqrdao":
            summary += "📝 Message was sent to sqrDAO groups only\n\n"
        elif grouptype == "summit":
            summary += "📝 Message was sent to Summit groups only\n\n"
        elif grouptype == "sqrfund":
            summary += "📝 Message was sent to sqrFUND groups only\n\n"
        elif grouptype == "web3":
            summary += "📝 Message was sent to Web3 community groups only\n\n"
        elif grouptype == "both":
            summary += "📝 Message was sent to both sqrDAO and sqrFUND groups\n\n"
        else:
            summary += "📝 Message was sent to all groups\n\n"
        
        if failed_users:
            summary += "❌ Failed to send to users:\n"
            summary += "\n".join(f"• {user}" for user in failed_users[:5])
            if len(failed_users) > 5:
                summary += f"\n... and {len(failed_users) - 5} more users"
        
        summary += "\n\n📊 User Statistics:\n"
        summary += f"• Successfully sent: {user_success_count}\n"
        summary += f"• Failed to send: {user_failure_count}\n"
        
        summary += "\n\n📊 Group/Channel Statistics:\n"
        summary += f"• Successfully sent: {group_success_count}\n"
        summary += f"• Failed to send: {group_failure_count}\n"
        
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error in mass_message: {str(e)}")
        await update.message.reply_text(get_error_message('general_error')) 