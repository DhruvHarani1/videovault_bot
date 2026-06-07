# VideoVault Bot

VideoVault Bot is a Python-based Telegram bot designed to monetize premium video content. It provides a seamless user flow to preview and purchase access to full videos.

## Features
- **Video Preview:** Users receive a 3-minute video preview.
- **Auto-Deletion:** The preview video is automatically deleted from the user's chat history after 3 minutes to prevent sharing or saving.
- **Payment Request:** After preview deletion, the bot prompts the user to pay to unlock the full video access using Razorpay.
- **Access Management:** Once payment is verified, the user is granted lifetime or configured access to the full video.

## Project Structure
```
videovault_bot/
├── bot/
│   ├── __init__.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py
│   │   ├── video.py
│   │   └── payment.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── access.py
│   │   └── scheduler.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── user.py
│   └── config.py
├── admin/
│   └── panel.py
├── .env.example
├── requirements.txt
├── main.py
└── README.md
```

## Setup & Installation
1. Clone this repository.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
4. Run the bot locally:
   ```bash
   python main.py
   ```

## Docker Deployment (Production)

To deploy the bot in webhook mode using Docker Compose with an Nginx SSL-terminating reverse proxy:

1. Ensure `.env` is configured with `WEBHOOK_URL` set (e.g. `https://yourdomain.com`).
2. Generate or obtain SSL certificates (`bot.crt` and `bot.key`) for Nginx SSL termination and place them in the `./certs` directory.
3. Start the services:
   ```bash
   docker-compose up -d --build
   ```
4. Check bot container status and health:
   ```bash
   docker-compose ps
   ```

SQLite database data is persisted under `./data/videovault.db` (mounted volume `./data:/app/data`), and logs are saved under `./logs` (mounted volume `./logs:/app/logs`).

