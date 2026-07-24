#!/usr/bin/env python3
"""
Cinehub Vault Bot – Clean Version, No Underscores
All links use bot.get_me().username dynamically.
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
from datetime import datetime, timedelta
from threading import Thread
from telebot import TeleBot, types

# ---------- ENVIRONMENT ----------
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    logging.error("TOKEN not set")
    sys.exit(1)

# Your correct admin ID
ADMIN_ID = 6537318639

CHANNEL = os.environ.get("CHANNEL", "").strip()
if not CHANNEL:
    logging.error("CHANNEL not set")
    sys.exit(1)

# ---------- LOGGING ----------
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
            uploaded TEXT NOT NULL,
            downloads INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            claim_limit INTEGER DEFAULT NULL,
            release_time TEXT DEFAULT NULL,
            expiry_date TEXT DEFAULT NULL
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
            daily_claims INTEGER DEFAULT 0,
            daily_claims_date TEXT,
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
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    defaults = {
        "welcome_message": "This is The Vault. Type a secret passcode or use a valid deep-link to pull files.",
        "daily_limit": "5",
        "auto_delete_expired": "1",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()
    logger.info("✅ Database ready.")

# ---------- CONFIG ----------
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

# ---------- USER HELPERS ----------
def register_user(user):
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, name, joined, last_seen) VALUES (?,?,?,?,?)",
        (user.id, user.username or "", user.first_name or "", now, now)
    )
    conn.execute(
        "UPDATE users SET username=?, name=?, last_seen=? WHERE id=?",
        (user.username or "", user.first_name or "", now, user.id)
    )
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
    logs = conn.execute(
        "SELECT file_key, time FROM logs WHERE user_id=? ORDER BY time DESC LIMIT 10",
        (user_id,)
    ).fetchall()
    total_claims = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return dict(user) | {"recent_claims": [dict(l) for l in logs], "total_claims": total_claims}

def get_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, name FROM users WHERE blacklisted=0").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_daily_claims(user_id):
    today = datetime.now().date().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT daily_claims, daily_claims_date FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    if row:
        if row["daily_claims_date"] == today:
            new_count = row["daily_claims"] + 1
        else:
            new_count = 1
        conn.execute(
            "UPDATE users SET daily_claims=?, daily_claims_date=? WHERE id=?",
            (new_count, today, user_id)
        )
    else:
        conn.execute(
            "UPDATE users SET daily_claims=1, daily_claims_date=? WHERE id=?",
            (today, user_id)
        )
    conn.commit()
    conn.close()

def get_today_claims(user_id):
    today = datetime.now().date().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT daily_claims FROM users WHERE id=? AND daily_claims_date=?",
        (user_id, today)
    ).fetchone()
    conn.close()
    return row["daily_claims"] if row else 0

# ---------- FILE HELPERS ----------
def add_file(key, file_id, caption="", release_time=None, expiry_date=None):
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO files (key, file_id, caption, uploaded, release_time, expiry_date) VALUES (?,?,?,?,?,?)",
        (key, file_id, caption, now, release_time, expiry_date)
    )
    conn.commit()
    conn.close()
    logger.info(f"✅ File added: {key}")

def get_file(key):
    conn = get_db()
    row = conn.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        logger.info(f"✅ File found: {key}")
        return dict(row)
    logger.warning(f"❌ File not found: {key}")
    return None

def get_all_files():
    conn = get_db()
    rows = conn.execute(
        "SELECT key, uploaded, downloads, claimed, claim_limit, release_time, expiry_date "
        "FROM files ORDER BY uploaded DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_file(key, new_key=None, claim_limit=None, release_time=None, expiry_date=None):
    conn = get_db()
    updates = []
    params = []
    if new_key is not None:
        updates.append("key = ?")
        params.append(new_key)
    if claim_limit is not None:
        updates.append("claim_limit = ?")
        params.append(claim_limit)
    if release_time is not None:
        updates.append("release_time = ?")
        params.append(release_time)
    if expiry_date is not None:
        updates.append("expiry_date = ?")
        params.append(expiry_date)
    if not updates:
        conn.close()
        return False
    params.append(key)
    query = "UPDATE files SET " + ", ".join(updates) + " WHERE key = ?"
    conn.execute(query, params)
    updated = conn.total_changes > 0
    conn.commit()
    conn.close()
    return updated

def delete_file(key):
    conn = get_db()
    conn.execute("DELETE FROM logs WHERE file_key=?", (key,))
    conn.execute("DELETE FROM files WHERE key=?", (key,))
    deleted = conn.total_changes > 0
    conn.commit()
    conn.close()
    return deleted

def log_claim(user_id, key):
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("UPDATE files SET downloads=downloads+1, claimed=claimed+1 WHERE key=?", (key,))
    conn.execute("INSERT INTO logs (user_id, file_key, time) VALUES (?,?,?)", (user_id, key, now))
    conn.execute("UPDATE users SET total_claims = total_claims + 1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def search_files(query):
    conn = get_db()
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT key, caption, uploaded, downloads, claimed, claim_limit FROM files "
        "WHERE key LIKE ? OR caption LIKE ? ORDER BY uploaded DESC LIMIT 50",
        (like, like)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_random_file():
    conn = get_db()
    now = datetime.now().isoformat()
    row = conn.execute(
        "SELECT * FROM files WHERE "
        "(release_time IS NULL OR release_time <= ?) AND "
        "(expiry_date IS NULL OR expiry_date > ?) AND "
        "(claim_limit IS NULL OR claimed < claim_limit) "
        "ORDER BY RANDOM() LIMIT 1",
        (now, now)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_claimed_keys(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT file_key FROM logs WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    return [r["file_key"] for r in rows]

def get_stats():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    deliveries = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    top_files = conn.execute(
        "SELECT key, claimed FROM files ORDER BY claimed DESC LIMIT 3"
    ).fetchall()
    top_users = conn.execute(
        "SELECT id, username, total_claims FROM users ORDER BY total_claims DESC LIMIT 3"
    ).fetchall()
    conn.close()
    return {
        "total_users": users,
        "total_files": files,
        "total_deliveries": deliveries,
        "top_files": [dict(r) for r in top_files],
        "top_users": [dict(r) for r in top_users],
    }

def get_all_logs():
    conn = get_db()
    rows = conn.execute(
        "SELECT l.id, l.user_id, l.file_key, l.time, u.username "
        "FROM logs l LEFT JOIN users u ON l.user_id = u.id "
        "ORDER BY l.time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ---------- AUTO CLEANUP ----------
def cleanup_expired_files():
    now = datetime.now().isoformat()
    conn = get_db()
    expired = conn.execute(
        "SELECT key FROM files WHERE expiry_date IS NOT NULL AND expiry_date <= ?",
        (now,)
    ).fetchall()
    for row in expired:
        key = row["key"]
        conn.execute("DELETE FROM logs WHERE file_key=?", (key,))
        conn.execute("DELETE FROM files WHERE key=?", (key,))
        logger.info(f"Auto-deleted expired file: {key}")
    conn.commit()
    conn.close()

def auto_cleanup_loop():
    while True:
        try:
            cleanup_expired_files()
            logger.info("Auto-cleanup completed.")
        except Exception as e:
            logger.error(f"Auto-cleanup error: {e}")
        time.sleep(86400)

# ---------- HELPERS ----------
def gen_key():
    return "vid_" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(f"@{CHANNEL}", user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return False

def join_verify_keyboard(key):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(types.InlineKeyboardButton("📢 Join Main Channel", url=f"https://t.me/{CHANNEL}"))
    keyboard.row(types.InlineKeyboardButton("🔄 Verify Access", callback_data=f"verify:{key}"))
    return keyboard

def main_menu_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("🔍 Search", callback_data="menu:search"),
        types.InlineKeyboardButton("🎲 Surprise", callback_data="menu:surprise")
    )
    keyboard.row(
        types.InlineKeyboardButton("📜 My History", callback_data="menu:history"),
        types.InlineKeyboardButton("📊 Stats", callback_data="menu:stats")
    )
    keyboard.row(
        types.InlineKeyboardButton("ℹ️ Help", callback_data="menu:help")
    )
    return keyboard

def parse_datetime(text):
    patterns = [
        (r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", "%Y-%m-%d %H:%M"),
        (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
        (r"(\d{2})/(\d{2})/(\d{4})", "%d/%m/%Y"),
        (r"(\d{2})\.(\d{2})\.(\d{4})", "%d.%m.%Y"),
    ]
    for pattern, fmt in patterns:
        if re.match(pattern, text):
            try:
                return datetime.strptime(text, fmt).isoformat()
            except:
                pass
    return None

def deliver_file(message, key):
    user = message.from_user
    f = get_file(key)

    if not f:
        bot.reply_to(message, "❌ Dead link or invalid code. This file doesn't exist in The Vault.")
        return

    now = datetime.now().isoformat()
    if f["release_time"] and f["release_time"] > now:
        dt = datetime.fromisoformat(f["release_time"])
        diff = dt - datetime.now()
        hours = int(diff.total_seconds() // 3600)
        minutes = int((diff.total_seconds() % 3600) // 60)
        bot.reply_to(
            message,
            f"⏳ This asset is locked until **{dt.strftime('%H:%M on %b %d')}**.\nWait {hours}h {minutes}m more."
        )
        return

    if f["expiry_date"] and f["expiry_date"] <= now:
        bot.reply_to(message, "🔒 This package has expired!")
        return

    if f["claim_limit"] is not None and f["claimed"] >= f["claim_limit"]:
        bot.reply_to(message, "🔒 This package has expired! Only the fastest Cineheads secure the payload.")
        return

    daily_limit = int(get_config("daily_limit", "5"))
    today_claims = get_today_claims(user.id)
    if today_claims >= daily_limit:
        bot.reply_to(
            message,
            f"⛔ You've reached your daily limit of **{daily_limit}** claims. Come back tomorrow!"
        )
        return

    log_claim(user.id, key)
    update_daily_claims(user.id)

    try:
        bot.send_video(message.chat.id, f["file_id"], caption=f.get("caption", ""))
        bot.send_message(message.chat.id, "🎬 Video dropping now. Keep quiet and enjoy the screen.")
    except Exception as e:
        logger.error(f"Delivery error: {e}")
        bot.reply_to(message, "⚠️ Error delivering file. Please try again later.")

# ---------- INLINE MODE ----------
@bot.inline_handler(func=lambda query: True)
def inline_query(query):
    user = query.from_user
    register_user(user)

    if is_blacklisted(user.id):
        results = [
            types.InlineQueryResultArticle(
                id="blocked",
                title="⛔ Access Denied",
                input_message_content=types.InputTextMessageContent(
                    "You are blacklisted from using The Vault."
                )
            )
        ]
        bot.answer_inline_query(query.id, results, cache_time=1)
        return

    if not check_subscription(user.id):
        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(types.InlineKeyboardButton("📢 Join Main Channel", url=f"https://t.me/{CHANNEL}"))
        results = [
            types.InlineQueryResultArticle(
                id="not_subscribed",
                title="❌ Not a Verified Cinehead",
                description="Join the main channel first",
                input_message_content=types.InputTextMessageContent(
                    "❌ You aren't a verified Cinehead. Join the main channel first."
                ),
                reply_markup=keyboard
            )
        ]
        bot.answer_inline_query(query.id, results, cache_time=5)
        return

    search_term = query.query or ""
    files = search_files(search_term) if search_term else get_all_files()[:10]

    if not files:
        results = [
            types.InlineQueryResultArticle(
                id="no_results",
                title="🔍 No Results Found",
                description="Try a different search term",
                input_message_content=types.InputTextMessageContent(
                    f"No files found for '{search_term}'. Try again!"
                )
            )
        ]
        bot.answer_inline_query(query.id, results, cache_time=5)
        return

    bot_username = bot.get_me().username
    results = []
    for idx, f in enumerate(files[:50]):
        key = f["key"]
        claimed = f["claimed"]
        limit = f["claim_limit"] or "∞"
        file_record = get_file(key)
        if not file_record:
            continue

        now = datetime.now().isoformat()
        if file_record["release_time"] and file_record["release_time"] > now:
            status = "⏳ Locked"
        elif file_record["expiry_date"] and file_record["expiry_date"] <= now:
            status = "🔒 Expired"
        elif file_record["claim_limit"] is not None and file_record["claimed"] >= file_record["claim_limit"]:
            status = "🔒 Claimed Out"
        else:
            status = "✅ Available"

        result = types.InlineQueryResultArticle(
            id=f"file_{idx}_{key}",
            title=f"🎬 {key}",
            description=f"{status} | {claimed}/{limit} claims",
            input_message_content=types.InputTextMessageContent(
                f"🔑 **Code:** `{key}`\n📊 Status: {status}\n📥 Claims: {claimed}/{limit}\n\n"
                f"Send this code to the bot to claim: @{bot_username}?start={key}"
            ),
            reply_markup=types.InlineKeyboardMarkup().row(
                types.InlineKeyboardButton("🎬 Claim Now", url=f"https://t.me/{bot_username}?start={key}")
            )
        )
        results.append(result)

    if not results:
        results = [
            types.InlineQueryResultArticle(
                id="no_valid",
                title="⚠️ No Available Files",
                input_message_content=types.InputTextMessageContent(
                    "No files are currently available. Check back later!"
                )
            )
        ]

    bot.answer_inline_query(query.id, results, cache_time=10)

# ---------- USER COMMANDS ----------
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

        if not check_subscription(user.id):
            bot.reply_to(
                message,
                "❌ You aren't a verified Cinehead. Join the main channel first.",
                reply_markup=join_verify_keyboard(key)
            )
            return

        deliver_file(message, key)
    else:
        welcome = get_config("welcome_message")
        bot.reply_to(message, welcome, reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['help'])
def help_command(message):
    bot_username = bot.get_me().username
    bot.reply_to(
        message,
        f"""
📖 **How to use The Vault**

1️⃣ Join our main channel: @{CHANNEL}
2️⃣ Get a passcode or click a deep-link.
3️⃣ Type the passcode here or click the link.
4️⃣ Enjoy the file!

**Inline Mode:**
Type `@{bot_username} <search>` in any chat!

**Commands:**
/search <term> – find files
/surprise – get a random file
/mylogs – see your history
/start – show this menu
        """
    )

@bot.message_handler(commands=['myid'])
def myid_command(message):
    bot.reply_to(message, f"Your user ID: `{message.from_user.id}`")

@bot.message_handler(commands=['debugadmin'])
def debugadmin_command(message):
    bot.reply_to(
        message,
        f"Your ID: {message.from_user.id}\n"
        f"ADMIN_ID in code: {ADMIN_ID}\n"
        f"Match: {message.from_user.id == ADMIN_ID}"
    )

@bot.message_handler(commands=['showusername'])
def show_username_command(message):
    bot_username = bot.get_me().username
    bot.reply_to(
        message,
        f"Bot username from API: `{bot_username}`\n"
        f"Deep-link test: https://t.me/{bot_username}?start=test"
    )

@bot.message_handler(commands=['search'])
def search_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /search <keyword>")
        return

    query = args[1]
    results = search_files(query)

    if not results:
        bot.reply_to(message, f"No files found for `{query}`.")
        return

    msg = f"🔎 **Search results for '{query}'**\n"
    for r in results[:10]:
        lim = r["claim_limit"] or "∞"
        msg += f"• `{r['key']}` – {r.get('caption', 'no caption')[:30]} (pulls: {r['downloads']}, claimed: {r['claimed']}/{lim})\n"

    if len(results) > 10:
        msg += f"\n... and {len(results) - 10} more."

    bot.reply_to(message, msg)

@bot.message_handler(commands=['surprise'])
def surprise_command(message):
    user = message.from_user
    register_user(user)

    if is_blacklisted(user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    f = get_random_file()
    if not f:
        bot.reply_to(message, "No available files right now. Try again later.")
        return

    if not check_subscription(user.id):
        bot.reply_to(
            message,
            "❌ You aren't a verified Cinehead. Join the main channel first.",
            reply_markup=join_verify_keyboard(f["key"])
        )
        return

    deliver_file(message, f["key"])

@bot.message_handler(commands=['mylogs'])
def mylogs_command(message):
    user = message.from_user
    keys = get_user_claimed_keys(user.id)

    if not keys:
        bot.reply_to(message, "You haven't claimed any files yet.")
        return

    msg = "📜 **Your claimed assets**\n"
    for k in keys:
        f = get_file(k)
        if f:
            claimed = f["claimed"]
            msg += f"• `{k}` – claimed {claimed} times total\n"

    bot.reply_to(message, msg)

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

    if not check_subscription(user.id):
        bot.reply_to(
            message,
            "❌ You aren't a verified Cinehead. Join the main channel first.",
            reply_markup=join_verify_keyboard(text)
        )
        return

    deliver_file(message, text)

# ---------- CALLBACK HANDLERS ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("verify:"))
def verify_callback(call):
    key = call.data.split(":", 1)[1]
    user = call.from_user

    register_user(user)

    if is_blacklisted(user.id):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        bot.edit_message_text("⛔ Access denied.", call.message.chat.id, call.message.message_id)
        return

    if not check_subscription(user.id):
        bot.answer_callback_query(call.id, "You are still not a member. Please join first.", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

    f = get_file(key)
    if not f:
        bot.send_message(call.message.chat.id, "❌ Dead link or invalid code.")
        return

    if f["claim_limit"] is not None and f["claimed"] >= f["claim_limit"]:
        bot.send_message(call.message.chat.id, "🔒 This package has expired!")
        return

    log_claim(user.id, key)
    update_daily_claims(user.id)

    try:
        bot.send_video(call.message.chat.id, f["file_id"], caption=f.get("caption", ""))
        bot.send_message(call.message.chat.id, "🎬 Video dropping now. Keep quiet and enjoy the screen.")
    except Exception as e:
        logger.error(f"Delivery via verify failed: {e}")
        bot.send_message(call.message.chat.id, "⚠️ Error delivering file. Please try again later.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu:"))
def menu_callback(call):
    action = call.data.split(":", 1)[1]

    if action == "search":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔍 Type /search <keyword> to find files.")

    elif action == "surprise":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🎲 Getting you a random file...")
        surprise_command(call.message)

    elif action == "history":
        bot.answer_callback_query(call.id)
        mylogs_command(call.message)

    elif action == "stats":
        bot.answer_callback_query(call.id)
        user = call.from_user
        info = get_user_info(user.id)
        if info:
            msg = (
                f"📊 **Your Stats**\n"
                f"• Total claims: {info['total_claims']}\n"
                f"• Today's claims: {get_today_claims(user.id)}\n"
                f"• Joined: {info['joined'][:10]}\n"
                f"• Last seen: {info['last_seen'][:10]}\n"
            )
            bot.send_message(call.message.chat.id, msg)

    elif action == "help":
        bot.answer_callback_query(call.id)
        help_command(call.message)

# ---------- ADMIN COMMANDS ----------
@bot.message_handler(content_types=['video', 'document'])
def admin_ingest(message):
    if message.from_user.id != ADMIN_ID:
        logger.warning(f"Unauthorized ingest attempt from {message.from_user.id}")
        bot.reply_to(message, f"❌ You are not admin. Your ID: {message.from_user.id}")
        return

    video = message.video or message.document
    if not video:
        bot.reply_to(message, "Please send a video file.")
        return

    caption = message.caption or ""
    release_time = None
    expiry_date = None

    release_match = re.search(r'release:\s*([\d\-: /]+)', caption, re.IGNORECASE)
    expiry_match = re.search(r'expiry:\s*([\d\-: /]+)', caption, re.IGNORECASE)

    if release_match:
        parsed = parse_datetime(release_match.group(1).strip())
        if parsed:
            release_time = parsed

    if expiry_match:
        parsed = parse_datetime(expiry_match.group(1).strip())
        if parsed:
            expiry_date = parsed

    clean_caption = re.sub(r'release:\s*[\d\-: /]+', '', caption, flags=re.IGNORECASE)
    clean_caption = re.sub(r'expiry:\s*[\d\-: /]+', '', clean_caption, flags=re.IGNORECASE).strip()

    key = gen_key()  # This generates vid_xxxxx
    add_file(key, video.file_id, clean_caption, release_time, expiry_date)

    bot_username = bot.get_me().username
    logger.info(f"✅ Key stored: {key}")  # DEBUG: check in Railway logs

    bot.reply_to(
        message,
        f"✅ Asset ingested.\n"
        f"Key: `{key}`\n"
        f"Deep-link: https://t.me/{bot_username}?start={key}\n"   # ← key used directly
        f"Inline search: @{bot_username} {key}\n"
        f"Release: {release_time or 'immediate'}\n"
        f"Expiry: {expiry_date or 'never'}\n\n"
        f"Use /setcode to rename or /setlimit to change limit.",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['setcode'])
def setcode_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(
            message,
            f"❌ You are not admin.\nYour ID: {message.from_user.id}\nExpected ADMIN_ID: {ADMIN_ID}"
        )
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Usage: /setcode <old_key> <new_key> [limit]")
        return

    old, new = args[1], args[2]
    limit = int(args[3]) if len(args) > 3 and args[3].isdigit() else None

    if get_file(new):
        bot.reply_to(message, "New key already taken.")
        return

    success = update_file(old, new_key=new, claim_limit=limit)
    if success:
        bot_username = bot.get_me().username
        bot.reply_to(
            message,
            f"✅ Updated to `{new}`.\n"
            f"Deep-link: https://t.me/{bot_username}?start={new}\n"
            f"Inline search: @{bot_username} {new}\n"
            f"Limit: {limit or '∞'}",
            parse_mode="Markdown"
        )
    else:
        bot.reply_to(message, f"Key '{old}' not found.")

@bot.message_handler(commands=['setlimit'])
def setlimit_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Usage: /setlimit <key> <limit>")
        return

    key, limit = args[1], args[2]
    try:
        limit = int(limit)
    except ValueError:
        bot.reply_to(message, "Limit must be a number.")
        return

    success = update_file(key, claim_limit=limit)
    if success:
        bot.reply_to(message, f"✅ Claim limit for `{key}` set to {limit}.", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"Key '{key}' not found.")

@bot.message_handler(commands=['deletecode'])
def deletecode_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /deletecode <target_key>")
        return

    key = args[1]
    if delete_file(key):
        bot.reply_to(message, f"🗑️ Key '{key}' and its history wiped.")
    else:
        bot.reply_to(message, f"Key '{key}' not found.")

@bot.message_handler(commands=['listfiles'])
def listfiles_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    page = message.from_user.id
    current_page = bot.user_data.get(str(page), {}).get("list_page", 0)

    per_page = 5
    files = get_all_files()
    total = len(files)

    if not files:
        bot.reply_to(message, "Archive is empty.")
        return

    start = current_page * per_page
    end = min(start + per_page, total)
    page_files = files[start:end]

    total_pages = ((total - 1) // per_page) + 1 if total > 0 else 1
    msg = f"🗂️ **Archive Inventory** (Page {current_page + 1}/{total_pages})\n\n"
    bot_username = bot.get_me().username
    for f in page_files:
        lim = f["claim_limit"] or "∞"
        release = f["release_time"][:16] if f["release_time"] else "now"
        expiry = f["expiry_date"][:10] if f["expiry_date"] else "never"
        msg += (
            f"• `{f['key']}`\n"
            f"  📥 {f['downloads']} pulls | {f['claimed']}/{lim} claimed\n"
            f"  📅 Release: {release} | Expiry: {expiry}\n"
            f"  🔗 https://t.me/{bot_username}?start={f['key']}\n"
            f"  🔍 @{bot_username} {f['key']}\n\n"
        )

    keyboard = types.InlineKeyboardMarkup()
    nav_row = []

    if current_page > 0:
        nav_row.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"list:prev:{current_page}"))
    if end < total:
        nav_row.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"list:next:{current_page}"))

    if nav_row:
        keyboard.row(*nav_row)

    for f in page_files:
        keyboard.row(types.InlineKeyboardButton(f"🗑️ Wipe {f['key']}", callback_data=f"list:wipe:{f['key']}"))

    bot.reply_to(message, msg, parse_mode="Markdown", reply_markup=keyboard if keyboard.keyboard else None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("list:"))
def list_callback(call):
    parts = call.data.split(":")
    action = parts[1]

    if action == "prev" or action == "next":
        current_page = int(parts[2])
        new_page = current_page - 1 if action == "prev" else current_page + 1

        user_id = call.from_user.id
        if user_id not in bot.user_data:
            bot.user_data[user_id] = {}
        bot.user_data[user_id]["list_page"] = new_page

        bot.answer_callback_query(call.id)
        listfiles_command(call.message)

    elif action == "wipe":
        key = parts[2]
        bot.answer_callback_query(call.id, f"Deleting {key}...")
        if delete_file(key):
            bot.edit_message_text(f"🗑️ Key '{key}' wiped.", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Key not found.", show_alert=True)

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    stats = get_stats()

    msg = (
        f"📊 **Vault Telemetry**\n"
        f"• Total Cineheads: {stats['total_users']}\n"
        f"• Total indexed assets: {stats['total_files']}\n"
        f"• Total deliveries: {stats['total_deliveries']}\n\n"
        f"🏆 **Top passkeys**\n"
    )

    if stats['top_files']:
        for i, f in enumerate(stats['top_files'], 1):
            msg += f"  {i}. `{f['key']}` – {f['claimed']} claims\n"
    else:
        msg += "  No files yet.\n"

    msg += f"\n🏆 **Top Cineheads**\n"
    if stats['top_users']:
        for i, u in enumerate(stats['top_users'], 1):
            name = u['username'] or u['id']
            msg += f"  {i}. @{name} – {u['total_claims']} claims\n"
    else:
        msg += "  No users yet."

    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['userinfo'])
def userinfo_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /userinfo <user_id>")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid user_id.")
        return

    info = get_user_info(user_id)
    if not info:
        bot.reply_to(message, f"User {user_id} not found.")
        return

    msg = (
        f"👤 **User {user_id}**\n"
        f"Username: @{info['username'] or 'None'}\n"
        f"Name: {info['name'] or 'None'}\n"
        f"Joined: {info['joined'][:10]}\n"
        f"Last seen: {info['last_seen'][:10]}\n"
        f"Blacklisted: {'Yes' if info['blacklisted'] else 'No'}\n"
        f"Total claims: {info['total_claims']}\n"
        f"Today's claims: {get_today_claims(user_id)}\n\n"
        f"Recent claims:\n"
    )

    if info['recent_claims']:
        for log in info['recent_claims'][:5]:
            msg += f"• `{log['file_key']}` at {log['time'][:16]}\n"
    else:
        msg += "  No claims yet."

    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['export'])
def export_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
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

    bot.send_document(
        message.chat.id,
        document=("distribution_logs.csv", csv_data.encode()),
        caption="📊 Full distribution log."
    )

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    if not message.reply_to_message:
        bot.reply_to(message, "Reply to the message you want to broadcast with /broadcast.")
        return

    target = message.reply_to_message
    users = get_all_users()

    if not users:
        bot.reply_to(message, "No users to broadcast to.")
        return

    bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")

    sent = 0
    for i, user in enumerate(users, 1):
        try:
            bot.copy_message(
                chat_id=user["id"],
                from_chat_id=target.chat.id,
                message_id=target.message_id
            )
            sent += 1
        except Exception as e:
            logger.error(f"Broadcast error to {user['id']}: {e}")

        if i % 25 == 0:
            time.sleep(1)

    bot.reply_to(message, f"✅ Broadcast completed. Sent to {sent} users.")

@bot.message_handler(commands=['ban'])
def ban_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /ban <user_id>")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid user_id.")
        return

    conn = get_db()
    conn.execute("UPDATE users SET blacklisted=1 WHERE id=?", (user_id,))
    if conn.total_changes:
        bot.reply_to(message, f"🚫 User {user_id} banned.")
    else:
        bot.reply_to(message, f"User {user_id} not found.")
    conn.commit()
    conn.close()

@bot.message_handler(commands=['unban'])
def unban_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /unban <user_id>")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid user_id.")
        return

    conn = get_db()
    conn.execute("UPDATE users SET blacklisted=0 WHERE id=?", (user_id,))
    if conn.total_changes:
        bot.reply_to(message, f"✅ User {user_id} unbanned.")
    else:
        bot.reply_to(message, f"User {user_id} not found.")
    conn.commit()
    conn.close()

@bot.message_handler(commands=['setdaily'])
def setdaily_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /setdaily <number>")
        return

    try:
        limit = int(args[1])
    except ValueError:
        bot.reply_to(message, "Invalid number.")
        return

    set_config("daily_limit", str(limit))
    bot.reply_to(message, f"✅ Daily claim limit set to {limit}.")

@bot.message_handler(commands=['setwelcome'])
def setwelcome_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(
            message,
            f"❌ You are not admin.\nYour ID: {message.from_user.id}\nExpected ADMIN_ID: {ADMIN_ID}"
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /setwelcome <new welcome message>")
        return

    set_config("welcome_message", args[1])
    bot.reply_to(message, "✅ Welcome message updated.")

@bot.message_handler(commands=['purge'])
def purge_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, f"❌ You are not admin.")
        return

    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("⚠️ YES, DELETE ALL", callback_data="purge:confirm"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="purge:cancel")
    )
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
        conn.commit()
        conn.close()
        bot.edit_message_text("✅ All files and logs have been deleted.", call.message.chat.id, call.message.message_id)

    elif action == "cancel":
        bot.answer_callback_query(call.id, "Cancelled.")
        bot.edit_message_text("❌ Purge cancelled.", call.message.chat.id, call.message.message_id)

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()

    cleanup_thread = Thread(target=auto_cleanup_loop, daemon=True)
    cleanup_thread.start()
    logger.info("✅ Auto-cleanup thread started.")

    logger.info(f"✅ Bot username (from API): {bot.get_me().username}")

    logger.info("✅ Bot started! Polling...")
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
