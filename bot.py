import os
import re
import asyncio
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import ChatActionRule

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199 
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DATABASE SETUP (С авто-исправлением)
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def init_db():
    # Создаем таблицы, если их нет
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT);
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
    cursor.execute("CREATE TABLE IF NOT EXISTS cmd_permissions (peer_id BIGINT, cmd_name TEXT, min_lvl INT, PRIMARY KEY (peer_id, cmd_name));")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_rules (peer_id BIGINT PRIMARY KEY, rules_text TEXT);")
    
    # ПРОВЕРКА И ДОБАВЛЕНИЕ КОЛОНОК (Fix для твоей ошибки)
    cursor.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS end_at TIMESTAMP;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
    
    conn.commit()
    print(">>> [DATABASE] Структура базы проверена и исправлена.")

init_db()

# =========================
# MAINTENANCE TASK
# =========================
async def maintenance_task():
    while True:
        try:
            now = datetime.now()
            # Чистим просроченные баны/муты
            cursor.execute("DELETE FROM punishments WHERE end_at <= %s", (now,))
            # Самоочистка неактивных
            cursor.execute("DELETE FROM users WHERE last_seen < %s AND role = 0", (now - timedelta(days=30),))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f">>> [MAINTENANCE ERROR] {e}")
        await asyncio.sleep(60)

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    return res if res else (0, 0, None, 0)

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res: return res[0]
    defaults = {"kick": 20, "ban": 60, "mute": 20, "warn": 20, "unban": 60, "setrole": 100, "newrole": 100, "rnick": 40, "staff": 0, "stats": 0}
    return defaults.get(cmd, 0)

def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец чата"
    cursor.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cursor.fetchone()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 20: "Модератор", 0: "Пользователь"}.get(lvl, f"Ранг {lvl}")

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"\[id(\d+)\|.*?\]|id(\d+)|vk\.com/([\w\.]+)", message.text)
    if not target: return None
    if target.group(1): return int(target.group(1))
    if target.group(2): return int(target.group(2))
    try:
        res = await bot.api.utils.resolve_screen_name(screen_name=target.group(3))
        if res.type.value == "user": return res.object_id
    except: pass
    return None

# =========================
# HANDLERS
# =========================

@bot.on.message()
async def main_handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    # 1. МУТ
    cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
    if cursor.fetchone():
        try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
        except: pass
        return

    # 2. АКТИВНОСТЬ
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs, last_seen) VALUES (%s, %s, 1, CURRENT_TIMESTAMP) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1, last_seen = CURRENT_TIMESTAMP", (uid, pid))
    conn.commit()

    text = message.text.replace("!", "/")
    if not text.startswith("/"): return
    parts = text.split()
    cmd, args = parts[0][1:].lower(), parts[1:]

    u_role, _, _, _ = get_user_data(uid, pid)

    # --- SYSROLE ---
    if cmd == "sysrole" and uid == OWNER_ID:
        target = await extract_id(message)
        if target and args:
            lvl = int(args[-1])
            cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
            conn.commit()
            return await message.answer(f"⚡ Ранг {lvl} выдан [id{target}|юзеру].")

    min_req = await get_min_role(pid, cmd)
    if u_role < min_req and uid != OWNER_ID: return

    # --- КОМАНДЫ ---
    if cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        staff = cursor.fetchall()
        res = "👮 Админы:\n" + "\n".join([f"• [id{s[0]}|{get_role_title(pid, s[1])}]" for s in staff])
        await message.answer(res if staff else "Админов нет.")

    elif cmd == "rnick":
        target = await extract_id(message) or uid
        cursor.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
        conn.commit()
        await message.answer(f"✅ Ник [id{target}|сброшен].")

    elif cmd == "warn":
        target = await extract_id(message)
        if not target: return
        cursor.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
        w = cursor.fetchone()[0]
        if w >= 3:
            cursor.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
            cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'ban', %s)", (target, pid, datetime.now()+timedelta(days=1)))
            conn.commit()
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            except: pass
            await message.answer(f"⛔ [id{target}|Бан] (3/3 варна).")
        else:
            conn.commit()
            await message.answer(f"⚠ [id{target}|Варн] ({w}/3).")

    elif cmd == "stats":
        target = await extract_id(message) or uid
        r, m, n, w = get_user_data(target, pid)
        await message.answer(f"📊 [id{target}]:\n✉ Сообщ: {m}\n⚠ Варны: {w}/3\n⭐ Роль: {get_role_title(pid, r)}")

# =========================
# START
# =========================
async def main():
    asyncio.create_task(maintenance_task())
    print(">>> БОТ ЗАПУСКАЕТСЯ")
    await bot.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
