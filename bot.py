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
# DATABASE
# =========================
def db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init():
    conn, cur = db()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT,
        peer_id BIGINT,
        role INT DEFAULT 0,
        msgs INT DEFAULT 0,
        nickname TEXT,
        warn_count INT DEFAULT 0,
        warn_reasons TEXT DEFAULT ''
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS punishments(
        user_id BIGINT,
        peer_id BIGINT,
        type TEXT,
        end_at TIMESTAMP,
        reason TEXT
    );
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS up ON users(user_id,peer_id);
    """)

    conn.close()

init()

# =========================
# UTILS
# =========================
def parse_time(t):
    if not t: return None
    m = re.match(r"(\d+)([mhd])", t.lower())
    if not m: return None
    v, u = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=v), "h": timedelta(hours=v), "d": timedelta(days=v)}[u]

def extract(msg: Message):
    if msg.reply_message:
        return msg.reply_message.from_id
    r = re.search(r"id(\d+)|\[id(\d+)\|", msg.text)
    return int(r.group(1) or r.group(2)) if r else None

# =========================
# HELP
# =========================
HELP = """
💠 FLEX BOT

👤 ПОЛЬЗОВАТЕЛЬ
• /stats — профиль
• /snick [ник]
• /rnick

⚠ ВАРНЫ
• /warn [id] [причина]
• /unwarn [id]
• 3 варна = бан

🔇 МУТ
• /mute [время] [причина]
• /unmute [id]

🚫 БАН
• /ban [дни] [причина]
• /unban [id]

👢 КИК
• /kick [id]

👑 АДМИН
• /setrole [id] [lvl]
• /sysrole [id] [lvl] (только владелец бота)

⏱ ВРЕМЯ:
m / h / d
"""

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
                INSERT INTO users (user_id, peer_id, role, msgs, nickname, warn_count, warn_reasons)
                VALUES (%s,%s,100,0,NULL,0,'')
                ON CONFLICT (user_id,peer_id)
                DO UPDATE SET role=100
                """, (m.member_id, msg.peer_id))

                return await msg.answer("👑 Владелец получил роль 100")

        await msg.answer("❌ Владелец не найден")

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
        text = msg.text.lower()

        # ===== BAN CHECK
        cur.execute("SELECT 1 FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (uid, pid))
        if cur.fetchone():
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid - 2000000000, user_id=uid)
            except:
                pass
            return

        # ===== MUTE CHECK
        cur.execute("SELECT end_at FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
        r = cur.fetchone()
        if r and r[0] and r[0] > datetime.now():
            try:
                await bot.api.messages.delete(message_ids=[msg.id], delete_for_all=True)
            except:
                pass
            return

        if not text.startswith("/"):
            return

        parts = text[1:].split()
        cmd, args = parts[0], parts[1:]

        # ===== HELP
        if cmd == "help":
            return await msg.answer(HELP)

        # ===== STATS
        if cmd == "stats":
            t = extract(msg) or uid

            cur.execute("""
            SELECT role,msgs,nickname,warn_count,warn_reasons
            FROM users WHERE user_id=%s AND peer_id=%s
            """, (t, pid))

            r = cur.fetchone()
            if not r:
                return await msg.answer("нет данных")

            return await msg.answer(
                f"📊 Профиль\n"
                f"⭐ Роль: {r[0]}\n"
                f"💬 Сообщений: {r[1]}\n"
                f"⚠ Варны: {r[3]}/3\n"
                f"📄 Причины:\n{r[4] or 'нет'}"
            )

        # ===== SNICK
        if cmd == "snick":
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname=%s WHERE user_id=%s AND peer_id=%s", (nick, uid, pid))
            return await msg.answer(f"ник: {nick}")

        if cmd == "rnick":
            t = extract(msg) or uid
            cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (t, pid))
            return await msg.answer("ник удалён")

        # ===== WARN
        if cmd == "warn":
            t = extract(msg)
            reason = " ".join(args) or "не указана"

            cur.execute("""
            INSERT INTO users (user_id,peer_id,role,msgs,nickname,warn_count,warn_reasons)
            VALUES (%s,%s,0,0,NULL,1,%s)
            ON CONFLICT (user_id,peer_id)
            DO UPDATE SET warn_count=users.warn_count+1,
            warn_reasons=users.warn_reasons||'\n'||%s
            RETURNING warn_count
            """, (t, pid, reason, reason))

            w = cur.fetchone()[0]

            if w >= 3:
                await bot.api.messages.remove_chat_user(chat_id=pid - 2000000000, user_id=t)
                return await msg.answer("⛔ бан (3 варна)")

            return await msg.answer(f"⚠ варн {w}/3")

        if cmd == "unwarn":
            t = extract(msg)
            cur.execute("""
            UPDATE users SET warn_count=GREATEST(warn_count-1,0)
            WHERE user_id=%s AND peer_id=%s
            RETURNING warn_count
            """, (t, pid))
            return await msg.answer(f"{cur.fetchone()[0]}/3")

        # ===== MUTE
        if cmd == "mute":
            t = extract(msg)
            dur = parse_time(args[0]) if args else timedelta(minutes=30)
            reason = " ".join(args[1:]) if len(args) > 1 else "не указана"

            cur.execute("""
            INSERT INTO punishments (user_id,peer_id,type,end_at,reason)
            VALUES (%s,%s,'mute',%s,%s)
            """, (t, pid, datetime.now() + dur, reason))

            return await msg.answer("мут выдан")

        if cmd == "unmute":
            t = extract(msg)
            cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (t, pid))
            return await msg.answer("мут снят")

        # ===== BAN
        if cmd == "ban":
            t = extract(msg)

            days = None
            reason = "не указана"

            if args:
                if args[0].isdigit():
                    days = int(args[0])
                    reason = " ".join(args[1:]) or reason
                else:
                    reason = " ".join(args)

            end = None if not days else datetime.now() + timedelta(days=days)

            cur.execute("""
            INSERT INTO punishments (user_id,peer_id,type,end_at,reason)
            VALUES (%s,%s,'ban',%s,%s)
            """, (t, pid, end, reason))

            await bot.api.messages.remove_chat_user(chat_id=pid - 2000000000, user_id=t)

            return await msg.answer(f"бан: {reason}")

        if cmd == "unban":
            t = extract(msg)
            cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (t, pid))
            return await msg.answer("разбан")

        # ===== KICK
        if cmd == "kick":
            t = extract(msg)
            await bot.api.messages.remove_chat_user(chat_id=pid - 2000000000, user_id=t)
            return await msg.answer("кик")

        # ===== SYSROLE
        if cmd == "sysrole" and uid == OWNER_ID:
            t = extract(msg)
            lvl = int(args[-1])

            cur.execute("""
            INSERT INTO users (user_id,peer_id,role,msgs,nickname,warn_count,warn_reasons)
            VALUES (%s,%s,%s,0,NULL,0,'')
            ON CONFLICT (user_id,peer_id)
            DO UPDATE SET role=%s
            """, (t, pid, lvl, lvl))

            return await msg.answer("роль выдана")

    except:
        print(traceback.format_exc())
    finally:
        conn.close()

# =========================
async def cleaner():
    while True:
        try:
            conn, cur = db()
            cur.execute("DELETE FROM punishments WHERE end_at<=%s", (datetime.now(),))
            conn.close()
        except:
            pass
        await asyncio.sleep(60)

if __name__ == "__main__":
    bot.loop_wrapper.add_task(cleaner())
    print("BOT STARTED")
    bot.run_forever()
