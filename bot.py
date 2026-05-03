import os
import psycopg2
from vkbottle.bot import Bot, Message

print("BOT STARTED (PRODUCTION MODE)")

# =========================
# DB (FROM RAILWAY VARIABLES)
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("DATABASE_URL is not set in Railway Variables")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199
bot = Bot(token=os.getenv("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw"))

# =========================
# TABLES
# =========================
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
def is_owner(uid):
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
        VALUES (%s, %s, 'ban', 'manual')
    """, (uid, message.peer_id))

    conn.commit()

    await bot.api.messages.remove_chat_user(
        chat_id=message.peer_id - 2000000000,
        user_id=uid
    )

    await message.answer("⛔ banned")

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
# WARN (SIMPLE)
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

    await message.answer("⚠ warned")

# =========================
# RUN
# =========================
bot.run_forever()        return

    cursor.execute("""
        INSERT INTO activity (user_id, peer_id, messages)
        VALUES (%s, %s, 1)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET messages = activity.messages + 1
    """, (message.from_id, message.peer_id))

    conn.commit()


# =========================
# GLOBAL CHECK
# =========================
@bot.on.message()
async def global_check(message: Message):

    if message.peer_id < 2000000000:
        return

    await clean()

    cursor.execute("""
        SELECT 1 FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
    """, (message.from_id, message.peer_id))

    if cursor.fetchone():
        await message.answer("⛔ banned")
        return


# =========================
# START / HELP
# =========================
@bot.on.message(text="/start")
async def start(message: Message):
    await message.answer("FLEX ACTIVE\n/help")


@bot.on.message(text="/help")
async def help(message: Message):
    await message.answer("""
/ban
/warn
/mute
/kick
/banlist
/stats
/setcmd
""")


# =========================
# BAN
# =========================
@bot.on.message(text="/ban")
async def ban(message: Message):

    if not message.reply_message:
        await message.answer("/ban (reply)")
        return

    if role(message.from_id, message.peer_id) < 40:
        await message.answer("⛔ no rights")
        return

    uid = message.reply_message.from_id
    args = message.text.split()

    days = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    reason = "ban"

    until = datetime.now() + timedelta(days=days) if days else None

    cursor.execute("""
        INSERT INTO punishments (user_id, peer_id, type, reason, until_time)
        VALUES (%s, %s, 'ban', %s, %s)
    """, (uid, message.peer_id, reason, until))

    conn.commit()

    await kick(message.peer_id, uid)
    await message.answer("⛔ banned")


# =========================
# WARN
# =========================
@bot.on.message(text="/warn")
async def warn(message: Message):

    uid = message.reply_message.from_id

    cursor.execute("""
        INSERT INTO punishments (user_id, peer_id, type, reason)
        VALUES (%s, %s, 'warn', 'warn')
    """, (uid, message.peer_id))

    conn.commit()

    cursor.execute("""
        SELECT COUNT(*) FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='warn'
    """, (uid, message.peer_id))

    if cursor.fetchone()[0] >= 3:
        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type, reason)
            VALUES (%s, %s, 'ban', '3 warns')
        """, (uid, message.peer_id))

        conn.commit()
        await kick(message.peer_id, uid)


# =========================
# MUTE
# =========================
@bot.on.message(text="/mute")
async def mute(message: Message):

    uid = message.reply_message.from_id

    cursor.execute("""
        INSERT INTO punishments (user_id, peer_id, type, reason)
        VALUES (%s, %s, 'mute', 'mute')
    """, (uid, message.peer_id))

    conn.commit()

    await message.answer("🔇 muted")


# =========================
# BANLIST
# =========================
@bot.on.message(text="/banlist")
async def banlist(message: Message):

    cursor.execute("""
        SELECT user_id, reason FROM punishments
        WHERE peer_id=%s AND type='ban'
    """, (message.peer_id,))

    rows = cursor.fetchall()

    text = "BANLIST:\n\n"

    for u, r in rows:
        text += f"{u} | {r}\n"

    await message.answer(text)


# =========================
# STATS
# =========================
@bot.on.message(text="/stats")
async def stats(message: Message):

    uid = message.reply_message.from_id if message.reply_message else message.from_id

    cursor.execute("SELECT messages FROM activity WHERE user_id=%s AND peer_id=%s",
                   (uid, message.peer_id))
    m = cursor.fetchone()
    m = m[0] if m else 0

    r = role(uid, message.peer_id)

    await message.answer(f"""
📊 STATS
ID: {uid}
Role: {r}
Messages: {m}
""")


# =========================
# SETCMD
# =========================
@bot.on.message(text="/setcmd")
async def setcmd(message: Message):

    if role(message.from_id, message.peer_id) < 70:
        return

    args = message.text.split()

    cmd = args[1].replace("/", "")
    lvl = int(args[2])

    cursor.execute("""
        INSERT INTO command_access (peer_id, command, min_role)
        VALUES (%s, %s, %s)
        ON CONFLICT DO UPDATE SET min_role=%s
    """, (message.peer_id, cmd, lvl, lvl))

    conn.commit()

    await message.answer(f"/{cmd} -> {lvl}")


# =========================
# AUTO KICK BAN
# =========================
@bot.on.chat_invite()
async def invite(message: Message):

    uid = message.from_id

    cursor.execute("""
        SELECT 1 FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
    """, (uid, message.peer_id))

    if cursor.fetchone():
        await kick(message.peer_id, uid)


# =========================
# RUN
# =========================
bot.run_forever()
