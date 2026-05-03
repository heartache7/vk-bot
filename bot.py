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
# DATABASE SETUP
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS punishments (id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT, end_at TIMESTAMP);")
    cursor.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
    cursor.execute("CREATE TABLE IF NOT EXISTS cmd_permissions (peer_id BIGINT, cmd_name TEXT, min_lvl INT, PRIMARY KEY (peer_id, cmd_name));")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_rules (peer_id BIGINT PRIMARY KEY, rules_text TEXT);")
    
    # Исправление структуры, если колонки пропали
    cursor.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS end_at TIMESTAMP;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
    conn.commit()
    print(">>> [DATABASE] Структура проверена.")

init_db()

async def maintenance_task():
    while True:
        try:
            now = datetime.now()
            cursor.execute("DELETE FROM punishments WHERE end_at <= %s", (now,))
            cursor.execute("DELETE FROM users WHERE last_seen < %s AND role = 0", (now - timedelta(days=30),))
            conn.commit()
        except:
            conn.rollback()
        await asyncio.sleep(60)

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    return res if res else (0, 0, None, 0)

def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец чата"
    cursor.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cursor.fetchone()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 20: "Модератор", 0: "Участник"}.get(lvl, f"Ранг {lvl}")

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    text = message.text
    res = re.search(r"id(\d+)|\[id(\d+)\|", text)
    if res: return int(res.group(1) or res.group(2))
    # Поиск по домену (ссылке)
    domain = re.search(r"vk\.com/([\w\.]+)", text)
    if domain:
        try:
            resolved = await bot.api.utils.resolve_screen_name(screen_name=domain.group(1))
            if resolved.type.value == "user": return resolved.object_id
        except: pass
    return None

# =========================
# HANDLERS
# =========================

@bot.on.message()
async def handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    # 1. Проверка мута
    cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
    if cursor.fetchone():
        try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
        except: pass
        return

    # 2. Обновление активности
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs, last_seen) VALUES (%s, %s, 1, CURRENT_TIMESTAMP) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1, last_seen = CURRENT_TIMESTAMP", (uid, pid))
    conn.commit()

    # 3. Парсинг команды
    pref = ("/", "!")
    if not message.text.startswith(pref): return
    
    parts = message.text.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]
    
    print(f">>> [COMMAND] {cmd} from {uid}") # Для логов Railway

    u_role, _, _, _ = get_user_data(uid, pid)

    # --- КОМАНДЫ ---
    
    if cmd == "sysrole" and uid == OWNER_ID:
        target = await extract_id(message)
        if target and args:
            lvl = int(args[-1])
            cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
            conn.commit()
            await message.answer(f"⚡ Системно: [id{target}|пользователю] выдан ранг {lvl}.")

    elif cmd == "stats":
        target = await extract_id(message) or uid
        r, m, n, w = get_user_data(target, pid)
        await message.answer(f"📊 Профиль [id{target}]:\n🎭 Ник: {n or 'Нет'}\n✉ Сообщ: {m}\n⚠ Варны: {w}/3\n⭐ Роль: {get_role_title(pid, r)}")

    elif cmd == "warn":
        if u_role < 20 and uid != OWNER_ID: return
        target = await extract_id(message)
        if not target: return await message.answer("📌 Кого варним?")
        cursor.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
        w = cursor.fetchone()[0]
        if w >= 3:
            cursor.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
            cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'ban', %s)", (target, pid, datetime.now()+timedelta(days=1)))
            conn.commit()
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            except: pass
            await message.answer(f"⛔ [id{target}|Бан] за 3/3 варна.")
        else:
            conn.commit()
            await message.answer(f"⚠ [id{target}|Варн] ({w}/3).")

    elif cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        data = cursor.fetchall()
        txt = "👮 Администрация:\n" + "\n".join([f"• [id{s[0]}|{get_role_title(pid, s[1])}]" for s in data])
        await message.answer(txt if data else "Админов нет.")

    elif cmd == "rnick":
        if u_role < 40 and uid != OWNER_ID: return
        target = await extract_id(message) or uid
        cursor.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
        conn.commit()
        await message.answer(f"✅ Ник [id{target}|сброшен].")

    elif cmd == "start":
        # Назначение владельца чата
        mems = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in mems.items:
            if getattr(m, 'is_owner', False):
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")

# =========================
# RUN
# =========================
async def main():
    asyncio.create_task(maintenance_task())
    print(">>> БОТ ЗАПУЩЕН")
    await bot.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
