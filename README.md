# sqrDAO AI Agent

An AI-powered Telegram bot for sqrDAO, developed by sqrFUND. This bot helps manage the sqrDAO community, provides information about sqrDAO and sqrFUND, and assists with various administrative tasks.

🤖 **Try it now**: [t.me/sqrAgent_bot](https://t.me/sqrAgent_bot)

## About

This AI agent is a project by sqrFUND, providing intelligent assistance for the sqrDAO community. It leverages Google's Gemini AI to deliver context-aware responses and manage community resources effectively.

## Features

### Core Features
- 🤖 AI-powered responses using Google's Gemini model
- 📚 Knowledge base management
- 👥 Member management system
- 💬 Group chat support
- 🔍 Web search capabilities
- 💰 SQR token balance checking
- 📢 Mass messaging system

### Member Management
- Member request system
- Member approval/rejection workflow
- Different access levels (Authorized Members and Regular Members)
- Member-only resources and commands

### Group Management
- Automatic group detection when bot is added/removed
- Manual group management commands
- Group tracking and persistence
- Mass messaging to groups

## Commands

### Basic Commands (All Users)
- `/start` - Start the bot and get welcome message
- `/help` - Show help and list of available commands
- `/about` - Learn about sqrDAO and sqrFUND
- `/website` - Get sqrDAO's and sqrFUND's website
- `/contact` - Get contact information
- `/events` - View sqrDAO events
- `/balance` - Check $SQR token balance
- `/request_member` - Request to become a member

### Member Commands
- `/resources` - Access internal resources for sqrDAO Members and sqrFUND Chads

### Authorized Member Commands
- `/learn` - Add information to the bot's knowledge base
- `/learn_from_url` - Learn from a web page by providing a URL
- `/bulk_learn` - Add multiple entries from CSV file
- `/approve_member` - Approve a member request
- `/reject_member` - Reject a member request
- `/list_requests` - View pending member requests
- `/list_members` - List all current members
- `/mass_message` - Send a message to all regular members and groups
- `/add_group` - Manually add a group to the bot's tracking
- `/list_groups` - List all tracked groups
- `/remove_group` - Remove a group from tracking

## Group Management

### Automatic Group Detection
The bot automatically detects and tracks groups when:
- It is added to a new group
- It receives messages from a group
- It is removed from a group

### Manual Group Management
Authorized members can manage groups using the following commands:
- `/add_group [group_id] [group_name]` - Add a group manually
- `/list_groups` - View all tracked groups
- `/remove_group [group_id]` - Remove a group from tracking

### Finding Group IDs
To find a group ID:
1. Add @username_to_id_bot to your group
2. Send `/id` in the group
3. Use the provided ID with the `/add_group` command

## Mass Messaging

The `/mass_message` command has been updated to:
- Send messages to regular members only (excluding authorized members)
- Send messages to all tracked groups
- Provide detailed delivery statistics
- Show failed message attempts
- Include proper error handling and logging

## Setup

### Setting Up the Bot on Telegram

To set up your bot on Telegram, follow these steps:

1. **Open Telegram**: If you haven't already, download and install the Telegram app on your device or use the web version.

2. **Find BotFather**: In the Telegram app, search for `@BotFather` and start a chat with it. BotFather is the official Telegram bot that helps you create and manage your bots.

3. **Create a New Bot**:
   - Type `/newbot` and send the message.
   - BotFather will ask you for a name for your bot. This is the display name that users will see.
   - Next, you will be prompted to choose a username for your bot. The username must end with `bot` (e.g., `sqrdao_bot`).

4. **Get Your Bot Token**: After successfully creating your bot, BotFather will provide you with a token. This token is essential for your bot to connect to the Telegram API. It will look something like this:
   ```
   123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
   ```

5. **Store Your Token**: Make sure to store this token securely. You will need to add it to your `.env` file in your project directory later as follows:
   ```
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   ```

### Setting Up Google Custom Search Engine

To set up Google Custom Search Engine (CSE) for your bot, follow these steps:

1. **Go to Google Custom Search**: Visit the [Google Custom Search Engine](https://cse.google.com/cse/all) page.

2. **Create a New Search Engine**:
   - Click on the "Add" button to create a new search engine.
   - Fill in the required fields:
     - **Sites to Search**: Enter the websites you want to include in your search engine. You can add multiple sites.
     - **Name**: Give your search engine a name.
   - Click on the "Create" button.

3. **Get Your Search Engine ID**:
   - After creating the search engine, you will be taken to the control panel.
   - Find your **Search Engine ID** in the control panel. You will need this ID for your bot.

4. **Enable the Custom Search API**:
   - Go to the [Google Cloud Console](https://console.cloud.google.com/).
   - Create a new project or select an existing project.
   - Navigate to **APIs & Services** > **Library**.
   - Search for "Custom Search API" and enable it for your project.

5. **Create API Credentials**:
   - In the Google Cloud Console, go to **APIs & Services** > **Credentials**.
   - Click on "Create Credentials" and select "API key".
   - Copy the generated API key. You will need to add it to your `.env` file.

6. **Store Your API Key and CSE ID**: Add the following lines to your `.env` file:
   ```
   GOOGLE_API_KEY=your_google_api_key
   GOOGLE_CSE_ID=your_google_cse_id
   ```

### Installing and Running the Bot

1. Clone the repository:
```bash
git clone https://github.com/sqrdao/sqrDAO-AI-Agent.git
cd sqrDAO-AI-Agent
```

2. Install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Create a `.env` file with all required variables:
```
TELEGRAM_BOT_TOKEN=your_bot_token
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_API_KEY=your_google_api_key
GOOGLE_CSE_ID=your_google_cse_id
SOLANA_RPC_URL=your_solana_rpc_url
```

4. Run the bot:
```bash
python bot.py
```

## Features in Detail

### AI Conversations
- Powered by Google's Gemini 2.0 Flash model
- Context-aware responses using conversation history
- Markdown formatting support
- HTML formatting for Telegram messages
- Enhanced response formatting with HTML tags
- Works in both private chats and group chats
- Responds to mentions in group chats
- Maintains context separately for each user
- Handles group chat permissions appropriately

### Balance Check
- Check Solana wallet balances using wallet addresses
- Support for .sol DID resolution using SNS.ID
- Works in both private and group chats
- Automatic wallet address validation
- Real-time balance updates
- User-friendly error messages
- Support for checking own balance in private chat
- Support for checking specific wallet balances
- Support for checking balances using .sol DIDs

### Knowledge Base
- SQLite database for storing information
- Topic-based knowledge storage
- Accessible through `/learn` and `/bulk_learn` commands
- CSV template for bulk learning
- Used to enhance bot responses

### Member Management
- Two-tier access system:
  - Regular members: Access to resources
  - Authorized members: Full access including knowledge base management and member approval
- Member request system with approval workflow
- User ID and username-based verification
- Members stored in knowledge base with user IDs
- Automatic command menu updates for members

### Conversation Memory
- Stores conversation history in SQLite
- Uses previous conversations for context
- Enhances response relevancy

### Web Search
- Google Custom Search integration
- Web content extraction
- URL processing and content summarization
- Automatic delimiter detection for CSV files
- Enhanced error handling and reporting
- Works in both private and group chats
- Respects group chat permissions
- Provides formatted responses suitable for group discussions

### Bulk Learning
- CSV file support for adding multiple entries
- Multiple delimiter support (comma, semicolon, tab, pipe)
- Template file with examples
- Error handling and reporting

## Deployment

For production deployment:

1. Set up the environment and dependencies
2. Configure systemd service (Linux):
```bash
sudo nano /etc/systemd/system/sqrdao-bot.service
```

Example service file:
```ini
[Unit]
Description=sqrDAO AI Agent
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/sqrDAO-AI-Agent
Environment=PYTHONPATH=/path/to/sqrDAO-AI-Agent
ExecStart=/path/to/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

3. Start the service:
```bash
sudo systemctl enable sqrdao-bot
sudo systemctl start sqrdao-bot
```

## Dependencies

Core dependencies:
- `beautifulsoup4` - Web scraping
- `google-generativeai` - Gemini AI integration
- `google-api-python-client` - Google Custom Search
- `python-dotenv` - Environment variables
- `python-telegram-bot` - Telegram bot functionality
- `requests` - HTTP requests
- `trafilatura` - Web content extraction

For a complete list with versions, see `requirements.txt`.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is open-source and available under the following terms:

### Permitted Uses
- ✅ Public, non-commercial use
- ✅ Educational purposes
- ✅ Research and development
- ✅ Personal projects
- ✅ Community building
- ✅ Non-profit organizations

### Restrictions
- ❌ Commercial use without explicit permission
- ❌ Token launches or cryptocurrency offerings
- ❌ Financial products or services
- ❌ Rebranding without attribution
- ❌ Closed-source modifications

### Attribution
- Must maintain original copyright notices
- Must credit sqrFUND as the original creator
- Must link to the original repository

Copyright © 2024 sqrFUND. For commercial licensing inquiries, please contact dev@sqrfund.ai.

## Support

Get in touch with us:
- 📧 Email: dev@sqrfund.ai
- 🐦 X (formerly Twitter): [@sqrfund_ai](https://x.com/sqrfund_ai)
- 💬 Telegram channel: [@sqrfund_ai](https://t.me/sqrfund_ai)
- 🌐 Website: [sqrfund.ai](https://sqrfund.ai)

Join our community channels for updates and discussions! 