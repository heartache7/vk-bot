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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS punishments (
            id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, 
            type TEXT, end_at TIMESTAMP
        );""")
        cur.execute("CREATE TABLE IF NOT EXISTS roles_titles (peer_id BIGINT, role_lvl INT, title TEXT, PRIMARY KEY (peer_id, role_lvl));")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_peer_idx ON users (user_id, peer_id);")
        print(">>> [DATABASE] Система FLEX инициализирована на 100%.")
    except Exception as e:
        print(f">>> [DB ERROR] {e}")
    finally:
        conn.close()

init_db()

# =========================
# УТИЛИТЫ
# =========================
def parse_time_ru(time_str):
    units = {'m': ('минут', 'минуты', 'минуту'), 'h': ('часов', 'часа', 'час'), 'd': ('дней', 'дня', 'день')}
    match = re.match(r"(\d+)([mhd])", time_str.lower())
    if not match: return None, None
    val, unit = int(match.group(1)), match.group(2)
    label = units[unit][2] if val == 1 else (units[unit][1] if 1 < val < 5 else units[unit][0])
    delta = {'m': timedelta(minutes=val), 'h': timedelta(hours=val), 'd': timedelta(days=val)}[unit]
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

        # Мут-фильтр (удаление сообщений забаненных/замученных)
        conn, cur = get_db()
        cur.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
        if cur.fetchone():
            try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
            except: pass
            return

        # Обновление статистики
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))

        if not (text.startswith("/") or text.startswith("!")): return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]
        u_role, _ = await get_user_data(uid, pid)

        # --- БАЗОВЫЕ КОМАНДЫ ---
        if cmd == "ping": return await message.answer("👋 FLEX активен!")
        if cmd in ["help", "помощь"]:
            return await message.answer(
                "📒 СПРАВКА FLEX (100%):\n\n"
                "👤 ОБЩИЕ: /stats, /snick, /nlist\n"
                "👮 МОДЕРАЦИЯ (Нужен ранг выше цели):\n"
                "• /warn, /mute, /kick, /ban\n"
                "• /unwarn, /unmute, /unban — Снятие наказаний.\n"
                "• /rnick — Сброс ника.\n\n"
                "👑 УПРАВЛЕНИЕ: /setrole, /start"
            )

        if cmd == "start":
            res = await bot.api.messages.get_conversation_members(peer_id=pid)
            for m in res.items:
                if getattr(m, "is_owner", False):
                    cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                    return await message.answer(f"✅ Создатель [id{m.member_id}|Владелец] синхронизирован.")
            return

        # --- КОМАНДЫ СНЯТИЯ НАКАЗАНИЙ (UN...) ---
        if cmd in ["unwarn", "unmute", "unban"]:
            target_id = await extract_id(message)
            if not target_id: return await message.answer(f"📖 Инструкция /{cmd}:\nОтветьте на сообщение или напишите: /{cmd} [ссылка]")
            
            # Проверка иерархии (Снимать может только тот, кто равен или выше по рангу, либо владелец)
            t_role, _ = await get_user_data(target_id, pid)
            if uid != OWNER_ID and u_role < t_role:
                return await message.answer("⚠️ Вы не можете снимать наказания с тех, кто выше вас по рангу.")

            if cmd == "unwarn":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужно 20+ ранг.")
                cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                return await message.answer(f"✅ Все варны [id{target_id}|пользователя] обнулены.")

            elif cmd == "unmute":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужно 20+ ранг.")
                cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (target_id, pid))
                return await message.answer(f"✅ Мут с [id{target_id}|пользователя] снят.")

            elif cmd == "unban":
                if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Нужно 80+ ранг.")
                cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (target_id, pid))
                return await message.answer(f"✅ [id{target_id}|Пользователь] разбанен в этой беседе.")

        # --- КОМАНДЫ НАКАЗАНИЯ ---
        if cmd in ["warn", "mute", "ban", "kick"]:
            target_id = await extract_id(message)
            if not target_id: return await message.answer(f"📖 Инструкция /{cmd}:\n/{cmd} [ссылка/ответ] [время] [причина]")
            
            t_role, _ = await get_user_data(target_id, pid)
            if uid != OWNER_ID and u_role <= t_role: return await message.answer(f"⚠️ Ранг цели ({t_role}) выше или равен вашему ({u_role}).")

            end_time, time_label, reason = None, "навсегда", "не указана"
            clean_args = [a for a in args if not re.search(r"id\d+|\[id\d+\|", a)]
            if clean_args:
                potential_time, t_text = parse_time_ru(clean_args[0])
                if potential_time:
                    end_time, time_label = potential_time, t_text
                    reason = " ".join(clean_args[1:]) if len(clean_args) > 1 else "не указана"
                else: reason = " ".join(clean_args)

            report = f"📝 ОТЧЕТ:\n🎭 Нарушитель: [id{target_id}|Юзер]\n👮 Админ: [id{uid}|Админ]\n⏳ Срок: {time_label}\n📄 Причина: {reason}\n"

            if cmd == "warn":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 20+")
                cur.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target_id, pid))
                w = cur.fetchone()[0]
                if w >= 3:
                    cur.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                    await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                    return await message.answer(f"{report}⛔ Итог: Бан (3/3 варнов).")
                return await message.answer(f"{report}⚠ Итог: Варн ({w}/3).")

            elif cmd == "mute":
                if u_role < 20 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 20+")
                if not end_time: end_time = datetime.now() + timedelta(hours=1); time_label = "1 час"
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'mute', %s)", (target_id, pid, end_time))
                return await message.answer(f"{report}🔇 Итог: Мут.")

            elif cmd == "ban":
                if u_role < 80 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 80+")
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'ban', %s)", (target_id, pid, end_time))
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                return await message.answer(f"{report}🚫 Итог: Бан.")

            elif cmd == "kick":
                if u_role < 60 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 60+")
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                return await message.answer(f"{report}👢 Итог: Кик.")

        # --- СИСТЕМА НИКОВ / РОЛЕЙ ---
        elif cmd == "snick":
            if not args: return await message.answer("📖 Инструкция /snick: /snick [ник]")
            nick = " ".join(args)[:20]
            cur.execute("UPDATE users SET nickname = %s WHERE user_id = %s AND peer_id = %s", (nick, uid, pid))
            return await message.answer(f"✅ Ник изменен: {nick}")

        elif cmd == "rnick":
            if u_role < 40 and uid != OWNER_ID: return await message.answer("🚫 Нужен ранг 40+")
            target = target_id or uid
            cur.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
            return await message.answer(f"✅ Ник [id{target}|участника] удален.")

        elif cmd == "setrole" and (uid == OWNER_ID or u_role >= 100):
            if not target_id or not args: return await message.answer("📖 Инструкция /setrole: /setrole [ссылка] [ранг]")
            lvl = int(args[-1])
            cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target_id, pid, lvl, lvl))
            return await message.answer(f"✅ Ранг {lvl} выдан [id{target_id}|юзеру].")

    except Exception: print(traceback.format_exc())
    finally:
        if conn: conn.close()

async def maintenance():
    while True:
        try:
            conn, cur = get_db()
            cur.execute("DELETE FROM punishments WHERE end_at <= %s", (datetime.now(),))
            conn.close()
        except: pass
        await asyncio.sleep(60)

if __name__ == "__main__":
    bot.loop_wrapper.add_task(maintenance())
    print(">>> FLEX ГОТОВ НА 100%.")
    bot.run_forever()
