#!/usr/bin/env python3
"""
Cinehub Vault Bot – MINIMAL WORKING VERSION (FIXED)
No underscores in keys – deep-links work perfectly.
"""

import os
import sys
import sqlite3
import random
import string
import logging
import re
from datetime import datetime
from telebot import TeleBot

# ---------- ENVIRONMENT ----------
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    logging.error("TOKEN not set")
    sys.exit(1)

ADMIN_ID = 6537318639
CHANNEL = os.environ.get("CHANNEL", "ulugbekiy_movies")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = TeleBot(TOKEN)
DB = "vault.db"

# ---------- DATABASE ----------
def get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS files (
            key TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            caption TEXT,
            uploaded TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ Database ready.")

def add_file(key, file_id, caption=""):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO files (key, file_id, caption, uploaded) VALUES (?,?,?,?)",
        (key, file_id, caption, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    logger.info(f"✅ File added: {key}")

def get_file(key):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def gen_key():
    # ✅ FIXED: No underscore in the key!
    return "vid" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(f"@{CHANNEL}", user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

# ---------- HANDLERS ----------
@bot.message_handler(commands=['start'])
def start_command(message):
    args = message.text.split()
    if len(args) > 1:
        key = args[1]
        f = get_file(key)
        if f:
            bot.reply_to(message, f"✅ Found file: {key}")
            try:
                bot.send_video(message.chat.id, f["file_id"], caption=f.get("caption", ""))
            except Exception as e:
                bot.reply_to(message, f"❌ Error: {e}")
        else:
            bot.reply_to(message, f"❌ Key '{key}' not found in The Vault.")
    else:
        bot.reply_to(message, "Send /start KEY to claim. Admin: upload a video.")

@bot.message_handler(content_types=['video', 'document'])
def ingest_file(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Not admin.")
        return

    video = message.video or message.document
    if not video:
        bot.reply_to(message, "❌ Send a video file.")
        return

    key = gen_key()
    add_file(key, video.file_id, message.caption or "")
    bot_username = bot.get_me().username

    bot.reply_to(
        message,
        f"✅ Ingested: `{key}`\n"
        f"Deep-link: https://t.me/{bot_username}?start={key}\n"
        f"Test: /start {key}",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: True)
def catch_all(message):
    text = message.text.strip()
    if text and not text.startswith("/"):
        f = get_file(text)
        if f:
            try:
                bot.send_video(message.chat.id, f["file_id"], caption=f.get("caption", ""))
                bot.reply_to(message, "🎬 Enjoy!")
            except Exception as e:
                bot.reply_to(message, f"❌ Error: {e}")
        else:
            bot.reply_to(message, f"❌ '{text}' not found in The Vault.")

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()
    logger.info(f"✅ Bot username: {bot.get_me().username}")
    logger.info("✅ Bot started! Polling...")
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
