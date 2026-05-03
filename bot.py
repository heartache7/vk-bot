import os
import re
import asyncio
import traceback
import psycopg2
from datetime import datetime, timedelta
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS punishments (
            id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, 
            type TEXT, end_at TIMESTAMP
        );""")
        cur.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
        
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warn_count INT DEFAULT 0;")
        cur.execute("ALTER TABLE punishments ADD COLUMN IF NOT EXISTS end_at TIMESTAMP;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
        print(">>> [DATABASE] Система FLEX полностью обновлена (RU-Time).")
    except Exception as e:
        print(f">>> [DB ERROR] {e}")
    finally:
        conn.close()

init_db()

# =========================
# УТИЛИТЫ (РУССКОЕ ВРЕМЯ)
# =========================
def parse_time_ru(time_str):
    """Парсит 10m, 2h, 1d и возвращает дельту и русское описание"""
    units = {
        'm': ('минут', 'минуты', 'минуту'),
        'h': ('часов', 'часа', 'час'),
        'd': ('дней', 'дня', 'день')
    }
    match = re.match(r"(\d+)([mhd])", time_str.lower())
    if not match: return None, None
    
    val, unit = int(match.group(1)), match.group(2)
    
    # Склонение (простой вариант для отчетов)
    if val == 1: label = units[unit][2]
    elif 1 < val < 5: label = units[unit][1]
    else: label = units[unit][0]
    
    delta = {
        'm': timedelta(minutes=val),
        'h': timedelta(hours=val),
        'd': timedelta(days=val)
    }[unit]
    
    return datetime.now() + delta, f"{val} {label}"

async def get_user_data(uid, pid):
    conn, cur = get_db()
    cur.execute("SELECT role, nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cur.fetchone()
    conn.close()
    return res if res else (0, None)

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
            return await message.answer("👋 FLEX активен. Временные метки переведены на русский!")

        conn, cur = get_db()
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))

        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]

        u_role, _ = await get_user_data(uid, pid)

        # --- КОМАНДЫ НАКАЗАНИЯ ---
        if cmd in ["warn", "mute", "ban", "kick"]:
            target_id = await extract_id(message)
            
            # Инструкция при пустой команде
            if not target_id:
                return await message.answer(
                    f"📖 Инструкция /{cmd}:\n"
                    f"Использование: /{cmd} [id/ссылка] [время] [причина]\n"
                    f"Примеры времени: 30m (30 мин), 2h (2 часа), 1d (1 день).\n"
                    f"Или просто ответьте на сообщение нарушителя."
                )

            # Проверка иерархии (Priority Check)
            t_role, _ = await get_user_data(target_id, pid)
            if uid != OWNER_ID and u_role <= t_role:
                return await message.answer(f"⚠️ Ошибка иерархии! Вы не можете наказать [id{target_id}|пользователя] с рангом {t_role} (ваш ранг: {u_role}).")

            # Парсинг времени и причины
            end_time, time_label = None, "навсегда"
            reason = "не указана"
            
            # Фильтруем аргументы, убирая ссылку на ID
            clean_args = [a for a in args if not re.search(r"id\d+|\[id\d+\|", a)]
            
            if clean_args:
                potential_time, t_text = parse_time_ru(clean_args[0])
                if potential_time:
                    end_time = potential_time
                    time_label = t_text
                    reason = " ".join(clean_args[1:]) if len(clean_args) > 1 else "не указана"
                else:
                    reason = " ".join(clean_args)

            # Формирование отчета
            report = (
                f"📝 ОТЧЕТ О НАКАЗАНИИ:\n"
                f"🎭 Нарушитель: [id{target_id}|Юзер]\n"
                f"👮 Выдал: [id{uid}|Администратор]\n"
                f"⏳ Срок: {time_label}\n"
                f"📄 Причина: {reason}\n"
                f"--------------------------\n"
            )

            if cmd == "warn":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 20+")
                cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target_id, pid))
                w = cur.fetchone()[0]
                if w >= 3:
                    cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                    await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                    return await message.answer(f"{report}⛔ Итог: Бан за достижение лимита варнов (3/3).")
                return await message.answer(f"{report}⚠ Итог: Предупреждение ({w}/3).")

            elif cmd == "mute":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 20+")
                if not end_time: 
                    end_time = datetime.now() + timedelta(hours=1)
                    time_label = "1 час (стандарт)"
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'mute', %s)", (target_id, pid, end_time))
                return await message.answer(f"{report}🔇 Итог: Мут (сообщения будут удаляться автоматически).")

            elif cmd == "ban":
                if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 80+")
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'ban', %s)", (target_id, pid, end_time))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                return await message.answer(f"{report}🚫 Итог: Блокировка доступа.")

            elif cmd == "kick":
                if u_role < 60 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 60+")
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                return await message.answer(f"{report}👢 Итог: Исключение из беседы.")

        # --- НИКИ И РОЛИ ---
        elif cmd == "snick":
            if not args: return await message.answer("📖 Инструкция /snick:\nИспользование: /snick [ваш новый ник]")
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname = %s WHERE user_id = %s AND peer_id = %s", (nick, uid, pid))
            return await message.answer(f"✅ Ник успешно изменен на: «{nick}»")

        elif cmd == "rnick":
            if u_role < 40 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 40+")
            target = target_id or uid
            if not target_id and not args: 
                return await message.answer("📖 Инструкция /rnick:\nОтветьте на сообщение или введите /rnick [ссылка]")
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            return await message.answer(f"✅ Ник пользователя [id{target}|id{target}] был сброшен администратором.")

        elif cmd == "setrole" and (uid == OWNER_ID or u_role >= 100):
            if not target_id or not args: 
                return await message.answer("📖 Инструкция /setrole:\nИспользование: /setrole [id] [уровень]")
            try:
                lvl = int(args[-1])
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target_id, pid, lvl, lvl))
                return await message.answer(f"✅ Ранг {lvl} успешно присвоен пользователю [id{target_id}|id{target_id}].")
            except: return await message.answer("❌ Ошибка: Уровень ранга должен быть числом.")

    except Exception:
        print(traceback.format_exc())
    finally:
        if conn: conn.close()

# --- ЦИКЛMaintenance ---
async def maintenance_loop():
    while True:
        try:
            conn, cur = get_db()
            cur.execute("DELETE FROM punishments WHERE end_at <= %s", (datetime.now(),))
            conn.close()
        except: pass
        await asyncio.sleep(60)

if __name__ == "__main__":
    bot.loop_wrapper.add_task(maintenance_loop())
    print(">>> [FLEX] БОТ ЗАПУЩЕН. ВРЕМЯ НА РУССКОМ. ИНСТРУКЦИИ ВКЛЮЧЕНЫ.")
    bot.run_forever()
