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
    # Создаем таблицы, если их нет
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
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT, cmd_name TEXT, min_role INT,
        PRIMARY KEY (peer_id, cmd_name));
    """)
    
    # ФИКС ОШИБКИ ИЗ ЛОГОВ: Проверяем наличие колонки nickname
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='nickname';")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE users ADD COLUMN nickname TEXT;")
        print(">>> ДОБАВЛЕНА КОЛОНКА NICKNAME")
    
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
        conn.rollback() # Откат, если транзакция прервана
        return (0, 0, None)

async def get_min_role(pid, cmd):
    cursor.execute("SELECT min_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res is not None: return res[0]
    admin_cmds = ["kick", "ban", "mute", "warn", "unban", "rnick", "delnicks", "setrole", "setcmd"]
    return 20 if cmd in admin_cmds else 0

def get_role_title(pid, lvl):
    if lvl >= 100: return "Владелец"
    defaults = {80: "Гл. Админ", 60: "Админ", 40: "Модератор", 0: "Пользователь"}
    return defaults.get(lvl, f"Уровень {lvl}")

def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"id(\d+)", message.text) if message.text else None
    return int(target.group(1)) if target else None

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def main_handler(message: Message):
    try:
        # Простейшая защита от системных ошибок vkbottle
        if not hasattr(message, "peer_id") or message.peer_id is None:
            return
            
        pid = message.peer_id

        # --- 1. ПРИВЕТСТВИЕ И СОБЫТИЯ ---
        if message.action:
            target_id = message.action.member_id or message.from_id
            if message.action.type in ["chat_invite_user", "chat_invite_user_by_link"]:
                await message.answer(f"👋 Приветствуем, [id{target_id}|нового участника]! Напиши /help для ознакомления.")
                return
            if message.action.type == "chat_kick_user":
                cursor.execute("DELETE FROM users WHERE user_id=%s AND peer_id=%s", (target_id, pid))
                conn.commit()
                return

        # --- 2. ОБРАБОТКА КОМАНД ---
        uid = message.from_id
        if uid <= 0: return 

        # Регистрация (защита от сломанных транзакций)
        try:
            cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
            conn.commit()
        except:
            conn.rollback()

        if not message.text: return
        text = message.text.replace("!", "/")
        if not text.startswith("/"): return

        parts = text.split()
        cmd = parts[0][1:].lower()
        args = parts[1:]

        u_role, _, _ = get_user_data(uid, pid)
        min_req = await get_min_role(pid, cmd)

        if u_role < min_req and uid != OWNER_ID: return

        # КОМАНДЫ
        if cmd == "start":
            members = await bot.api.messages.get_conversation_members(peer_id=pid)
            for m in members.items:
                if getattr(m, 'is_owner', False):
                    cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, 100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, pid))
                    conn.commit()
                    await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")
            return

        elif cmd == "snick":
            target_id = extract_id(message)
            if target_id and target_id != uid:
                t_role, _, _ = get_user_data(target_id, pid)
                if u_role <= t_role and uid != OWNER_ID:
                    return await message.answer("❌ Недостаточно прав для смены чужого ника.")
                new_nick = " ".join([p for p in args if "id" not in p])
            else:
                target_id = uid
                new_nick = " ".join(args)

            if not new_nick: return await message.answer("📌 Напишите ник!")
            cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s AND peer_id=%s", (new_nick[:20], target_id, pid))
            conn.commit()
            await message.answer(f"✅ Ник изменен на: {new_nick[:20]}")

        elif cmd == "help":
            await message.answer("📖 Команды:\n/stats - твоя стата\n/snick [ник] - сменить ник\n/setrole [id] [уровень] - выдать ранг")

        elif cmd == "stats":
            r, m, n = get_user_data(uid, pid)
            await message.answer(f"📊 Статистика:\nНик: {n or 'нет'}\nРоль: {get_role_title(pid, r)}")

        elif cmd == "setrole":
            target = extract_id(message)
            if target and args:
                lvl = int(args[-1])
                cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (lvl, target, pid))
                conn.commit()
                await message.answer(f"✅ Роль {lvl} выдана.")

    except Exception as e:
        print(f"Ошибка: {e}")
        conn.rollback()

if __name__ == "__main__":
    bot.run_forever()
