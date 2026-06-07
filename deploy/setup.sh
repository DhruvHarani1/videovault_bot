#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting VideoVault Bot VPS Setup..."

# 1. Update apt packages
echo "Updating packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# Install prerequisite tools
sudo apt-get install -y curl git apt-transport-https ca-certificates software-properties-common

# 2. Install Docker and Docker Compose
echo "Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
    echo "Docker installed successfully."
else
    echo "Docker is already installed."
fi

echo "Installing Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    echo "Docker Compose installed successfully."
else
    echo "Docker Compose is already installed."
fi

# 3. Install Certbot for Let's Encrypt SSL
echo "Installing Certbot..."
sudo apt-get install -y certbot python3-certbot-nginx

# 4. Clone the repository (placeholder URL)
REPO_URL="https://github.com/placeholder/videovault_bot.git"
echo "Cloning repository from $REPO_URL..."
if [ ! -d "videovault_bot" ]; then
    git clone "$REPO_URL" videovault_bot
    cd videovault_bot
else
    echo "Directory 'videovault_bot' already exists. Navigating into it."
    cd videovault_bot
fi

# 5. Copy .env.example to .env
echo "Configuring environment variables..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  CRITICAL: A template .env file has been created. Please edit it (e.g. 'nano .env') and fill in your credentials (BOT_TOKEN, RAZORPAY_*, WEBHOOK_URL, etc.) before running the services."
else
    echo ".env file already exists."
fi

# 6. Build and start containers
echo "Starting Docker services..."
sudo docker-compose up -d --build

# 7. Get SSL Cert via Certbot (prompt for domain)
read -p "Enter your domain (e.g., bot.yourdomain.com) for SSL registration (leave empty to skip): " DOMAIN
if [ -n "$DOMAIN" ]; then
    echo "Requesting Let's Encrypt SSL certificate for $DOMAIN..."
    # Stops any local port 80 proxy temporarily or uses nginx plugin
    sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email webmaster@"$DOMAIN"
    echo "SSL Certificate configured successfully!"
else
    echo "Skipping Certbot SSL registration. You can run 'sudo certbot --nginx' manually later."
fi

# 8. Setup cron to auto-renew Let's Encrypt SSL daily at 3:00 AM
echo "Configuring SSL auto-renewal cron job..."
CRON_JOB="0 3 * * * certbot renew --quiet"
(crontab -l 2>/dev/null | grep -F "$CRON_JOB") || (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

echo "✅ VPS Setup complete! Remember to ensure your port 80 and 443 are open to let webhooks and Nginx work."
