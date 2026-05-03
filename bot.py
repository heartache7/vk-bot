import os
import re
import psycopg2
from datetime import timedelta
from vkbottle.bot import Bot, Message

print("FLEX BOT WITH PARSER STARTED")

OWNER_ID = 676081199

VK_TOKEN = os.getenv("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")
DATABASE_URL = os.getenv("DATABASE_URL")

if not VK_TOKEN:
    raise Exception("VK_TOKEN not set")

if not DATABASE_URL:
    raise Exception("DATABASE_URL not set")

bot = Bot(token=VK_TOKEN)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# =========================
# TABLES
# =========================
cursor.execute("""
CREATE TABLE IF NOT EXISTS roles (
    user_id BIGINT,
    peer_id BIGINT,
    role INT DEFAULT 0,
    PRIMARY KEY (user_id, peer_id)
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS punishments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    peer_id BIGINT,
    type TEXT,
    until_time TIMESTAMP
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity (
    user_id BIGINT,
    peer_id BIGINT,
    messages INT DEFAULT 0,
    PRIMARY KEY (user_id, peer_id)
);
""")

conn.commit()

# =========================
# UTILS
# =========================
def parse_cmd(text: str):
    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]
    return cmd, args

def extract_user_id(message: Message, args):
    # 1. reply
    if message.reply_message:
        return message.reply_message.from_id

    # 2. @id123
    if args:
        match = re.search(r"id(\d+)", args[0])
        if match:
            return int(match.group(1))

        # просто число
        if args[0].isdigit():
            return int(args[0])

    return None

def get_role(uid, peer):
    if uid == OWNER_ID:
        return 100

    cursor.execute(
        "SELECT role FROM roles WHERE user_id=%s AND peer_id=%s",
        (uid, peer)
    )
    r = cursor.fetchone()
    return r[0] if r else 0

def set_role(uid, peer, role):
    cursor.execute("""
        INSERT INTO roles (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
    """, (uid, peer, role, role))
    conn.commit()

async def kick(peer_id, uid):
    await bot.api.messages.remove_chat_user(
        chat_id=peer_id - 2000000000,
        user_id=uid
    )

# =========================
# GLOBAL HANDLER
# =========================
@bot.on.message()
async def handler(message: Message):

    if not message.text:
        return

    # счетчик сообщений
    if message.peer_id > 2000000000:
        cursor.execute("""
            INSERT INTO activity (user_id, peer_id, messages)
            VALUES (%s, %s, 1)
            ON CONFLICT (user_id, peer_id)
            DO UPDATE SET messages = activity.messages + 1
        """, (message.from_id, message.peer_id))
        conn.commit()

    cmd, args = parse_cmd(message.text)

    # =========================
    # START
    # =========================
    if cmd == "/start":
        await message.answer("BOT ONLINE\n/help")
        return

    # =========================
    # HELP
    # =========================
    if cmd == "/help":
        await message.answer("""
/ban (reply или id)
/warn (reply)
/mute (reply)
/kick (reply)
/stats
/sysrole (owner only)
""")
        return

    # =========================
    # SYSROLE (ТОЛЬКО OWNER)
    # =========================
    if cmd == "/sysrole":

        if message.from_id != OWNER_ID:
            return

        if not args:
            await message.answer("используй: /sysrole (reply) 100")
            return

        uid = extract_user_id(message, args)
        if not uid:
            await message.answer("укажи пользователя")
            return

        try:
            lvl = int(args[-1])
        except:
            await message.answer("уровень должен быть числом")
            return

        set_role(uid, message.peer_id, lvl)

        await message.answer(f"роль {lvl} выдана")
        return

    # =========================
    # BAN
    # =========================
    if cmd == "/ban":

        if get_role(message.from_id, message.peer_id) < 40:
            await message.answer("⛔ no rights")
            return

        uid = extract_user_id(message, args)
        if not uid:
            await message.answer("reply или /ban id")
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'ban')
        """, (uid, message.peer_id))
        conn.commit()

        await kick(message.peer_id, uid)
        await message.answer("⛔ banned")
        return

    # =========================
    # WARN
    # =========================
    if cmd == "/warn":

        uid = extract_user_id(message, args)
        if not uid:
            await message.answer("reply или /warn id")
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'warn')
        """, (uid, message.peer_id))
        conn.commit()

        await message.answer("⚠ warn")
        return

    # =========================
    # MUTE
    # =========================
    if cmd == "/mute":

        uid = extract_user_id(message, args)
        if not uid:
            await message.answer("reply или /mute id")
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'mute')
        """, (uid, message.peer_id))
        conn.commit()

        await message.answer("🔇 muted")
        return

    # =========================
    # STATS
    # =========================
    if cmd == "/stats":

        uid = extract_user_id(message, args) or message.from_id

        cursor.execute("""
            SELECT messages FROM activity
            WHERE user_id=%s AND peer_id=%s
        """, (uid, message.peer_id))

        res = cursor.fetchone()
        msgs = res[0] if res else 0

        role = get_role(uid, message.peer_id)

        await message.answer(f"""
📊 STATS
ID: {uid}
Role: {role}
Messages: {msgs}
""")
        return

# =========================
# RUN
# =========================
bot.run_forever()
