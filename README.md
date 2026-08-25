# BL4 SHiFT Code Tracker

A Python-based bot and web dashboard that automatically tracks and aggregates SHiFT codes for **Borderlands 4**. 

It monitors community sources for new codes, filters out codes for older Borderlands games, alerts you on Discord, and provides a sleek web dashboard to keep track of which codes you have activated.

<img width="2268" height="934" alt="image" src="https://github.com/user-attachments/assets/1e4448ea-c0e1-4fa7-a25d-f481c35715f6" />

## Features

- **Automated Scraping**: Regularly fetches new posts from the `/r/BorderlandsShiftCodes` subreddit and official Twitter accounts (`@GearboxOfficial`, `@Borderlands`).
- **Smart Filtering**: Ignores SHiFT codes for older titles (BL2, BL3, Wonderlands, etc.) and only triggers on Borderlands 4 codes.
- **Discord Integration**: Sends a beautiful embed message to a Discord channel via Webhook as soon as a new code is discovered.
- **Web Dashboard**: A built-in web dashboard to view all active and expired codes, copy them to your clipboard, and mark them as activated.
- **Steam Formats**: Quickly export the list of active codes in Steam-compatible BBCode or JSON formats to share on Steam discussions.

## Prerequisites

- **Docker** and **Docker Compose** (Recommended)
- *Alternatively*: Python 3.9+ if you wish to run it manually without Docker.

## Setup Instructions (Docker)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/shift-code-bot.git
   cd shift-code-bot
   ```

2. **Configure your Environment Variables:**
   Copy the example environment file and create your own `.env` file:
   ```bash
   cp .env.example .env
   ```
   Open the `.env` file and set your `DISCORD_WEBHOOK_URL`. If you don't want Discord notifications, you can leave it blank.

3. **Build and Run the Container:**
   Use Docker Compose to build and start the bot in the background:
   ```bash
   docker-compose up -d
   ```

4. **Access the Web Dashboard:**
   Open your browser and navigate to:
   ```
   http://localhost:5500
   ```

## Setup Instructions (Manual / Local)

1. Make sure you have Python 3 installed.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set your Discord webhook as an environment variable (optional):
   * On Windows (PowerShell): `$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."`
   * On Linux/Mac: `export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."`
4. Run the script:
   ```bash
   python shift-bot.py
   ```
5. Access the web dashboard at `http://localhost:5000` (Note: Default Flask port is 5000 when run directly).

## How it works

The bot uses a background worker thread that runs every 30 minutes to check the RSS feeds and JSON endpoints of Reddit and Twitter. 
All discovered codes, along with metadata (when they were found, when they expire), are saved locally in `data/shift_codes_state.json`.

## License

This project is open-source. Feel free to modify and distribute it as needed!
