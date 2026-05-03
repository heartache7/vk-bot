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
# ДВИЖОК БАЗЫ ДАННЫХ
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
        
        # Миграции структуры
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
        print(">>> [DATABASE] Система готова. Иерархия и инструкции активны.")
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
# ОСНОВНОЙ ОБРАБОТЧИК
# =========================
@bot.on.message()
async def handler(message: Message):
    conn = None
    try:
        if not message.text or message.from_id <= 0: return
        uid, pid, text = message.from_id, message.peer_id, message.text.strip()

        # Пинг системы
        if text.lower() in ["/ping", "!ping"]:
            return await message.answer("👋 FLEX активен! Все системы иерархии и подсказок в норме.")

        conn, cur = get_db()

        # Статистика сообщений
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))

        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _, _, _ = await get_user_data(uid, pid)

        # --- ОБЩАЯ СПРАВКА (HELP) ---
        if cmd in ["help", "помощь"]:
            help_msg = (
                "📒 ИНСТРУКЦИЯ ПО КОМАНДАМ:\n\n"
                "👤 ДЛЯ ВСЕХ:\n"
                "• /stats [id] — Информация о ранге и сообщениях.\n"
                "• /snick [текст] — Установить себе ник.\n"
                "• /nlist — Список всех ников в чате.\n\n"
                "👮 МОДЕРАЦИЯ (Доступно, если цель НИЖЕ вас по рангу):\n"
                "• /warn [id/ответ] — Выдать варн. Инструкция: Наберите в ответ на нарушение. (Ранг 20+)\n"
                "• /rnick [id/ответ] — Сбросить чужой ник. Инструкция: Используйте, если ник нарушает правила. (Ранг 40+)\n"
                "• /kick [id/ответ] — Выгнать из беседы. (Ранг 60+)\n"
                "• /ban [id/ответ] — Навсегда заблокировать в чате. (Ранг 80+)\n\n"
                "👑 УПРАВЛЕНИЕ:\n"
                "• /setrole [id] [lvl] — Назначить ранг другому. (Ранг 100)\n"
                "• /newrole [lvl] [имя] — Переименовать системный ранг."
            )
            return await message.answer(help_msg)

        # --- ПРОВЕРКА ЦЕЛИ И ИЕРАРХИИ ---
        target_id = await extract_id(message)
        
        # Если команда направлена на кого-то, проверяем приоритет (кроме Владельца бота)
        if target_id and cmd in ["warn", "kick", "ban", "rnick"]:
            t_role, _, _, _ = await get_user_data(target_id, pid)
            if uid != OWNER_ID and u_role <= t_role:
                return await message.answer(f"⚠️ Отказано! [id{target_id}|Этот пользователь] имеет ранг {t_role}, что равно или выше вашего ({u_role}). Наказывать можно только тех, кто ниже по иерархии.")

        # --- ЛОГИКА КОМАНД С ПОДСКАЗКАМИ ---

        if cmd == "warn":
            if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Инструкция: Для варна нужен ранг 20 (Модератор) или выше.")
            if not target_id: return await message.answer("📌 Подсказка: Ответьте на сообщение нарушителя или укажите его ID.")
            cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target_id, pid))
            w = cur.fetchone()[0]
            if w >= 3:
                cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                await message.answer(f"⛔ [id{target_id}|Нарушитель] набрал лимит (3/3) и исключен.")
            else:
                await message.answer(f"⚠ [id{target_id}|Варн] выдан. Текущий счет: {w}/3.")

        elif cmd == "kick":
            if u_role < 60 and uid != OWNER_ID: return await message.answer("🚫 Инструкция: Кик доступен Админам (60+).")
            if target_id:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                await message.answer(f"👢 [id{target_id}|Пользователь] исключен из беседы.")

        elif cmd == "ban":
            if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Инструкция: Бан доступен только Гл. Админам (80+).")
            if target_id:
                cur.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, 'ban')", (target_id, pid))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                await message.answer(f"🚫 [id{target_id}|Пользователь] забанен навсегда.")

        elif cmd == "snick":
            if not args: return await message.answer("📌 Подсказка: Введите имя после команды. Пример: /snick Король")
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname = %s WHERE user_id = %s AND peer_id = %s", (nick, uid, pid))
            await message.answer(f"✅ Ник изменен на: «{nick}»")

        elif cmd == "rnick":
            if u_role < 40 and uid != OWNER_ID: return await message.answer("🚫 Инструкция: Удаление чужих ников доступно Модераторам+ (40+).")
            target = target_id or uid
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            await message.answer(f"✅ Ник участника [id{target}|id{target}] успешно сброшен.")

        elif cmd == "nlist":
            cur.execute("SELECT user_id, nickname FROM users WHERE peer_id=%s AND nickname IS NOT NULL", (pid,))
            rows = cur.fetchall()
            if not rows: return await message.answer("📝 В этом чате еще никто не поставил ник.")
            res = [f"• [id{r[0]}|{r[1]}]" for r in rows]
            await message.answer("📝 Список ников чата:\n" + "\n".join(res))

        elif cmd == "stats":
            target = target_id or uid
            r, m, n, w = await get_user_data(target, pid)
            t = await get_role_title(pid, r)
            await message.answer(f"📊 [id{target}|Статистика]:\n🎭 Ник: {n or '—'}\n⭐ Ранг: {t} ({r})\n✉ СМС: {m}\n⚠ Варны: {w}/3")

        elif cmd == "setrole" and (uid == OWNER_ID or u_role >= 100):
            if target_id and args:
                try:
                    lvl = int(args[-1])
                    cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target_id, pid, lvl, lvl))
                    await message.answer(f"✅ [id{target_id}|Ранг] изменен на {lvl}.")
                except: await message.answer("❌ Ошибка: Ранг должен быть числом.")

    except Exception:
        print(traceback.format_exc())
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    print(">>> [SYSTEM] FLEX ЗАПУЩЕН СО ВСЕМИ ИНСТРУКЦИЯМИ.")
    bot.run_forever()
