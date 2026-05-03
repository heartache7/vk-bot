import os
import psycopg2
from vkbottle.bot import Bot, Message

print("BOT STARTED SUCCESSFULLY")

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199

VK_TOKEN = os.getenv("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")
if not VK_TOKEN:
    raise Exception("VK_TOKEN not set in Variables")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL not set in Variables")

bot = Bot(token=VK_TOKEN)

# =========================
# DB CONNECT
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity (
    user_id BIGINT,
    peer_id BIGINT,
    messages INT DEFAULT 0,
    PRIMARY KEY (user_id, peer_id)
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS punishments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    peer_id BIGINT,
    type TEXT,
    reason TEXT
);
""")

conn.commit()

# =========================
# OWNER CHECK
# =========================
def is_owner(uid: int):
    return uid == OWNER_ID

# =========================
# MESSAGE COUNTER
# =========================
@bot.on.message()
async def counter(message: Message):

    if message.peer_id < 2000000000:
        return

    cursor.execute("""
        INSERT INTO activity (user_id, peer_id, messages)
        VALUES (%s, %s, 1)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET messages = activity.messages + 1
    """, (message.from_id, message.peer_id))

    conn.commit()

# =========================
# START
# =========================
@bot.on.message(text="/start")
async def start(message: Message):
    await message.answer("BOT ONLINE ✅")

# =========================
# BAN
# =========================
@bot.on.message(text="/ban")
async def ban(message: Message):

    if not is_owner(message.from_id):
        await message.answer("⛔ no rights")
        return

    if not message.reply_message:
        await message.answer("reply → /ban")
        return

    uid = message.reply_message.from_id

    cursor.execute("""
        INSERT INTO punishments (user_id, peer_id, type, reason)
        VALUES (%s, %s, 'ban', 'manual ban')
    """, (uid, message.peer_id))

    conn.commit()

    await bot.api.messages.remove_chat_user(
        chat_id=message.peer_id - 2000000000,
        user_id=uid
    )

    await message.answer("⛔ banned")

# =========================
# WARN
# =========================
@bot.on.message(text="/warn")
async def warn(message: Message):

    if not is_owner(message.from_id):
        return

    if not message.reply_message:
        await message.answer("reply → /warn")
        return

    uid = message.reply_message.from_id

    cursor.execute("""
        INSERT INTO punishments (user_id, peer_id, type, reason)
        VALUES (%s, %s, 'warn', 'warn')
    """, (uid, message.peer_id))

    conn.commit()

    await message.answer("⚠ warn added")

# =========================
# STATS
# =========================
@bot.on.message(text="/stats")
async def stats(message: Message):

    uid = message.reply_message.from_id if message.reply_message else message.from_id

    cursor.execute("""
        SELECT messages FROM activity
        WHERE user_id=%s AND peer_id=%s
    """, (uid, message.peer_id))

    res = cursor.fetchone()
    msgs = res[0] if res else 0

    await message.answer(f"""
📊 STATS

ID: {uid}
Messages: {msgs}
""")

# =========================
# RUN (ВАЖНО — ЧИСТАЯ СТРОКА)
# =========================
bot.run_forever()
