import os
import re
import asyncio
import traceback
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199 
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DATABASE ENGINE
# =========================
conn = None
cursor = None

def get_db():
    """Подключение к БД с авто-переподключением."""
    global conn, cursor
    try:
        if cursor is None:
            raise Exception("No cursor")
        cursor.execute("SELECT 1")
    except:
        print(">>> [DB] Подключение к базе данных...")
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
    return conn, cursor

def init_db():
    """Создание таблиц и исправление структуры из логов."""
    c, cur = get_db()
    # Создание базовых таблиц
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    cur.execute("CREATE TABLE IF NOT EXISTS punishments (id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT, end_at TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
    cur.execute("CREATE TABLE IF NOT EXISTS cmd_permissions (peer_id BIGINT, cmd_name TEXT, min_lvl INT, PRIMARY KEY (peer_id, cmd_name));")
    
    # Исправление ошибок из логов (добавление недостающих колонок)
    [span_3](start_span)cur.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS end_at TIMESTAMP;") #[span_3](end_span)
    [span_4](start_span)cur.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS peer_id BIGINT;") #[span_4](end_span)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
    print(">>> [DATABASE] Структура проверена и исправлена.")

# Инициализируем БД при запуске
init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    _, cur = get_db()
    cur.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cur.fetchone()
    return res if res else (0, 0, None, 0)

def get_role_title(pid, lvl):
    _, cur = get_db()
    if lvl >= 100: return "Владелец чата"
    cur.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cur.fetchone()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 20: "Модератор", 0: "Пользователь"}.get(lvl, f"Ранг {lvl}")

async def get_min_role(pid, cmd):
    _, cur = get_db()
    cur.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cur.fetchone()
    if res: return res[0]
    defaults = {"kick": 20, "ban": 60, "mute": 20, "warn": 20, "unban": 60, "setrole": 100, "rnick": 40, "staff": 0, "stats": 0}
    return defaults.get(cmd, 0)

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    res = re.search(r"id(\d+)|\[id(\d+)\|", message.text)
    if res: return int(res.group(1) or res.group(2))
    return None

# =========================
# HANDLERS
# =========================

@bot.on.message()
async def handler(message: Message):
    try:
        if not message.text or message.from_id <= 0: return
        
        uid, pid, text = message.from_id, message.peer_id, message.text.strip()

        # Тестовая команда без БД
        if text.lower() in ["/ping", "!ping"]:
            return await message.answer("🏓 Понг! Бот активен, ошибки цикла asyncio устранены.")

        _, cur = get_db()

        # 1. Проверка мута
        cur.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
        if cur.fetchone():
            try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
            except: pass
            return

        # 2. Логирование активности
        cur.execute("""
            INSERT INTO users (user_id, peer_id, msgs, last_seen) 
            VALUES (%s, %s, 1, CURRENT_TIMESTAMP) 
            ON CONFLICT (user_id, peer_id) 
            DO UPDATE SET msgs = users.msgs + 1, last_seen = CURRENT_TIMESTAMP
        """, (uid, pid))

        # 3. Обработка команд
        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _, _, _ = get_user_data(uid, pid)
        
        # Системная выдача прав владельцем
        if cmd == "sysrole" and uid == OWNER_ID:
            target = await extract_id(message)
            if target and args:
                lvl = int(args[-1])
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
                return await message.answer(f"⚡ Ранг {lvl} выдан пользователю [id{target}|id{target}].")

        # Проверка прав доступа
        min_req = await get_min_role(pid, cmd)
        if u_role < min_req and uid != OWNER_ID: return

        # Логика команд
        if cmd == "stats":
            target = await extract_id(message) or uid
            r, m, n, w = get_user_data(target, pid)
            await message.answer(f"📊 Профиль [id{target}]:\n🎭 Ник: {n or 'Нет'}\n✉ Сообщ: {m}\n⚠ Варны: {w}/3\n⭐ Роль: {get_role_title(pid, r)}")

        elif cmd == "warn":
            target = await extract_id(message)
            if not target: return await message.answer("📌 Укажите пользователя.")
            cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
            w = cur.fetchone()[0]
            if w >= 3:
                cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
                try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                except: pass
                await message.answer(f"⛔ [id{target}|Бан] (3/3 варна).")
            else:
                await message.answer(f"⚠ [id{target}|Варн] ({w}/3).")

        elif cmd == "staff":
            cur.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
            data = cur.fetchall()
            txt = "👮 Администрация:\n" + "\n".join([f"• [id{s[0]}|{get_role_title(pid, s[1])}]" for s in data])
            await message.answer(txt if data else "Администрация не назначена.")

        elif cmd == "rnick":
            target = await extract_id(message) or uid
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            await message.answer(f"✅ Ник [id{target}|пользователя] сброшен.")

        elif cmd == "start":
            try:
                mems = await bot.api.messages.get_conversation_members(peer_id=pid)
                for m in mems.items:
                    if getattr(m, 'is_owner', False):
                        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                        await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")
            except:
                await message.answer("❌ Мне нужны права администратора.")

    except Exception:
        print(f"Ошибка в обработчике:\n{traceback.format_exc()}")

# =========================
# MAINTENANCE & RUN
# =========================
async def maintenance_task():
    """Фоновая очистка старых наказаний."""
    while True:
        try:
            _, cur = get_db()
            cur.execute("DELETE FROM punishments WHERE end_at <= %s", (datetime.now(),))
        except: pass
        await asyncio.sleep(60)

if __name__ == "__main__":
    # [span_5](start_span)Исправляем RuntimeError: добавляем фоновую задачу в цикл бота vkbottle[span_5](end_span)
    bot.loop_wrapper.add_task(maintenance_task())
    print(">>> [SYSTEM] БОТ И БАЗА ГОТОВЫ. ОШИБКИ ЦИКЛА ИСПРАВЛЕНЫ.")
    # [span_6](start_span)Запускаем через run_forever, чтобы библиотека сама управляла циклом[span_6](end_span)
    bot.run_forever()
