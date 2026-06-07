#!/bin/bash

# Create backups folder if not exists
mkdir -p /app/backups

DATE=$(date +%Y%m%d_%H%M)

# Perform backup copy
cp /app/data/videovault.db /app/backups/videovault_$DATE.db

# Delete backups older than 7 days
find /app/backups -name "videovault_*.db" -mtime +7 -delete

echo "Backup complete: videovault_$DATE.db"
