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
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init_db():
    conn, cur = get_db()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT,
        peer_id BIGINT,
        role INT DEFAULT 0,
        msgs INT DEFAULT 0,
        nickname TEXT,
        warn_count INT DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        peer_id BIGINT,
        type TEXT,
        end_at TIMESTAMP,
        reason TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS roles_titles (
        peer_id BIGINT,
        role_lvl INT,
        title TEXT,
        PRIMARY KEY (peer_id, role_lvl)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT,
        cmd_name TEXT,
        min_lvl INT,
        PRIMARY KEY (peer_id, cmd_name)
    );
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx
    ON users (user_id, peer_id);
    """)

    conn.close()

init_db()

# =========================
# UTILS
# =========================
def parse_time(t):
    if not t: return None
    m = re.match(r"(\d+)([mhd])", t.lower())
    if not m: return None
    v, u = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=v), "h": timedelta(hours=v), "d": timedelta(days=v)}[u]

async def extract_id(msg: Message):
    if msg.reply_message:
        return msg.reply_message.from_id

    r = re.search(r"id(\d+)|\[id(\d+)\|", msg.text)
    if r:
        return int(r.group(1) or r.group(2))

    for p in msg.text.split():
        if p.isdigit():
            return int(p)

    return None

async def get_user(uid, pid):
    conn, cur = get_db()
    cur.execute("SELECT role, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    r = cur.fetchone()
    conn.close()
    return r if r else (0, 0)

async def get_role_title(pid, lvl):
    if lvl >= 100:
        return "Владелец"
    conn, cur = get_db()
    cur.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    r = cur.fetchone()
    conn.close()
    if r:
        return r[0]
    return {80:"Админ",60:"Модер",20:"Помощник",0:"Пользователь"}.get(lvl, f"Роль {lvl}")

async def get_min_role(pid, cmd):
    conn, cur = get_db()
    cur.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    r = cur.fetchone()
    conn.close()
    if r:
        return r[0]
    return {
        "warn":20,"unwarn":20,
        "mute":20,"unmute":20,
        "kick":60,
        "ban":80,"unban":80,
        "setrole":100,"sysrole":100,
        "newrole":100,"delrole":100,
        "setcmd":100
    }.get(cmd,0)

# =========================
# MAIN
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn = None
    try:
        if not msg.text or msg.from_id <= 0:
            return

        uid, pid = msg.from_id, msg.peer_id
        text = msg.text.strip()

        conn, cur = get_db()

        # 🔴 АВТО-КИК ЗА БАН
        cur.execute("SELECT reason FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (uid, pid))
        row = cur.fetchone()
        if row:
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            except:
                pass
            return

        # 🔇 МУТ (удаление сообщений)
        cur.execute("SELECT end_at FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
        row = cur.fetchone()
        if row:
            if row[0] and row[0] <= datetime.now():
                cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
            else:
                try:
                    await bot.api.messages.delete(message_ids=[msg.id], delete_for_all=True)
                except:
                    pass
                return

        # активность
        cur.execute("""
        INSERT INTO users (user_id, peer_id, msgs)
        VALUES (%s,%s,1)
        ON CONFLICT (user_id,peer_id)
        DO UPDATE SET msgs=users.msgs+1
        """,(uid,pid))

        if not (text.startswith("/") or text.startswith("!")):
            return

        parts = text[1:].split()
        cmd = parts[0].lower()
        args = parts[1:]

        role, warns = await get_user(uid, pid)

        need = await get_min_role(pid, cmd)
        if uid != OWNER_ID and role < need:
            return await msg.answer(f"🚫 Нужно {need}+")

        # =========================
        # SYSROLE (ТОЛЬКО ТЫ)
        if cmd == "sysrole":
            if uid != OWNER_ID:
                return

            tid = await extract_id(msg)
            if not tid or not args:
                return await msg.answer(
                    "📖 Инструкция:\n/sysrole [id/ответ] [уровень]\n(только владелец)"
                )

            lvl = int(args[-1])

            cur.execute("""
            INSERT INTO users (user_id,peer_id,role)
            VALUES (%s,%s,%s)
            ON CONFLICT (user_id,peer_id)
            DO UPDATE SET role=%s
            """,(tid,pid,lvl,lvl))

            return await msg.answer(f"⚡ SYS: выдан {lvl}")

        # =========================
        if cmd == "stats":
            tid = await extract_id(msg) or uid

            cur.execute("""
            SELECT role,msgs,nickname,warn_count
            FROM users WHERE user_id=%s AND peer_id=%s
            """,(tid,pid))

            row = cur.fetchone()
            if not row:
                return await msg.answer("нет данных")

            title = await get_role_title(pid, row[0])

            return await msg.answer(
                f"📊 Профиль\n"
                f"⭐ Роль: {title} ({row[0]})\n"
                f"💬 Сообщения: {row[1]}\n"
                f"⚠ Варны: {row[3]}/3"
            )

        # =========================
        if cmd == "warn":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /warn [id/ответ]")

            cur.execute("""
            INSERT INTO users (user_id,peer_id,warn_count)
            VALUES (%s,%s,1)
            ON CONFLICT (user_id,peer_id)
            DO UPDATE SET warn_count=users.warn_count+1
            RETURNING warn_count
            """,(tid,pid))

            w = cur.fetchone()[0]

            if w >= 3:
                cur.execute("UPDATE users SET warn_count=0 WHERE user_id=%s AND peer_id=%s",(tid,pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=tid)
                return await msg.answer(f"⛔ Бан (3/3)\nАдмин: [id{uid}|ты]")

            return await msg.answer(f"⚠ Варн {w}/3")

        if cmd == "unwarn":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /unwarn [id/ответ]")

            cur.execute("""
            UPDATE users SET warn_count=GREATEST(warn_count-1,0)
            WHERE user_id=%s AND peer_id=%s RETURNING warn_count
            """,(tid,pid))

            return await msg.answer(f"✅ {cur.fetchone()[0]}/3")

        # =========================
        if cmd == "mute":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /mute [id] [время]")

            dur = parse_time(args[0]) if args else timedelta(minutes=30)
            if not dur:
                dur = timedelta(minutes=30)

            end = datetime.now() + dur

            cur.execute("""
            INSERT INTO punishments (user_id,peer_id,type,end_at,reason)
            VALUES (%s,%s,'mute',%s,%s)
            """,(tid,pid,end," ".join(args[1:]) if len(args)>1 else None))

            return await msg.answer(
                f"🔇 Мут\n👤 [id{tid}|пользователь]\n⏳ до {end.strftime('%H:%M')}"
            )

        if cmd == "unmute":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /unmute [id/ответ]")

            cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'",(tid,pid))
            return await msg.answer("🔊 размут")

        # =========================
        if cmd == "ban":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /ban [id/ответ]")

            reason = " ".join(args) if args else "не указана"

            cur.execute("INSERT INTO punishments (user_id,peer_id,type,reason) VALUES (%s,%s,'ban',%s)",(tid,pid,reason))

            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=tid)

            return await msg.answer(
                f"🚫 БАН\n👤 [id{tid}|юзер]\n👮 Админ: [id{uid}|ты]\n📄 Причина: {reason}"
            )

        if cmd == "unban":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /unban [id/ответ]")

            cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'",(tid,pid))
            return await msg.answer("♻ разбан")

        # =========================
        if cmd == "kick":
            tid = await extract_id(msg)
            if not tid:
                return await msg.answer("📖 /kick [id/ответ]")

            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=tid)
            return await msg.answer("👢 кик")

    except:
        print(traceback.format_exc())
    finally:
        if conn:
            conn.close()

# =========================
async def maintenance():
    while True:
        try:
            conn, cur = get_db()
            cur.execute("DELETE FROM punishments WHERE end_at <= %s",(datetime.now(),))
            conn.close()
        except:
            pass
        await asyncio.sleep(60)

if __name__ == "__main__":
    bot.loop_wrapper.add_task(maintenance())
    print("БОТ ЗАПУЩЕН")
    bot.run_forever()
