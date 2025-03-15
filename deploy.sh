#!/bin/bash

# Exit on error
set -e

echo "Starting deployment on Google Cloud..."

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and required packages
sudo apt install -y python3-pip python3-venv git

# Create project directory
mkdir -p ~/sqrDAO-AI-Agent
cd ~/sqrDAO-AI-Agent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy service file
sudo cp sqrdao-bot.service /etc/systemd/system/

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cat > .env << EOL
TELEGRAM_BOT_TOKEN=your_telegram_token
GEMINI_API_KEY=your_gemini_token
GOOGLE_API_KEY=your_google_api_key
GOOGLE_CSE_ID=your_google_cse_id
EOL
    echo "Please edit .env file with your actual API keys"
fi

# Set correct permissions
sudo chown -R $USER:$USER ~/sqrDAO-AI-Agent
chmod 600 .env

# Reload systemd and start the bot
sudo systemctl daemon-reload
sudo systemctl enable sqrdao-bot
sudo systemctl restart sqrdao-bot

# Show status
echo "Deployment completed! Checking service status..."
sudo systemctl status sqrdao-bot

echo "
==============================================
Deployment completed successfully!

Next steps:
1. Edit your .env file: nano ~/sqrDAO-AI-Agent/.env
2. Check logs: sudo journalctl -u sqrdao-bot -f
3. Restart bot after editing .env: sudo systemctl restart sqrdao-bot

To monitor the bot:
- View logs: sudo journalctl -u sqrdao-bot -f
- Check status: sudo systemctl status sqrdao-bot
- Restart bot: sudo systemctl restart sqrdao-bot
==============================================
" 