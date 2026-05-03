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
        nickname TEXT, PRIMARY KEY (user_id, peer_id));
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
    print(">>> БАЗА ДАННЫХ ГОТОВА")

init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs, nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    return res if res else (0, 0, None)

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res is not None: return res[0]
    
    # Дефолтные настройки прав
    admin_cmds = ["kick", "ban", "mute", "warn", "unban", "unmute", "unwarn", "setrole", "newrole", "setcmd", "staff", "rnick", "delnicks"]
    return 20 if cmd in admin_cmds else 0

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

# =========================
# BACKGROUND TASK
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
                try: await bot.api.messages.send(peer_id=pid_val, message=f"⏰ Срок наказания ({p_type}) истек для [id{u_id}|пользователя].", random_id=0)
                except: pass
        except: pass

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def main_handler(message: Message):
    uid, pid = message.from_id, message.peer_id
    if uid < 0: return

    # --- СОБЫТИЯ (Вход/Выход) ---
    if message.action:
        if message.action.type in ["chat_invite_user", "chat_invite_user_by_link"]:
            target_id = message.action.member_id or uid
            bot_info = await bot.api.groups.get_by_id()
            if target_id == -bot_info[0].id:
                await message.answer("Приветствую тебя, friend! Спасибо за то, что добавил меня! FLEX чат-менеджер – к твоим услугам. Для того, чтобы узнать свои возможности, пропиши команду /help")
                return
            cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (target_id, pid))
            if cursor.fetchone():
                try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target_id)
                except: pass
        
        elif message.action.type in ["chat_kick_user"]:
            target_id = message.action.member_id or uid
            cursor.execute("DELETE FROM users WHERE user_id=%s AND peer_id=%s", (target_id, pid))
            conn.commit()
        return

    # Регистрация
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
    conn.commit()

    if not message.text: return
    raw_text = message.text.replace("!", "/")
    if not raw_text.startswith("/"): return
    parts = raw_text.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]

    u_role, _, u_nick = get_user_data(uid, pid)
    min_req = await get_min_role(pid, cmd)

    # 1. SYSROLE (Разработчик)
    if cmd == "sysrole":
        if uid != OWNER_ID: return
        target = extract_id(message)
        if target and args:
            try:
                new_lvl = int(args[-1])
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, new_lvl, new_lvl))
                conn.commit()
                await message.answer(f"✅ Системная роль {new_lvl} выдана [id{target}|пользователю].")
            except: pass
        return

    # 2. ПРОВЕРКА ПРАВ
    if u_role < min_req and uid != OWNER_ID: return

    # --- КОМАНДЫ ---
    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if m.is_admin and getattr(m, 'is_owner', False):
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ FLEX активирован. [id{m.member_id}|Владелец] получил роль 100.")
                break

    elif cmd == "help":
        # Списки команд по категориям
        user_cmds = {"snick": "свой ник", "stats": "статистика", "warns": "свои варны", "help": "помощь"}
        mod_cmds = {"kick": "кик", "ban": "бан", "mute": "мут", "warn": "варн", "unban": "разбан", "unmute": "размут", "unwarn": "снять варн", "rnick": "удалить чужой ник", "staff": "список админов"}
        admin_cmds = {"setrole": "выдать роль", "newrole": "назвать роль", "setcmd": "доступ команд", "delnicks": "очистка вышедших"}

        sections = []

        # Формируем список доступных команд пользователя
        avail_user = [f"• /{c} — {d}" for c, d in user_cmds.items() if u_role >= await get_min_role(pid, c) or uid == OWNER_ID]
        if avail_user: sections.append("📖 ДОСТУПНО ТЕБЕ:\n" + "\n".join(avail_user))

        avail_mod = [f"• /{c} — {d}" for c, d in mod_cmds.items() if u_role >= await get_min_role(pid, c) or uid == OWNER_ID]
        if avail_mod: sections.append("👮 МОДЕРАЦИЯ:\n" + "\n".join(avail_mod))

        avail_adm = [f"• /{c} — {d}" for c, d in admin_cmds.items() if u_role >= await get_min_role(pid, c) or uid == OWNER_ID]
        if avail_adm: sections.append("👑 УПРАВЛЕНИЕ:\n" + "\n".join(avail_adm))

        if uid == OWNER_ID:
            sections.append("🛠 DEV:\n• /sysrole [id] [lvl] — выдать роль везде")

        await message.answer("\n\n".join(sections))

    elif cmd == "snick":
        nick = " ".join(args)
        if not nick: return await message.answer("📌 Шаблон: /snick [ник]")
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s AND peer_id=%s", (nick[:20], uid, pid))
        conn.commit()
        await message.answer(f"✅ Твой ник изменен на: {nick[:20]}")

    elif cmd == "rnick":
        target = extract_id(message)
        if not target: return await message.answer("📌 Шаблон: /rnick [id/reply]")
        cursor.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
        conn.commit()
        await message.answer("✅ Ник пользователя удален.")

    elif cmd == "delnicks":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        present_ids = [m.member_id for m in members.items]
        cursor.execute("DELETE FROM users WHERE peer_id=%s AND user_id != ALL(%s)", (pid, present_ids))
        conn.commit()
        await message.answer("✅ Данные вышедших участников удалены из базы.")

    elif cmd == "stats":
        target = extract_id(message) or uid
        r, m, n = get_user_data(target, pid)
        await message.answer(f"📊 [id{target}|Статистика]:\n🎭 Ник: {n or 'отсутствует'}\n✉ Сообщений: {m}\n⭐ Роль: {get_role_title(pid, r)}")

    elif cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        rows = cursor.fetchall()
        res = "👥 Персонал беседы:\n" + "\n".join([f"• {get_role_title(pid, r[1])}: [id{r[0]}|профиль]" for r in rows])
        await message.answer(res if rows else "Администрация не назначена.")

    elif cmd in ["ban", "mute", "warn"]:
        target = extract_id(message)
        if not target: return await message.answer("📌 Шаблон: /команда [время] [причина]")
        dur, res = "навсегда", "не указана"
        if len(args) >= 2 and re.match(r"^\d+[smhdwy]?$", args[0]):
            dur, res = args[0], " ".join(args[1:])
        elif len(args) >= 1: res = " ".join(args)
        end_at = parse_time(dur)
        cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, %s, %s)", (target, pid, cmd, end_at))
        conn.commit()
        if cmd == "ban":
            try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            except: pass
        await message.answer(f"✅ {cmd.upper()} для [id{target}|пользователя].\n⏳ Срок: {dur}\n📝 Причина: {res}")

    elif cmd == "setrole":
        if u_role < 100 and uid != OWNER_ID: return
        target = extract_id(message)
        if target and args:
            try:
                new_lvl = int(args[-1])
                if new_lvl >= 100 and uid != OWNER_ID: return
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, new_lvl, new_lvl))
                conn.commit()
                await message.answer(f"✅ Роль {new_lvl} выдана.")
            except: pass

    elif cmd == "setcmd":
        if u_role < 100 and uid != OWNER_ID: return
        if len(args) >= 2:
            try:
                c_name, lvl = args[0].replace("/", ""), int(args[1])
                cursor.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, min_role) VALUES (%s, %s, %s) ON CONFLICT(peer_id, cmd_name) DO UPDATE SET min_role=EXCLUDED.min_role", (pid, c_name, lvl))
                conn.commit()
                await message.answer(f"✅ Команда /{c_name} теперь доступна с уровня {lvl}")
            except: pass

    elif cmd == "warns":
        target = extract_id(message) or uid
        cursor.execute("SELECT count(*) FROM punishments WHERE user_id=%s AND peer_id=%s AND type='warn'", (target, pid))
        await message.answer(f"⚠ У [id{target}|пользователя] {cursor.fetchone()[0]}/3 варнов.")

    elif cmd in ["unban", "unmute", "unwarn"]:
        target = extract_id(message)
        if not target: return
        t_type = cmd[2:]
        cursor.execute(f"DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='{t_type}'", (target, pid))
        conn.commit()
        await message.answer(f"✅ Наказание ({t_type}) снято.")

# =========================
# RUN
# =========================
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(punishment_scheduler())
    bot.run_forever()
