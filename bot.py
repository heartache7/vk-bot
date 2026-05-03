import os
import re
import asyncio
import psycopg2
from datetime import datetime
from vkbottle.bot import Bot, Message

# =========================
# НАСТРОЙКИ
# =========================
OWNER_ID = 676081199 
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# РАБОТА С БАЗОЙ ДАННЫХ
# =========================
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init_db():
    conn, cur = get_db()
    # Создание таблиц
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );""")
    cur.execute("CREATE TABLE IF NOT EXISTS punishments (id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT, end_at TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
    cur.execute("CREATE TABLE IF NOT EXISTS cmd_permissions (peer_id BIGINT, cmd_name TEXT, min_lvl INT, PRIMARY KEY (peer_id, cmd_name));")
    
    # Синхронизация структуры
    cur.execute("""
        DO $$ BEGIN 
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cmd_permissions' AND column_name='min_role') THEN
                ALTER TABLE cmd_permissions RENAME COLUMN min_role TO min_lvl;
            END IF;
        END $$;
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
    conn.close()

init_db()

# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================
async def get_user_data(uid, pid):
    conn, cur = get_db()
    cur.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cur.fetchone()
    conn.close()
    return res if res else (0, 0, None, 0)

async def get_role_title(pid, lvl):
    if lvl >= 100: return "Создатель"
    conn, cur = get_db()
    cur.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cur.fetchone()
    conn.close()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 40: "Модератор+", 20: "Модератор", 0: "Участник"}.get(lvl, f"Ранг {lvl}")

async def get_min_role(pid, cmd):
    conn, cur = get_db()
    cur.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cur.fetchone()
    conn.close()
    if res: return res[0]
    return {"kick": 60, "ban": 80, "mute": 20, "warn": 20, "setrole": 100, "newrole": 100, "rnick": 40}.get(cmd, 0)

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    res = re.search(r"id(\d+)|\[id(\d+)\|", message.text)
    return int(res.group(1) or res.group(2)) if res else None

# =========================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================
@bot.on.message()
async def handler(message: Message):
    conn = None
    try:
        if not message.text or message.from_id <= 0: return
        uid, pid, text = message.from_id, message.peer_id, message.text.strip()

        if text.lower() in ["/ping", "!ping"]:
            return await message.answer("✅ FLEX работает стабильно.")

        conn, cur = get_db()

        # Проверка мута
        cur.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
        if cur.fetchone():
            try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
            except: pass
            return

        # Учет статистики
        cur.execute("""
            INSERT INTO users (user_id, peer_id, msgs, last_seen) 
            VALUES (%s, %s, 1, CURRENT_TIMESTAMP) 
            ON CONFLICT (user_id, peer_id) 
            DO UPDATE SET msgs = users.msgs + 1, last_seen = CURRENT_TIMESTAMP
        """, (uid, pid))

        # Команды
        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _, _, _ = await get_user_data(uid, pid)
        min_req = await get_min_role(pid, cmd)
        
        if u_role < min_req and uid != OWNER_ID:
            return await message.answer(f"🚫 Доступ запрещен (нужен ранг {min_req}).")

        # --- КОРПУС КОМАНД ---

        if cmd in ["help", "помощь"]:
            await message.answer(
                "📖 Команды FLEX:\n\n"
                "• /stats [id] — Статистика\n"
                "• /staff — Список админов\n"
                "• /warn [id] — Выдать варн\n"
                "• /rnick [id] — Сбросить ник\n"
                "• /setrole [id] [lvl] — Выдать ранг (Владельцу)\n"
                "• /newrole [lvl] [имя] — Назвать ранг\n"
                "• /start — Стать владельцем (создателю)"
            )

        elif cmd == "stats":
            target = await extract_id(message) or uid
            r, m, n, w = await get_user_data(target, pid)
            t = await get_role_title(pid, r)
            await message.answer(f"📊 [id{target}|Профиль]:\n🎭 Ник: {n or 'Нет'}\n⭐ Ранг: {t} ({r})\n✉ Сообщений: {m}\n⚠ Варны: {w}/3")

        elif cmd == "warn":
            target = await extract_id(message)
            if not target: return await message.answer("📌 Кого варним?")
            cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
            w = cur.fetchone()[0]
            if w >= 3:
                cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer(f"⛔ [id{target}|Бан] за 3/3 варна.")
            else:
                await message.answer(f"⚠ [id{target}|Варн] ({w}/3).")

        elif cmd == "staff":
            cur.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
            rows = cur.fetchall()
            txt = "\n".join([f"• [id{r[0]}|{await get_role_title(pid, r[1])}]" for r in rows])
            await message.answer("👮 Админы чата:\n" + txt if rows else "Админов нет.")

        elif cmd == "setrole" and uid == OWNER_ID:
            target = await extract_id(message)
            if target and args:
                lvl = int(args[-1])
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
                await message.answer(f"✅ [id{target}|Ранг] {lvl} выдан.")

        elif cmd == "newrole":
            if not args or len(args) < 2: return
            lvl, title = int(args[0]), " ".join(args[1:])
            cur.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT (peer_id, role_lvl) DO UPDATE SET title=%s", (pid, lvl, title, title))
            await message.answer(f"✅ Ранг {lvl} теперь «{title}».")

        elif cmd == "rnick":
            target = await extract_id(message) or uid
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            await message.answer(f"✅ Ник [id{target}|сброшен].")

        elif cmd == "start":
            res = await bot.api.messages.get_conversation_members(peer_id=pid)
            for m in res.items:
                if getattr(m, "is_owner", False):
                    cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                    return await message.answer(f"✅ Владелец [id{m.member_id}|зарегистрирован].")

    except Exception: pass
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    print(">>> FLEX БОТ ЗАПУЩЕН.")
    bot.run_forever()
