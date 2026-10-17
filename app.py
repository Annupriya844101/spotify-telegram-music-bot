import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("music-web")
# Credentials and chat IDs must be supplied by the host, never hard-coded.
TOKEN = os.getenv("8890651987:AAEttmWhVnWo9lfIvdVwR1TnbZRmyPDLmEQ", "").strip()
try:
    STORAGE_CHAT_ID = int(os.getenv("5573802067", "").strip())
except (TypeError, ValueError):
    STORAGE_CHAT_ID = None
DB_PATH = os.getenv("DB_PATH", "/data/music.db")
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "40")))
SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)", re.I)
BASE_DIR = Path(__file__).resolve().parent
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def connect():
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("""CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        artist TEXT NOT NULL DEFAULT '', telegram_file_id TEXT NOT NULL,
        storage_message_id INTEGER NOT NULL, mime_type TEXT NOT NULL DEFAULT 'audio/mpeg',
        file_size INTEGER NOT NULL DEFAULT 0, uploader_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    return db


@contextmanager
def db_session():
    db = connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "🎵 Music library ready!\n\nSend audio you own or are licensed to distribute. "
            "It will be copied to the private storage group and listed on the website.\n\n"
            "/list — latest tracks\nSpotify links are references only; protected Spotify audio is not downloaded."
        )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db_session() as db:
        rows = db.execute("SELECT id,title,artist FROM tracks ORDER BY id DESC LIMIT 15").fetchall()
    message = update.effective_message
    if not message:
        return
    if not rows:
        await message.reply_text("No tracks saved yet.")
        return
    await message.reply_text("\n".join(
        f"{r['id']}. {r['title']}" + (f" — {r['artist']}" if r['artist'] else "") for r in rows
    ))


async def incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    match = SPOTIFY_RE.search(message.text or "")
    if match:
        await message.reply_text(
            f"Spotify {match.group(1)} link detected (ID: {match.group(2)}). "
            "Protected Spotify audio is not downloaded. Send your own/licensed audio file instead.",
            disable_web_page_preview=True,
        )
    else:
        await message.reply_text("Send licensed audio or use /list.")


async def incoming_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    media = message.audio or message.document
    if not media:
        return
    size = int(getattr(media, "file_size", 0) or 0)
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        await message.reply_text(f"File exceeds the configured {MAX_UPLOAD_MB} MB limit.")
        return
    title = (getattr(media, "title", None) or getattr(media, "file_name", None) or "Untitled").strip()[:250] or "Untitled"
    artist = (getattr(media, "performer", None) or "")[:250]
    try:
        stored = await context.bot.copy_message(
            chat_id=STORAGE_CHAT_ID, from_chat_id=message.chat_id, message_id=message.message_id
        )
        with db_session() as db:
            db.execute(
                """INSERT INTO tracks (title,artist,telegram_file_id,storage_message_id,mime_type,file_size,uploader_id)
                VALUES(?,?,?,?,?,?,?)""",
                (title, artist, media.file_id, stored.message_id,
                 getattr(media, "mime_type", None) or "audio/mpeg", size, user.id),
            )
        await message.reply_text(f"✅ Saved to the private storage group and added to the website:\n🎵 {title}")
    except Exception:
        log.exception("Saving uploaded audio failed")
        await message.reply_text("Save failed. Check that the bot is in the private storage group and can post messages.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_app = None
    if TOKEN and STORAGE_CHAT_ID is not None:
        try:
            bot_app = Application.builder().token(TOKEN).build()
            bot_app.add_handler(CommandHandler("start", start))
            bot_app.add_handler(CommandHandler("list", list_cmd))
            bot_app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, incoming_audio))
            bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_text))
            await bot_app.initialize()
            await bot_app.start()
            if bot_app.updater is None:
                raise RuntimeError("Telegram updater unavailable")
            await bot_app.updater.start_polling()
            log.info("Telegram polling started")
        except Exception:
            log.exception("Telegram bot could not start; website remains available")
            if bot_app is not None:
                try:
                    if bot_app.updater and bot_app.updater.running:
                        await bot_app.updater.stop()
                    if bot_app.running:
                        await bot_app.stop()
                    await bot_app.shutdown()
                except Exception:
                    log.exception("Telegram bot cleanup failed")
            bot_app = None
    else:
        log.warning("Telegram bot disabled: set TELEGRAM_BOT_TOKEN and TELEGRAM_STORAGE_CHAT_ID in host variables")
    try:
        yield
    finally:
        if bot_app is not None:
            try:
                if bot_app.updater and bot_app.updater.running:
                    await bot_app.updater.stop()
                if bot_app.running:
                    await bot_app.stop()
                await bot_app.shutdown()
            except Exception:
                log.exception("Telegram bot shutdown failed")


app = FastAPI(title="Telegram Music Library", lifespan=lifespan)


@app.get("/", response_class=FileResponse)
async def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "telegram_configured": bool(TOKEN and STORAGE_CHAT_ID is not None)}


@app.get("/api/tracks")
async def tracks(q: str = ""):
    with db_session() as db:
        if q.strip():
            term = f"%{q.strip()[:100]}%"
            rows = db.execute(
                """SELECT id,title,artist,mime_type,file_size,created_at FROM tracks
                WHERE title LIKE ? OR artist LIKE ? ORDER BY id DESC LIMIT 200""", (term, term)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id,title,artist,mime_type,file_size,created_at FROM tracks ORDER BY id DESC LIMIT 200"
            ).fetchall()
    return [dict(row) for row in rows]


def get_track(track_id: int):
    with db_session() as db:
        row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return row


async def fetch_audio(file_id: str) -> tuple[bytes, str]:
    if not TOKEN:
        raise HTTPException(status_code=503, detail="Telegram bot is not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        try:
            meta_response = await client.get(f"https://api.telegram.org/bot{TOKEN}/getFile", params={"file_id": file_id})
            meta = meta_response.json()
            if meta_response.status_code != 200 or not meta.get("ok"):
                raise HTTPException(status_code=502, detail="Telegram could not resolve this audio file")
            file_response = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{quote(meta['result']['file_path'], safe='/')}")
            if file_response.status_code != 200:
                raise HTTPException(status_code=502, detail="Telegram audio download failed")
            return file_response.content, file_response.headers.get("content-type", "application/octet-stream")
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError, KeyError):
            log.exception("Telegram audio retrieval failed")
            raise HTTPException(status_code=502, detail="Audio retrieval failed")


def audio_response(data: bytes, title: str, mime_type: str, request: Request, attachment: bool):
    size = len(data)
    start, end, status_code = 0, size - 1, 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
    range_header = request.headers.get("range")
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            raise HTTPException(status_code=416, detail="Invalid byte range")
        if not match.group(1):
            suffix = int(match.group(2))
            if suffix <= 0:
                raise HTTPException(status_code=416, detail="Invalid byte range")
            start = max(0, size - suffix)
        else:
            start = int(match.group(1))
            if match.group(2):
                end = int(match.group(2))
        if start >= size or end < start:
            raise HTTPException(status_code=416, detail="Requested range is not satisfiable")
        end = min(end, size - 1)
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    filename = quote(Path(title).name[:120] or "track", safe="")
    headers["Content-Disposition"] = ("attachment" if attachment else "inline") + f"; filename*=UTF-8''{filename}"
    payload = data[start:end + 1]
    headers["Content-Length"] = str(len(payload))
    return Response(content=payload, status_code=status_code, media_type=mime_type or "audio/mpeg", headers=headers)


@app.get("/api/stream/{track_id}")
async def stream(track_id: int, request: Request):
    row = get_track(track_id)
    data, upstream_type = await fetch_audio(row["telegram_file_id"])
    return audio_response(data, row["title"], row["mime_type"] or upstream_type or "audio/mpeg", request, False)


@app.get("/api/download/{track_id}")
async def download(track_id: int, request: Request):
    row = get_track(track_id)
    data, upstream_type = await fetch_audio(row["telegram_file_id"])
    return audio_response(data, row["title"], row["mime_type"] or upstream_type or "audio/mpeg", request, True)
