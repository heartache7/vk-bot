import os
import re
import asyncio
import traceback
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message

OWNER_ID = 676081199
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DB
# =========================
def db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init():
    conn, cur = db()
    try:
        # ===== CREATE TABLE =====
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT,
            peer_id BIGINT
        );
        """)

        # ===== AUTO FIX STRUCTURE =====
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS msgs INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_reasons TEXT DEFAULT '';")

        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS users_unique
        ON users(user_id, peer_id);
        """)

        # ===== PUNISHMENTS =====
        cur.execute("""
        CREATE TABLE IF NOT EXISTS punishments(
            user_id BIGINT,
            peer_id BIGINT,
            type TEXT
        );
        """)

        cur.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS end_at TIMESTAMP;")
        cur.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS reason TEXT;")

        print(">>> DB OK")

    except Exception as e:
        print("DB ERROR:", e)
    finally:
        conn.close()

init()

# =========================
# UTILS
# =========================
def parse_time(t):
    m = re.match(r"(\d+)([mhd])", t.lower()) if t else None
    if not m: return None
    v, u = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=v), "h": timedelta(hours=v), "d": timedelta(days=v)}[u]

def extract(msg: Message):
    if msg.reply_message:
        return msg.reply_message.from_id
    r = re.search(r"id(\d+)|\[id(\d+)\|", msg.text)
    return int(r.group(1) or r.group(2)) if r else None

# =========================
# START
# =========================
@bot.on.message(text="/start")
async def start(msg: Message):
    conn, cur = db()
    try:
        try:
            res = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
        except:
            return await msg.answer("⚠️ Дай мне права администратора ⭐")

        for m in res.items:
            if getattr(m, "is_owner", False):
                cur.execute("""
                INSERT INTO users (user_id,peer_id,role)
                VALUES (%s,%s,100)
                ON CONFLICT (user_id,peer_id)
                DO UPDATE SET role=100
                """, (m.member_id, msg.peer_id))

                return await msg.answer("👑 Владелец получил роль 100")

    finally:
        conn.close()

# =========================
# MAIN
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()

    try:
        if not msg.text:
            return

        uid, pid = msg.from_id, msg.peer_id
        text = msg.text.strip()

        # ===== AUTO KICK BAN =====
        cur.execute("""
        SELECT type FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
        """, (uid, pid))

        if cur.fetchone():
            try:
                await bot.api.messages.remove_chat_user(
                    chat_id=pid-2000000000,
                    user_id=uid
                )
            except:
                pass
            return

        # ===== AUTO DELETE MUTE =====
        cur.execute("""
        SELECT type FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        """, (uid, pid))

        if cur.fetchone():
            try:
                await bot.api.messages.delete(
                    message_ids=[msg.id],
                    delete_for_all=True
                )
            except:
                pass
            return

        # ===== UPDATE USER =====
        cur.execute("""
        INSERT INTO users (user_id,peer_id,msgs)
        VALUES (%s,%s,1)
        ON CONFLICT (user_id,peer_id)
        DO UPDATE SET msgs = users.msgs + 1
        """, (uid, pid))

        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0][1:].lower()
        args = parts[1:]

        # ===== HELP =====
        if cmd == "help":
            return await msg.answer(
                "💠 FLEX BOT\n\n"
                "🏷 /snick [ник]\n"
                "🧹 /rnick\n"
                "⚠ /warn\n"
                "🔇 /mute\n"
                "🚫 /ban\n"
                "👢 /kick\n"
            )

        # ===== SNICK =====
        if cmd == "snick":
            target = extract(msg) or uid

            if args and ("id" in args[0]):
                args = args[1:]

            if not args:
                return await msg.answer("📖 /snick [ник] или /snick [id] [ник]")

            nick = " ".join(args)

            cur.execute("""
            INSERT INTO users (user_id,peer_id,nickname)
            VALUES (%s,%s,%s)
            ON CONFLICT (user_id,peer_id)
            DO UPDATE SET nickname=%s
            """, (target, pid, nick, nick))

            return await msg.answer(f"🏷 Ник установлен: {nick}")

        # ===== RNICK =====
        if cmd == "rnick":
            target = extract(msg) or uid

            cur.execute("""
            UPDATE users SET nickname=NULL
            WHERE user_id=%s AND peer_id=%s
            """, (target, pid))

            return await msg.answer("🧹 Ник удалён")

    except:
        print(traceback.format_exc())
    finally:
        conn.close()

# =========================
if __name__ == "__main__":
    print(">>> BOT START")
    bot.run_forever()
