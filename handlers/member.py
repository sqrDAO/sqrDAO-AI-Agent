from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from typing import Optional
import logging
from datetime import datetime
import json
from utils.utils import format_response_for_telegram
from config import ERROR_MESSAGES, SUCCESS_MESSAGES

logger = logging.getLogger(__name__)

async def request_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /request_member command - Request to be added as a member."""
    user = update.effective_user
    user_id = user.id

    # Check if the user is already a member
    if find_authorized_member_by_username(user.username, context) or find_member_by_username(user.username, context):
        await update.message.reply_text(
            "❌ You are already a member and cannot request membership again.",
            parse_mode=ParseMode.HTML
        )
        return

    # Store the user ID in pending_requests
    if user.username:
        context.bot_data['pending_requests'][user.username] = {
            'user_id': user_id,
            'username': user.username,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
    else:
        context.bot_data['pending_requests'][f"@{user_id}"] = {
            'user_id': user_id,
            'username': str(user_id),
            'timestamp': datetime.now(),
            'status': 'pending'
        }

    # Notify authorized members
    for member in context.bot_data['authorized_members']:
        try:
            await context.bot.send_message(
                chat_id=member['user_id'],
                text=f"🔔 New member request from {user.username}\n\n"
                     f"Use /approve_member {user_id} to approve or\n"
                     f"/reject_member {user_id} to reject"
            )
        except Exception as e:
            logger.error(f"Failed to notify authorized member {member['username']}: {str(e)}")
    
    await update.message.reply_text(
        "✅ Your membership request has been submitted!\n"
        "Our team will review it and get back to you soon.",
        parse_mode=ParseMode.HTML
    )

async def approve_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /approve_member command - Approve a member request."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to approve.\n"
            "Usage: /approve_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    if username not in context.bot_data['pending_requests']:
        await update.message.reply_text(
            "❌ No pending request found for this username.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Get user ID from pending requests
    user_id = context.bot_data['pending_requests'][username]['user_id']
    
    # Add to members list
    new_member = {
        'username': username,
        'user_id': user_id
    }
    context.bot_data['members'].append(new_member)
    
    # Store the member in the knowledge base
    try:
        current_members = context.bot_data['members']
        context.bot_data['db'].store_knowledge("members", json.dumps(current_members))
    except Exception as e:
        logger.error(f"Error storing member data: {str(e)}")
    
    # Remove from pending requests
    del context.bot_data['pending_requests'][username]
    
    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Congratulations! Your membership request has been approved!\n\n"
                 "You now have access to member-only features. Use /help to see available commands."
        )
    except Exception as e:
        logger.error(f"Failed to notify approved user: {str(e)}")
    
    await update.message.reply_text(
        f"✅ Successfully approved @{username} as a member.",
        parse_mode=ParseMode.HTML
    )

async def reject_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reject_member command - Reject a member request."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to reject.\n"
            "Usage: /reject_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    if username not in context.bot_data['pending_requests']:
        await update.message.reply_text(
            "❌ No pending request found for this username.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Get user ID from pending requests before removing it
    user_id = context.bot_data['pending_requests'][username]['user_id']
    
    # Remove from pending requests
    del context.bot_data['pending_requests'][username]
    
    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Your membership request has been rejected.\n\n"
                 "If you believe this was a mistake, please use /contact command to contact the team."
        )
    except Exception as e:
        logger.error(f"Failed to notify rejected user: {str(e)}")
    
    await update.message.reply_text(
        f"✅ Successfully rejected @{username}'s membership request.",
        parse_mode=ParseMode.HTML
    )

async def list_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_requests command - List pending member requests."""
    if not context.bot_data['pending_requests']:
        await update.message.reply_text(
            "📝 No pending member requests.",
            parse_mode=ParseMode.HTML
        )
        return
    
    requests_text = "<b>Pending Member Requests:</b>\n\n"
    for username, request in context.bot_data['pending_requests'].items():
        requests_text += f"• @{username} (Requested: {request['timestamp'].strftime('%Y-%m-%d %H:%M:%S')})\n"

    await update.message.reply_text(requests_text, parse_mode=ParseMode.HTML)

async def list_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_members command - List all members."""
    try:
        if not context.bot_data.get('members'):
            await update.message.reply_text(
                "📝 No members found.",
                parse_mode=ParseMode.HTML
            )
            return
        
        members_text = "<b>Current Members:</b>\n\n"
        for member in context.bot_data['members']:
            try:
                # Handle dictionary format
                if isinstance(member, dict):
                    username = member.get('username', 'Unknown')
                    user_id = member.get('user_id', 'Unknown')
                # Handle list/tuple format
                elif isinstance(member, (list, tuple)) and len(member) >= 2:
                    username = member[0]
                    user_id = member[1]
                else:
                    logger.error(f"Invalid member data format: {member}")
                    continue
                
                members_text += f"• @{username} (User ID: {user_id})\n"
            except Exception as e:
                logger.error(f"Error formatting member data: {str(e)}")
                continue
        
        await update.message.reply_text(members_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in list_members: {str(e)}")
        await update.message.reply_text(
            "❌ An error occurred while listing members. Please try again later.",
            parse_mode=ParseMode.HTML
        )

async def resources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resources command - Available to all members."""
    resources_text = """
<b>sqrDAO Members' and sqrFUND Chads' Resources</b>

Here are our internal resources:
• <b>GitHub:</b> https://github.com/sqrdao
• <b>AWS Credits Guide:</b> https://drive.google.com/file/d/12DjM2P5x0T_koLI6o_UMXMo_LUJpYrXL/view?usp=sharing
• <b>AWS Org ID ($10K):</b> 3Ehcy
• <b>Legal Service (20% off):</b> https://teamoutlaw.io/
• <b>sqrDAO & sqrFUND Brand Kit:</b> https://sqrdao.notion.site/sqrdao-brand-kit
• <b>$SQR CHADS TG group:</b> https://t.me/+Yh6VkC81BdljZDg1

For access issues, please contact @DarthCastelian.
"""
    await update.message.reply_text(resources_text, parse_mode=ParseMode.HTML)

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_groups command - List all tracked groups."""
    try:
        if not context.bot_data.get('group_members'):
            await update.message.reply_text(
                "📝 No groups are currently being tracked.",
                parse_mode=ParseMode.HTML
            )
            return
        
        groups_text = "<b>Tracked Groups:</b>\n\n"
        groups = context.bot_data['group_members']
        
        # Handle case where groups is a list of dictionaries or a single list
        if isinstance(groups, list):
            if not groups:
                await update.message.reply_text(
                    "📝 No groups are currently being tracked.",
                    parse_mode=ParseMode.HTML
                )
                return
                
            # If first item is a dictionary, treat as list of dictionaries
            if isinstance(groups[0], dict):
                for group in groups:
                    # Check for title field which is used in the database
                    group_name = group.get('title', group.get('name', 'Unknown'))
                    group_id = group.get('id', 'Unknown')
                    groups_text += f"• {group_name} (ID: {group_id})\n"
            # If first item is a list/tuple, treat as list of lists/tuples
            elif isinstance(groups[0], (list, tuple)):
                for group in groups:
                    if len(group) >= 2:
                        groups_text += f"• {group[0]} (ID: {group[1]})\n"
                    else:
                        groups_text += f"• Unknown Group (Invalid format)\n"
            else:
                groups_text += f"• Unknown Group (Invalid format)\n"
        else:
            groups_text += f"• Unknown Group (Invalid format)\n"
        
        await update.message.reply_text(groups_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in list_groups: {str(e)}")
        await update.message.reply_text(
            "❌ An error occurred while listing groups. Please try again later.",
            parse_mode=ParseMode.HTML
        )

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_group command - Add a group to tracking list."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a group ID.\n"
            "Usage: /add_group <group_id>",
            parse_mode=ParseMode.HTML
        )
        return
    
    group_id = context.args[0]
    group_name = context.args[1] if len(context.args) > 1 else f"Group {group_id}"
    
    # Check if group already exists
    if any(group['id'] == group_id for group in context.bot_data['group_members']):
        await update.message.reply_text(
            "❌ This group is already being tracked.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Add group with title field to match database format
    context.bot_data['group_members'].append({
        'id': group_id,
        'title': group_name,
        'type': 'group',
        'added_at': datetime.now().isoformat()
    })
    
    # Store the updated groups in the database
    try:
        context.bot_data['db'].store_knowledge("groups", json.dumps(context.bot_data['group_members']))
    except Exception as e:
        logger.error(f"Error storing group data: {str(e)}")
    
    await update.message.reply_text(
        f"✅ Successfully added {group_name} to tracked groups.",
        parse_mode=ParseMode.HTML
    )

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove_group command - Remove a group from tracking list."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a group ID.\n"
            "Usage: /remove_group <group_id>",
            parse_mode=ParseMode.HTML
        )
        return
    
    group_id = context.args[0]
    
    # Find and remove group
    for i, group in enumerate(context.bot_data['group_members']):
        if group['id'] == group_id:
            group_name = group.get('title', 'Unknown Group')
            context.bot_data['group_members'].pop(i)
            
            # Store the updated groups in the database
            try:
                context.bot_data['db'].store_knowledge("groups", json.dumps(context.bot_data['group_members']))
            except Exception as e:
                logger.error(f"Error storing group data: {str(e)}")
                
            await update.message.reply_text(
                f"✅ Successfully removed {group_name} from tracked groups.",
                parse_mode=ParseMode.HTML
            )
            return
    
    await update.message.reply_text(
        "❌ Group not found in tracking list.",
        parse_mode=ParseMode.HTML
    )

def find_authorized_member_by_username(username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Find an authorized member by username."""
    for member in context.bot_data['authorized_members']:
        if member['username'] == username:
            return member
    return None

def find_member_by_username(username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Find a regular member by username."""
    for member in context.bot_data['members']:
        if member['username'] == username:
            return member
    return None 