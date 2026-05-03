import os
import re
import asyncio
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message
from vkbottle import GroupTypes

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
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='nickname';")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE users ADD COLUMN nickname TEXT;")
    conn.commit()
    print(">>> БАЗА ДАННЫХ ПРОВЕРЕНА И ГОТОВА")

init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    try:
        cursor.execute("SELECT role, msgs, nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
        res = cursor.fetchone()
        return res if res else (0, 0, None)
    except:
        conn.rollback()
        return (0, 0, None)

async def get_min_role(pid, cmd):
    levels = {
        "kick": 20, "ban": 60, "mute": 20, "warn": 20,
        "unban": 60, "unmute": 20, "unwarn": 20,
        "rnick": 40, "staff": 0, "stats": 0,
        "snick": 0, "setrole": 100, "help": 0, "start": 0
    }
    return levels.get(cmd, 0)

def get_role_title(lvl):
    if lvl >= 100: return "Создатель"
    defaults = {80: "Гл. Админ", 60: "Админ", 40: "Ст. Модератор", 20: "Модератор", 0: "Пользователь"}
    return defaults.get(lvl, f"Ранг {lvl}")

def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"id(\d+)", message.text) if message.text else None
    return int(target.group(1)) if target else None

# =========================
# HANDLERS
# =========================

# ПРИВЕТСТВИЕ И СИСТЕМНЫЕ ДЕЙСТВИЯ
@bot.on.chat_message(func=lambda message: message.action is not None)
async def action_handler(message: Message):
    pid = message.peer_id
    action = message.action
    target_id = action.member_id or message.from_id
    
    group_info = await bot.api.groups.get_by_id()
    bot_id = -group_info[0].id

    if action.type in [GroupTypes.CHAT_INVITE_USER, GroupTypes.CHAT_INVITE_USER_BY_LINK]:
        if target_id == bot_id:
            await message.answer("👋 FLEX Чат-менеджер добавлен! Выдайте мне права админа и напишите /start.")
        else:
            await message.answer(f"👋 Приветствуем, [id{target_id}|нового участника]! Напиши /help для ознакомления.")
    
    elif action.type == GroupTypes.CHAT_KICK_USER:
        cursor.execute("DELETE FROM users WHERE user_id=%s AND peer_id=%s", (target_id, pid))
        conn.commit()

# ОБРАБОТКА ТЕКСТОВЫХ КОМАНД
@bot.on.message()
async def message_handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    # Логирование активности
    try:
        cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
        conn.commit()
    except:
        conn.rollback()

    text = message.text.replace("!", "/")
    if not text.startswith("/"): return
    
    parts = text.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]

    u_role, _, _ = get_user_data(uid, pid)
    min_req = await get_min_role(pid, cmd)
    
    if u_role < min_req and uid != OWNER_ID: return

    # --- КОМАНДЫ ---

    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if getattr(m, 'is_owner', False):
                cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")
        return

    elif cmd == "help":
        res = "📖 Команды:\n👤 /stats, /snick, /staff\n"
        if u_role >= 20: res += "👮 /kick, /mute, /warn, /unmute, /rnick\n"
        if u_role >= 60: res += "👑 /ban, /unban, /setrole"
        await message.answer(res)

    elif cmd == "snick":
        target_id = extract_id(message)
        if target_id and target_id != uid:
            t_role, _, _ = get_user_data(target_id, pid)
            if u_role <= t_role and uid != OWNER_ID:
                return await message.answer("❌ Ранг цели выше вашего.")
            new_nick = " ".join([p for p in args if "id" not in p and "[" not in p])
        else:
            target_id, new_nick = uid, " ".join(args)

        if not new_nick: return await message.answer("📌 Инструкция: /snick [ваш ник] или /snick @ссылка [ник]")
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s AND peer_id=%s", (new_nick[:20], target_id, pid))
        conn.commit()
        await message.answer(f"✅ Ник изменен на: {new_nick[:20]}")

    elif cmd in ["kick", "ban", "mute", "warn"]:
        target = extract_id(message)
        if not target: 
            return await message.answer(f"📌 Инструкция: ответьте на сообщение человека или укажите его через @ссылку: /{cmd} [причина]")
        
        t_role, _, _ = get_user_data(target, pid)
        if u_role <= t_role and uid != OWNER_ID: 
            return await message.answer("❌ Нельзя применять команду к равному или старшему по рангу.")

        if cmd == "kick":
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
        elif cmd == "ban":
            cursor.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, 'ban')", (target, pid))
            conn.commit()
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
        
        await message.answer(f"✅ Команда /{cmd} выполнена для [id{target}|пользователя].")

    elif cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        data = cursor.fetchall()
        res = "👮 Администрация чата:\n" + "\n".join([f"• [id{s[0]}|{get_role_title(s[1])}]" for s in data])
        await message.answer(res)

if __name__ == "__main__":
    bot.run_forever()
