#!/usr/bin/env python3
"""
Cinehub Vault Bot – FULLY WORKING with Debug
All commands reply with debug info so you know what's happening.
"""

import os
import sys
import time
import sqlite3
import random
import string
import logging
import csv
import re
from io import StringIO
from datetime import datetime
import pytz
from telebot import TeleBot, types

# ---------- CONFIG ----------
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ TOKEN not set")
    sys.exit(1)

# ⚠️ REPLACE THIS WITH YOUR ACTUAL USER ID (from /myid)
ADMIN_ID = 6537318639  # <-- CHANGE THIS TO YOUR REAL ID

CHANNEL = "ulugbekiy_movies"
TIMEZONE = pytz.timezone('Asia/Tashkent')
DB = "vault.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = TeleBot(TOKEN)

# ---------- DATABASE ----------
def get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS files (
            key TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            caption TEXT,
            uploaded TEXT NOT NULL,
            downloads INTEGER DEFAULT 0,
            genre TEXT DEFAULT NULL,
            collection TEXT DEFAULT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            joined TEXT NOT NULL,
            last_seen TEXT,
            blacklisted INTEGER DEFAULT 0,
            total_claims INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_key TEXT NOT NULL,
            time TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trivia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            title TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    defaults = {
        "welcome_message": "This is The Vault. Type a secret passcode or use a valid deep-link to pull files.",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()
    logger.info("✅ Database ready.")

# ---------- ALL HELPERS ----------
def get_config(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_config(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def register_user(user):
    now = datetime.now(TIMEZONE).isoformat()
    conn = get_db()
    conn.execute("""
        INSERT OR IGNORE INTO users (id, username, name, joined, last_seen)
        VALUES (?,?,?,?,?)
    """, (user.id, user.username or "", user.first_name or "", now, now))
    conn.execute("UPDATE users SET username=?, name=?, last_seen=? WHERE id=?", (user.username or "", user.first_name or "", now, user.id))
    conn.commit()
    conn.close()

def is_blacklisted(user_id):
    conn = get_db()
    row = conn.execute("SELECT blacklisted FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None and row["blacklisted"] == 1

def get_user_info(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return None
    logs = conn.execute("SELECT file_key, time FROM logs WHERE user_id=? ORDER BY time DESC LIMIT 10", (user_id,)).fetchall()
    total_claims = conn.execute("SELECT COUNT(*) FROM logs WHERE user_id=?", (user_id,)).fetchone()[0]
    conn.close()
    return dict(user) | {"recent_claims": [dict(l) for l in logs], "total_claims": total_claims}

def get_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, name FROM users WHERE blacklisted=0").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_logs():
    conn = get_db()
    rows = conn.execute("""
        SELECT l.id, l.user_id, l.file_key, l.time, u.username
        FROM logs l LEFT JOIN users u ON l.user_id = u.id
        ORDER BY l.time DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_user_stats(user_id):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM logs WHERE user_id=?", (user_id,)).fetchone()[0]
    today = datetime.now(TIMEZONE).date().isoformat()
    today_claims = conn.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND date(time) = ?", (user_id, today)).fetchone()[0]
    last = conn.execute("SELECT time FROM logs WHERE user_id=? ORDER BY time DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return {"total_claims": total, "today_claims": today_claims, "last_claim": last["time"] if last else None}

def add_file(key, file_id, caption="", genre=None, collection=None):
    now = datetime.now(TIMEZONE).isoformat()
    conn = get_db()
    conn.execute("INSERT INTO files (key, file_id, caption, uploaded, genre, collection) VALUES (?,?,?,?,?,?)", (key, file_id, caption, now, genre, collection))
    conn.commit()
    conn.close()
    logger.info(f"✅ File added: {key}")

def get_file(key):
    conn = get_db()
    row = conn.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_files():
    conn = get_db()
    rows = conn.execute("SELECT key, uploaded, downloads, caption, genre, collection FROM files ORDER BY uploaded DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_file(key):
    conn = get_db()
    conn.execute("DELETE FROM logs WHERE file_key=?", (key,))
    conn.execute("DELETE FROM files WHERE key=?", (key,))
    deleted = conn.total_changes > 0
    conn.commit()
    conn.close()
    return deleted

def log_claim(user_id, key):
    now = datetime.now(TIMEZONE).isoformat()
    conn = get_db()
    conn.execute("UPDATE files SET downloads = downloads + 1 WHERE key=?", (key,))
    conn.execute("INSERT INTO logs (user_id, file_key, time) VALUES (?,?,?)", (user_id, key, now))
    conn.execute("UPDATE users SET total_claims = total_claims + 1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def search_files(query):
    conn = get_db()
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT key, caption, uploaded, downloads, genre, collection FROM files
        WHERE key LIKE ? OR caption LIKE ? OR genre LIKE ? OR collection LIKE ?
        ORDER BY uploaded DESC LIMIT 50
    """, (like, like, like, like)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_files_by_collection(collection):
    conn = get_db()
    rows = conn.execute("SELECT key, caption, uploaded, downloads FROM files WHERE collection=? ORDER BY uploaded DESC", (collection,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_files_by_genre(genre):
    conn = get_db()
    rows = conn.execute("SELECT key, caption, uploaded, downloads FROM files WHERE genre=? ORDER BY uploaded DESC", (genre,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_collections():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT collection FROM files WHERE collection IS NOT NULL AND collection != ''").fetchall()
    conn.close()
    return [r["collection"] for r in rows]

def get_genres():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT genre FROM files WHERE genre IS NOT NULL AND genre != ''").fetchall()
    conn.close()
    return [r["genre"] for r in rows]

def get_random_file_by_genre(genre):
    conn = get_db()
    row = conn.execute("SELECT * FROM files WHERE genre=? ORDER BY RANDOM() LIMIT 1", (genre,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_stats():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    deliveries = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    top_files = conn.execute("SELECT key, downloads FROM files ORDER BY downloads DESC LIMIT 3").fetchall()
    top_users = conn.execute("SELECT id, username, total_claims FROM users ORDER BY total_claims DESC LIMIT 10").fetchall()
    conn.close()
    return {
        "total_users": users,
        "total_files": files,
        "total_deliveries": deliveries,
        "top_files": [{"key": r[0], "downloads": r[1]} for r in top_files],
        "top_users": [{"id": r[0], "username": r[1], "total_claims": r[2]} for r in top_users],
    }

def get_random_trivia():
    conn = get_db()
    row = conn.execute("SELECT text FROM trivia ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return row["text"] if row else None

def add_trivia(text):
    conn = get_db()
    conn.execute("INSERT INTO trivia (text) VALUES (?)", (text,))
    conn.commit()
    conn.close()

def get_all_trivia():
    conn = get_db()
    rows = conn.execute("SELECT id, text FROM trivia").fetchall()
    conn.close()
    return [{"id": r[0], "text": r[1]} for r in rows]

def delete_trivia(trivia_id):
    conn = get_db()
    conn.execute("DELETE FROM trivia WHERE id=?", (trivia_id,))
    deleted = conn.total_changes > 0
    conn.commit()
    conn.close()
    return deleted

def add_request(user_id, username, title):
    now = datetime.now(TIMEZONE).isoformat()
    conn = get_db()
    conn.execute("INSERT INTO requests (user_id, username, title, time) VALUES (?,?,?,?)", (user_id, username, title, now))
    conn.commit()
    conn.close()

def get_pending_requests():
    conn = get_db()
    rows = conn.execute("SELECT * FROM requests WHERE status='pending' ORDER BY time ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def resolve_request(request_id):
    conn = get_db()
    conn.execute("UPDATE requests SET status='resolved' WHERE id=?", (request_id,))
    resolved = conn.total_changes > 0
    conn.commit()
    conn.close()
    return resolved

def gen_key():
    return "vid" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def generate_key_from_title(title):
    if not title:
        return gen_key()
    clean = re.sub(r'[^a-zA-Z0-9 ]', '', title)
    clean = clean.strip().lower().replace(' ', '-')
    clean = re.sub(r'-+', '-', clean)
    if not clean:
        return gen_key()
    if len(clean) > 40:
        clean = clean[:40]
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM files WHERE key=?", (clean,)).fetchone()
    conn.close()
    if existing:
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        clean = clean + "-" + suffix
    return clean

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(f"@{CHANNEL}", user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def is_user_subscribed_or_admin(user_id):
    if user_id == ADMIN_ID:
        return True
    return check_subscription(user_id)

def join_verify_keyboard(key):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("📢 Join Main Channel", url=f"https://t.me/{CHANNEL}"))
    keyboard.row(types.InlineKeyboardButton("🔄 Verify Access", callback_data=f"verify:{key}"))
    return keyboard

def main_menu_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🔍 Search", callback_data="menu:search"),
        types.InlineKeyboardButton("🎲 Surprise", callback_data="menu:surprise"),
        types.InlineKeyboardButton("📜 My History", callback_data="menu:history"),
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="menu:leaderboard"),
        types.InlineKeyboardButton("📊 My Stats", callback_data="menu:mystats"),
        types.InlineKeyboardButton("ℹ️ Help", callback_data="menu:help")
    )
    return keyboard

def generate_claim_card(user, key, trivia_text=None):
    now = datetime.now(TIMEZONE).strftime('%B %d, %Y at %I:%M %p')
    lines = []
    lines.append("══════════════════════════════")
    lines.append("        🎬 THE VAULT 🎬")
    lines.append("══════════════════════════════")
    lines.append("")
    lines.append(f"   👤 {user.first_name or 'Cinehead'}")
    lines.append(f"   🔑 Claimed: {key}")
    lines.append(f"   📅 {now}")
    lines.append("")
    lines.append("   🎯 Stay tuned for more assets!")
    if trivia_text:
        lines.append("")
        lines.append("   💡 Fun Fact:")
        lines.append(f"   {trivia_text}")
    lines.append("")
    lines.append("══════════════════════════════")
    lines.append("    @cinehubvaultbot")
    return "\n".join(lines)

def deliver_file(message, key):
    user = message.from_user
    f = get_file(key)
    if not f:
        bot.reply_to(message, "❌ Dead link or invalid code. This file doesn't exist in The Vault.")
        return
    log_claim(user.id, key)
    try:
        bot.send_video(message.chat.id, f["file_id"], caption=f.get("caption", ""))
        trivia_text = get_random_trivia()
        card = generate_claim_card(user, key, trivia_text)
        bot.send_message(message.chat.id, card)
        bot.send_message(message.chat.id, "🎬 Video dropping now. Keep quiet and enjoy the screen.")
    except Exception as e:
        logger.error(f"Delivery error: {e}")
        bot.reply_to(message, "⚠️ Error delivering file. Please try again later.")

# ---------- COMMAND HANDLERS ----------
@bot.message_handler(commands=['start'])
def start_command(message):
    user = message.from_user
    register_user(user)
    if is_blacklisted(user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    args = message.text.split()
    if len(args) > 1:
        key = args[1]
        f = get_file(key)
        if not f:
            bot.reply_to(message, "❌ Dead link or invalid code. This file doesn't exist in The Vault.")
            return
        if not is_user_subscribed_or_admin(user.id):
            bot.reply_to(message, "❌ You aren't a verified Cinehead. Join the main channel first.", reply_markup=join_verify_keyboard(key))
            return
        deliver_file(message, key)
    else:
        welcome = get_config("welcome_message")
        bot.reply_to(message, welcome, reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['help'])
def help_command(message):
    bot_username = bot.get_me().username
    bot.reply_to(message, f"""
📖 **How to use The Vault**

1️⃣ Join our main channel: @{CHANNEL}
2️⃣ Get a passcode or click a deep-link.
3️⃣ Type the passcode here or click the link.
4️⃣ Enjoy the file!

**User Commands:**
/search <term> – find files
/surprise [genre] – get random file
/mylogs – your history
/mystats – your stats
/leaderboard – top claimers
/request <title> – request a file
/collections – see collections
/genres – see genres
/start – show menu

**Admin Commands:**
/addtrivia <text> – add fun fact
/trivialist – list all trivia
/deletetrivia <id> – delete trivia
/requests – view pending requests
/resolverequest <id> – resolve a request
/listfiles – view all files
/stats – view telemetry
/ban <user_id> – ban a user
/unban <user_id> – unban a user
/broadcast – reply to a message
/setwelcome <text> – change welcome
/export – export logs as CSV
/userinfo <user_id> – view user details
/backup – backup files as CSV
/purge – delete all files (with confirmation)
        """)

@bot.message_handler(commands=['myid'])
def myid_command(message):
    bot.reply_to(message, f"Your user ID: `{message.from_user.id}`")

@bot.message_handler(commands=['debugadmin'])
def debugadmin_command(message):
    bot.reply_to(message, f"""
🔍 **Admin Debug Info**
Your ID: `{message.from_user.id}`
ADMIN_ID in code: `{ADMIN_ID}`
Match: `{message.from_user.id == ADMIN_ID}`

If they don't match, update ADMIN_ID in bot.py and redeploy.
        """)

@bot.message_handler(commands=['search'])
def search_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /search <keyword>")
        return
    results = search_files(args[1])
    if not results:
        bot.reply_to(message, f"No files found for `{args[1]}`.")
        return
    msg = f"🔎 **Search results for '{args[1]}'**\n"
    for r in results[:10]:
        genre = f" [{r.get('genre')}]" if r.get('genre') else ""
        collection = f" 📁{r['collection']}" if r.get('collection') else ""
        msg += f"• `{r['key']}` – {r.get('caption', 'no caption')[:30]}{genre}{collection} (pulls: {r['downloads']})\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['surprise'])
def surprise_command(message):
    user = message.from_user
    register_user(user)
    if is_blacklisted(user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    args = message.text.split(maxsplit=1)
    genre = args[1] if len(args) > 1 else None
    f = get_random_file_by_genre(genre) if genre else (random.choice(get_all_files()) if get_all_files() else None)
    if not f:
        bot.reply_to(message, "No available files right now.")
        return
    if not is_user_subscribed_or_admin(user.id):
        bot.reply_to(message, "❌ You aren't a verified Cinehead. Join the main channel first.", reply_markup=join_verify_keyboard(f["key"]))
        return
    deliver_file(message, f["key"])

@bot.message_handler(commands=['mylogs'])
def mylogs_command(message):
    user = message.from_user
    conn = get_db()
    keys = conn.execute("SELECT DISTINCT file_key FROM logs WHERE user_id=?", (user.id,)).fetchall()
    conn.close()
    if not keys:
        bot.reply_to(message, "You haven't claimed any files yet.")
        return
    msg = "📜 **Your claimed assets**\n"
    for k in keys:
        f = get_file(k[0])
        if f:
            msg += f"• `{k[0]}` – pulled {f['downloads']} times total\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['mystats'])
def mystats_command(message):
    user = message.from_user
    stats = get_user_stats(user.id)
    msg = f"📊 **Your Personal Stats**\n• Total claims: {stats['total_claims']}\n• Today's claims: {stats['today_claims']}\n• Joined: {datetime.now(TIMEZONE).strftime('%B %d, %Y')}"
    if stats['last_claim']:
        dt = datetime.fromisoformat(stats['last_claim'])
        msg += f"\n• Last claim: {dt.strftime('%B %d, %Y at %I:%M %p')}"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['leaderboard'])
def leaderboard_command(message):
    stats = get_stats()
    if not stats['top_users']:
        bot.reply_to(message, "No users have claimed anything yet.")
        return
    msg = "🏆 **Cinehead Leaderboard**\n\n"
    emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, user in enumerate(stats['top_users'], 1):
        name = user['username'] or f"User {user['id']}"
        emoji = emojis[i-1] if i <= len(emojis) else f"{i}."
        msg += f"{emoji} @{name} – {user['total_claims']} claims\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['collections'])
def collections_command(message):
    collections = get_collections()
    if not collections:
        bot.reply_to(message, "No collections available.")
        return
    msg = "📁 **Available Collections**\n\n"
    for c in collections:
        count = len(get_files_by_collection(c))
        msg += f"• 📂 {c} ({count} files)\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['collection'])
def collection_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /collection <name>")
        return
    files = get_files_by_collection(args[1])
    if not files:
        bot.reply_to(message, f"No files found in collection `{args[1]}`.")
        return
    msg = f"📁 **Collection: {args[1]}**\n\n"
    for f in files:
        msg += f"• `{f['key']}` – {f.get('caption', 'no caption')[:30]} (pulls: {f['downloads']})\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['genres'])
def genres_command(message):
    genres = get_genres()
    if not genres:
        bot.reply_to(message, "No genres available.")
        return
    msg = "🎭 **Available Genres**\n\n"
    for g in genres:
        count = len(get_files_by_genre(g))
        msg += f"• 🎬 {g} ({count} files)\n"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['request'])
def request_command(message):
    user = message.from_user
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /request <movie/file name>")
        return
    add_request(user.id, user.username or "", args[1])
    bot.reply_to(message, f"✅ Request submitted: '{args[1]}'")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_passcode(message):
    text = message.text.strip()
    if not text or text.startswith("/"):
        return
    user = message.from_user
    register_user(user)
    if is_blacklisted(user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    f = get_file(text)
    if not f:
        bot.reply_to(message, "❌ Dead link or invalid code.")
        return
    if not is_user_subscribed_or_admin(user.id):
        bot.reply_to(message, "❌ You aren't a verified Cinehead. Join the main channel first.", reply_markup=join_verify_keyboard(text))
        return
    deliver_file(message, text)

# ---------- CALLBACKS ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("verify:"))
def verify_callback(call):
    key = call.data.split(":", 1)[1]
    user = call.from_user
    register_user(user)
    if is_blacklisted(user.id):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        bot.edit_message_text("⛔ Access denied.", call.message.chat.id, call.message.message_id)
        return
    if not is_user_subscribed_or_admin(user.id):
        bot.answer_callback_query(call.id, "You are still not a member. Please join first.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    f = get_file(key)
    if not f:
        bot.send_message(call.message.chat.id, "❌ Dead link or invalid code.")
        return
    log_claim(user.id, key)
    try:
        bot.send_video(call.message.chat.id, f["file_id"], caption=f.get("caption", ""))
        trivia_text = get_random_trivia()
        card = generate_claim_card(user, key, trivia_text)
        bot.send_message(call.message.chat.id, card)
        bot.send_message(call.message.chat.id, "🎬 Video dropping now. Keep quiet and enjoy the screen.")
    except Exception as e:
        logger.error(f"Delivery via verify failed: {e}")
        bot.send_message(call.message.chat.id, "⚠️ Error delivering file. Please try again later.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu:"))
def menu_callback(call):
    action = call.data.split(":", 1)[1]
    if action == "search":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔍 Type /search <keyword>")
    elif action == "surprise":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🎲 Getting you a random file...")
        surprise_command(call.message)
    elif action == "history":
        bot.answer_callback_query(call.id)
        mylogs_command(call.message)
    elif action == "leaderboard":
        bot.answer_callback_query(call.id)
        leaderboard_command(call.message)
    elif action == "mystats":
        bot.answer_callback_query(call.id)
        mystats_command(call.message)
    elif action == "help":
        bot.answer_callback_query(call.id)
        help_command(call.message)

# ---------- ADMIN COMMANDS (WITH DEBUG) ----------
@bot.message_handler(content_types=['video', 'document'])
def admin_ingest(message):
    logger.info(f"📥 Ingest from {message.from_user.id}, ADMIN_ID: {ADMIN_ID}")
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    video = None
    if message.content_type == 'video':
        video = message.video
    elif message.content_type == 'document':
        doc = message.document
        if doc.mime_type and doc.mime_type.startswith('video/'):
            video = doc
        elif doc.file_name and doc.file_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
            video = doc
        else:
            bot.reply_to(message, "❌ Please send a video file.")
            return
    if not video:
        bot.reply_to(message, "❌ Could not process this file.")
        return
    caption = message.caption or ""
    genre = None
    collection = None
    genre_match = re.search(r'genre:\s*([^\n]+)', caption, re.IGNORECASE)
    collection_match = re.search(r'collection:\s*([^\n]+)', caption, re.IGNORECASE)
    if genre_match:
        genre = genre_match.group(1).strip()
    if collection_match:
        collection = collection_match.group(1).strip()
    clean_caption = re.sub(r'genre:\s*[^\n]+', '', caption, flags=re.IGNORECASE)
    clean_caption = re.sub(r'collection:\s*[^\n]+', '', clean_caption, flags=re.IGNORECASE).strip()
    title = clean_caption.strip()
    if not title and message.content_type == 'document' and message.document.file_name:
        title = os.path.splitext(message.document.file_name)[0]
    key = generate_key_from_title(title) if title else gen_key()
    add_file(key, video.file_id, clean_caption, genre, collection)
    bot_username = bot.get_me().username
    response = f"✅ Asset ingested.\nKey: `{key}`\nDeep-link: https://t.me/{bot_username}?start={key}\n"
    if genre:
        response += f"Genre: {genre}\n"
    if collection:
        response += f"Collection: {collection}\n"
    bot.reply_to(message, response, parse_mode="Markdown")

@bot.message_handler(commands=['addtrivia'])
def add_trivia_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /addtrivia <fun fact>")
        return
    add_trivia(args[1])
    bot.reply_to(message, "✅ Trivia added!")

@bot.message_handler(commands=['trivialist'])
def trivialist_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    t_list = get_all_trivia()
    if not t_list:
        bot.reply_to(message, "No trivia added yet.")
        return
    msg = "📚 **Trivia List**\n\n"
    for t in t_list:
        msg += f"• `{t['id']}` – {t['text'][:50]}...\n"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['deletetrivia'])
def deletetrivia_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /deletetrivia <id>")
        return
    try:
        if delete_trivia(int(args[1])):
            bot.reply_to(message, f"✅ Trivia {args[1]} deleted.")
        else:
            bot.reply_to(message, f"Trivia {args[1]} not found.")
    except:
        bot.reply_to(message, "Invalid ID.")

@bot.message_handler(commands=['requests'])
def requests_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    reqs = get_pending_requests()
    if not reqs:
        bot.reply_to(message, "No pending requests.")
        return
    msg = "📋 **Pending Requests**\n\n"
    for r in reqs:
        dt = datetime.fromisoformat(r['time'])
        msg += f"• `{r['id']}` – {r['title']} (by @{r['username'] or 'unknown'}) at {dt.strftime('%B %d, %I:%M %p')}\n"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['resolverequest'])
def resolverequest_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /resolverequest <id>")
        return
    try:
        if resolve_request(int(args[1])):
            bot.reply_to(message, f"✅ Request {args[1]} marked as resolved.")
        else:
            bot.reply_to(message, f"Request {args[1]} not found.")
    except:
        bot.reply_to(message, "Invalid ID.")

@bot.message_handler(commands=['listfiles'])
def listfiles_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    files = get_all_files()
    if not files:
        bot.reply_to(message, "Archive is empty.")
        return
    msg = "🗂️ **Archive Inventory**\n\n"
    for f in files:
        msg += f"• `{f['key']}` – pulls: {f['downloads']} | uploaded: {f['uploaded'][:10]}"
        if f.get('genre'):
            msg += f" | 🎬 {f['genre']}"
        if f.get('collection'):
            msg += f" | 📁 {f['collection']}"
        msg += "\n"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    stats = get_stats()
    msg = f"📊 **Vault Telemetry**\n• Total Cineheads: {stats['total_users']}\n• Total indexed assets: {stats['total_files']}\n• Total deliveries: {stats['total_deliveries']}\n\n🏆 **Top passkeys**\n"
    if stats['top_files']:
        for i, f in enumerate(stats['top_files'], 1):
            msg += f"  {i}. `{f['key']}` – {f['downloads']} pulls\n"
    else:
        msg += "  No files yet.\n"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['ban'])
def ban_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /ban <user_id>")
        return
    try:
        user_id = int(args[1])
        conn = get_db()
        conn.execute("UPDATE users SET blacklisted=1 WHERE id=?", (user_id,))
        if conn.total_changes:
            bot.reply_to(message, f"🚫 User {user_id} banned.")
        else:
            bot.reply_to(message, f"User {user_id} not found.")
        conn.commit()
        conn.close()
    except:
        bot.reply_to(message, "Invalid user_id.")

@bot.message_handler(commands=['unban'])
def unban_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /unban <user_id>")
        return
    try:
        user_id = int(args[1])
        conn = get_db()
        conn.execute("UPDATE users SET blacklisted=0 WHERE id=?", (user_id,))
        if conn.total_changes:
            bot.reply_to(message, f"✅ User {user_id} unbanned.")
        else:
            bot.reply_to(message, f"User {user_id} not found.")
        conn.commit()
        conn.close()
    except:
        bot.reply_to(message, "Invalid user_id.")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Reply to a message with /broadcast.")
        return
    target = message.reply_to_message
    users = get_all_users()
    if not users:
        bot.reply_to(message, "No users.")
        return
    bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    sent = 0
    for i, user in enumerate(users, 1):
        try:
            bot.copy_message(chat_id=user["id"], from_chat_id=target.chat.id, message_id=target.message_id)
            sent += 1
        except:
            pass
        if i % 25 == 0:
            time.sleep(1)
    bot.reply_to(message, f"✅ Broadcast completed. Sent to {sent} users.")

@bot.message_handler(commands=['setwelcome'])
def setwelcome_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /setwelcome <new welcome message>")
        return
    set_config("welcome_message", args[1])
    bot.reply_to(message, "✅ Welcome message updated.")

@bot.message_handler(commands=['export'])
def export_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    logs = get_all_logs()
    if not logs:
        bot.reply_to(message, "No logs to export.")
        return
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "user_id", "username", "file_key", "time"])
    writer.writeheader()
    for row in logs:
        writer.writerow(row)
    csv_data = output.getvalue()
    output.close()
    bot.send_document(message.chat.id, document=("distribution_logs.csv", csv_data.encode()), caption="📊 Full distribution log.")

@bot.message_handler(commands=['userinfo'])
def userinfo_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /userinfo <user_id>")
        return
    try:
        user_id = int(args[1])
        info = get_user_info(user_id)
        if not info:
            bot.reply_to(message, f"User {user_id} not found.")
            return
        msg = f"👤 **User {user_id}**\nUsername: @{info.get('username', 'None')}\nName: {info.get('name', 'None')}\nJoined: {info['joined'][:10]}\nLast seen: {info['last_seen'][:10]}\nBlacklisted: {'Yes' if info.get('blacklisted', 0) else 'No'}\nTotal claims: {info['total_claims']}\n\nRecent claims:\n"
        for log in info['recent_claims'][:5]:
            msg += f"• `{log['file_key']}` at {log['time'][:16]}\n"
        bot.reply_to(message, msg, parse_mode="Markdown")
    except:
        bot.reply_to(message, "Invalid user_id.")

@bot.message_handler(commands=['backup'])
def backup_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    try:
        files = get_all_files()
        if not files:
            bot.reply_to(message, "No files to back up.")
            return
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=["key", "file_id", "caption", "uploaded", "downloads", "genre", "collection"])
        writer.writeheader()
        for f in files:
            writer.writerow(f)
        csv_data = output.getvalue()
        output.close()
        bot.send_document(message.chat.id, document=("files_backup.csv", csv_data.encode()), caption="📦 Full files backup (CSV)")
    except Exception as e:
        bot.reply_to(message, f"❌ Backup failed: {e}")

@bot.message_handler(commands=['purge'])
def purge_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ Not admin. Your ID: {message.from_user.id}")
        return
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("⚠️ YES, DELETE ALL", callback_data="purge:confirm"), types.InlineKeyboardButton("❌ Cancel", callback_data="purge:cancel"))
    bot.reply_to(message, "⚠️ This will delete ALL files and logs. Are you sure?", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("purge:"))
def purge_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Not authorised.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    if action == "confirm":
        bot.answer_callback_query(call.id, "Purging...")
        conn = get_db()
        conn.execute("DELETE FROM logs")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM requests")
        conn.commit()
        conn.close()
        bot.edit_message_text("✅ All files and logs have been deleted.", call.message.chat.id, call.message.message_id)
    elif action == "cancel":
        bot.answer_callback_query(call.id, "Cancelled.")
        bot.edit_message_text("❌ Purge cancelled.", call.message.chat.id, call.message.message_id)

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()
    logger.info(f"✅ Bot username: {bot.get_me().username}")
    logger.info(f"✅ ADMIN_ID: {ADMIN_ID}")
    logger.info("✅ Bot started! Polling...")
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
