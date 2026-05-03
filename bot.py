import os
import re
import psycopg2
from vkbottle.bot import Bot, Message

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199

VK_TOKEN = os.getenv("vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DB
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

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
    type TEXT
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS command_access (
    peer_id BIGINT,
    command TEXT,
    min_role INT DEFAULT 0,
    PRIMARY KEY (peer_id, command)
);
""")

conn.commit()

# =========================
# HELPERS
# =========================
def parse_cmd(text):
    parts = text.split()
    return parts[0].lower(), parts[1:]

def extract_user(message: Message, args):
    if message.reply_message:
        return message.reply_message.from_id

    if args:
        if args[0].isdigit():
            return int(args[0])

        m = re.search(r"id(\d+)", args[0])
        if m:
            return int(m.group(1))

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

def can_use(uid, peer, cmd):
    if uid == OWNER_ID:
        return True

    cursor.execute("""
        SELECT min_role FROM command_access
        WHERE peer_id=%s AND command=%s
    """, (peer, cmd))

    r = cursor.fetchone()
    need = r[0] if r else 0

    return get_role(uid, peer) >= need

async def kick(peer_id, user_id):
    await bot.api.messages.remove_chat_user(
        chat_id=peer_id - 2000000000,
        user_id=user_id
    )

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(message: Message):

    if not message.text:
        return

    # activity counter
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
        await message.answer("BOT ONLINE")
        return

    # =========================
    # HELP
    # =========================
    if cmd == "/help":
        await message.answer("""
/ban (reply or id)
/warn (reply or id)
/mute (reply or id)
/kick (reply or id)
/stats
/banlist
/sysrole (owner only)
""")
        return

    # =========================
    # SYSROLE (OWNER ONLY)
    # =========================
    if cmd == "/sysrole":
        if message.from_id != OWNER_ID:
            return

        uid = extract_user(message, args)
        if not uid or not args:
            return

        try:
            role = int(args[-1])
        except:
            return

        set_role(uid, message.peer_id, role)
        await message.answer("role updated")
        return

    # =========================
    # BAN
    # =========================
    if cmd == "/ban":
        if not can_use(message.from_id, message.peer_id, "ban"):
            return

        uid = extract_user(message, args)
        if not uid:
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'ban')
        """, (uid, message.peer_id))
        conn.commit()

        await kick(message.peer_id, uid)
        return

    # =========================
    # WARN
    # =========================
    if cmd == "/warn":
        uid = extract_user(message, args)
        if not uid:
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'warn')
        """, (uid, message.peer_id))
        conn.commit()
        return

    # =========================
    # MUTE
    # =========================
    if cmd == "/mute":
        uid = extract_user(message, args)
        if not uid:
            return

        cursor.execute("""
            INSERT INTO punishments (user_id, peer_id, type)
            VALUES (%s, %s, 'mute')
        """, (uid, message.peer_id))
        conn.commit()
        return

    # =========================
    # KICK
    # =========================
    if cmd == "/kick":
        uid = extract_user(message, args)
        if not uid:
            return

        await kick(message.peer_id, uid)
        return

    # =========================
    # STATS
    # =========================
    if cmd == "/stats":
        uid = extract_user(message, args) or message.from_id

        cursor.execute("""
            SELECT messages FROM activity
            WHERE user_id=%s AND peer_id=%s
        """, (uid, message.peer_id))

        res = cursor.fetchone()
        msgs = res[0] if res else 0

        role = get_role(uid, message.peer_id)

        await message.answer(f"{uid} | role {role} | msgs {msgs}")
        return

    # =========================
    # BANLIST
    # =========================
    if cmd == "/banlist":
        cursor.execute("""
            SELECT user_id FROM punishments
            WHERE peer_id=%s AND type='ban'
        """, (message.peer_id,))

        rows = cursor.fetchall()

        text = "BANLIST:\n"
        for r in rows:
            text += f"{r[0]}\n"

        await message.answer(text)
        return

# =========================
# RUN
# =========================
bot.run_forever()
