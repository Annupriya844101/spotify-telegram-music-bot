# Telegram Music Website

Responsive music library/player backed by a Telegram bot, private Telegram storage group, FastAPI, and SQLite.

## Security and copyright
- Store `TELEGRAM_BOT_TOKEN` only in Railway Variables or another secret manager. Never commit a bot token or storage chat ID.
- Revoke any token previously exposed in chat, repository, screenshots, or deployment logs.
- Spotify links are references only. This app does not rip protected Spotify audio or bypass DRM.
- Upload only audio you own or have permission to distribute.
- The website/API is public unless authentication is added; do not upload private or sensitive audio.

## Railway setup
1. Add the bot to a private Telegram storage group and allow it to post messages.
2. In Railway service Variables, set `TELEGRAM_BOT_TOKEN` to a newly rotated token, `TELEGRAM_STORAGE_CHAT_ID` to the numeric group ID, and `DB_PATH` to `/data/music.db`.
3. Attach a persistent Railway Volume mounted at `/data`.
4. Deploy from `main` and check `/health`.
5. Send `/start` to the bot, then upload a small licensed audio file.

The website can start without Telegram variables, but bot features stay disabled until both required variables are configured.

## Features
- Search/browse tracks, play/stop, previous/next, shuffle, repeat all, and repeat one.
- Download licensed audio through the backend without exposing the bot token to browser JavaScript.
- SQLite catalogue intended for a persistent volume; byte-range requests support audio seeking.
- GitHub Actions Python syntax check.

## Constraints
Telegram Bot API file download size limits apply. Test with a small file first. The library endpoints are public by default, so anyone who can reach the website can list and play uploaded tracks.
