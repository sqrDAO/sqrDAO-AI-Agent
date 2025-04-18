from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import logging
from datetime import datetime
import json
from handlers.general import find_authorized_member_by_username, find_member_by_username

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
    if 'pending_requests' not in context.bot_data:
        context.bot_data['pending_requests'] = {}

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
    if 'authorized_members' not in context.bot_data:
        context.bot_data['authorized_members'] = []
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
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to approve.\n"
            "Usage: /approve_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    pending_requests = context.bot_data.get('pending_requests', {})
    if username not in pending_requests:
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
    if 'members' not in context.bot_data:
        context.bot_data['members'] = []
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
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Please specify the username to reject.\n"
            "Usage: /reject_member @username",
            parse_mode=ParseMode.HTML
        )
        return
    
    username = context.args[0].strip('@')
    pending_requests = context.bot_data.get('pending_requests', {})
    if username not in pending_requests:
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
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.bot_data.get('pending_requests', {}):
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
    """Handle /list_members command - List all current members."""
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    members = context.bot_data.get('members', [])

    if not members:
        await update.message.reply_text(
            "📝 No members found.",
            parse_mode=ParseMode.HTML
        )
        logger.debug("No members found in the list.")  # Log when no members are found
        return
        
    members_text = "<b>Current Members:</b>\n\n"
    for member in members:
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
    if not find_authorized_member_by_username(update.effective_user['username'], context):
        await update.message.reply_text("❌ You are not authorized to use this command.", parse_mode=ParseMode.HTML)
        return

    if not context.bot_data.get('group_members', []):
        await update.message.reply_text(
            "📝 No groups are currently being tracked.",
            parse_mode=ParseMode.HTML
        )
        return
        
    groups_text = "<b>Tracked Groups:</b>\n\n"
    groups = context.bot_data.get('group_members', [])
    logger.debug(f"Tracked groups count: {len(groups)}")
    
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
                    groups_text += "• Unknown Group (Invalid format)\n"
        else:
            groups_text += "• Unknown Group (Invalid format)\n"
    else:
        groups_text += "• Unknown Group (Invalid format)\n"
    
    await update.message.reply_text(groups_text, parse_mode=ParseMode.HTML)