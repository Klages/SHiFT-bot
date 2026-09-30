# BL4 SHiFT Code Tracker

A Python bot and web dashboard that tracks **Borderlands 4** SHiFT codes. It watches `/r/BorderlandsShiftCodes`, alerts you on Discord, and gives you a local dashboard to keep track of which codes you have activated.

## Features

- **Reddit scraping that doesn't get blocked**: uses the subreddit's RSS feed with an honest User-Agent, a slow polling interval and automatic back-off on `429`/`403`. (Reddit blocks the `.json` endpoint and rate-limits browser-like User-Agents, so neither is used.)
- **Smart filtering**: understands posts like `BL4`, `MULTI` (all games) and lists such as `BL3: ... / BL4: ...`, and only keeps codes valid for Borderlands 4.
- **Expiry detection**: parses `Expires 9/29`, `valid until Oct 5, 2026`, `expires 1st October`, ... Expired codes are greyed out automatically and left out of the Steam exports.
- **Hide expired**: a "Show expired" toggle (remembered in your browser) keeps the list to codes that still work.
- **Outage alerts**: if Reddit checks fail 3 times in a row you get one Discord message, and another when it recovers.
- **Newest first**: codes are sorted by the time they were posted.
- **Discord notifications** via webhook for codes posted in the last 3 days (so the initial import doesn't flood your channel).
- **Web dashboard**: copy codes, mark them activated/expired, "Check now" button, logs, and Steam BBCode/JSON export.

## Run with Docker (recommended)

```bash
cp .env.example .env      # optionally set DISCORD_WEBHOOK_URL
docker compose up -d
```

Open <http://localhost:5500>. The port is only published on `127.0.0.1`; there is no authentication, so change the mapping in `docker-compose.yml` only if you know what you're doing.

### Keeping your state

All state is stored in a single file, `shift_codes_state.json`, in the folder mounted at `/app/data`. `docker-compose.yml` bind-mounts `./data` for that, so it survives rebuilds and `docker compose down`. To store it somewhere else, change the left side of the volume, e.g. `D:/shift-bot-data:/app/data`.

## Run locally

```bash
pip install -r requirements.txt
python shift-bot.py
```

Open <http://localhost:5000>. State is stored in `./data/`.

## Configuration (environment variables)

| Variable | Default | Description |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | empty | Discord webhook; leave empty to disable notifications |
| `CHECK_INTERVAL_MINUTES` | `120` | How often to check Reddit (minimum 5; lower values risk rate limits) |
| `DATA_DIR` | `./data` (`/app/data` in Docker) | Folder for the state file |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Address the dashboard listens on |
| `REDDIT_USER_AGENT` | `windows:bl4-shift-tracker:2.0 (...)` | Sent to Reddit; a descriptive one avoids blocks |

## Upgrading from the old version

Existing state is imported automatically (from `./shift_codes_state.json` if the new file doesn't exist yet). If your old Docker volume `datastore` held data, it was never used by the old code; there is nothing to migrate from it.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the code/expiry parser (with real post shapes), Reddit error handling and back-off, storage, the API, Discord notifications and outage alerts.
