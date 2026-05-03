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
        
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
        print(">>> [DATABASE] Система FLEX инициализирована.")
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

        if text.lower() in ["/ping", "!ping"]:
            return await message.answer("👋 FLEX активен. Все системы инструкций работают!")

        conn, cur = get_db()
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))

        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _, _, _ = await get_user_data(uid, pid)

        # --- HELP ---
        if cmd in ["help", "помощь"]:
            return await message.answer(
                "📒 СПРАВКА ПО КОМАНДАМ:\n\n"
                "👤 ОБЩИЕ: /stats, /snick, /nlist\n"
                "👮 МОДЕРАЦИЯ: /warn, /kick, /ban, /rnick\n"
                "👑 АДМИН: /setrole, /newrole, /start\n\n"
                "💡 Чтобы узнать, как работает команда, введите её без параметров (напр. /warn)."
            )

        # --- ЛОГИКА КОМАНД С ИНСТРУКЦИЯМИ ---

        # 1. СТАТИСТИКА
        if cmd == "stats":
            target = await extract_id(message) or uid
            r, m, n, w = await get_user_data(target, pid)
            t = await get_role_title(pid, r)
            return await message.answer(f"📊 [id{target}|Профиль]:\n🎭 Ник: {n or '—'}\n⭐ Ранг: {t} ({r})\n✉ СМС: {m}\n⚠ Варны: {w}/3")

        # 2. НИКНЕЙМЫ
        elif cmd == "snick":
            if not args:
                return await message.answer("📖 Инструкция /snick:\nИспользование: /snick [ваш ник]\nПример: /snick Легенда")
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname = %s WHERE user_id = %s AND peer_id = %s", (nick, uid, pid))
            return await message.answer(f"✅ Ваш ник изменен на: «{nick}»")

        elif cmd == "nlist":
            cur.execute("SELECT user_id, nickname FROM users WHERE peer_id=%s AND nickname IS NOT NULL", (pid,))
            rows = cur.fetchall()
            if not rows: return await message.answer("📝 В чате пока нет участников с никами.")
            res = [f"• [id{r[0]}|{r[1]}]" for r in rows]
            return await message.answer("📝 Список ников чата:\n" + "\n".join(res))

        # --- БЛОК С ПРОВЕРКОЙ ИЕРАРХИИ И АРГУМЕНТОВ ---
        
        elif cmd in ["warn", "kick", "ban", "rnick", "setrole", "newrole"]:
            # Проверка пустых аргументов (Инструкции)
            target_id = await extract_id(message)
            
            if cmd == "warn":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 20+")
                if not target_id:
                    return await message.answer("📖 Инструкция /warn:\nВыдает предупреждение (3/3 = кик).\nИспользование: Ответьте на сообщение или /warn [ссылка]")
            
            elif cmd == "kick":
                if u_role < 60 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 60+")
                if not target_id:
                    return await message.answer("📖 Инструкция /kick:\nИсключает участника.\nИспользование: Ответьте на сообщение или /kick [ссылка]")
            
            elif cmd == "ban":
                if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 80+")
                if not target_id:
                    return await message.answer("📖 Инструкция /ban:\nВечный бан в беседе.\nИспользование: Ответьте на сообщение или /ban [ссылка]")

            elif cmd == "rnick":
                if u_role < 40 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 40+")
                if not target_id and not args:
                    return await message.answer("📖 Инструкция /rnick:\nСбрасывает чужой ник.\nИспользование: Ответьте на сообщение или /rnick [ссылка]")

            elif cmd == "setrole":
                if u_role < 100 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 100")
                if not target_id or not args:
                    return await message.answer("📖 Инструкция /setrole:\nВыдает ранг участнику.\nИспользование: /setrole [ссылка] [уровень]\nПример: /setrole id1 20")

            elif cmd == "newrole":
                if u_role < 100 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 100")
                if len(args) < 2:
                    return await message.answer("📖 Инструкция /newrole:\nМеняет название ранга.\nИспользование: /newrole [уровень] [название]\nПример: /newrole 20 Модератор")

            # Проверка Иерархии (Priority Check)
            if target_id:
                t_role, _, _, _ = await get_user_data(target_id, pid)
                if uid != OWNER_ID and u_role <= t_role:
                    return await message.answer(f"⚠️ Ошибка иерархии! Ваш ранг ({u_role}) должен быть выше ранга цели ({t_role}).")

            # Исполнение команд
            if cmd == "warn":
                cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target_id, pid))
                w = cur.fetchone()[0]
                if w >= 3:
                    cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                    await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                    await message.answer(f"⛔ [id{target_id}|Юзер] исключен (3/3 варна).")
                else: await message.answer(f"⚠ [id{target_id}|Варн] ({w}/3).")

            elif cmd == "kick":
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                await message.answer(f"👢 [id{target_id}|Юзер] кикнут.")

            elif cmd == "ban":
                cur.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, 'ban')", (target_id, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                await message.answer(f"🚫 [id{target_id}|Юзер] забанен.")

            elif cmd == "rnick":
                target = target_id or uid
                cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
                await message.answer(f"✅ Ник [id{target}|пользователя] удален.")

            elif cmd == "setrole":
                try:
                    lvl = int(args[-1])
                    cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target_id, pid, lvl, lvl))
                    await message.answer(f"✅ [id{target_id}|Ранг] {lvl} выдан.")
                except: await message.answer("❌ Укажите числовой уровень ранга.")

            elif cmd == "newrole":
                try:
                    lvl, name = int(args[0]), " ".join(args[1:])
                    cur.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT (peer_id, role_lvl) DO UPDATE SET title=%s", (pid, lvl, name, name))
                    await message.answer(f"✅ Ранг {lvl} теперь называется «{name}»")
                except: await message.answer("❌ Ошибка в формате. Используйте: /newrole [число] [текст]")

        elif cmd == "start":
            res = await bot.api.messages.get_conversation_members(peer_id=pid)
            for m in res.items:
                if getattr(m, "is_owner", False):
                    cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                    return await message.answer(f"✅ Создатель [id{m.member_id}|синхронизирован]!")

    except Exception:
        print(traceback.format_exc())
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    print(">>> FLEX ЗАПУЩЕН. ИНСТРУКЦИИ ДЛЯ ПУСТЫХ КОМАНД ВКЛЮЧЕНЫ.")
    bot.run_forever()
