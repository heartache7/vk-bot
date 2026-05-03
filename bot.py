import os
import re
import psycopg2
from vkbottle.bot import Bot, Message

# =========================
# CONFIG
# =========================
# OWNER_ID остается в коде, это безопасно
OWNER_ID = 676081199

# Берем данные из переменных окружения Railway
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not VK_TOKEN:
    print("ОШИБКА: Переменная VK_TOKEN не найдена в Railway Variables!")
    exit(1)

bot = Bot(token=VK_TOKEN)

# =========================
# DATABASE INIT
# =========================
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        user_id BIGINT,
        peer_id BIGINT,
        role INT DEFAULT 0,
        PRIMARY KEY (user_id, peer_id)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS punishments (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        peer_id BIGINT,
        type TEXT
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity (
        user_id BIGINT,
        peer_id BIGINT,
        messages INT DEFAULT 0,
        PRIMARY KEY (user_id, peer_id)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS command_access (
        peer_id BIGINT,
        command TEXT,
        min_role INT DEFAULT 0,
        PRIMARY KEY (peer_id, command)
    );
    """)
    conn.commit()

init_db()

# =========================
# HELPERS
# =========================
def parse_cmd(text):
    parts = text.split()
    if not parts: return "", []
    return parts[0].lower(), parts[1:]

def extract_user(message: Message, args):
    if message.reply_message:
        return message.reply_message.from_id
    if args:
        m = re.search(r"id(\d+)", args[0])
        if m: return int(m.group(1))
        if args[0].isdigit(): return int(args[0])
    return None

def get_role(uid, peer):
    if uid == OWNER_ID: return 100
    cursor.execute("SELECT role FROM roles WHERE user_id=%s AND peer_id=%s", (uid, peer))
    r = cursor.fetchone()
    return r[0] if r else 0

def set_role(uid, peer, role):
    cursor.execute("""
        INSERT INTO roles (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=EXCLUDED.role
    """, (uid, peer, role))
    conn.commit()

def can_use(uid, peer, cmd):
    if uid == OWNER_ID: return True
    cursor.execute("SELECT min_role FROM command_access WHERE peer_id=%s AND command=%s", (peer, cmd))
    r = cursor.fetchone()
    need = r[0] if r else 0
    return get_role(uid, peer) >= need

async def kick_user(peer_id, user_id):
    # Метод работает только в беседах (peer_id > 2e9)
    if peer_id > 2000000000:
        try:
            await bot.api.messages.remove_chat_user(
                chat_id=peer_id - 2000000000,
                user_id=user_id
            )
        except Exception as e:
            print(f"Ошибка при кике: {e}")

# =========================
# HANDLER
# =========================
@bot.on.message()
async def handler(message: Message):
    if not message.text: return

    # Логика подсчета сообщений
    if message.peer_id > 2000000000:
        cursor.execute("""
            INSERT INTO activity (user_id, peer_id, messages)
            VALUES (%s, %s, 1)
            ON CONFLICT (user_id, peer_id)
            DO UPDATE SET messages = activity.messages + 1
        """, (message.from_id, message.peer_id))
        conn.commit()

    cmd, args = parse_cmd(message.text)

    # --- Команды ---
    if cmd == "/start":
        await message.answer("✅ Бот запущен и готов к работе!")

    elif cmd == "/help":
        await message.answer(
            "📋 Список команд:\n"
            "/stats - твоя статистика\n"
            "/ban [ID/reply] - бан (нужны права)\n"
            "/kick [ID/reply] - кик\n"
            "/banlist - список забаненных\n"
            "/sysrole [ID] [число] - выдать роль (Owner)"
        )

    elif cmd == "/sysrole":
        if message.from_id == OWNER_ID:
            uid = extract_user(message, args)
            if uid and len(args) >= 1:
                try:
                    role = int(args[-1])
                    set_role(uid, message.peer_id, role)
                    await message.answer(f"💾 Для пользователя {uid} установлена роль {role}")
                except ValueError:
                    await message.answer("⚠ Ошибка: роль должна быть числом")

    elif cmd == "/ban":
        if can_use(message.from_id, message.peer_id, "ban"):
            uid = extract_user(message, args)
            if uid:
                cursor.execute("INSERT INTO punishments (user_id, peer_id, type) VALUES (%s, %s, 'ban')", (uid, message.peer_id))
                conn.commit()
                await kick_user(message.peer_id, uid)
                await message.answer(f"🚫 Пользователь {uid} забанен.")

    elif cmd == "/kick":
        if can_use(message.from_id, message.peer_id, "kick"):
            uid = extract_user(message, args)
            if uid:
                await kick_user(message.peer_id, uid)
                await message.answer(f"👢 Пользователь {uid} исключен.")

    elif cmd == "/stats":
        uid = extract_user(message, args) or message.from_id
        cursor.execute("SELECT messages FROM activity WHERE user_id=%s AND peer_id=%s", (uid, message.peer_id))
        res = cursor.fetchone()
        msgs = res[0] if res else 0
        role = get_role(uid, message.peer_id)
        await message.answer(f"📊 Статистика {uid}:\n⭐ Роль: {role}\n✉ Сообщений: {msgs}")

    elif cmd == "/banlist":
        cursor.execute("SELECT user_id FROM punishments WHERE peer_id=%s AND type='ban'", (message.peer_id,))
        rows = cursor.fetchall()
        if not rows:
            await message.answer("😇 Список банов пуст.")
        else:
            text = "🚫 Список забаненных:\n" + "\n".join([f"• id{r[0]}" for r in rows])
            await message.answer(text)

# =========================
# RUN
# =========================
if __name__ == "__main__":
    print("Бот запущен...")
    bot.run_forever()
