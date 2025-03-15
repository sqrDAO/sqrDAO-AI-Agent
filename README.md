# sqrDAO AI Agent

A Telegram bot powered by Google's Gemini AI model to assist sqrDAO members and users. Developed and maintained by [sqrFUND](https://sqrfund.ai).

🤖 **Try it now**: [t.me/sqrAgent_bot](https://t.me/sqrAgent_bot)

## About

This AI agent is a project by sqrFUND, providing intelligent assistance for the sqrDAO community. It leverages Google's Gemini AI to deliver context-aware responses and manage community resources effectively.

## Features

- 🤖 AI-powered conversations using Gemini 1.5 Pro
- 💬 Context-aware responses
- 🔒 Member-only access control
- 📚 Knowledge base management
- 🌐 Web search capabilities
- 🧠 Conversation memory

## Commands

### Public Commands
- `/start` - Start the bot and get welcome message
- `/help` - Show help and list of available commands
- `/about` - Learn about sqrDAO
- `/website` - Get sqrDAO's website
- `/contact` - Get contact information
- `/events` - View sqrDAO events calendar
- `/resources` - Access internal resources (members only)

### Member Commands
Members have access to:
- All public commands
- `/resources` - Access internal resources and documentation

### Authorized Member Commands
Authorized members have access to:
- All public and member commands
- `/learn` - Add information to the bot's knowledge base

## Setup

1. Clone the repository:
```bash
git clone https://github.com/sqrdao-intern/sqrDAO-AI-Agent.git
cd sqrDAO-AI-Agent
```

2. Install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Create a `.env` file with your API keys:
```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_API_KEY=your_google_api_key
GOOGLE_CSE_ID=your_google_cse_id
```

4. Configure member access in `config.json`:
```json
{
    "authorized_members": [
        "username1",
        "username2"
    ],
    "members": [
        "member1",
        "member2"
    ]
}
```

5. Run the bot:
```bash
python bot.py
```

## Features in Detail

### AI Conversations
- Powered by Google's Gemini 1.5 Pro model
- Context-aware responses using conversation history
- Markdown formatting support
- HTML formatting for Telegram messages

### Knowledge Base
- SQLite database for storing information
- Topic-based knowledge storage
- Accessible through the `/learn` command (authorized members only)
- Used to enhance bot responses

### Member Access Control
- Two-tier access system:
  - Regular members: Access to resources
  - Authorized members: Full access including knowledge base management
- Username-based verification
- Configurable through `config.json`

### Conversation Memory
- Stores conversation history in SQLite
- Uses previous conversations for context
- Enhances response relevancy

### Web Search
- Google Custom Search integration
- Web content extraction
- URL processing and content summarization

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