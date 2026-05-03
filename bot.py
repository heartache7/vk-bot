import os
import re
import psycopg2
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
    # Таблица пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, PRIMARY KEY (user_id, peer_id));
    """)
    # Таблица названий ролей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles_titles (
        peer_id BIGINT, role_lvl INT, title TEXT,
        PRIMARY KEY (peer_id, role_lvl));
    """)
    # Таблица прав доступа к командам
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cmd_permissions (
        peer_id BIGINT, cmd_name TEXT, min_lvl INT,
        PRIMARY KEY (peer_id, cmd_name));
    """)
    # Проверка колонки nickname (фикс ошибки из логов)
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='nickname';")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE users ADD COLUMN nickname TEXT;")
    conn.commit()

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
    # Пытаемся достать из базы, если нет — берем стандарт
    cursor.execute("SELECT min_lvl FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (pid, cmd))
    res = cursor.fetchone()
    if res: return res[0]
    
    defaults = {
        "kick": 20, "ban": 60, "setrole": 100, "newrole": 100, 
        "setcmd": 100, "snick": 0, "stats": 0, "staff": 0
    }
    return defaults.get(cmd, 0)

def get_role_title(pid, lvl):
    if lvl >= 100: return "Создатель"
    cursor.execute("SELECT title FROM roles_titles WHERE peer_id=%s AND role_lvl=%s", (pid, lvl))
    res = cursor.fetchone()
    if res: return res[0]
    defaults = {80: "Гл. Админ", 60: "Админ", 20: "Модератор", 0: "Пользователь"}
    return defaults.get(lvl, f"Ранг {lvl}")

async def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"\[id(\d+)\|.*?\]|id(\d+)|vk\.com/([\w\.]+)", message.text)
    if not target: return None
    raw_id = target.group(1) or target.group(2)
    if raw_id: return int(raw_id)
    domain = target.group(3)
    res = await bot.api.utils.resolve_screen_name(screen_name=domain)
    return res.object_id if res and res.type.value == "user" else None

# =========================
# HANDLERS
# =========================

# Приветствие
@bot.on.chat_message(ChatActionRule(["chat_invite_user", "chat_invite_user_by_link"]))
async def welcome(message: Message):
    tid = message.action.member_id or message.from_id
    await message.answer(f"👋 Приветствуем, [id{tid}|нового участника]! Напиши /help.")

@bot.on.message()
async def main_handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    try:
        cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
        conn.commit()
    except: conn.rollback()

    text = message.text.replace("!", "/")
    if not text.startswith("/"): return
    parts = text.split()
    cmd, args = parts[0][1:].lower(), parts[1:]

    u_role, _, _ = get_user_data(uid, pid)
    min_req = await get_min_role(pid, cmd)
    if u_role < min_req and uid != OWNER_ID: return

    # --- КОМАНДЫ ---

    if cmd == "start":
        members = await bot.api.messages.get_conversation_members(peer_id=pid)
        for m in members.items:
            if getattr(m, 'is_owner', False):
                cursor.execute("UPDATE users SET role=100 WHERE user_id=%s AND peer_id=%s", (m.member_id, pid))
                conn.commit()
                await message.answer(f"✅ Владелец [id{m.member_id}|назначен].")
        return

    if cmd == "help":
        await message.answer(
            "📖 Команды чата:\n"
            "👤 /stats, /snick, /staff\n"
            "👮 /kick, /ban\n"
            "👑 /setrole, /newrole, /setcmd"
        )
        return

    if cmd == "newrole":
        if len(args) < 2: return await message.answer("📌 /newrole [lvl] [название]")
        try:
            lvl, name = int(args[0]), " ".join(args[1:])
            cursor.execute("INSERT INTO roles_titles (peer_id, role_lvl, title) VALUES (%s, %s, %s) ON CONFLICT (peer_id, role_lvl) DO UPDATE SET title=%s", (pid, lvl, name, name))
            conn.commit()
            await message.answer(f"✅ Роль уровня {lvl} названа: {name}")
        except: await message.answer("❌ Ошибка в данных.")
        return

    if cmd == "setcmd":
        if len(args) < 2: return await message.answer("📌 /setcmd [команда] [lvl]")
        try:
            c_name, c_lvl = args[0].lower(), int(args[1])
            cursor.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, min_lvl) VALUES (%s, %s, %s) ON CONFLICT (peer_id, cmd_name) DO UPDATE SET min_lvl=%s", (pid, c_name, c_lvl, c_lvl))
            conn.commit()
            await message.answer(f"✅ Команда /{c_name} доступна с ранга {c_lvl}.")
        except: await message.answer("❌ Ошибка!")
        return

    if cmd == "stats":
        target = await extract_id(message) or uid
        r, m, n = get_user_data(target, pid)
        await message.answer(f"📊 [id{target}|Профиль]:\n🎭 Ник: {n or 'Нет'}\n✉ Сообщений: {m}\n⭐ Роль: {get_role_title(pid, r)}")
        return

    if cmd == "setrole":
        target = await extract_id(message)
        if not target or not args: return await message.answer("📌 /setrole [ссылка] [lvl]")
        try:
            lvl = int(args[-1])
            cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (lvl, target, pid))
            conn.commit()
            await message.answer(f"✅ Установлен ранг {lvl} для [id{target}|него].")
        except: await message.answer("❌ Ошибка!")
        return

if __name__ == "__main__":
    bot.run_forever()
