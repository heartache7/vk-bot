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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0, msgs INT DEFAULT 0,
        nickname TEXT, PRIMARY KEY (user_id, peer_id));
    """)
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

def get_role_title(lvl):
    if lvl >= 100: return "Создатель"
    defaults = {80: "Гл. Админ", 60: "Админ", 40: "Модератор", 0: "Пользователь"}
    return defaults.get(lvl, f"Ранг {lvl}")

def extract_id(message: Message):
    if message.reply_message: return message.reply_message.from_id
    target = re.search(r"id(\d+)", message.text) if message.text else None
    return int(target.group(1)) if target else None

# =========================
# ПРИВЕТСТВИЕ (ОТДЕЛЬНЫЙ БЛОК)
# =========================
@bot.on.chat_message(ChatActionRule(["chat_invite_user", "chat_invite_user_by_link"]))
async def welcome(message: Message):
    target_id = message.action.member_id or message.from_id
    await message.answer(
        f"👋 Приветствуем, [id{target_id}|нового участника]!\n"
        "Я — FLEX Чат-менеджер. Напиши /help, чтобы увидеть список команд."
    )

# =========================
# КОМАНДЫ
# =========================
@bot.on.message()
async def main_handler(message: Message):
    if not message.text or message.from_id <= 0: return
    uid, pid = message.from_id, message.peer_id

    # Регистрация
    try:
        cursor.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s, %s, 1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1", (uid, pid))
        conn.commit()
    except: conn.rollback()

    text = message.text.replace("!", "/")
    if not text.startswith("/"): return
    
    parts = text.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]

    u_role, _, _ = get_user_data(uid, pid)

    # --- ЛОГИКА ---
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
            "📖 Команды менеджера:\n"
            "👤 /stats — твоя статистика\n"
            "👤 /snick [ник] — сменить ник\n"
            "👤 /staff — список админов\n"
            "👮 /kick [ссылка/реплай] — исключить\n"
            "👮 /ban [ссылка/реплай] — забанить\n"
            "👑 /setrole [ссылка] [уровень] — выдать ранг"
        )
        return

    if cmd == "stats":
        target = extract_id(message) or uid
        r, m, n = get_user_data(target, pid)
        await message.answer(f"📊 [id{target}|Профиль]:\n🎭 Ник: {n or 'Нет'}\n✉ Сообщений: {m}\n⭐ Роль: {get_role_title(r)}")
        return

    if cmd == "snick":
        new_nick = " ".join(args)
        if not new_nick: return await message.answer("📌 Ошибка! Напиши: /snick [твой новый ник]")
        cursor.execute("UPDATE users SET nickname=%s WHERE user_id=%s AND peer_id=%s", (new_nick[:20], uid, pid))
        conn.commit()
        await message.answer(f"✅ Твой ник изменен на: {new_nick[:20]}")
        return

    if cmd == "staff":
        cursor.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role > 0 ORDER BY role DESC", (pid,))
        data = cursor.fetchall()
        res = "👮 Администрация чата:\n" + "\n".join([f"• [id{s[0]}|{get_role_title(s[1])}]" for s in data])
        await message.answer(res)
        return

    # Команды модерации (нужен ранг 20+)
    if cmd in ["kick", "ban"]:
        if u_role < 20 and uid != OWNER_ID: return
        target = extract_id(message)
        if not target: return await message.answer(f"📌 Ошибка! Чтобы использовать /{cmd}, ответь на сообщение цели или прикрепи ссылку @id...")
        
        t_role, _, _ = get_user_data(target, pid)
        if u_role <= t_role and uid != OWNER_ID: return await message.answer("❌ Ошибка! Нельзя наказать того, кто выше или равен тебе по рангу.")

        try:
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
            await message.answer(f"✅ Пользователь [id{target}|исключен] командой {cmd}.")
        except Exception as e:
            await message.answer(f"❌ Ошибка API: Проверьте, есть ли у бота права админа.")
        return

    if cmd == "setrole":
        if u_role < 100 and uid != OWNER_ID: return
        target = extract_id(message)
        if not target or len(args) < 1: return await message.answer("📌 Ошибка! Напиши: /setrole @ссылка [число уровня]")
        
        try:
            lvl = int(args[-1])
            cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (target, pid, lvl, lvl))
            conn.commit()
            await message.answer(f"✅ Ранг {lvl} ({get_role_title(lvl)}) выдан [id{target}|пользователю].")
        except: await message.answer("❌ Ошибка! Уровень роли должен быть числом.")

if __name__ == "__main__":
    bot.run_forever()
