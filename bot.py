from vkbottle.bot import Bot, Message
import psycopg2
import time
import os

bot = Bot("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

OWNER_ID = 676081199

# =========================
# INIT DB
# =========================
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT,
    chat_id BIGINT,
    role INT DEFAULT 0,
    messages INT DEFAULT 0,
    joined INT,
    PRIMARY KEY (user_id, chat_id)
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS punishments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    chat_id BIGINT,
    type TEXT,
    reason TEXT,
    until BIGINT,
    admin BIGINT,
    created BIGINT
)
""")

conn.commit()

# =========================
# HELPERS
# =========================
def now():
    return int(time.time())

def chat_id(peer_id):
    return peer_id - 2000000000

def is_chat(msg):
    return msg.peer_id > 2000000000

def create_user(uid, chat):
    cur.execute("""
        INSERT INTO users (user_id, chat_id, joined)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
    """, (uid, chat, now()))
    conn.commit()

def get_role(uid, chat):
    if uid == OWNER_ID:
        return 100

    cur.execute("""
        SELECT role FROM users
        WHERE user_id=%s AND chat_id=%s
    """, (uid, chat))

    r = cur.fetchone()
    return r[0] if r else 0

def set_role(uid, chat, role):
    cur.execute("""
        INSERT INTO users (user_id, chat_id, role, joined)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, chat_id)
        DO UPDATE SET role=%s
    """, (uid, chat, role, now(), role))
    conn.commit()

def add_msg(uid, chat):
    create_user(uid, chat)
    cur.execute("""
        UPDATE users SET messages = messages + 1
        WHERE user_id=%s AND chat_id=%s
    """, (uid, chat))
    conn.commit()

def target(msg: Message):
    return msg.reply_message.from_id if msg.reply_message else None

# =========================
# MESSAGE TRACK
# =========================
@bot.on.message()
async def track(msg: Message):
    if not is_chat(msg):
        return

    add_msg(msg.from_id, chat_id(msg.peer_id))

# =========================
# ROLE CHECK
# =========================
def can(msg, need):
    return get_role(msg.from_id, chat_id(msg.peer_id)) >= need

# =========================
# BAN
# =========================
@bot.on.message(text="/ban <reason>")
async def ban(msg: Message, reason: str):
    if not can(msg, 40):
        return await msg.answer("❌ No access")

    uid = target(msg)
    if not uid:
        return await msg.answer("Reply required")

    cur.execute("""
        INSERT INTO punishments (user_id, chat_id, type, reason, until, admin, created)
        VALUES (%s,%s,'ban',%s,NULL,%s,%s)
    """, (uid, chat_id(msg.peer_id), reason, msg.from_id, now()))
    conn.commit()

    await bot.api.messages.remove_chat_user(
        chat_id=chat_id(msg.peer_id),
        member_id=uid
    )

    await msg.answer(f"🚫 Banned {uid}")

# =========================
# MUTE
# =========================
@bot.on.message(text="/mute <days> <reason>")
async def mute(msg: Message, days: int, reason: str):
    if not can(msg, 30):
        return await msg.answer("❌ No access")

    uid = target(msg)
    if not uid:
        return await msg.answer("Reply required")

    until = now() + days * 86400

    cur.execute("""
        INSERT INTO punishments (user_id, chat_id, type, reason, until, admin, created)
        VALUES (%s,%s,'mute',%s,%s,%s,%s)
    """, (uid, chat_id(msg.peer_id), reason, until, msg.from_id, now()))
    conn.commit()

    await msg.answer(f"🔇 Muted {uid} for {days} days")

# =========================
# SET ROLE
# =========================
@bot.on.message(text="/setrole <role>")
async def role(msg: Message, role: int):
    if not can(msg, 80):
        return await msg.answer("❌ No access")

    uid = target(msg)
    if not uid:
        return await msg.answer("Reply required")

    set_role(uid, chat_id(msg.peer_id), role)

    await msg.answer(f"👑 Role {uid} → {role}")

# =========================
# STATS
# =========================
@bot.on.message(text="/stats")
async def stats(msg: Message):
    uid = msg.from_id
    chat = chat_id(msg.peer_id)

    cur.execute("""
        SELECT messages, role, joined FROM users
        WHERE user_id=%s AND chat_id=%s
    """, (uid, chat))

    r = cur.fetchone()

    if not r:
        return await msg.answer("No data")

    await msg.answer(
        f"📊 STATS\n"
        f"ID: {uid}\n"
        f"Messages: {r[0]}\n"
        f"Role: {r[1]}\n"
        f"Joined: {time.strftime('%Y-%m-%d', time.localtime(r[2]))}"
    )

# =========================
# BANLIST
# =========================
@bot.on.message(text="/banlist")
async def banlist(msg: Message):
    cur.execute("""
        SELECT user_id, reason, created FROM punishments
        WHERE chat_id=%s AND type='ban'
    """, (chat_id(msg.peer_id),))

    rows = cur.fetchall()

    if not rows:
        return await msg.answer("Empty")

    text = "🚫 BANLIST:\n"
    for r in rows:
        text += f"{r[0]} | {r[1]} | {time.strftime('%Y-%m-%d', time.localtime(r[2]))}\n"

    await msg.answer(text)

# =========================
# MUTE CHECK
# =========================
@bot.on.message()
async def mute_guard(msg: Message):
    if not is_chat(msg):
        return

    uid = msg.from_id

    cur.execute("""
        SELECT until FROM punishments
        WHERE user_id=%s AND chat_id=%s AND type='mute'
        ORDER BY id DESC LIMIT 1
    """, (uid, chat_id(msg.peer_id)))

    m = cur.fetchone()

    if m and m[0] and now() < m[0]:
        return

bot.run_forever()
