import os
import re
import asyncio
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import ChatActionRule

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
    # Пользователи
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, warn_count INT DEFAULT 0, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    # Наказания (бан/мут)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, 
        type TEXT, end_at TIMESTAMP);
    """)
    # Названия ролей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles_titles (
        peer_id BIGINT, role_lvl INT, title TEXT,
        PRIMARY KEY (peer_id, role_lvl));
    """)
    # Права команд
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT, cmd_name TEXT, min_lvl INT,
        PRIMARY KEY (peer_id, cmd_name));
    """)
    # Правила чатов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_rules (
        peer_id BIGINT PRIMARY KEY, rules_text TEXT);
    """)
    conn.commit()

init_db()

# =========================
# MAINTENANCE TASK
# =========================
async def maintenance_task():
    while True:
        try:
            now = datetime.now()
            cursor.execute("DELETE FROM punishments WHERE end_at <= %s", (now,))
            # Очистка неактивных (30 дней) с 0 рангом
            cursor.execute("DELETE FROM users WHERE last_seen < %s AND role = 0", (now - timedelta(days=30),))
            conn.commit()
        except: conn.rollback()
        await asyncio.sleep(60)

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs, nickname, warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    return res if res else (0, 0, None, 0)

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res: return res[0]
    defaults = {
        "kick": 20, "ban": 60, "mute": 20, "warn": 20, "unban": 60, 
        "unwarn": 20, "setrole": 100, "newrole": 100, "setrules": 100, "rnick": 40
    }
    return defaults.get(cmd, 0)

def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец чата"
    cursor.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cursor.fetchone()
    return res[0] if res else {80: "Гл. Админ", 60: "Админ", 20: "Модератор", 0: "Пользователь"}.get(lvl, f"Ранг {lvl}")

def parse_time(time_str):
    n = int(re.search(r'\d+', time_str).group()) if re.search(r'\d+', time_str) else 1
    if 'm' in time_str: return timedelta(minutes=n)
    if 'h' in time_str: return timedelta(hours=n)
    if 'd' in time_str: return timedelta(days=n)
    return timedelta(hours=n)

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"\[id(\d+)\|.*?\]|id(\d+)|vk\.com/([\w\.]+)", message.text)
    if not target: return None
    if target.group(1) or target.group(2): return int(target.group(1) or target.group(2))
    res = await bot.api.utils.resolve_screen_name(screen_name=target.group(3))
    return res.object_id if res else None

# =========================
# HANDLERS
# =========================

@bot.on.chat_message(ChatActionRule(["chat_invite_user", "chat_invite_user_by_link"]))
async def welcome(message: Message):
    tid = message.action.member_id or message.from_id
    pid = message.peer_id
    cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (tid, pid))
    if cursor.fetchone():
        try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=tid)
        except: pass
        return
    await message.answer(f"👋 Приветствуем, [id{tid}|нового участника]! Напиши /help.")

@bot.on.message()
async def main_handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    # Проверка МУТА
    cursor.execute("SELECT id FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, pid))
    if cursor.fetchone():
        try: await bot.api.messages.delete(message_ids=[message.id], delete_for_all=True)
        except: pass
        return

    # Лог активности
    cursor.execute("INSERT INTO users (user_id, peer_id, msgs, last_seen) VALUES (%s, %s, 1, CURRENT_TIMESTAMP) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1, last_seen = CURRENT_TIMESTAMP", (uid, pid))
    conn.commit()

    text = message.text.replace("!", "/")
    if not text.startswith("/"): return
    parts = text.split()
    cmd, args = parts[0][1:].lower(), parts[1:]

    u_role, _, u_nick, _ = get_user_data(uid, pid)

    # --- SYSROLE (Владелец Бота) ---
    if cmd == "sysrole" and uid == OWNER_ID:
        target = await extract_id(message)
        if target and args:
            lvl = int(args[-1])
            cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
            conn.commit()
            await message.answer(f"⚡ Системно: [id{target}|пользователю] выдан ранг {lvl}.")
        return

    min_req = await get_min_role(pid, cmd)
    if u_role < min_req and uid != OWNER_ID: return

    # --- КОМАНДЫ ---

    if cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        staff_list = cursor.fetchall()
        if not staff_list: return await message.answer("😶 В этой беседе еще нет администрации.")
        res = "👮 Список администрации:\n"
        for s_id, s_lvl in staff_list:
            res += f"• [id{s_id}|{get_role_title(pid, s_lvl)}]\n"
        await message.answer(res)

    if cmd == "rnick":
        target = await extract_id(message) or uid
        if target != uid and u_role < 40 and uid != OWNER_ID: return
        cursor.execute("UPDATE users SET nickname = NULL WHERE user_id=%s AND peer_id=%s", (target, pid))
        conn.commit()
        await message.answer(f"✅ Никнейм [id{target}|пользователя] был сброшен.")

    if cmd == "rules":
        cursor.execute("SELECT rules_text FROM chat_rules WHERE peer_id=%s", (pid,))
        res = cursor.fetchone()
        await message.answer(f"📜 Правила беседы:\n\n{res[0] if res else 'Правила еще не установлены.'}")

    if cmd == "setrules":
        rules_txt = " ".join(args)
        if not rules_txt: return await message.answer("📌 Напишите текст правил после команды.")
        cursor.execute("INSERT INTO chat_rules (peer_id, rules_text) VALUES (%s, %s) ON CONFLICT (peer_id) DO UPDATE SET rules_text=%s", (pid, rules_txt, rules_txt))
        conn.commit()
        await message.answer("✅ Правила успешно обновлены.")

    if cmd == "warn":
        target = await extract_id(message)
        if not target: return
        cursor.execute("UPDATE users SET warn_count = warn_count + 1 WHERE user_id=%s AND peer_id=%s RETURNING warn_count", (target, pid))
        w = cursor.fetchone()[0]
        if w >= 3:
            cursor.execute("UPDATE users SET warn_count = 0 WHERE user_id=%s AND peer_id=%s", (target, pid))
            cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, 'ban', %s)", (target, pid, datetime.now()+timedelta(days=1)))
            conn.commit()
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            await message.answer(f"⛔ [id{target}|Пользователь] забанен (3/3 варна).")
        else:
            conn.commit()
            await message.answer(f"⚠ [id{target}|Предупреждение] ({w}/3).")

    if cmd in ["ban", "mute"]:
        target = await extract_id(message)
        if not target: return
        duration = parse_time(args[-1] if len(args) > 1 else "1h")
        cursor.execute("INSERT INTO punishments (user_id, peer_id, type, end_at) VALUES (%s, %s, %s, %s)", (target, pid, cmd, datetime.now()+duration))
        conn.commit()
        if cmd == "ban": await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
        await message.answer(f"✅ {cmd.capitalize()} выдан пользователю [id{target}|на {duration}].")

    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if getattr(m, 'is_owner', False):
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")

    if cmd == "newrole":
        if len(args) < 2: return
        lvl, name = int(args[0]), " ".join(args[1:])
        cursor.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT (peer_id, role_lvl) DO UPDATE SET title=%s", (pid, lvl, name, name))
        conn.commit()
        await message.answer(f"✅ Роль {lvl} теперь: {name}")

    if cmd == "setrole":
        target = await extract_id(message)
        if target and args:
            lvl = int(args[-1])
            if lvl >= 100 and uid != OWNER_ID: return
            cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (lvl, target, pid))
            conn.commit()
            await message.answer(f"✅ Ранг {lvl} выдан.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(maintenance_task())
    bot.run_forever()
