#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🔄 Initiating zero-downtime bot application update..."

# 1. Fetch latest changes
echo "Pulling latest changes from git..."
git pull origin main

# 2. Build the bot container (cache layers where possible)
echo "Building bot container..."
sudo docker-compose build bot

# 3. Recreate the bot service container without affecting other services (e.g. nginx)
echo "Recreating bot service..."
sudo docker-compose up -d --no-deps bot

# Prune unused docker assets to save disk space
echo "Pruning dangling images..."
sudo docker image prune -f

echo "✅ Update complete!"
