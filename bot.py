from vkbottle.bot import Bot, Message
import psycopg2
import os
import time

# =========================
# CONFIG
# =========================
bot = Bot("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")

OWNER_ID = 676081199

DATABASE_URL = os.getenv("DATABASE_URL")

# =========================
# SAFE DB CONNECT
# =========================
conn = None
cur = None

if DATABASE_URL:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

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
# MEMORY
# =========================
bot_started = {}

# =========================
# HELPERS
# =========================
def now():
    return int(time.time())

def chat_id(peer_id):
    return peer_id - 2000000000

def is_chat(msg):
    return msg.peer_id > 2000000000

def check_started(msg):
    return bot_started.get(msg.peer_id, False)

def target(msg):
    return msg.reply_message.from_id if msg.reply_message else None

def get_role(uid, chat):
    if uid == OWNER_ID:
        return 100

    if not cur:
        return 0

    cur.execute("""
        SELECT role FROM users
        WHERE user_id=%s AND chat_id=%s
    """, (uid, chat))

    r = cur.fetchone()
    return r[0] if r else 0

def can(msg, need):
    return get_role(msg.from_id, chat_id(msg.peer_id)) >= need

# =========================
# GET CHAT OWNER
# =========================
async def get_chat_owner(peer_id):
    data = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
    return data.items[0].chat_settings.owner_id

# =========================
# START
# =========================
@bot.on.message(text="/start")
async def start(msg: Message):
    if not DATABASE_URL:
        return await msg.answer("❌ DATABASE_URL не подключен")

    try:
        await get_chat_owner(msg.peer_id)
    except:
        return await msg.answer("❌ Нет доступа / бот не админ")

    bot_started[msg.peer_id] = True
    await msg.answer("✅ Бот активирован")

# =========================
# TRACK USERS + AUTO OWNER ROLE
# =========================
@bot.on.message()
async def tracker(msg: Message):
    if not is_chat(msg):
        return

    if not check_started(msg):
        return

    if not cur:
        return

    chat = chat_id(msg.peer_id)

    # insert user
    cur.execute("""
        INSERT INTO users (user_id, chat_id, joined)
        VALUES (%s,%s,%s)
        ON CONFLICT DO NOTHING
    """, (msg.from_id, chat, now()))

    cur.execute("""
        UPDATE users SET messages = messages + 1
        WHERE user_id=%s AND chat_id=%s
    """, (msg.from_id, chat))

    conn.commit()

    # AUTO OWNER ROLE (100)
    try:
        owner_id = await get_chat_owner(msg.peer_id)

        if msg.from_id == owner_id:
            cur.execute("""
                INSERT INTO users (user_id, chat_id, role, joined)
                VALUES (%s,%s,100,%s)
                ON CONFLICT (user_id, chat_id)
                DO UPDATE SET role=100
            """, (msg.from_id, chat, now()))

            conn.commit()

    except:
        pass

# =========================
# BAN
# =========================
@bot.on.message(text="/ban <reason>")
async def ban(msg: Message, reason: str):
    if not check_started(msg):
        return await msg.answer("❌ /start не выполнен")

    if not cur:
        return await msg.answer("❌ DB не подключена")

    if not can(msg, 40):
        return await msg.answer("❌ Нет прав")

    uid = target(msg)
    if not uid:
        return await msg.answer("❌ reply нужен")

    chat = chat_id(msg.peer_id)

    cur.execute("""
        INSERT INTO punishments (user_id, chat_id, type, reason, until, admin, created)
        VALUES (%s,%s,'ban',%s,NULL,%s,%s)
    """, (uid, chat, reason, msg.from_id, now()))

    conn.commit()

    await bot.api.messages.remove_chat_user(chat_id=chat, member_id=uid)

    await msg.answer(f"🚫 BAN: {uid}")

# =========================
# MUTE
# =========================
@bot.on.message(text="/mute <days> <reason>")
async def mute(msg: Message, days: int, reason: str):
    if not check_started(msg):
        return await msg.answer("❌ /start не выполнен")

    if not cur:
        return await msg.answer("❌ DB не подключена")

    if not can(msg, 30):
        return await msg.answer("❌ Нет прав")

    uid = target(msg)
    if not uid:
        return await msg.answer("❌ reply нужен")

    chat = chat_id(msg.peer_id)
    until = now() + days * 86400

    cur.execute("""
        INSERT INTO punishments (user_id, chat_id, type, reason, until, admin, created)
        VALUES (%s,%s,'mute',%s,%s,%s,%s)
    """, (uid, chat, reason, until, msg.from_id, now()))

    conn.commit()

    await msg.answer(f"🔇 MUTE: {uid} ({days} days)")

# =========================
# ROLE CHECK
# =========================
@bot.on.message(text="/stats")
async def stats(msg: Message):
    if not check_started(msg):
        return await msg.answer("❌ /start не выполнен")

    if not cur:
        return await msg.answer("❌ DB не подключена")

    chat = chat_id(msg.peer_id)

    cur.execute("""
        SELECT messages, role, joined FROM users
        WHERE user_id=%s AND chat_id=%s
    """, (msg.from_id, chat))

    r = cur.fetchone()

    if not r:
        return await msg.answer("Нет данных")

    await msg.answer(
        f"📊 STATS\n"
        f"Messages: {r[0]}\n"
        f"Role: {r[1]}\n"
        f"Joined: {time.strftime('%Y-%m-%d', time.localtime(r[2]))}"
    )

# =========================
# MUTE CHECK (simple)
# =========================
@bot.on.message()
async def mute_guard(msg: Message):
    if not is_chat(msg):
        return

    if not check_started(msg):
        return

    if not cur:
        return

    chat = chat_id(msg.peer_id)

    cur.execute("""
        SELECT until FROM punishments
        WHERE user_id=%s AND chat_id=%s AND type='mute'
        ORDER BY id DESC LIMIT 1
    """, (msg.from_id, chat))

    m = cur.fetchone()

    if m and m[0] and now() < m[0]:
        return

bot.run_forever()
