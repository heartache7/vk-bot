import os
import re
import asyncio
import traceback
import psycopg2
from datetime import datetime
from vkbottle.bot import Bot, Message

# =========================
# КОНФИГУРАЦИЯ
# =========================
OWNER_ID = 676081199 
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# БАЗА ДАННЫХ
# =========================
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init_db():
    conn, cur = get_db()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
            nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        cur.execute("CREATE TABLE IF NOT EXISTS punishments (id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT, end_at TIMESTAMP);")
        cur.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
        
        # Исправления и миграции
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
        print(">>> [DATABASE] Структура FLEX готова.")
    except Exception as e:
        print(f">>> [DB ERROR] {e}")
    finally:
        conn.close()

init_db()

# =========================
# ФУНКЦИИ ПОДДЕРЖКИ
# =========================
async def get_user_data(uid, pid):
    conn, cur = get_db()
    cur.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cur.fetchone()
    conn.close()
    return res if res else (0, 0, None, 0)

async def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец"
    conn, cur = get_db()
    cur.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cur.fetchone()
    conn.close()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 40: "Модератор+", 20: "Модератор", 0: "Участник"}.get(lvl, f"Ранг {lvl}")

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    res = re.search(r"id(\d+)|\[id(\d+)\|", message.text)
    return int(res.group(1) or res.group(2)) if res else None

# =========================
# ОБРАБОТЧИК
# =========================
@bot.on.message()
async def handler(message: Message):
    conn = None
    try:
        if not message.text or message.from_id <= 0: return
        uid, pid, text = message.from_id, message.peer_id, message.text.strip()

        # Пинг
        if text.lower() in ["/ping", "!ping"]:
            return await message.answer("👋 FLEX Менеджер на связи!")

        conn, cur = get_db()

        # Статистика
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))

        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _, u_nick, _ = await get_user_data(uid, pid)

        # --- ИНСТРУКЦИЯ КО ВСЕМ КОМАНДАМ ---
        if cmd in ["help", "помощь"]:
            help_text = (
                "📖 ИНСТРУКЦИЯ FLEX:\n\n"
                "🔹 /stats [id] — Статистика и ранг участника.\n"
                "🔹 /staff — Список тех, у кого есть права в чате.\n"
                "🔹 /snick [ник] — Установить себе ник (до 20 симв.).\n"
                "🔹 /nlist — Список всех участников с никами.\n\n"
                "👮 МОДЕРАЦИЯ (20-60+):\n"
                "🔸 /warn [ответ] — Предпреждение. 3/3 = автоматический кик. (Ранг 20)\n"
                "🔸 /rnick [ответ/id] — Удалить чужой неподобающий ник. (Ранг 40)\n"
                "🔸 /kick [ответ/id] — Удалить участника из беседы. (Ранг 60)\n\n"
                "👑 УПРАВЛЕНИЕ (80-100):\n"
                "🔹 /ban [ответ/id] — Вечный бан в этой беседе. (Ранг 80)\n"
                "🔹 /setrole [id] [lvl] — Назначить ранг (0-100). (Ранг 100)\n"
                "🔹 /newrole [lvl] [имя] — Свое название для ранга. (Ранг 100)\n"
                "🔹 /start — Синхронизация Создателя беседы (Владелец)."
            )
            return await message.answer(help_text)

        # --- ЛОГИКА КОМАНД ---

        elif cmd == "warn":
            if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужно иметь ранг Модератор (20+)")
            target = await extract_id(message)
            if not target: return await message.answer("📌 Напишите /warn в ответ на сообщение.")
            cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
            w = cur.fetchone()[0]
            if w >= 3:
                cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer(f"⛔ [id{target}|Нарушитель] исключен за 3/3 варна.")
            else:
                await message.answer(f"⚠ [id{target}|Варн] выдан. Всего: {w}/3.")

        elif cmd == "kick":
            if u_role < 60 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг Админ (60+)")
            target = await extract_id(message)
            if target:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer(f"👢 [id{target}|Пользователь] кикнут.")

        elif cmd == "ban":
            if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг Гл. Админ (80+)")
            target = await extract_id(message)
            if target:
                cur.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, 'ban')", (target, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer(f"🚫 [id{target}|Юзер] забанен в этой беседе.")

        elif cmd == "snick":
            if not args: return await message.answer("📌 Напишите: /snick [ваш ник]")
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname = %s WHERE user_id = %s AND peer_id = %s", (nick, uid, pid))
            await message.answer(f"✅ Ник изменен на «{nick}»")

        elif cmd == "rnick":
            if u_role < 40 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг Модератор+ (40+)")
            target = await extract_id(message) or uid
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            await message.answer(f"✅ Ник участника [id{target}|id{target}] удален.")

        elif cmd == "nlist":
            cur.execute("SELECT user_id, nickname FROM users WHERE peer_id=%s AND nickname IS NOT NULL", (pid,))
            rows = cur.fetchall()
            if not rows: return await message.answer("📝 Никто еще не поставил ник.")
            res = [f"• [id{r[0]}|{r[1]}]" for r in rows]
            await message.answer("📝 Установленные ники:\n" + "\n".join(res))

        elif cmd == "stats":
            target = await extract_id(message) or uid
            r, m, n, w = await get_user_data(target, pid)
            t = await get_role_title(pid, r)
            await message.answer(f"📊 [id{target}|Профиль]:\n🎭 Ник: {n or '—'}\n⭐ Ранг: {t} ({r})\n✉ СМС: {m}\n⚠ Варны: {w}/3")

        elif cmd == "staff":
            cur.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
            rows = cur.fetchall()
            if not rows: return await message.answer("👮 В чате нет админов.")
            res = [f"• [id{r[0]}|{await get_role_title(pid, r[1])}]" for r in rows]
            await message.answer("👮 Администрация:\n" + "\n".join(res))

        elif cmd == "setrole" and (uid == OWNER_ID or u_role >= 100):
            target = await extract_id(message)
            if target and args:
                lvl = int(args[-1])
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
                await message.answer(f"✅ [id{target}|Ранг] {lvl} успешно выдан.")

    except Exception:
        print(traceback.format_exc())
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    print(">>> FLEX БОТ С ИНСТРУКЦИЯМИ ЗАПУЩЕН.")
    bot.run_forever()
