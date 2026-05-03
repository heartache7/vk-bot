import os
import re
import asyncio
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
# DATABASE SETUP
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def init_db():
    # Таблица пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        invited_by BIGINT, invited_at TIMESTAMP, last_msg_at TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    # Таблица наказаний (с колонкой времени окончания)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, 
        type TEXT, end_at TIMESTAMP);
    """)
    # Таблица имен ролей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles_titles (
        peer_id BIGINT, role_lvl INT, title TEXT,
        PRIMARY KEY (peer_id, role_lvl));
    """)
    # Таблица прав команд
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT, cmd_name TEXT, min_role INT,
        PRIMARY KEY (peer_id, cmd_name));
    """)
    conn.commit()
    print(">>> БАЗА ДАННЫХ ПРОВЕРЕНА И ГОТОВА")

init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    if not res: return (0, 0)
    return (res[0], res[1])

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res: return res[0]
    # Дефолтные настройки
    admins = ["kick", "ban", "warn", "mute", "unban", "unmute", "unwarn", "setrole", "newrole", "setcmd", "staff"]
    return 20 if cmd in admins else 0

def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец беседы"
    cursor.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cursor.fetchone()
    if res: return res[0]
    defaults = {80: "Гл. Админ", 60: "Админ", 40: "Ст. Модератор", 20: "Модератор", 0: "Пользователь"}
    return defaults.get(lvl, f"Уровень {lvl}")

def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"id(\d+)", message.text)
    if target: return int(target.group(1))
    return None

def parse_time(time_str):
    if not time_str or time_str.lower() == "навсегда": return datetime(2099, 1, 1)
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    match = re.match(r"(\d+)([smhdw]?)", time_str.lower())
    if not match: return datetime(2099, 1, 1)
    amount, unit = int(match.group(1)), match.group(2) or 'm'
    return datetime.now() + timedelta(seconds=amount * units[unit])

# =========================
# BACKGROUND TASK (Авто-разбан)
# =========================
async def punishment_scheduler():
    while True:
        await asyncio.sleep(60)
        cursor.execute("SELECT id, user_id, peer_id, type FROM punishments WHERE end_at <= NOW()")
        expired = cursor.fetchall()
        for p_id, u_id, pid, p_type in expired:
            cursor.execute("DELETE FROM punishments WHERE id = %s", (p_id,))
            conn.commit()
            try: await bot.api.messages.send(peer_id=pid, message=f"⏰ Срок наказания ({p_type}) истек для [id{u_id}|пользователя].", random_id=0)
            except: pass

# =========================
# EVENTS
# =========================
@bot.on.raw_event("message_new", dataclass=Message)
async def event_handler(message: Message):
    pid, uid = message.peer_id, message.from_id
    if message.action and message.action.type == "chat_invite_user":
        target_id = message.action.member_id
        bot_info = await bot.api.groups.get_by_id()
        
        # Если добавили бота
        if target_id == -bot_info[0].id:
            await message.answer("Приветствую тебя, друг! Спасибо за то, что добавил меня! FLEX чат-менеджер – к твоим услугам. Для того, чтобы узнать свои возможности, пропиши команду /help")
            return

        # Проверка на бан
        cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (target_id, pid))
        if cursor.fetchone():
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
            except: pass

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(message: Message):
    if not message.text or message.from_id < 0: return
    uid, pid = message.from_id, message.peer_id
    
    # Регистрация активности
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
    conn.commit()

    text = message.text.replace("!", "/").lower()
    args = text.split()
    if not args[0].startswith("/"): return
    cmd = args[0][1:]

    u_role, _ = get_user_data(uid, pid)
    min_req = await get_min_role(pid, cmd)

    # 1. СИСТЕМНАЯ РОЛЬ (Только OWNER_ID)
    if cmd == "sysrole":
        if uid != OWNER_ID: return
        target = extract_id(message)
        if target and len(args) > 1:
            try:
                new_lvl = int(args[-1])
                cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (new_lvl, target, pid))
                conn.commit()
                await message.answer(f"✅ Системная роль {new_lvl} выдана [id{target}|пользователю].")
            except: pass
        return

    # 2. ПРОВЕРКА ПРАВ
    if u_role < min_req and uid != OWNER_ID: return

    # 3. КОМАНДЫ
    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if m.is_admin and getattr(m, 'is_owner', False):
                cursor.execute("UPDATE users SET role=100 WHERE user_id=%s AND peer_id=%s", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ FLEX активирован. [id{m.member_id}|Владелец] получил роль 100.")
        
    elif cmd == "help":
        await message.answer("📖 Команды:\n/stats, /staff, /warns\n\n👮 Админ:\n/kick, /ban, /mute, /warn, /unban, /unmute, /unwarn\n\n👑 Владелец:\n/setrole, /newrole, /setcmd")

    elif cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        rows = cursor.fetchall()
        res = "👥 Персонал:\n"
        for r in rows: res += f"• {get_role_title(pid, r[1])}: id{r[0]}\n"
        await message.answer(res if rows else "Список пуст.")

    elif cmd == "setrole":
        if u_role < 100 and uid != OWNER_ID: return
        target = extract_id(message)
        if target and len(args) > 2:
            new_lvl = int(args[-1])
            if new_lvl < 100 or uid == OWNER_ID:
                cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (new_lvl, target, pid))
                conn.commit()
                await message.answer("✅ Роль выдана.")

    elif cmd == "newrole":
        if u_role < 100 and uid != OWNER_ID: return
        if len(args) > 2:
            cursor.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT(peer_id, role_lvl) DO UPDATE SET title=EXCLUDED.title", (pid, int(args[1]), " ".join(args[2:])))
            conn.commit()
            await message.answer("✅ Название роли сохранено.")

    elif cmd in ["ban", "mute", "warn"]:
        target = extract_id(message)
        if not target: return await message.answer(f"📌 Шаблон: /{cmd} [время] [причина]")
        duration = "навсегда"
        reason = "не указана"
        if len(args) >= 3:
            if re.match(r"^\d+[smhdwy]?$", args[1]):
                duration = args[1]
                reason = " ".join(args[2:])
            else: reason = " ".join(args[1:])
        
        end_at = parse_time(duration)
        cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, %s, %s)", (target, pid, cmd, end_at))
        conn.commit()
        if cmd == "ban":
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            except: pass
        await message.answer(f"✅ {cmd.upper()} для [id{target}|пользователя]. Срок: {duration}. Причина: {reason}")

    elif cmd == "warns":
        target = extract_id(message) or uid
        cursor.execute("SELECT count(*) FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn'", (target, pid))
        count = cursor.fetchone()[0]
        await message.answer(f"⚠ У [id{target}|пользователя] {count}/3 варнов.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(punishment_scheduler())
    bot.run_forever()
