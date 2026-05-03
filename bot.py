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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        invited_by BIGINT, invited_at TIMESTAMP, last_msg_at TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, 
        type TEXT, end_at TIMESTAMP);
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles_titles (
        peer_id BIGINT, role_lvl INT, title TEXT,
        PRIMARY KEY (peer_id, role_lvl));
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT, cmd_name TEXT, min_role INT,
        PRIMARY KEY (peer_id, cmd_name));
    """)
    conn.commit()
    print(">>> БАЗА ДАННЫХ ПРОИНИЦИАЛИЗИРОВАНА")

init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    try:
        cursor.execute("SELECT role, msgs FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
        res = cursor.fetchone()
        return res if res else (0, 0)
    except: return (0, 0)

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res: return res[0]
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
    return int(target.group(1)) if target else None

def parse_time(time_str):
    if not time_str or time_str.lower() == "навсегда": return datetime(2099, 1, 1)
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    match = re.match(r"(\d+)([smhdw]?)", time_str.lower())
    if not match: return datetime(2099, 1, 1)
    amount, unit = int(match.group(1)), match.group(2) or 'm'
    return datetime.now() + timedelta(seconds=amount * units[unit])

async def check_warns(uid, pid, message: Message):
    cursor.execute("SELECT COUNT(*) FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn'", (uid, pid))
    if cursor.fetchone()[0] >= 3:
        try:
            cursor.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn'", (uid, pid))
            conn.commit()
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            await message.answer(f"⛔ [id{uid}|Пользователь] набрал 3/3 варна и исключен.")
        except: pass

# =========================
# BACKGROUND TASK (Авто-разбан)
# =========================
async def punishment_scheduler():
    while True:
        await asyncio.sleep(60)
        try:
            cursor.execute("SELECT id, user_id, peer_id, type FROM punishments WHERE end_at <= NOW()")
            expired = cursor.fetchall()
            for p_id, u_id, pid_val, p_type in expired:
                cursor.execute("DELETE FROM punishments WHERE id = %s", (p_id,))
                conn.commit()
                try: 
                    await bot.api.messages.send(
                        peer_id=pid_val, 
                        message=f"⏰ Срок наказания ({p_type}) истек для [id{u_id}|пользователя].", 
                        random_id=0
                    )
                except: pass
        except: pass

# =========================
# ОСНОВНОЙ ОБРАБОТЧИК
# =========================
@bot.on.message()
async def main_handler(message: Message):
    uid, pid = message.from_id, message.peer_id
    if uid < 0: return # Игнорируем сообщения от ботов и групп

    # --- 1. ПРОВЕРКА СЕРВИСНЫХ СОБЫТИЙ (Приглашения) ---
    if message.action:
        if message.action.type == "chat_invite_user" or message.action.type == "chat_invite_user_by_link":
            target_id = message.action.member_id or uid
            
            # Если добавили самого бота
            bot_info = await bot.api.groups.get_by_id()
            if target_id == -bot_info[0].id:
                await message.answer("Приветствую тебя, друг! Спасибо за то, что добавил меня! FLEX чат-менеджер – к твоим услугам. Для того, чтобы узнать свои возможности, пропиши команду /help")
                return

            # Если зашел забаненный
            cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (target_id, pid))
            if cursor.fetchone():
                try: 
                    await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                    return # Дальше не обрабатываем
                except: pass
        return

    # --- 2. УЧЕТ АКТИВНОСТИ ---
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
    conn.commit()

    if not message.text: return
    text = message.text.replace("!", "/").lower()
    args = text.split()
    if not args[0].startswith("/"): return
    cmd = args[0][1:]

    u_role, _ = get_user_data(uid, pid)
    min_req = await get_min_role(pid, cmd)

    # --- 3. КОМАНДЫ ---

    # Специальная команда для Разработчика
    if cmd == "sysrole":
        if uid != OWNER_ID: return
        target = extract_id(message)
        if target and len(args) > 1:
            try:
                new_lvl = int(args[-1])
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, new_lvl, new_lvl))
                conn.commit()
                await message.answer(f"✅ Системная роль {new_lvl} выдана [id{target}|пользователю].")
            except: pass
        return

    # Проверка прав доступа
    if u_role < min_req and uid != OWNER_ID: return

    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if m.is_admin and getattr(m, 'is_owner', False):
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ FLEX активирован. [id{m.member_id}|Владелец беседы] получил роль 100.")
                break

    elif cmd == "help":
        await message.answer(
            "📖 Команды:\n/stats, /staff, /warns\n\n"
            "👮 Модерация:\n/kick, /ban, /mute, /warn, /unban, /unmute, /unwarn\n\n"
            "👑 Настройки:\n/setrole, /newrole, /setcmd"
        )

    elif cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        rows = cursor.fetchall()
        if not rows: return await message.answer("В этой беседе персонал еще не назначен.")
        res = "👥 Персонал беседы:\n"
        for r in rows: res += f"• {get_role_title(pid, r[1])}: [id{r[0]}|профиль]\n"
        await message.answer(res)

    elif cmd == "setrole":
        if u_role < 100 and uid != OWNER_ID: return
        target = extract_id(message)
        if target and len(args) > 2:
            try:
                new_lvl = int(args[-1])
                if new_lvl >= 100 and uid != OWNER_ID: return
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, new_lvl, new_lvl))
                conn.commit()
                await message.answer(f"✅ Роль {new_lvl} выдана.")
            except: pass

    elif cmd == "newrole":
        if u_role < 100 and uid != OWNER_ID: return
        if len(args) > 2:
            try:
                lvl = int(args[1])
                title = " ".join(args[2:])
                cursor.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT(peer_id, role_lvl) DO UPDATE SET title=EXCLUDED.title", (pid, lvl, title))
                conn.commit()
                await message.answer(f"✅ Название для уровня {lvl} изменено на: {title}")
            except: pass

    elif cmd == "setcmd":
        if u_role < 100 and uid != OWNER_ID: return
        if len(args) > 2:
            target_cmd = args[1].replace("/", "")
            if target_cmd == "sysrole": return
            try:
                new_min = int(args[2])
                cursor.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, min_role) VALUES (%s, %s, %s) ON CONFLICT(peer_id, cmd_name) DO UPDATE SET min_role=EXCLUDED.min_role", (pid, target_cmd, new_min))
                conn.commit()
                await message.answer(f"✅ Доступ к /{target_cmd} теперь с роли {new_min}.")
            except: pass

    elif cmd in ["ban", "mute", "warn"]:
        target = extract_id(message)
        if not target: return await message.answer(f"📌 Шаблон: /{cmd} [время] [причина]")
        
        duration, reason = "навсегда", "не указана"
        if len(args) >= 3:
            if re.match(r"^\d+[smhdwy]?$", args[1]):
                duration, reason = args[1], " ".join(args[2:])
            else: reason = " ".join(args[1:])
        elif len(args) == 2:
            reason = args[1]
        
        end_at = parse_time(duration)
        cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, %s, %s)", (target, pid, cmd, end_at))
        conn.commit()
        
        if cmd == "ban":
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            except: pass
        
        await message.answer(f"✅ {cmd.upper()} для [id{target}|пользователя].\n⏳ Срок: {duration}\n📝 Причина: {reason}")
        if cmd == "warn": await check_warns(target, pid, message)

    elif cmd == "kick":
        target = extract_id(message)
        if target:
            try: 
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer(f"✅ [id{target}|Пользователь] исключен.")
            except: pass

    elif cmd in ["unban", "unmute", "unwarn"]:
        target = extract_id(message)
        if not target: return
        t_type = cmd[2:]
        if t_type == "warn":
            cursor.execute("DELETE FROM punishments WHERE id = (SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn' ORDER BY id DESC LIMIT 1)", (target, pid))
        else:
            cursor.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type=%s", (target, pid, t_type))
        conn.commit()
        await message.answer(f"✅ Наказание ({t_type}) для [id{target}|пользователя] снято.")

    elif cmd == "warns":
        target = extract_id(message) or uid
        cursor.execute("SELECT count(*) FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn'", (target, pid))
        await message.answer(f"⚠ У [id{target}|пользователя] {cursor.fetchone()[0]}/3 варнов.")

    elif cmd == "stats":
        target = extract_id(message) or uid
        r, m = get_user_data(target, pid)
        await message.answer(f"📊 [id{target}|Статистика]:\n✉ Сообщений: {m}\n⭐ Роль: {get_role_title(pid, r)}")

# =========================
# ЗАПУСК
# =========================
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(punishment_scheduler())
    print(">>> FLEX ЧАТ-МЕНЕДЖЕР УСПЕШНО ЗАПУЩЕН")
    bot.run_forever()
