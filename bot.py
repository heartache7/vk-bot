import os
import re
import psycopg2
from datetime import datetime
from vkbottle.bot import Bot, Message

# =========================
# CONFIG
# =========================
OWNER_ID = 676081199
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DATABASE
# =========================
# Подключаемся к БД
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def init_db():
    # ВАЖНО: Эта строка лечит твою ошибку "column peer_id does not exist"
    # Она удалит старую кривую таблицу и создаст новую.
    # ПОСЛЕ ПЕРВОГО ЗАПУСКА ЭТУ СТРОКУ НУЖНО УДАЛИТЬ!
    cursor.execute("DROP TABLE IF EXISTS users CASCADE;") 
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT, 
        peer_id BIGINT, 
        role INT DEFAULT 0, 
        msgs INT DEFAULT 0,
        invited_by BIGINT,
        invited_at TIMESTAMP,
        last_msg_at TIMESTAMP,
        PRIMARY KEY (user_id, peer_id));
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT, type TEXT);
    """)
    conn.commit()
    print(">>> БАЗА ДАННЫХ ПЕРЕСОЗДАНА И ГОТОВА")

init_db()

# =========================
# HELPERS
# =========================
def get_user_data(uid, pid):
    cursor.execute("SELECT role, msgs, invited_by, invited_at, last_msg_at FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
    res = cursor.fetchone()
    if not res:
        return (0, 0, 0, None, None)
    role = 100 if uid == OWNER_ID else res[0]
    return (role, res[1], res[2], res[3], res[4])

def extract_id(message: Message):
    if message.reply_message:
        return message.reply_message.from_id
    target = re.search(r"id(\d+)", message.text)
    if target: return int(target.group(1))
    args = message.text.split()
    for arg in args:
        if arg.isdigit(): return int(arg)
    return None

# =========================
# EVENTS
# =========================
@bot.on.raw_event("message_new", dataclass=Message)
async def invitation_handler(message: Message):
    if message.action and message.action.type == "chat_invite_user":
        target_id = message.action.member_id
        inviter_id = message.from_id
        now = datetime.now()
        
        cursor.execute("""
            INSERT INTO users (user_id, peer_id, invited_by, invited_at) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, peer_id) 
            DO UPDATE SET invited_by=EXCLUDED.invited_by, invited_at=EXCLUDED.invited_at
        """, (target_id, message.peer_id, inviter_id, now))
        conn.commit()

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(message: Message):
    if not message.text or message.from_id < 0: return

    uid, pid = message.from_id, message.peer_id
    now = datetime.now()
    
    # 1. Запись активности
    cursor.execute("""
        INSERT INTO users (user_id, peer_id, msgs, last_msg_at) VALUES (%s, %s, 1, %s)
        ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs = users.msgs + 1, last_msg_at = %s
    """, (uid, pid, now, now))
    conn.commit()

    # 2. Парсинг команды
    text = message.text.replace("!", "/").lower()
    args = text.split()
    cmd = args[0] if args else ""

    u_role, _, _, _, _ = get_user_data(uid, pid)

    # --- КОМАНДЫ ДЛЯ ВСЕХ ---
    if cmd == "/start":
        await message.answer("✅ Бот запущен. Напиши /help для списка команд.")

    elif cmd == "/help":
        help_text = "🔧 Доступные команды:\n/stats [id] — статистика"
        if u_role >= 1 or uid == OWNER_ID:
            help_text += "\n\n👮 Админ-команды:\n/kick, /ban, /warn, /mute, /banlist"
        if uid == OWNER_ID:
            help_text += "\n👑 /sysrole [id] [role]"
        await message.answer(help_text)

    elif cmd == "/stats":
        target = extract_id(message) or uid
        t_role, t_msgs, t_inviter, t_at, t_last = get_user_data(target, pid)
        
        inv_by = f"[id{t_inviter}|Пользователем]" if t_inviter else "Неизвестно"
        date_inv = t_at.strftime('%d.%m.%Y %H:%M') if t_at else "Нет данных"
        last_act = t_last.strftime('%d.%m.%Y %H:%M') if t_last else "Только что"

        await message.answer(
            f"📊 Статистика [id{target}|пользователя]:\n\n"
            f"✉ Сообщений: {t_msgs}\n"
            f"📅 Приглашён: {date_inv}\n"
            f"👤 Кем: {inv_by}\n"
            f"🕒 Последний актив: {last_act}\n"
            f"⭐ Приоритет: {t_role}"
        )

    # --- КОМАНДЫ ДЛЯ АДМИНОВ ---
    elif cmd in ["/kick", "/ban", "/warn", "/mute", "/banlist", "/sysrole"]:
        if u_role < 1 and uid != OWNER_ID: return 

        if cmd == "/sysrole":
            if uid != OWNER_ID: return
            target = extract_id(message)
            if target and len(args) > 1:
                try:
                    new_role = int(args[-1])
                    cursor.execute("UPDATE users SET role=%s WHERE user_id=%s AND peer_id=%s", (new_role, target, pid))
                    conn.commit()
                    await message.answer(f"✅ Установлен приоритет {new_role} для [id{target}|пользователя]")
                except: pass
            return

        target = extract_id(message)
        if not target and cmd != "/banlist":
            await message.answer("⚠ Укажите ID или ответьте на сообщение.")
            return

        if cmd == "/kick":
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                await message.answer("✅ Исключен.")
            except: await message.answer("❌ Ошибка: проверьте мои права админа.")
        
        elif cmd == "/banlist":
            cursor.execute("SELECT DISTINCT user_id FROM punishments WHERE peer_id=%s AND type='ban'", (pid,))
            rows = cursor.fetchall()
            text = "🚫 Баны:\n" + "\n".join([f"• id{r[0]}" for r in rows]) if rows else "Пусто"
            await message.answer(text)

        else: # ban, warn, mute
            cursor.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, %s)", (target, pid, cmd[1:]))
            conn.commit()
            if cmd == "/ban":
                try: await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=target)
                except: pass
            await message.answer(f"✅ Команда {cmd} выполнена.")

if __name__ == "__main__":
    bot.run_forever()
