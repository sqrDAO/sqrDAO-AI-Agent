# sqrDAO AI Agent

A Telegram bot powered by Google's Gemini AI model for sqrDAO.

## Deployment Instructions

### Prerequisites
- Google Cloud Platform account
- `gcloud` CLI tool installed locally
- Required API keys:
  - Telegram Bot Token (from @BotFather)
  - Google Gemini API Key
  - Google Custom Search API Key and Search Engine ID (for web search functionality)

### Deployment Steps

1. **Set up Google Cloud**
   ```bash
   # Install gcloud CLI if you haven't already
   # Visit: https://cloud.google.com/sdk/docs/install
   
   # Initialize gcloud and set your project
   gcloud init
   
   # Create a new VM instance
   gcloud compute instances create sqrdao-bot \
     --machine-type=e2-micro \
     --zone=us-central1-a \
     --image-family=ubuntu-2204-lts \
     --image-project=ubuntu-os-cloud \
     --tags=http-server,https-server
   ```

2. **Prepare deployment files**
   Make sure you have the following files in your repository:
   - `bot.py` - Main bot code
   - `requirements.txt` - Python dependencies
   - `sqrdao-bot.service` - Systemd service file
   - `deploy.sh` - Deployment script

3. **Deploy to Google Cloud**
   ```bash
   # Copy files to your GCP instance
   gcloud compute scp --zone=us-central1-a ./* sqrdao-bot:~/sqrDAO-AI-Agent/

   # SSH into your instance
   gcloud compute ssh --zone=us-central1-a sqrdao-bot

   # Make deploy script executable
   chmod +x ~/sqrDAO-AI-Agent/deploy.sh

   # Run deployment script
   cd ~/sqrDAO-AI-Agent && ./deploy.sh
   ```

4. **Configure environment variables**
   Edit the `.env` file with your actual API keys:
   ```bash
   nano ~/sqrDAO-AI-Agent/.env
   ```

5. **Restart the bot**
   ```bash
   sudo systemctl restart sqrdao-bot
   ```

6. **Check bot status**
   ```bash
   sudo systemctl status sqrdao-bot
   ```

### Maintenance

- **View logs**
  ```bash
  sudo journalctl -u sqrdao-bot -f
  ```

- **Update the bot**
  ```bash
  cd ~/sqrDAO-AI-Agent
  git pull  # if using version control
  sudo systemctl restart sqrdao-bot
  ```

- **Stop the bot**
  ```bash
  sudo systemctl stop sqrdao-bot
  ```

- **Connect to VM**
  ```bash
  gcloud compute ssh --zone=us-central1-a sqrdao-bot
  ```

### Cost Optimization
- The bot runs on an e2-micro instance, which is part of GCP's free tier
- Estimated monthly cost: ~$5-10 USD (depending on usage)
- Consider setting up billing alerts in GCP Console

## Development

To run the bot locally:

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables in `.env`

4. Run the bot:
   ```bash
   python bot.py
   ```

## License

[Add your license information here] 