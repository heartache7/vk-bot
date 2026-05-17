import os
import re
import traceback
import logging
import asyncio
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message
from vkbottle import API

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OWNER_ID = 676081199
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not VK_TOKEN or not DATABASE_URL:
    raise ValueError("VK_TOKEN and DATABASE_URL must be set in environment variables")

bot = Bot(token=VK_TOKEN)
api = API(token=VK_TOKEN)

def db():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = True
            return conn, conn.cursor()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.error(f"DB connection attempt {attempt + 1} failed: {e}")
            import time
            time.sleep(2)

def init():
    try:
        conn, cur = db()
        try:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id BIGINT, peer_id BIGINT, role INT DEFAULT 0,
                msgs INT DEFAULT 0, nickname TEXT,
                warn_count INT DEFAULT 0, warn_reasons TEXT DEFAULT '',
                PRIMARY KEY (user_id, peer_id)
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS punishments(
                id SERIAL PRIMARY KEY, user_id BIGINT, peer_id BIGINT,
                type TEXT, end_at TIMESTAMP, reason TEXT,
                banned_by BIGINT, created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS roles(
                id SERIAL PRIMARY KEY, peer_id BIGINT,
                role_priority INT, role_name TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, role_priority)
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS cmd_permissions(
                id SERIAL PRIMARY KEY, peer_id BIGINT, cmd_name TEXT,
                required_role INT DEFAULT 10, created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, cmd_name)
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS groups(
                id SERIAL PRIMARY KEY, name TEXT NOT NULL,
                creator_id BIGINT NOT NULL, created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS group_chats(
                group_id INT REFERENCES groups(id) ON DELETE CASCADE,
                peer_id BIGINT, added_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (group_id, peer_id)
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS reports(
                id SERIAL PRIMARY KEY,
                user_id BIGINT, peer_id BIGINT,
                description TEXT,
                status TEXT DEFAULT 'open',
                reply TEXT, replied_by BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS moderation_logs(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT, moderator_id BIGINT, target_id BIGINT,
                action TEXT, reason TEXT, created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_roles(
                user_id BIGINT PRIMARY KEY,
                role_name TEXT, given_by BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS marriages(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                user1_id BIGINT,
                user2_id BIGINT,
                married_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, user1_id),
                UNIQUE(peer_id, user2_id)
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS marriage_proposals(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                from_id BIGINT,
                to_id BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_messages(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                user_id BIGINT,
                message_id BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            );""")

            cur.execute("""
            INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
            VALUES (0, 'log', 70)
            ON CONFLICT (peer_id, cmd_name) DO UPDATE SET required_role = 70
            """)

            cur.execute("""
            INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
            VALUES (0, 'clear', 50)
            ON CONFLICT (peer_id, cmd_name) DO UPDATE SET required_role = 50
            """)

            cur.execute("""
            INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
            VALUES (0, 'giveowner', 100)
            ON CONFLICT (peer_id, cmd_name) DO UPDATE SET required_role = 100
            """)

            default_permissions = [
                ('warn', 20), ('unwarn', 20), ('mute', 20), ('unmute', 20),
                ('ban', 50), ('unban', 50), ('kick', 20),
                ('snick', 20), ('rnick', 20), ('giverole', 50),
                ('stats', 0), ('addrole', 100), ('removerole', 50),
                ('delrole', 100), ('nlist', 20), ('clearnicks', 60),
                ('zov', 20), ('setcmd', 100), ('gsetcmd', 100),
                ('creategroup', 100), ('setgroup', 100), ('leavegroup', 100),
                ('gban', 50), ('gkick', 50), ('ggiverole', 50), ('gzov', 50),
                ('gremoverole', 50), ('gsnick', 50), ('grnick', 50),
                ('gunban', 50), ('gnick', 20), ('getban', 50), ('groups', 100),
                ('report', 0), ('top', 0), ('activity', 0), ('log', 70),
                ('marry', 0), ('divorce', 0), ('marriages', 0), ('marryinfo', 0),
                ('clear', 50), ('giveowner', 100)
            ]
            
            for cmd, role in default_permissions:
                cur.execute("""
                INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
                VALUES (0, %s, %s)
                ON CONFLICT (peer_id, cmd_name) 
                DO UPDATE SET required_role = EXCLUDED.required_role
                """, (cmd, role))

            default_roles = [
                (20, 'Заместитель Главного Следящего'),
                (30, 'Главный Следящий'),
                (40, 'Куратор за Администрацией'),
                (50, 'Заместитель Главного Администратора'),
                (60, 'Главный Администратор'),
                (70, 'Специальный Администратор'),
                (80, 'Заместитель Руководителя'),
                (90, 'Руководитель проекта'),
                (95, 'Команда Проекта'),
                (98, 'Заместитель Основателя'),
                (99, 'admin')
            ]
            
            for priority, name in default_roles:
                cur.execute("""
                INSERT INTO roles (peer_id, role_priority, role_name)
                VALUES (0, %s, %s)
                ON CONFLICT (peer_id, role_priority) 
                DO UPDATE SET role_name = EXCLUDED.role_name
                """, (priority, name))

            logger.info(">>> DB OK")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"DB INIT ERROR: {e}")
        raise

init()

BOT_ROLE_RANKS = {
    'Агент Поддержки': 1,
    'Помощник куратора по отделу агентов поддержки': 2,
    'Куратор по отделу агентов поддержки': 3,
    'Генеральный Директор': 4
}

def get_bot_role(cur, user_id):
    cur.execute("SELECT role_name FROM bot_roles WHERE user_id=%s", (user_id,))
    res = cur.fetchone()
    return res[0] if res else None

def get_bot_role_rank(role_name):
    return BOT_ROLE_RANKS.get(role_name, 0)

def can_manage_bot_role(giver_role, target_role):
    if target_role is None: return True
    giver_rank = get_bot_role_rank(giver_role)
    target_rank = get_bot_role_rank(target_role)
    if giver_role == 'Генеральный Директор' and target_role == 'Генеральный Директор': return False
    return giver_rank > target_rank

def can_access_reports(cur, user_id):
    role = get_bot_role(cur, user_id)
    return role is not None and get_bot_role_rank(role) >= 1

def can_manage_agents(cur, user_id):
    role = get_bot_role(cur, user_id)
    return role is not None and get_bot_role_rank(role) >= 2

def is_admin_or_director(cur, user_id):
    if user_id == OWNER_ID: return True
    role = get_bot_role(cur, user_id)
    return role == 'Генеральный Директор'

def parse_time(t):
    if not t: return None
    m = re.match(r"(\d+)([mhd])", t.lower())
    if not m: return None
    v, u = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=v), "h": timedelta(hours=v), "d": timedelta(days=v)}[u]

def format_time(td):
    if not td: return "навсегда"
    total_seconds = int(td.total_seconds())
    days, hours, minutes = total_seconds // 86400, (total_seconds % 86400) // 3600, (total_seconds % 3600) // 60
    parts = []
    if days > 0: parts.append(f"{days} д.")
    if hours > 0: parts.append(f"{hours} ч.")
    if minutes > 0: parts.append(f"{minutes} м.")
    return " ".join(parts) if parts else "менее минуты"

def get_target_id(msg: Message):
    if msg.reply_message: return msg.reply_message.from_id
    if msg.text:
        r = re.search(r"\[id(\d+)\|", msg.text)
        if r: return int(r.group(1))
        r = re.search(r"id(\d+)", msg.text)
        if r: return int(r.group(1))
    return None

async def get_user_name(uid):
    try:
        user = await bot.api.users.get(user_ids=uid, name_case='nom')
        return f"{user[0].first_name} {user[0].last_name}"
    except: return f"Пользователь {uid}"

async def resolve_user_id(username):
    if isinstance(username, int): return username
    if username.isdigit(): return int(username)
    try:
        users = await bot.api.users.get(user_ids=[username.replace("@", "").strip()])
        if users: return users[0].id
    except: pass
    return None

def is_user_banned(peer_id, user_id):
    conn, cur = db()
    try:
        cur.execute("SELECT end_at FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban' AND (end_at IS NULL OR end_at > NOW())", (user_id, peer_id))
        return cur.fetchone() is not None
    finally: conn.close()

def is_user_muted(peer_id, user_id):
    conn, cur = db()
    try:
        cur.execute("SELECT end_at FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute' AND (end_at IS NULL OR end_at > NOW())", (user_id, peer_id))
        return cur.fetchone() is not None
    finally: conn.close()

def get_user_role(cur, peer_id, user_id):
    cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (user_id, peer_id))
    res = cur.fetchone()
    return res[0] if res else 0

def get_role_name(cur, peer_id, priority):
    cur.execute("SELECT role_name FROM roles WHERE peer_id=%s AND role_priority=%s", (peer_id, priority))
    res = cur.fetchone()
    if res: return res[0]
    cur.execute("SELECT role_name FROM roles WHERE peer_id=0 AND role_priority=%s", (priority,))
    res = cur.fetchone()
    return res[0] if res else f"Уровень {priority}"

def role_exists(cur, peer_id, priority):
    cur.execute("SELECT 1 FROM roles WHERE peer_id=%s AND role_priority=%s UNION SELECT 1 FROM roles WHERE peer_id=0 AND role_priority=%s", (peer_id, priority, priority))
    return cur.fetchone() is not None

def get_cmd_required_role(cur, peer_id, cmd_name):
    cur.execute("SELECT required_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (peer_id, cmd_name))
    res = cur.fetchone()
    if res is not None: return res[0]
    cur.execute("SELECT required_role FROM cmd_permissions WHERE peer_id=0 AND cmd_name=%s", (cmd_name,))
    res = cur.fetchone()
    if res is not None: return res[0]
    return 999999

def check_permission(cur, peer_id, user_id, cmd_name):
    if user_id == OWNER_ID: return True, 0, 0
    user_role = get_user_role(cur, peer_id, user_id)
    required_role = get_cmd_required_role(cur, peer_id, cmd_name)
    return user_role >= required_role, user_role, required_role

def can_punish(cur, peer_id, punisher_id, target_id):
    if punisher_id == OWNER_ID: return True
    if punisher_id == target_id: return True
    punisher_role = get_user_role(cur, peer_id, punisher_id)
    target_role = get_user_role(cur, peer_id, target_id)
    return punisher_role > target_role

def add_log(cur, peer_id, moderator_id, target_id, action, reason=""):
    try: cur.execute("INSERT INTO moderation_logs (peer_id, moderator_id, target_id, action, reason) VALUES (%s,%s,%s,%s,%s)", (peer_id, moderator_id, target_id, action, reason))
    except: pass

def get_group_id(cur, peer_id):
    cur.execute("SELECT group_id FROM group_chats WHERE peer_id=%s", (peer_id,))
    res = cur.fetchone()
    return res[0] if res else None

def get_group_chats(cur, group_id):
    cur.execute("SELECT peer_id FROM group_chats WHERE group_id=%s", (group_id,))
    return [r[0] for r in cur.fetchall()]

def is_married(cur, peer_id, user_id):
    cur.execute("SELECT id FROM marriages WHERE peer_id=%s AND (user1_id=%s OR user2_id=%s)", (peer_id, user_id, user_id))
    return cur.fetchone() is not None

def get_marriage(cur, peer_id, user_id):
    cur.execute("SELECT user1_id, user2_id FROM marriages WHERE peer_id=%s AND (user1_id=%s OR user2_id=%s)", (peer_id, user_id, user_id))
    return cur.fetchone()

async def kick_user(peer_id, user_id):
    try:
        await bot.api.messages.remove_chat_user(chat_id=peer_id-2000000000, user_id=user_id)
        return True
    except: return False

async def delete_message(peer_id, cmid):
    try:
        await bot.api.messages.delete(peer_id=peer_id, conversation_message_ids=[cmid], delete_for_all=True)
        return True
    except: return False

async def get_bot_id():
    try:
        data = await bot.api.groups.get_by_id()
        return data[0].id
    except: return None

async def get_chat_members_mentions(peer_id):
    try:
        members = await bot.api.messages.get_conversation_members(peer_id=peer_id)
        bot_id = await get_bot_id()
        mentions = []
        for m in members.items:
            if m.member_id < 0: continue
            if bot_id and m.member_id == bot_id: continue
            mentions.append(f"@id{m.member_id}")
        return mentions
    except: return []

# =========================
# HELP
# =========================
@bot.on.message(text="/help")
async def help_cmd(msg: Message):
    if msg.peer_id < 2000000000: return
    return await msg.answer(
        "💠 FLEX BOT - ПОМОЩЬ\n\n"
        "🏷 НИКИ:\n/snick @user [ник] | /rnick @user | /gnick @user | /nlist | /clearnicks\n\n"
        "⚠️ МОДЕРАЦИЯ:\n/warn @user [причина] | /unwarn @user\n/mute @user [время] [причина] | /unmute @user\n/ban @user [время] [причина] | /unban @user\n/kick @user | /getban @user\n/clear @user - очистить сообщения\n\n"
        "🎖️ РОЛИ:\n/giverole @user [приоритет] | /removerole @user\n/addrole [приоритет] [имя] | /delrole [приоритет]\n/roles | /staff\n/giveowner @user - передать права создателя\n\n"
        "💍 БРАКИ:\n/marry @user | /accept | /decline | /divorce | /marriages | /marryinfo\n\n"
        "📢 /zov [причина] - позвать всех\n\n"
        "🌐 ОБЪЕДИНЕНИЯ:\n/creategroup | /setgroup | /leavegroup | /groups\n/gban /gunban /gkick /ggiverole /gremoverole /gzov /gsnick /grnick /gsetcmd\n\n"
        "📊 /stats | /top | /activity\n📝 /log - логи модерации\n⚙️ /setcmd /gsetcmd\n👑 /sysrole\n🐛 /report"
    )

# =========================
# CLEAR
# =========================
@bot.on.message(text="/clear")
async def clear_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'clear')
    finally: conn.close()
    return await msg.answer(f"🧹 /clear @user\nУдаляет все сообщения пользователя из беседы\nТребуется роль {req}+")

@bot.on.message(text="/clear <target>")
async def clear_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'clear')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ Нельзя очистить сообщения равного или высшего")
        
        user_name = await get_user_name(uid)
        await msg.answer(f"🧹 Начинаю очистку сообщений пользователя {user_name}... Это может занять время.")
        
        # Получаем все сообщения пользователя из базы
        cur.execute("SELECT message_id FROM user_messages WHERE peer_id=%s AND user_id=%s", (msg.peer_id, uid))
        messages = cur.fetchall()
        
        if not messages:
            return await msg.answer(f"🧹 Нет сохранённых сообщений для {user_name}")
        
        deleted = 0
        for (message_id,) in messages:
            try:
                await bot.api.messages.delete(peer_id=msg.peer_id, conversation_message_ids=[message_id], delete_for_all=True)
                deleted += 1
            except:
                pass
        
        # Удаляем записи из базы
        cur.execute("DELETE FROM user_messages WHERE peer_id=%s AND user_id=%s", (msg.peer_id, uid))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'очистка сообщений')
        
        await msg.answer(f"🧹 ОЧИСТКА ЗАВЕРШЕНА\n\n👤 {user_name}\n🗑 Удалено сообщений: {deleted}\n📊 Всего в базе: {len(messages)}")
    finally: conn.close()

# =========================
# GIVEOWNER
# =========================
@bot.on.message(text="/giveowner")
async def giveowner_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'giveowner')
    finally: conn.close()
    return await msg.answer(f"👑 /giveowner @user\nПередаёт права создателя беседы\nТребуется роль {req}+")

@bot.on.message(text="/giveowner <target>")
async def giveowner_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'giveowner')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if uid == msg.from_id: return await msg.answer("❌ Вы уже являетесь создателем")
        
        # Проверяем, является ли отправитель создателем беседы
        try:
            members = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
            is_owner = False
            for m in members.items:
                if m.member_id == msg.from_id and getattr(m, "is_owner", False):
                    is_owner = True
                    break
            if not is_owner and msg.from_id != OWNER_ID:
                return await msg.answer("❌ Только создатель беседы может передавать права")
        except:
            if msg.from_id != OWNER_ID:
                return await msg.answer("❌ Не удалось проверить права создателя")
        
        user_name = await get_user_name(uid)
        # Выдаём роль 100 (создатель) и пытаемся передать права через API
        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (uid, msg.peer_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'передача прав создателя')
        
        try:
            await bot.api.messages.set_chat_owner(chat_id=msg.peer_id-2000000000, user_id=uid)
            await msg.answer(f"👑 ПРАВА ПЕРЕДАНЫ\n\n👤 {user_name} теперь создатель беседы\n📊 Роль: 100")
        except:
            await msg.answer(f"👑 РОЛЬ ВЫДАНА\n\n👤 {user_name} получил роль 100\n⚠️ Не удалось передать права через API (нет прав)")
    finally: conn.close()

# =========================
# MARRY
# =========================
@bot.on.message(text="/marry")
async def marry_help(msg: Message):
    if msg.peer_id < 2000000000: return
    return await msg.answer("💍 БРАК\n\n/marry @user - предложить брак\n/accept - принять\n/decline - отклонить\n/divorce - развестись")

@bot.on.message(text="/marry <target>")
async def marry_propose(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    if uid == msg.from_id: return await msg.answer("❌ Нельзя жениться на себе")
    
    conn, cur = db()
    try:
        if is_married(cur, msg.peer_id, msg.from_id):
            return await msg.answer("❌ Вы уже состоите в браке\n💡 Используйте /divorce для развода")
        if is_married(cur, msg.peer_id, uid):
            return await msg.answer("❌ Этот пользователь уже состоит в браке")
        
        cur.execute("SELECT id FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, uid))
        if cur.fetchone():
            return await msg.answer("❌ Этому пользователю уже отправлено предложение")
        
        cur.execute("INSERT INTO marriage_proposals (peer_id, from_id, to_id) VALUES (%s,%s,%s)", (msg.peer_id, msg.from_id, uid))
        from_name = await get_user_name(msg.from_id)
        to_name = await get_user_name(uid)
        await msg.answer(f"💍 ПРЕДЛОЖЕНИЕ ОТПРАВЛЕНО\n\n👤 {from_name} предлагает брак {to_name}\n\n💡 {to_name}, напишите /accept или /decline")
    finally: conn.close()

@bot.on.message(text="/accept")
async def marry_accept(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT from_id FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, msg.from_id))
        proposal = cur.fetchone()
        if not proposal: return await msg.answer("❌ У вас нет активных предложений")
        
        from_id = proposal[0]
        if is_married(cur, msg.peer_id, msg.from_id) or is_married(cur, msg.peer_id, from_id):
            cur.execute("DELETE FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, msg.from_id))
            return await msg.answer("❌ Один из вас уже в браке")
        
        cur.execute("INSERT INTO marriages (peer_id, user1_id, user2_id) VALUES (%s,%s,%s)", (msg.peer_id, from_id, msg.from_id))
        cur.execute("DELETE FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, msg.from_id))
        
        name1 = await get_user_name(from_id)
        name2 = await get_user_name(msg.from_id)
        await msg.answer(f"💍 ПОЗДРАВЛЯЕМ!\n\n{name1} и {name2} теперь в браке! 💕")
    finally: conn.close()

@bot.on.message(text="/decline")
async def marry_decline(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT from_id FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, msg.from_id))
        if not cur.fetchone(): return await msg.answer("❌ У вас нет активных предложений")
        cur.execute("DELETE FROM marriage_proposals WHERE peer_id=%s AND to_id=%s", (msg.peer_id, msg.from_id))
        await msg.answer("💔 Предложение отклонено")
    finally: conn.close()

# =========================
# DIVORCE
# =========================
@bot.on.message(text="/divorce")
async def divorce_cmd(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        marriage = get_marriage(cur, msg.peer_id, msg.from_id)
        if not marriage: return await msg.answer("❌ Вы не состоите в браке")
        user1, user2 = marriage
        cur.execute("DELETE FROM marriages WHERE peer_id=%s AND (user1_id=%s OR user2_id=%s)", (msg.peer_id, msg.from_id, msg.from_id))
        spouse_name = await get_user_name(user1 if user1 != msg.from_id else user2)
        await msg.answer(f"💔 РАЗВОД\n\nВы развелись с {spouse_name}")
    finally: conn.close()

# =========================
# MARRIAGES / MARRYINFO
# =========================
@bot.on.message(text="/marriages")
async def marriages_list(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT user1_id, user2_id FROM marriages WHERE peer_id=%s", (msg.peer_id,))
        marriages = cur.fetchall()
        if not marriages: return await msg.answer("💍 БРАКИ\n\n❌ В этой беседе нет браков")
        text = f"💍 БРАКИ ({len(marriages)}):\n\n"
        for u1, u2 in marriages:
            try: n1 = await get_user_name(u1)
            except: n1 = f"id{u1}"
            try: n2 = await get_user_name(u2)
            except: n2 = f"id{u2}"
            text += f"💕 {n1} ❤️ {n2}\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/marryinfo <target>")
async def marryinfo_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
    if not uid: uid = msg.from_id
    conn, cur = db()
    try:
        marriage = get_marriage(cur, msg.peer_id, uid)
        if not marriage:
            user_name = await get_user_name(uid)
            return await msg.answer(f"💍 ИНФОРМАЦИЯ О БРАКЕ\n\n👤 {user_name}\n💔 Не в браке")
        u1, u2 = marriage
        spouse_id = u1 if u1 != uid else u2
        user_name = await get_user_name(uid)
        spouse_name = await get_user_name(spouse_id)
        await msg.answer(f"💍 ИНФОРМАЦИЯ О БРАКЕ\n\n👤 {user_name}\n💕 В браке с {spouse_name}")
    finally: conn.close()

# =========================
# BOTROLE
# =========================
@bot.on.message(text="/botrole")
async def botrole_help(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id) and not can_manage_agents(cur, msg.from_id):
            return await msg.answer("❌ У вас нет доступа к управлению должностями")
    finally: conn.close()
    return await msg.answer(
        "🏅 УПРАВЛЕНИЕ ДОЛЖНОСТЯМИ\n\n"
        "/botrole @user [должность] - выдать\n"
        "/removebotrole @user - снять\n"
        "/botroles - список\n\n"
        "📊 Должности:\n"
        "• Агент Поддержки\n"
        "• Помощник куратора по отделу агентов поддержки\n"
        "• Куратор по отделу агентов поддержки\n"
        "• Генеральный Директор"
    )

@bot.on.message(text="/botrole <target> <role_name>")
async def botrole_give(msg: Message, target: str, role_name: str):
    if msg.peer_id > 2000000000: return
    if role_name not in BOT_ROLE_RANKS:
        return await msg.answer(f"❌ Неизвестная должность\n\nДоступные: {', '.join(BOT_ROLE_RANKS.keys())}")
    conn, cur = db()
    try:
        giver_role = get_bot_role(cur, msg.from_id)
        if msg.from_id != OWNER_ID and not can_manage_agents(cur, msg.from_id) and not is_admin_or_director(cur, msg.from_id):
            return await msg.answer("❌ У вас нет прав")
        uid = get_target_id(msg)
        if not uid:
            clean = target.replace("@", "").replace("[", "").replace("]", "").strip()
            uid = int(clean) if clean.isdigit() else await resolve_user_id(clean)
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if msg.from_id != OWNER_ID:
            if not can_manage_bot_role(giver_role if giver_role else 'Генеральный Директор', role_name):
                return await msg.answer(f"❌ Вы не можете выдать '{role_name}'")
        cur.execute("INSERT INTO bot_roles (user_id, role_name, given_by) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET role_name=%s, given_by=%s", (uid, role_name, msg.from_id, role_name, msg.from_id))
        await msg.answer(f"🏅 ДОЛЖНОСТЬ ВЫДАНА\n\n👤 {await get_user_name(uid)}\n📋 {role_name}")
    finally: conn.close()

@bot.on.message(text="/removebotrole")
async def removebotrole_help(msg: Message):
    if msg.peer_id > 2000000000: return
    return await msg.answer("🗑 /removebotrole @user - снять должность")

@bot.on.message(text="/removebotrole <target>")
async def removebotrole_cmd(msg: Message, target: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        giver_role = get_bot_role(cur, msg.from_id)
        if msg.from_id != OWNER_ID and not can_manage_agents(cur, msg.from_id) and not is_admin_or_director(cur, msg.from_id):
            return await msg.answer("❌ У вас нет прав")
        uid = get_target_id(msg)
        if not uid:
            clean = target.replace("@", "").replace("[", "").replace("]", "").strip()
            uid = int(clean) if clean.isdigit() else await resolve_user_id(clean)
        if not uid: return await msg.answer("❌ Пользователь не найден")
        target_role = get_bot_role(cur, uid)
        if not target_role: return await msg.answer("❌ У пользователя нет должности")
        if msg.from_id != OWNER_ID and not can_manage_bot_role(giver_role if giver_role else 'Генеральный Директор', target_role):
            return await msg.answer(f"❌ Вы не можете снять '{target_role}'")
        cur.execute("DELETE FROM bot_roles WHERE user_id=%s", (uid,))
        await msg.answer(f"🗑 ДОЛЖНОСТЬ СНЯТА\n\n👤 {await get_user_name(uid)}\n❌ Была: {target_role}")
    finally: conn.close()

@bot.on.message(text="/botroles")
async def botroles_list(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if msg.from_id != OWNER_ID and not can_manage_agents(cur, msg.from_id) and not is_admin_or_director(cur, msg.from_id):
            return await msg.answer("❌ У вас нет доступа")
        cur.execute("SELECT user_id, role_name FROM bot_roles ORDER BY role_name")
        roles = cur.fetchall()
        if not roles: return await msg.answer("🏅 ДОЛЖНОСТИ\n\n❌ Нет назначенных должностей")
        text = f"🏅 СПИСОК ДОЛЖНОСТЕЙ ({len(roles)}):\n\n"
        for uid, rname in roles:
            try: name = await get_user_name(uid)
            except: name = f"id{uid}"
            text += f"👤 {name} — {rname}\n"
        await msg.answer(text)
    finally: conn.close()

# =========================
# REPORTS (ЛС)
# =========================
@bot.on.message(text="/reports")
async def reports_list(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if msg.from_id != OWNER_ID and not can_access_reports(cur, msg.from_id) and not is_admin_or_director(cur, msg.from_id):
            return await msg.answer("❌ У вас нет доступа к репортам")
        cur.execute("SELECT id, user_id, description, created_at FROM reports WHERE status='open' ORDER BY id")
        reports = cur.fetchall()
        if not reports: return await msg.answer("📋 РЕПОРТЫ\n\n✅ Нет открытых репортов")
        text = f"📋 ОТКРЫТЫЕ РЕПОРТЫ ({len(reports)}):\n\n"
        for rid, uid, desc, created_at in reports:
            try: name = await get_user_name(uid)
            except: name = f"id{uid}"
            date_str = created_at.strftime('%d.%m в %H:%M') if created_at else "Неизвестно"
            short_desc = desc[:50] + "..." if len(desc) > 50 else desc
            text += f"🐛 #{rid} | {name}\n   📝 {short_desc}\n   📅 {date_str}\n   💡 /replyreport {rid} [ответ]\n\n"
        if len(text) > 4000:
            for i in range(0, len(text), 4000): await msg.answer(text[i:i+4000])
        else: await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/replyreport <report_id> <text>")
async def replyreport_cmd(msg: Message, report_id: str, text: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if msg.from_id != OWNER_ID and not can_access_reports(cur, msg.from_id) and not is_admin_or_director(cur, msg.from_id):
            return await msg.answer("❌ У вас нет доступа к репортам")
        try: rid = int(report_id)
        except: return await msg.answer("❌ Номер репорта должен быть числом")
        cur.execute("SELECT user_id, peer_id FROM reports WHERE id=%s AND status='open'", (rid,))
        report = cur.fetchone()
        if not report: return await msg.answer(f"❌ Репорт #{rid} не найден или уже отвечен")
        user_id, peer_id = report
        sent = False
        replier_name = await get_user_name(msg.from_id)
        try:
            await bot.api.messages.send(user_id=user_id, message=f"📢 ОТВЕТ НА РЕПОРТ #{rid}\n\n👤 {replier_name} ответил:\n\n{text}\n\n📅 {datetime.now().strftime('%d.%m.%Y в %H:%M')}", random_id=0)
            sent = True
        except: pass
        if not sent and peer_id > 2000000000:
            try:
                await bot.api.messages.send(peer_id=peer_id, message=f"📢 ОТВЕТ НА РЕПОРТ #{rid}\n\n👤 @id{user_id}\n👤 Ответил: {replier_name}\n\n{text}\n\n💡 Откройте ЛС для бота чтобы получать ответы там.", random_id=0)
                sent = True
            except: pass
        cur.execute("UPDATE reports SET status='closed', reply=%s, replied_by=%s WHERE id=%s", (text, msg.from_id, rid))
        if sent: await msg.answer(f"✅ Ответ на репорт #{rid} отправлен")
        else: await msg.answer("⚠️ Ответ сохранён, но не удалось отправить (ЛС закрыты)")
    finally: conn.close()

@bot.on.message(text="/report")
async def report_help(msg: Message):
    return await msg.answer("🐛 /report [описание]\nМинимум 10 символов, максимум 500")

@bot.on.message(text="/report <description>")
async def report_cmd(msg: Message, description: str):
    if len(description) < 10: return await msg.answer("❌ Минимум 10 символов")
    if len(description) > 500: return await msg.answer("❌ Максимум 500 символов")
    conn, cur = db()
    try:
        cur.execute("INSERT INTO reports (user_id, peer_id, description) VALUES (%s, %s, %s) RETURNING id", (msg.from_id, msg.peer_id, description))
        report_id = cur.fetchone()[0]
        try: await bot.api.messages.send(user_id=OWNER_ID, message=f"🐛 НОВЫЙ РЕПОРТ #{report_id}\n\n👤 {await get_user_name(msg.from_id)}\n📝 {description}\n📅 {datetime.now().strftime('%d.%m.%Y в %H:%M')}", random_id=0)
        except: pass
        await msg.answer(f"🐛 Репорт #{report_id} отправлен!")
    finally: conn.close()

# =========================
# ADMIN PANEL
# =========================
@bot.on.message(text="/admin")
async def admin_panel(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    conn, cur = db()
    try:
        cur.execute("SELECT COUNT(DISTINCT peer_id) FROM users WHERE peer_id > 2000000000"); total_chats = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users"); total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM punishments WHERE type='ban' AND (end_at IS NULL OR end_at > NOW())"); active_bans = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM groups"); total_groups = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM reports WHERE status='open'"); open_reports = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bot_roles"); total_bot_roles = cur.fetchone()[0]
        await msg.answer(
            f"👑 АДМИН-ПАНЕЛЬ FLEX BOT\n\n"
            f"📊 Статистика:\n"
            f"💬 Бесед: {total_chats}\n👥 Пользователей: {total_users}\n"
            f"🚫 Активных банов: {active_bans}\n🌐 Объединений: {total_groups}\n"
            f"🐛 Открытых репортов: {open_reports}\n🏅 Должностей: {total_bot_roles}\n\n"
            f"📋 Команды:\n/globalban /globalunban /broadcast /sendto\n/reports /replyreport\n/botrole /removebotrole /botroles\n/sysrole /adminchats /adminhelp"
        )
    finally: conn.close()

@bot.on.message(text="/adminhelp")
async def admin_help(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    await msg.answer("👑 АДМИН-ПАНЕЛЬ\n\n/admin /adminchats /globalban /globalunban /broadcast /sendto /reports /replyreport /botrole /removebotrole /botroles /sysrole")

@bot.on.message(text="/adminchats")
async def admin_chats(msg: Message):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    await msg.answer("📋 Загружаю...")
    conn, cur = db()
    try:
        cur.execute("SELECT COUNT(DISTINCT peer_id) FROM users WHERE peer_id > 2000000000"); total = cur.fetchone()[0]
        cur.execute("SELECT DISTINCT peer_id FROM users WHERE peer_id > 2000000000 ORDER BY peer_id"); chats = cur.fetchall()
        text = f"📋 БЕСЕДЫ ({total}):\n\n"
        for i, (peer_id,) in enumerate(chats, 1):
            title = None
            try:
                conv = await bot.api.messages.get_conversations_by_id(peer_ids=[peer_id])
                if conv and conv.items:
                    item = conv.items[0]
                    if hasattr(item, 'chat_settings') and item.chat_settings: title = item.chat_settings.title
            except: pass
            text += f"{i}. {title or f'Беседа {peer_id}'} (ID: {peer_id})\n"
        if len(text) > 4000:
            for i in range(0, len(text), 4000): await msg.answer(text[i:i+4000])
        else: await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/globalban <target_id> <time_str> <reason>")
async def globalban_cmd(msg: Message, target_id: str, time_str: str, reason: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    try: uid = int(target_id)
    except: return await msg.answer("❌ ID должен быть числом")
    duration = None
    if time_str.lower() != "permanent":
        parsed = parse_time(time_str)
        if parsed: duration = parsed
    end_time = datetime.now() + duration if duration else None
    ft = format_time(duration) if duration else "навсегда"
    conn, cur = db()
    try:
        cur.execute("SELECT DISTINCT peer_id FROM users WHERE peer_id > 2000000000"); chats = cur.fetchall()
        user_name = await get_user_name(uid)
        for (peer_id,) in chats:
            try:
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason, banned_by) VALUES (%s,%s,'ban',%s,%s,%s)", (uid, peer_id, end_time, reason, msg.from_id))
                await kick_user(peer_id, uid)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ БАН\n\n👤 {user_name}\n⏰ {ft}\n📝 {reason}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/globalunban <target_id>")
async def globalunban_cmd(msg: Message, target_id: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    try: uid = int(target_id)
    except: return await msg.answer("❌ ID должен быть числом")
    conn, cur = db()
    try:
        cur.execute("DELETE FROM punishments WHERE user_id=%s AND type='ban'", (uid,))
        await msg.answer(f"✅ ГЛОБАЛЬНЫЙ РАЗБАН\n\n👤 id{uid}")
    finally: conn.close()

@bot.on.message(text="/broadcast <text>")
async def broadcast_cmd(msg: Message, text: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    conn, cur = db()
    try:
        cur.execute("SELECT DISTINCT peer_id FROM users WHERE peer_id > 2000000000"); chats = cur.fetchall()
        for (peer_id,) in chats:
            try: await bot.api.messages.send(peer_id=peer_id, message=f"📢 РАССЫЛКА\n\n{text}", random_id=0)
            except: pass
        await msg.answer(f"📢 Отправлено в {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/sendto <peer> <text>")
async def sendto_cmd(msg: Message, peer: str, text: str):
    if msg.peer_id > 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return
    finally: conn.close()
    try: peer_id = int(peer)
    except: return await msg.answer("❌ ID беседы должен быть числом")
    try:
        await bot.api.messages.send(peer_id=peer_id, message=f"📢 СООБЩЕНИЕ\n\n{text}", random_id=0)
        await msg.answer(f"✅ Отправлено в {peer_id}")
    except Exception as e: await msg.answer(f"❌ Ошибка: {e}")

# =========================
# START
# =========================
@bot.on.message(text="/start")
async def start(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        try: res = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
        except: return await msg.answer("❌ Нужны права администратора")
        for m in res.items:
            if getattr(m, "is_owner", False):
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, msg.peer_id))
                cur.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, required_role) SELECT %s, cmd_name, required_role FROM cmd_permissions WHERE peer_id=0 ON CONFLICT DO NOTHING", (msg.peer_id,))
                cur.execute("INSERT INTO roles (peer_id, role_priority, role_name) SELECT %s, role_priority, role_name FROM roles WHERE peer_id=0 ON CONFLICT DO NOTHING", (msg.peer_id,))
                return await msg.answer(f"✅ БОТ АКТИВИРОВАН\n\n👑 Создатель получил роль 100\n📋 Роли созданы\n\n/help")
        return await msg.answer("❌ Не удалось найти создателя")
    finally: conn.close()

# =========================
# SETCMD / GSETCMD
# =========================
@bot.on.message(text="/setcmd")
async def setcmd_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'setcmd')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT DISTINCT ON (cmd_name) cmd_name, required_role FROM cmd_permissions WHERE peer_id=%s OR peer_id=0 ORDER BY cmd_name, peer_id DESC", (msg.peer_id,))
        perms = cur.fetchall()
        text = "📊 ПРАВА КОМАНД:\n\n"
        for cmd, role in perms:
            if cmd != 'sysrole': text += f"/{cmd} - роль {role}+\n"
        return await msg.answer(f"⚙️ /setcmd [команда] [приоритет]\n\n{text}")
    finally: conn.close()

@bot.on.message(text="/setcmd <cmd_name> <priority>")
async def setcmd(msg: Message, cmd_name: str, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'setcmd')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        if cmd_name.lower() == "sysrole": return await msg.answer("❌ Нельзя изменить /sysrole")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p < 0 or p > 1000: return await msg.answer("❌ Приоритет от 0 до 1000")
        cur.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, required_role) VALUES (%s,%s,%s) ON CONFLICT (peer_id, cmd_name) DO UPDATE SET required_role = EXCLUDED.required_role", (msg.peer_id, cmd_name.lower(), p))
        await msg.answer(f"✅ /{cmd_name.lower()} - роль {p}+")
    finally: conn.close()

@bot.on.message(text="/gsetcmd")
async def gsetcmd_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gsetcmd')
    finally: conn.close()
    return await msg.answer(f"🌐 /gsetcmd [команда] [приоритет]\nТребуется роль {req}+")

@bot.on.message(text="/gsetcmd <cmd_name> <priority>")
async def gsetcmd_cmd(msg: Message, cmd_name: str, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gsetcmd')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        if cmd_name.lower() == "sysrole": return await msg.answer("❌ Нельзя изменить /sysrole")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        chats = get_group_chats(cur, group_id)
        for peer_id in chats:
            try: cur.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, required_role) VALUES (%s,%s,%s) ON CONFLICT (peer_id, cmd_name) DO UPDATE SET required_role = EXCLUDED.required_role", (peer_id, cmd_name.lower(), p))
            except: pass
        await msg.answer(f"🌐 /{cmd_name.lower()} - роль {p}+\n📊 {len(chats)} бесед")
    finally: conn.close()

# =========================
# GROUPS
# =========================
@bot.on.message(text="/groups")
async def groups_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'groups')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        return await msg.answer("🌐 /groups - ваши объединения\n/groups [ID] - инфо")
    finally: conn.close()

@bot.on.message(text="/groups <group_id>")
async def groups_info(msg: Message, group_id: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'groups')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try: gid = int(group_id)
        except: return await msg.answer("❌ ID должен быть числом")
        cur.execute("SELECT name, creator_id FROM groups WHERE id=%s", (gid,))
        group = cur.fetchone()
        if not group: return await msg.answer("❌ Объединение не найдено")
        chats = get_group_chats(cur, gid)
        creator_name = await get_user_name(group[1])
        await msg.answer(f"🌐 {group[0]}\n🆔 ID: {gid}\n👑 Создатель: {creator_name}\n📊 Бесед: {len(chats)}")
    finally: conn.close()

@bot.on.message(text="/groups")
async def groups_list(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'groups')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT id, name FROM groups WHERE creator_id=%s ORDER BY id", (msg.from_id,))
        groups = cur.fetchall()
        if not groups: return await msg.answer("🌐 У вас нет объединений\n/creategroup Название")
        text = f"🌐 ВАШИ ОБЪЕДИНЕНИЯ ({len(groups)}):\n\n"
        for gid, name in groups:
            chats = get_group_chats(cur, gid)
            text += f"📋 {name} (ID: {gid}) - бесед: {len(chats)}\n"
        await msg.answer(text)
    finally: conn.close()

# =========================
# CREATEGROUP / SETGROUP / LEAVEGROUP
# =========================
@bot.on.message(text="/creategroup")
async def creategroup_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'creategroup')
    finally: conn.close()
    return await msg.answer(f"🌐 /creategroup [название]\nТребуется роль {req}+")

@bot.on.message(text="/creategroup <name>")
async def creategroup_cmd(msg: Message, name: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'creategroup')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        if len(name) > 50: return await msg.answer("❌ Макс. 50 символов")
        cur.execute("INSERT INTO groups (name, creator_id) VALUES (%s, %s) RETURNING id", (name, msg.from_id))
        gid = cur.fetchone()[0]
        await msg.answer(f"🌐 СОЗДАНО\n\n📋 {name}\n🆔 ID: {gid}\n💡 /setgroup {gid}")
    finally: conn.close()

@bot.on.message(text="/setgroup")
async def setgroup_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'setgroup')
    finally: conn.close()
    return await msg.answer(f"🌐 /setgroup [ID]\nТребуется роль {req}+")

@bot.on.message(text="/setgroup <group_id>")
async def setgroup_cmd(msg: Message, group_id: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'setgroup')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try: gid = int(group_id)
        except: return await msg.answer("❌ ID должен быть числом")
        cur.execute("SELECT name, creator_id FROM groups WHERE id=%s", (gid,))
        group = cur.fetchone()
        if not group: return await msg.answer("❌ Объединение не найдено")
        if msg.from_id != group[1] and msg.from_id != OWNER_ID:
            conn2, cur2 = db()
            try:
                if not is_admin_or_director(cur2, msg.from_id): return await msg.answer("❌ Только создатель может привязывать")
            finally: conn2.close()
        cur.execute("INSERT INTO group_chats (group_id, peer_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (gid, msg.peer_id))
        await msg.answer(f"🌐 ПРИВЯЗАНО\n\n📋 {group[0]}\n🆔 ID: {gid}")
    finally: conn.close()

@bot.on.message(text="/leavegroup")
async def leavegroup_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'leavegroup')
    finally: conn.close()
    return await msg.answer(f"🌐 /leavegroup - отвязать\nТребуется роль {req}+")

@bot.on.message(text="/leavegroup")
async def leavegroup_cmd(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'leavegroup')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT g.id, g.creator_id FROM groups g JOIN group_chats gc ON g.id=gc.group_id WHERE gc.peer_id=%s", (msg.peer_id,))
        group = cur.fetchone()
        if not group: return await msg.answer("❌ Беседа не привязана")
        if msg.from_id != group[1] and msg.from_id != OWNER_ID: return await msg.answer("❌ Только создатель может отвязывать")
        cur.execute("DELETE FROM group_chats WHERE peer_id=%s", (msg.peer_id,))
        await msg.answer("🌐 Отвязано")
    finally: conn.close()

# =========================
# GBAN / GUNBAN / GKICK / GGIVEROLE / GREMOVEROLE / GZOV / GSNICK / GRNICK
# =========================
@bot.on.message(text="/gban")
async def gban_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gban')
    finally: conn.close()
    return await msg.answer(f"🌐 /gban @user [время] [причина]\nТребуется роль {req}+")

@bot.on.message(text="/gban <target> <time_str> <reason>")
async def gban_full(msg: Message, target: str, time_str: str, reason: str): await process_gban(msg, target, time_str, reason)
@bot.on.message(text="/gban <target> <time_str>")
async def gban_simple(msg: Message, target: str, time_str: str): await process_gban(msg, target, time_str, "")

async def process_gban(msg, target, time_str, reason):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя наказать равного или высшего")
        duration = None; final_reason = reason or "Без причины"
        if time_str.lower() != "permanent":
            parsed = parse_time(time_str)
            if parsed: duration = parsed
            else: final_reason = time_str + (" " + reason if reason else "")
        end_time = datetime.now() + duration if duration else None
        ft = format_time(duration) if duration else "навсегда"
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason, banned_by) VALUES (%s,%s,'ban',%s,%s,%s)", (uid, peer_id, end_time, final_reason, msg.from_id))
                add_log(cur, peer_id, msg.from_id, uid, 'глобальный бан', final_reason)
                await kick_user(peer_id, uid)
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ БАН\n👤 {user_name}\n⏰ {ft}\n📝 {final_reason}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ БАН\n\n👤 {user_name}\n⏰ {ft}\n📝 {final_reason}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/gunban")
async def gunban_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gunban')
    finally: conn.close()
    return await msg.answer(f"🌐 /gunban @user\nТребуется роль {req}+")

@bot.on.message(text="/gunban <target>")
async def gunban_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gunban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя разбанить равного или высшего")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (uid, peer_id))
                add_log(cur, peer_id, msg.from_id, uid, 'глобальный разбан')
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ РАЗБАН\n👤 {user_name}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ РАЗБАН\n\n👤 {user_name}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/gkick")
async def gkick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gkick')
    finally: conn.close()
    return await msg.answer(f"🌐 /gkick @user\nТребуется роль {req}+")

@bot.on.message(text="/gkick <target>")
async def gkick_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gkick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя наказать равного или высшего")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                await kick_user(peer_id, uid)
                add_log(cur, peer_id, msg.from_id, uid, 'глобальный кик')
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ КИК\n👤 {user_name}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ КИК\n\n👤 {user_name}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/ggiverole")
async def ggiverole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'ggiverole')
    finally: conn.close()
    return await msg.answer(f"🌐 /ggiverole @user [приоритет]\nТребуется роль {req}+")

@bot.on.message(text="/ggiverole <target> <priority>")
async def ggiverole_cmd(msg: Message, target: str, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'ggiverole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if not role_exists(cur, msg.peer_id, p): return await msg.answer(f"❌ Роль {p} не создана")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer(f"❌ Нельзя выдать роль {p}")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя выдать роль равному или высшему")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (uid, peer_id, p, p))
                add_log(cur, peer_id, msg.from_id, uid, f'глобальная роль {p}')
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНАЯ РОЛЬ\n👤 {user_name}\n📋 {get_role_name(cur, peer_id, p)}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНАЯ РОЛЬ\n\n👤 {user_name}\n📋 {get_role_name(cur, msg.peer_id, p)}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/gremoverole")
async def gremoverole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gremoverole')
    finally: conn.close()
    return await msg.answer(f"🌐 /gremoverole @user\nТребуется роль {req}+")

@bot.on.message(text="/gremoverole <target>")
async def gremoverole_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gremoverole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя снять роль у равного или высшего")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("UPDATE users SET role=0 WHERE user_id=%s AND peer_id=%s", (uid, peer_id))
                add_log(cur, peer_id, msg.from_id, uid, 'глобальный сброс роли')
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ СБРОС РОЛИ\n👤 {user_name}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ СБРОС РОЛИ\n\n👤 {user_name}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/gzov")
async def gzov_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gzov')
    finally: conn.close()
    return await msg.answer(f"🌐 /gzov [причина]\nТребуется роль {req}+")

@bot.on.message(text="/gzov <reason>")
async def gzov_cmd(msg: Message, reason: str = "Без причины"):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gzov')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        chats = get_group_chats(cur, group_id)
        caller_name = await get_user_name(msg.from_id)
        for peer_id in chats:
            try:
                mentions = await get_chat_members_mentions(peer_id)
                if mentions:
                    if len(mentions) <= 50:
                        await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ ЗОВ\n🔊 {caller_name}\n📢 {reason}\n\n{' '.join(mentions)}", random_id=0)
                    else:
                        await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ ЗОВ\n🔊 {caller_name}\n📢 {reason}", random_id=0)
                        for i in range(0, len(mentions), 50): await bot.api.messages.send(peer_id=peer_id, message=" ".join(mentions[i:i+50]), random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ ЗОВ\n\n🔊 {caller_name}\n📢 {reason}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/gsnick")
async def gsnick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'gsnick')
    finally: conn.close()
    return await msg.answer(f"🌐 /gsnick @user [ник]\nТребуется роль {req}+")

@bot.on.message(text="/gsnick <target> <nick>")
async def gsnick_cmd(msg: Message, target: str, nick: str):
    if msg.peer_id < 2000000000: return
    if len(nick) > 50: return await msg.answer("❌ Макс. 50 символов")
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gsnick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя установить ник равному или высшему")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("INSERT INTO users (user_id, peer_id, nickname) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET nickname=%s", (uid, peer_id, nick, nick))
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНЫЙ НИК\n👤 {user_name}\n🏷 {nick}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНЫЙ НИК\n\n👤 {user_name}\n🏷 {nick}\n📊 {len(chats)} бесед")
    finally: conn.close()

@bot.on.message(text="/grnick")
async def grnick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'grnick')
    finally: conn.close()
    return await msg.answer(f"🌐 /grnick @user\nТребуется роль {req}+")

@bot.on.message(text="/grnick <target>")
async def grnick_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'grnick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        group_id = get_group_id(cur, msg.peer_id)
        if not group_id: return await msg.answer("❌ Беседа не привязана")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя удалить ник равному или высшему")
        chats = get_group_chats(cur, group_id)
        user_name = await get_user_name(uid)
        for peer_id in chats:
            try:
                cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, peer_id))
                await bot.api.messages.send(peer_id=peer_id, message=f"🌐 ГЛОБАЛЬНОЕ УДАЛЕНИЕ НИКА\n👤 {user_name}", random_id=0)
            except: pass
        await msg.answer(f"🌐 ГЛОБАЛЬНОЕ УДАЛЕНИЕ НИКА\n\n👤 {user_name}\n📊 {len(chats)} бесед")
    finally: conn.close()

# =========================
# ZOV
# =========================
@bot.on.message(text="/zov")
async def zov_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'zov')
    finally: conn.close()
    return await msg.answer(f"📢 /zov [причина]\nТребуется роль {req}+")

@bot.on.message(text="/zov <reason>")
async def zov_cmd(msg: Message, reason: str = "Без причины"):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'zov')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        mentions = await get_chat_members_mentions(msg.peer_id)
        if not mentions: return await msg.answer("❌ Некого звать")
        caller_name = await get_user_name(msg.from_id)
        if len(mentions) <= 50: await msg.answer(f"🔊 {caller_name} зовёт всех!\n\n📢 {reason}\n\n{' '.join(mentions)}")
        else:
            await msg.answer(f"🔊 {caller_name} зовёт всех!\n\n📢 {reason}")
            for i in range(0, len(mentions), 50): await msg.answer(" ".join(mentions[i:i+50]))
    finally: conn.close()

# =========================
# SYSRole (Владелец + Ген.Директор)
# =========================
@bot.on.message(text="/sysrole")
async def sysrole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return await msg.answer("❌ Только для владельца бота и Генерального Директора")
    finally: conn.close()
    return await msg.answer("👑 /sysrole @user [приоритет]")

@bot.on.message(text="/sysrole <target> <priority>")
async def sysrole_cmd(msg: Message, target: str, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        if not is_admin_or_director(cur, msg.from_id): return await msg.answer("❌ Только для владельца бота и Генерального Директора")
    finally: conn.close()
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    try: p = int(priority)
    except: return await msg.answer("❌ Приоритет - число")
    conn, cur = db()
    try:
        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (uid, msg.peer_id, p, p))
        add_log(cur, msg.peer_id, msg.from_id, uid, f'системная роль {p}')
        await msg.answer(f"👑 РОЛЬ ВЫДАНА\n\n👤 {await get_user_name(uid)}\n📋 {get_role_name(cur, msg.peer_id, p)}")
    finally: conn.close()

# =========================
# ADDROLE / GIVEROLE / REMOVEROLE / DELROLE / ROLES / STAFF
# =========================
@bot.on.message(text="/addrole")
async def addrole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'addrole')
    finally: conn.close()
    return await msg.answer(f"📋 /addrole [приоритет] [имя]\nТребуется роль {req}+")

@bot.on.message(text="/addrole <priority> <role_name>")
async def addrole(msg: Message, priority: str, role_name: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'addrole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя создать роль выше или равную вашей")
        if len(role_name) > 50: return await msg.answer("❌ Макс. 50 символов")
        cur.execute("SELECT role_name FROM roles WHERE peer_id=%s AND role_priority=%s", (msg.peer_id, p))
        old = cur.fetchone()
        cur.execute("INSERT INTO roles (peer_id, role_priority, role_name) VALUES (%s,%s,%s) ON CONFLICT (peer_id, role_priority) DO UPDATE SET role_name=%s", (msg.peer_id, p, role_name, role_name))
        if old: await msg.answer(f"✅ РОЛЬ ИЗМЕНЕНА\n\n📋 Было: {old[0]}\n📋 Стало: {role_name}")
        else: await msg.answer(f"✅ РОЛЬ СОЗДАНА\n\n📋 {role_name}")
    finally: conn.close()

@bot.on.message(text="/giverole")
async def giverole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'giverole')
    finally: conn.close()
    return await msg.answer(f"🎖️ /giverole @user [приоритет]\nТребуется роль {req}+")

@bot.on.message(text="/giverole <target> <priority>")
async def giverole_cmd(msg: Message, target: str, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'giverole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя выдать роль равному или высшему")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if not role_exists(cur, msg.peer_id, p): return await msg.answer(f"❌ Роль {p} не создана\n/addrole {p} Название")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer(f"❌ Нельзя выдать роль {p}")
        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (uid, msg.peer_id, p, p))
        add_log(cur, msg.peer_id, msg.from_id, uid, f'выдача роли {p}')
        await msg.answer(f"🎖️ РОЛЬ ВЫДАНА\n\n👤 {await get_user_name(uid)}\n📋 {get_role_name(cur, msg.peer_id, p)}")
    finally: conn.close()

@bot.on.message(text="/removerole")
async def removerole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'removerole')
    finally: conn.close()
    return await msg.answer(f"🗑 /removerole @user\nТребуется роль {req}+")

@bot.on.message(text="/removerole <target>")
async def removerole_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'removerole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя снять роль у равного или высшего")
        cur.execute("UPDATE users SET role=0 WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'сброс роли')
        await msg.answer(f"🗑 РОЛЬ СБРОШЕНА\n\n👤 {await get_user_name(uid)}")
    finally: conn.close()

@bot.on.message(text="/delrole")
async def delrole_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'delrole')
    finally: conn.close()
    return await msg.answer(f"🗑 /delrole [приоритет]\nТребуется роль {req}+")

@bot.on.message(text="/delrole <priority>")
async def delrole_cmd(msg: Message, priority: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'delrole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя удалить роль выше или равную вашей")
        cur.execute("SELECT role_name FROM roles WHERE peer_id=%s AND role_priority=%s", (msg.peer_id, p))
        role_data = cur.fetchone()
        if not role_data: return await msg.answer(f"❌ Роль {p} не найдена")
        cur.execute("DELETE FROM roles WHERE peer_id=%s AND role_priority=%s", (msg.peer_id, p))
        await msg.answer(f"🗑 РОЛЬ УДАЛЕНА\n\n📋 {role_data[0]}")
    finally: conn.close()

@bot.on.message(text="/roles")
async def list_roles(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT role_priority, role_name FROM roles WHERE peer_id=%s ORDER BY role_priority DESC", (msg.peer_id,))
        roles = cur.fetchall()
        if not roles: return await msg.answer("📊 Нет ролей\n/addrole 50 Модератор")
        text = f"📊 РОЛИ ({len(roles)}):\n\n"
        for p, n in roles: text += f"📋 {p} - {n}\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/staff")
async def staff(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role>0 ORDER BY role DESC LIMIT 50", (msg.peer_id,))
        staff_list = cur.fetchall()
        if not staff_list: return await msg.answer("👥 Нет персонала\n/giverole @user 50")
        text = f"👥 ПЕРСОНАЛ ({len(staff_list)}):\n\n"
        for uid, role in staff_list:
            try: name = await get_user_name(uid)
            except: name = f"ID {uid}"
            s = "🚫" if is_user_banned(msg.peer_id, uid) else "🔇" if is_user_muted(msg.peer_id, uid) else "✅"
            text += f"{s} @id{uid} ({name}) - {get_role_name(cur, msg.peer_id, role)}\n"
        await msg.answer(text)
    finally: conn.close()

# =========================
# WARN / UNWARN / MUTE / UNMUTE / BAN / UNBAN / GETBAN / KICK
# =========================
@bot.on.message(text="/warn")
async def warn_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'warn')
    finally: conn.close()
    return await msg.answer(f"⚠️ /warn @user [причина]\n3 предупреждения = бан\nТребуется роль {req}+")

@bot.on.message(text="/warn <target> <reason>")
async def warn_cmd(msg: Message, target: str, reason: str = "Без причины"):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'warn')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя наказать равного или высшего")
        cur.execute("INSERT INTO users (user_id, peer_id, warn_count, warn_reasons) VALUES (%s,%s,1,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET warn_count=users.warn_count+1, warn_reasons=CASE WHEN users.warn_reasons='' THEN EXCLUDED.warn_reasons ELSE users.warn_reasons||' | '||EXCLUDED.warn_reasons END", (uid, msg.peer_id, reason))
        cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        warns = cur.fetchone()[0]
        user_name = await get_user_name(uid)
        add_log(cur, msg.peer_id, msg.from_id, uid, 'предупреждение', reason)
        if warns >= 3:
            cur.execute("INSERT INTO punishments (user_id, peer_id, type, reason, banned_by) VALUES (%s,%s,'ban','Авто-бан (3 пред.)',%s)", (uid, msg.peer_id, msg.from_id))
            add_log(cur, msg.peer_id, msg.from_id, uid, 'авто-бан', '3 предупреждения')
            await kick_user(msg.peer_id, uid)
            return await msg.answer(f"🚫 АВТО-БАН\n\n👤 {user_name}\n3 предупреждения")
        await msg.answer(f"⚠️ ПРЕДУПРЕЖДЕНИЕ\n\n👤 {user_name}\n📊 {warns}/3\n📝 {reason}")
    finally: conn.close()

@bot.on.message(text="/unwarn")
async def unwarn_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'unwarn')
    finally: conn.close()
    return await msg.answer(f"⚠️ /unwarn @user\nСнимает предупреждение\nТребуется роль {req}+")

@bot.on.message(text="/unwarn <target>")
async def unwarn_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'unwarn')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя снять предупреждение с равного или высшего")
        cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        if not res or res[0] <= 0: return await msg.answer("❌ У пользователя нет предупреждений")
        cur.execute("UPDATE users SET warn_count = GREATEST(warn_count - 1, 0) WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        warns = cur.fetchone()[0]
        add_log(cur, msg.peer_id, msg.from_id, uid, 'снятие предупреждения')
        await msg.answer(f"⚠️ ПРЕДУПРЕЖДЕНИЕ СНЯТО\n\n👤 {await get_user_name(uid)}\n📊 {warns}/3")
    finally: conn.close()

@bot.on.message(text="/mute")
async def mute_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'mute')
    finally: conn.close()
    return await msg.answer(f"🔇 /mute @user [время] [причина]\nФорматы: 10m, 1h, 1d\nТребуется роль {req}+")

@bot.on.message(text="/mute <target> <time_str> <reason>")
async def mute_full(msg: Message, target: str, time_str: str, reason: str): await process_mute(msg, target, time_str, reason)
@bot.on.message(text="/mute <target> <time_str>")
async def mute_simple(msg: Message, target: str, time_str: str): await process_mute(msg, target, time_str, "")

async def process_mute(msg, target, time_str, reason):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'mute')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя наказать равного или высшего")
        duration = None; final_reason = reason or "Без причины"
        parsed = parse_time(time_str)
        if parsed: duration = parsed
        else: final_reason = time_str + (" " + reason if reason else "")
        end_time = datetime.now() + duration if duration else None
        ft = format_time(duration) if duration else "навсегда"
        cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason, banned_by) VALUES (%s,%s,'mute',%s,%s,%s)", (uid, msg.peer_id, end_time, final_reason, msg.from_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'мут', final_reason)
        end_info = f"🔚 До: {end_time.strftime('%d.%m в %H:%M')}" if end_time else "⏰ Навсегда"
        await msg.answer(f"🔇 МУТ\n\n👤 {await get_user_name(uid)}\n⏰ {ft}\n{end_info}\n📝 {final_reason}")
    finally: conn.close()

@bot.on.message(text="/unmute")
async def unmute_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'unmute')
    finally: conn.close()
    return await msg.answer(f"🔊 /unmute @user\nТребуется роль {req}+")

@bot.on.message(text="/unmute <target>")
async def unmute_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'unmute')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя снять мут с равного или высшего")
        cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, msg.peer_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'снятие мута')
        await msg.answer(f"🔊 МУТ СНЯТ\n\n👤 {await get_user_name(uid)}")
    finally: conn.close()

@bot.on.message(text="/ban")
async def ban_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'ban')
    finally: conn.close()
    return await msg.answer(f"🚫 /ban @user [время] [причина]\nФорматы: 1h, 1d, permanent\nТребуется роль {req}+")

@bot.on.message(text="/ban <target> <time_str> <reason>")
async def ban_full(msg: Message, target: str, time_str: str, reason: str): await process_ban(msg, target, time_str, reason)
@bot.on.message(text="/ban <target> <time_str>")
async def ban_simple(msg: Message, target: str, time_str: str): await process_ban(msg, target, time_str, "")

async def process_ban(msg, target, time_str, reason):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'ban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя наказать равного или высшего")
        duration = None; final_reason = reason or "Без причины"
        if time_str.lower() != "permanent":
            parsed = parse_time(time_str)
            if parsed: duration = parsed
            else: final_reason = time_str + (" " + reason if reason else "")
        end_time = datetime.now() + duration if duration else None
        ft = format_time(duration) if duration else "навсегда"
        cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason, banned_by) VALUES (%s,%s,'ban',%s,%s,%s)", (uid, msg.peer_id, end_time, final_reason, msg.from_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'бан', final_reason)
        await kick_user(msg.peer_id, uid)
        await msg.answer(f"🚫 БАН\n\n👤 {await get_user_name(uid)}\n⏰ {ft}\n📝 {final_reason}")
    finally: conn.close()

@bot.on.message(text="/unban")
async def unban_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'unban')
    finally: conn.close()
    return await msg.answer(f"✅ /unban @user\nТребуется роль {req}+")

@bot.on.message(text="/unban <target>")
async def unban_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'unban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя разбанить равного или высшего")
        cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (uid, msg.peer_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'разбан')
        await msg.answer(f"✅ БАН СНЯТ\n\n👤 {await get_user_name(uid)}")
    finally: conn.close()

@bot.on.message(text="/getban")
async def getban_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'getban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
    finally: conn.close()
    return await msg.answer("🔍 /getban @user")

@bot.on.message(text="/getban <target>")
async def getban_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'getban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT reason, created_at, end_at, banned_by FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban' AND (end_at IS NULL OR end_at > NOW()) ORDER BY created_at DESC LIMIT 1", (uid, msg.peer_id))
        ban = cur.fetchone()
        user_name = await get_user_name(uid)
        if not ban: return await msg.answer(f"🔍 {user_name}\n✅ Не забанен")
        reason, created_at, end_at, banned_by = ban
        ban_type = f"⏰ Временный (до {end_at.strftime('%d.%m.%Y %H:%M')})" if end_at else "🚫 Перманентный"
        created_str = created_at.strftime('%d.%m.%Y в %H:%M') if created_at else "Неизвестно"
        banner_text = f"👮 Выдал: {await get_user_name(banned_by)}" if banned_by else "👮 Выдал: Неизвестно"
        await msg.answer(f"🔍 ИНФО О БАНЕ\n\n👤 {user_name}\n📋 {ban_type}\n📝 {reason}\n📅 {created_str}\n{banner_text}")
    finally: conn.close()

@bot.on.message(text="/kick")
async def kick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'kick')
    finally: conn.close()
    return await msg.answer(f"👢 /kick @user\nТребуется роль {req}+")

@bot.on.message(text="/kick <target>")
async def kick_cmd(msg: Message, target: str):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'kick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя кикнуть равного или высшего")
        await kick_user(msg.peer_id, uid)
        add_log(cur, msg.peer_id, msg.from_id, uid, 'кик')
        await msg.answer(f"👢 ИСКЛЮЧЁН\n\n👤 {await get_user_name(uid)}")
    finally: conn.close()

# =========================
# SNICK / RNICK / GNICK / NLIST / CLEARNICKS / STATS
# =========================
@bot.on.message(text="/snick")
async def snick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'snick')
    finally: conn.close()
    return await msg.answer(f"🏷 /snick @user [ник]\nМакс. 50 символов\nТребуется роль {req}+")

@bot.on.message(text="/snick <target> <nick>")
async def snick_cmd(msg: Message, target: str, nick: str):
    if msg.peer_id < 2000000000: return
    if len(nick) > 50: return await msg.answer("❌ Макс. 50 символов")
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'snick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        if uid == msg.from_id:
            cur.execute("INSERT INTO users (user_id, peer_id, nickname) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET nickname=%s", (uid, msg.peer_id, nick, nick))
            return await msg.answer(f"🏷 НИК: {nick}")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя установить ник равному или высшему")
        cur.execute("INSERT INTO users (user_id, peer_id, nickname) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET nickname=%s", (uid, msg.peer_id, nick, nick))
        add_log(cur, msg.peer_id, msg.from_id, uid, f'установка ника: {nick}')
        await msg.answer(f"🏷 НИК УСТАНОВЛЕН\n\n👤 {await get_user_name(uid)}\n🏷 {nick}")
    finally: conn.close()

@bot.on.message(text="/rnick")
async def rnick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try: req = get_cmd_required_role(cur, msg.peer_id, 'rnick')
    finally: conn.close()
    return await msg.answer(f"🧹 /rnick @user\nТребуется роль {req}+")

@bot.on.message(text="/rnick <target>")
async def rnick_cmd(msg: Message, target: str = ""):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or await resolve_user_id(target.replace("@", "").strip()) or msg.from_id
    conn, cur = db()
    try:
        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        if not res or not res[0]: return await msg.answer("❌ У пользователя нет ника")
        old_nick = res[0]
        if uid != msg.from_id:
            ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'rnick')
            if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
            if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя удалить ник равному или высшему")
        cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        add_log(cur, msg.peer_id, msg.from_id, uid, 'удаление ника')
        await msg.answer(f"🧹 НИК УДАЛЁН\n\n👤 {await get_user_name(uid)}\n❌ Был: {old_nick}")
    finally: conn.close()

@bot.on.message(text="/gnick")
async def gnick_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gnick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
    finally: conn.close()
    return await msg.answer("🏷 /gnick @user")

@bot.on.message(text="/gnick <target>")
async def gnick_cmd(msg: Message, target: str = ""):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or (await resolve_user_id(target.replace("@", "").strip()) if target else None) or msg.from_id
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'gnick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        user_name = await get_user_name(uid)
        if not res or not res[0]: return await msg.answer(f"🏷 НИК НЕ УСТАНОВЛЕН\n\n👤 {user_name}")
        await msg.answer(f"🏷 НИК\n\n👤 {user_name}\n🏷 {res[0]}")
    finally: conn.close()

@bot.on.message(text="/nlist")
async def nlist(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'nlist')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT user_id, nickname FROM users WHERE peer_id=%s AND nickname IS NOT NULL ORDER BY nickname", (msg.peer_id,))
        nicks = cur.fetchall()
        if not nicks: return await msg.answer("🏷 Нет ников\n/snick @user Ник")
        text = f"🏷 НИКИ ({len(nicks)}):\n\n"
        for uid, nick in nicks:
            try: name = await get_user_name(uid)
            except: name = f"ID {uid}"
            text += f"👤 @id{uid} ({name}) - {nick}\n"
        if len(text) > 4000:
            for i in range(0, len(text), 4000): await msg.answer(text[i:i+4000])
        else: await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/clearnicks")
async def clearnicks(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'clearnicks')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try:
            members = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
            member_ids = [m.member_id for m in members.items]
        except: return await msg.answer("❌ Не удалось получить список")
        cur.execute("SELECT user_id FROM users WHERE peer_id=%s AND nickname IS NOT NULL", (msg.peer_id,))
        removed = 0
        for (uid,) in cur.fetchall():
            if uid not in member_ids:
                cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
                removed += 1
        await msg.answer(f"🧹 Удалено ников: {removed}")
    finally: conn.close()

@bot.on.message(text="/stats")
async def stats_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'stats')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
    finally: conn.close()
    return await msg.answer("📊 /stats @user")

@bot.on.message(text="/stats <target>")
async def stats_cmd(msg: Message, target: str = ""):
    if msg.peer_id < 2000000000: return
    uid = get_target_id(msg) or (await resolve_user_id(target.replace("@", "").strip()) if target else None) or msg.from_id
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'stats')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        cur.execute("SELECT role, msgs, warn_count, nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        if not res:
            cur.execute("INSERT INTO users (user_id, peer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (uid, msg.peer_id))
            role, msgs, warns, nick = 0, 0, 0, None
        else: role, msgs, warns, nick = res
        name = await get_user_name(uid)
        status = "🚫 ЗАБАНЕН" if is_user_banned(msg.peer_id, uid) else "🔇 ЗАМУЧЕН" if is_user_muted(msg.peer_id, uid) else "✅ Активен"
        role_display = get_role_name(cur, msg.peer_id, role)
        bot_role = get_bot_role(cur, uid)
        marriage = get_marriage(cur, msg.peer_id, uid)
        text = f"📊 СТАТИСТИКА\n\n👤 {name}\n🆔 ID: {uid}\n📊 Статус: {status}\n🎖️ Роль: {role_display}\n"
        if bot_role: text += f"🏅 Должность: {bot_role}\n"
        if marriage:
            spouse_id = marriage[0] if marriage[0] != uid else marriage[1]
            try: spouse_name = await get_user_name(spouse_id)
            except: spouse_name = f"id{spouse_id}"
            text += f"💍 В браке с: {spouse_name}\n"
        text += f"💬 Сообщений: {msgs}\n⚠️ Предупреждений: {warns}/3"
        if nick: text += f"\n🏷 Ник: {nick}"
        await msg.answer(text)
    finally: conn.close()

# =========================
# TOP / ACTIVITY / LOG
# =========================
@bot.on.message(text="/top")
async def top_help(msg: Message):
    if msg.peer_id < 2000000000: return
    return await msg.answer("📊 /top [количество]\nПример: /top 10")

@bot.on.message(text="/top <count>")
async def top_cmd(msg: Message, count: str = "10"):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        try: limit = int(count)
        except: limit = 10
        if limit < 1: limit = 1
        if limit > 50: limit = 50
        cur.execute("SELECT user_id, msgs FROM users WHERE peer_id=%s AND msgs>0 ORDER BY msgs DESC LIMIT %s", (msg.peer_id, limit))
        top_users = cur.fetchall()
        if not top_users: return await msg.answer("📊 ТОП ПО СООБЩЕНИЯМ\n\n❌ Нет данных")
        text = f"📊 ТОП {limit} ПО СООБЩЕНИЯМ\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, msgs) in enumerate(top_users):
            try: name = await get_user_name(user_id)
            except: name = f"id{user_id}"
            prefix = medals[i] if i < 3 else f"{i+1}."
            text += f"{prefix} {name} — {msgs} сообщ.\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/activity")
async def activity_today(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        cur.execute("SELECT COUNT(*) FROM users WHERE peer_id=%s AND msgs>0", (msg.peer_id,)); active_today = cur.fetchone()[0]
        cur.execute("SELECT SUM(msgs) FROM users WHERE peer_id=%s", (msg.peer_id,)); total_msgs = cur.fetchone()[0] or 0
        cur.execute("SELECT user_id, msgs FROM users WHERE peer_id=%s ORDER BY msgs DESC LIMIT 1", (msg.peer_id,)); top_user = cur.fetchone()
        text = "📈 АКТИВНОСТЬ БЕСЕДЫ\n\n"
        text += f"👥 Участников с сообщениями: {active_today}\n"
        text += f"💬 Всего сообщений: {total_msgs}\n"
        if top_user:
            try: name = await get_user_name(top_user[0])
            except: name = f"id{top_user[0]}"
            text += f"🔥 Самый активный: {name} ({top_user[1]} сообщ.)\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/activity week")
async def activity_week(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        week_ago = datetime.now() - timedelta(days=7)
        cur.execute("SELECT COUNT(*) FROM moderation_logs WHERE peer_id=%s AND created_at > %s", (msg.peer_id, week_ago)); actions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM punishments WHERE peer_id=%s AND created_at > %s", (msg.peer_id, week_ago)); punishments = cur.fetchone()[0]
        text = "📈 АКТИВНОСТЬ ЗА НЕДЕЛЮ\n\n"
        text += f"🛡 Действий модерации: {actions}\n"
        text += f"⚠️ Наказаний: {punishments}\n"
        text += f"📅 с {week_ago.strftime('%d.%m')} по {datetime.now().strftime('%d.%m')}\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/log")
async def log_help(msg: Message):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'log')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
    finally: conn.close()
    return await msg.answer("📝 /log [количество]\nПример: /log 10")

@bot.on.message(text="/log <count>")
async def log_cmd(msg: Message, count: str = "10"):
    if msg.peer_id < 2000000000: return
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'log')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try: limit = int(count)
        except: limit = 10
        if limit < 1: limit = 1
        if limit > 50: limit = 50
        cur.execute("SELECT moderator_id, target_id, action, reason, created_at FROM moderation_logs WHERE peer_id=%s ORDER BY id DESC LIMIT %s", (msg.peer_id, limit))
        logs = cur.fetchall()
        if not logs: return await msg.answer("📝 ЛОГИ МОДЕРАЦИИ\n\n❌ Нет записей")
        text = f"📝 ЛОГИ МОДЕРАЦИИ (последние {len(logs)}):\n\n"
        for i, (mod_id, target_id, action, reason, created_at) in enumerate(logs, 1):
            try: mod_name = await get_user_name(mod_id)
            except: mod_name = f"id{mod_id}"
            try: target_name = await get_user_name(target_id)
            except: target_name = f"id{target_id}"
            time_str = created_at.strftime('%H:%M') if created_at else "?"
            reason_str = f" — {reason}" if reason else ""
            text += f"{i}. {mod_name} {action} {target_name}{reason_str} | {time_str}\n"
        await msg.answer(text)
    finally: conn.close()

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()
    try:
        uid, pid = msg.from_id, msg.peer_id
        
        if pid < 2000000000 and uid != OWNER_ID:
            bot_role = get_bot_role(cur, uid)
            if not bot_role: return
        
        if pid < 2000000000: return
        
        if msg.action and msg.action.type == 'chat_kick_user':
            if msg.action.member_id == uid:
                cur.execute("UPDATE users SET role=0, nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, pid))
                await kick_user(pid, uid)
                name = await get_user_name(uid)
                await msg.answer(f"👢 @id{uid} ({name}) вышел из чата\n📊 Роль сброшена\n\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}")
                return
        
        if not msg.text: return
        
        # Сохраняем ID сообщения для команды /clear
        if hasattr(msg, 'conversation_message_id') and pid > 2000000000:
            try: cur.execute("INSERT INTO user_messages (peer_id, user_id, message_id) VALUES (%s,%s,%s)", (pid, uid, msg.conversation_message_id))
            except: pass
        
        if is_user_banned(pid, uid):
            await kick_user(pid, uid)
            return
        
        if is_user_muted(pid, uid):
            if hasattr(msg, 'conversation_message_id'):
                await delete_message(pid, msg.conversation_message_id)
            return
        
        if msg.action and msg.action.type == 'chat_invite_user':
            inviter_role = get_user_role(cur, pid, uid)
            if inviter_role <= 0 and uid != OWNER_ID:
                invited_uid = msg.action.member_id
                await kick_user(pid, invited_uid)
                await msg.answer("🛡 ЗАЩИТА ОТ ПРИГЛАШЕНИЙ\n\nОбычные пользователи не могут приглашать\nПриглашённый исключён")
                return
        
        cur.execute("INSERT INTO users (user_id, peer_id, msgs) VALUES (%s,%s,1) ON CONFLICT (user_id, peer_id) DO UPDATE SET msgs=users.msgs+1", (uid, pid))
    except Exception as e:
        logger.error(f"ERROR in handler: {e}")
    finally: conn.close()

# =========================
# PERIODIC CHECK
# =========================
async def check_expired():
    while True:
        try:
            conn, cur = db()
            try:
                cur.execute("SELECT user_id, peer_id FROM punishments WHERE type='ban' AND end_at IS NOT NULL AND end_at < NOW()")
                bans = cur.fetchall()
                cur.execute("DELETE FROM punishments WHERE end_at IS NOT NULL AND end_at < NOW()")
                for uid, pid in bans:
                    try: await bot.api.messages.send(peer_id=pid, message=f"✅ СРОК БАНА ИСТЁК\n👤 @id{uid} может вернуться", random_id=0)
                    except: pass
            finally: conn.close()
        except Exception as e:
            logger.error(f"ERROR in check_expired: {e}")
        await asyncio.sleep(60)

# =========================
# STARTUP
# =========================
if __name__ == "__main__":
    logger.info(">>> BOT STARTING...")
    print(">>> FLEX BOT STARTED")
    print(f">>> Owner ID: {OWNER_ID}")
    loop = asyncio.get_event_loop()
    loop.create_task(check_expired())
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        print("\n>>> Stopped")
    except Exception as e:
        print(f">>> FATAL ERROR: {e}")
        traceback.print_exc()
