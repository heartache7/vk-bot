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
            cur.execute("DROP TABLE IF EXISTS roles CASCADE")
            cur.execute("DROP TABLE IF EXISTS cmd_permissions CASCADE")
            cur.execute("DROP TABLE IF EXISTS punishments CASCADE")
            cur.execute("DROP TABLE IF EXISTS users CASCADE")
            
            cur.execute("""
            CREATE TABLE users(
                user_id BIGINT,
                peer_id BIGINT,
                role INT DEFAULT 0,
                msgs INT DEFAULT 0,
                nickname TEXT,
                warn_count INT DEFAULT 0,
                warn_reasons TEXT DEFAULT '',
                PRIMARY KEY (user_id, peer_id)
            );
            """)

            cur.execute("""
            CREATE TABLE punishments(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                peer_id BIGINT,
                type TEXT,
                end_at TIMESTAMP,
                reason TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """)

            cur.execute("""
            CREATE TABLE roles(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                role_priority INT,
                role_name TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, role_priority)
            );
            """)

            cur.execute("""
            CREATE TABLE cmd_permissions(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                cmd_name TEXT,
                required_role INT DEFAULT 10,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, cmd_name)
            );
            """)

            default_permissions = [
                ('warn', 10), ('mute', 10), ('unmute', 10),
                ('ban', 50), ('unban', 50), ('kick', 10),
                ('snick', 10), ('rnick', 10), ('giverole', 60),
                ('stats', 0), ('addrole', 50), ('removerole', 60),
                ('delrole', 60), ('nlist', 0), ('clearnicks', 60)
            ]
            
            for cmd, role in default_permissions:
                cur.execute("""
                INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
                VALUES (0, %s, %s)
                ON CONFLICT (peer_id, cmd_name) 
                DO UPDATE SET required_role = EXCLUDED.required_role
                """, (cmd, role))

            logger.info(">>> DB OK")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"DB INIT ERROR: {e}")
        raise

init()

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
    except:
        return f"Пользователь {uid}"

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

def get_cmd_required_role(cur, peer_id, cmd_name):
    # Сначала ищем настройку для конкретной беседы
    cur.execute("SELECT required_role FROM cmd_permissions WHERE peer_id=%s AND cmd_name=%s", (peer_id, cmd_name))
    res = cur.fetchone()
    if res: return res[0]
    # Если нет - ищем глобальную
    cur.execute("SELECT required_role FROM cmd_permissions WHERE peer_id=0 AND cmd_name=%s", (cmd_name,))
    res = cur.fetchone()
    return res[0] if res else 0

def check_permission(cur, peer_id, user_id, cmd_name):
    """Проверяет права на команду. Возвращает (разрешено, роль_юзер, требуемая_роль)"""
    if user_id == OWNER_ID: return True, 0, 0
    user_role = get_user_role(cur, peer_id, user_id)
    required_role = get_cmd_required_role(cur, peer_id, cmd_name)
    return user_role >= required_role, user_role, required_role

def can_punish(cur, peer_id, punisher_id, target_id):
    """Проверяет иерархию: punisher должен быть СТРОГО ВЫШЕ target"""
    if punisher_id == OWNER_ID: return True
    if punisher_id == target_id: return True  # Себя можно
    punisher_role = get_user_role(cur, peer_id, punisher_id)
    target_role = get_user_role(cur, peer_id, target_id)
    return punisher_role > target_role

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

# =========================
# HELP
# =========================
@bot.on.message(text="/help")
async def help_cmd(msg: Message):
    return await msg.answer(
        "💠 FLEX BOT\n\n"
        "🏷 /snick /rnick /nlist /clearnicks\n"
        "⚠️ /warn /mute /unmute /ban /unban /kick\n"
        "🎖️ /giverole /removerole /delrole /addrole /roles /staff\n"
        "📊 /stats\n⚙️ /setcmd\n👑 /sysrole\n\n"
        "🛡 Иерархия: нельзя трогать равных и высших\n"
        "💡 Все команды настраиваются через /setcmd"
    )

# =========================
# START
# =========================
@bot.on.message(text="/start")
async def start(msg: Message):
    conn, cur = db()
    try:
        try:
            res = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
        except:
            return await msg.answer("❌ Нужны права администратора")
        
        for m in res.items:
            if getattr(m, "is_owner", False):
                cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,100) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=100", (m.member_id, msg.peer_id))
                cur.execute("INSERT INTO cmd_permissions (peer_id, cmd_name, required_role) SELECT %s, cmd_name, required_role FROM cmd_permissions WHERE peer_id=0 ON CONFLICT DO NOTHING", (msg.peer_id,))
                return await msg.answer(f"✅ БОТ АКТИВИРОВАН\n👑 Создатель получил роль 100\n/help - список команд")
        return await msg.answer("❌ Не удалось найти создателя")
    finally: conn.close()

# =========================
# SETCMD
# =========================
@bot.on.message(text="/setcmd")
async def setcmd_help(msg: Message):
    conn, cur = db()
    try:
        cur.execute("""
        SELECT DISTINCT ON (cmd_name) cmd_name, required_role 
        FROM cmd_permissions 
        WHERE peer_id=%s OR peer_id=0 
        ORDER BY cmd_name, peer_id DESC
        """, (msg.peer_id,))
        perms = cur.fetchall()
        text = "📊 ПРАВА КОМАНД:\n\n"
        for cmd, role in perms:
            if cmd != 'sysrole': text += f"/{cmd} - роль {role}+\n"
        return await msg.answer(f"⚙️ /setcmd [команда] [приоритет]\nПример: /setcmd ban 30\n\n{text}")
    finally: conn.close()

@bot.on.message(text="/setcmd <cmd_name> <priority>")
async def setcmd(msg: Message, cmd_name: str, priority: str):
    conn, cur = db()
    try:
        if cmd_name.lower() == "sysrole":
            return await msg.answer("❌ Нельзя изменить /sysrole")
        
        try:
            p = int(priority)
        except:
            return await msg.answer("❌ Приоритет должен быть числом")
        
        if p < 0 or p > 1000:
            return await msg.answer("❌ Приоритет от 0 до 1000")
        
        # Вставляем или обновляем настройку для беседы
        cur.execute("""
        INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
        VALUES (%s, %s, %s)
        ON CONFLICT (peer_id, cmd_name) 
        DO UPDATE SET required_role = %s
        """, (msg.peer_id, cmd_name.lower(), p, p))
        
        await msg.answer(f"✅ /{cmd_name.lower()} - роль {p}+")
    
    except Exception as e:
        logger.error(f"ERROR in setcmd: {e}")
        return await msg.answer("❌ Ошибка при настройке прав")
    finally: conn.close()

# =========================
# SYSRole
# =========================
@bot.on.message(text="/sysrole")
async def sysrole_help(msg: Message):
    if msg.from_id != OWNER_ID: return await msg.answer("❌ Только для владельца бота")
    return await msg.answer("👑 /sysrole @user [приоритет]")

@bot.on.message(text="/sysrole <target> <priority>")
async def sysrole_cmd(msg: Message, target: str, priority: str):
    if msg.from_id != OWNER_ID: return await msg.answer("❌ Только для владельца бота")
    uid = get_target_id(msg)
    if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    try: p = int(priority)
    except: return await msg.answer("❌ Приоритет - число")
    conn, cur = db()
    try:
        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (uid, msg.peer_id, p, p))
        user_name = await get_user_name(uid)
        await msg.answer(f"🎖️ РОЛЬ ВЫДАНА\n👤 {user_name}\n📊 Приоритет: {p}")
    finally: conn.close()

# =========================
# ADDROLE
# =========================
@bot.on.message(text="/addrole")
async def addrole_help(msg: Message):
    return await msg.answer("📋 /addrole [приоритет] [имя]\nПример: /addrole 50 Модератор")

@bot.on.message(text="/addrole <priority> <role_name>")
async def addrole(msg: Message, priority: str, role_name: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'addrole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer("❌ Нельзя создать роль выше или равную вашей")
        if len(role_name) > 30: return await msg.answer("❌ Макс. 30 символов")
        cur.execute("INSERT INTO roles (peer_id, role_priority, role_name) VALUES (%s,%s,%s) ON CONFLICT (peer_id, role_priority) DO UPDATE SET role_name=%s", (msg.peer_id, p, role_name, role_name))
        await msg.answer(f"✅ Роль: {role_name} (приоритет {p})")
    finally: conn.close()

# =========================
# GIVEROLE
# =========================
@bot.on.message(text="/giverole")
async def giverole_help(msg: Message):
    return await msg.answer("🎖️ /giverole @user [приоритет]")

@bot.on.message(text="/giverole <target> <priority>")
async def giverole_cmd(msg: Message, target: str, priority: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'giverole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя выдать роль равному или высшему\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer(f"❌ Нельзя выдать роль {p} (у вас {user_role})")
        cur.execute("INSERT INTO users (user_id, peer_id, role) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET role=%s", (uid, msg.peer_id, p, p))
        user_name = await get_user_name(uid)
        await msg.answer(f"🎖️ РОЛЬ ВЫДАНА\n👤 {user_name}\n📊 Приоритет: {p}")
    finally: conn.close()

# =========================
# REMOVEROLE
# =========================
@bot.on.message(text="/removerole")
async def removerole_help(msg: Message):
    return await msg.answer("🗑 /removerole @user - сбросить роль")

@bot.on.message(text="/removerole <target>")
async def removerole_cmd(msg: Message, target: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'removerole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя снять роль у равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("UPDATE users SET role=0 WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        user_name = await get_user_name(uid)
        await msg.answer(f"🗑 РОЛЬ СБРОШЕНА\n👤 {user_name}\n📊 Приоритет: 0")
    finally: conn.close()

# =========================
# DELROLE
# =========================
@bot.on.message(text="/delrole")
async def delrole_help(msg: Message):
    return await msg.answer("🗑 /delrole [приоритет] - удалить роль из базы")

@bot.on.message(text="/delrole <priority>")
async def delrole_cmd(msg: Message, priority: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'delrole')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        try: p = int(priority)
        except: return await msg.answer("❌ Приоритет - число")
        if p >= user_role and msg.from_id != OWNER_ID: return await msg.answer(f"❌ Нельзя удалить роль выше или равную вашей (у вас {user_role})")
        cur.execute("SELECT role_name FROM roles WHERE peer_id=%s AND role_priority=%s", (msg.peer_id, p))
        role_data = cur.fetchone()
        if not role_data: return await msg.answer(f"❌ Роль с приоритетом {p} не найдена")
        cur.execute("DELETE FROM roles WHERE peer_id=%s AND role_priority=%s", (msg.peer_id, p))
        await msg.answer(f"🗑 РОЛЬ УДАЛЕНА\n📋 {role_data[0]}\n📊 Приоритет: {p}")
    finally: conn.close()

# =========================
# ROLES / STAFF
# =========================
@bot.on.message(text="/roles")
async def list_roles(msg: Message):
    conn, cur = db()
    try:
        cur.execute("SELECT role_priority, role_name FROM roles WHERE peer_id=%s ORDER BY role_priority DESC", (msg.peer_id,))
        roles = cur.fetchall()
        if not roles: return await msg.answer("📊 Нет ролей\n/addrole 50 Модератор")
        text = "📊 РОЛИ:\n\n"
        for p, n in roles: text += f"📋 {p} - {n}\n"
        await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/staff")
async def staff(msg: Message):
    conn, cur = db()
    try:
        cur.execute("SELECT user_id, role FROM users WHERE peer_id=%s AND role>0 ORDER BY role DESC LIMIT 50", (msg.peer_id,))
        staff = cur.fetchall()
        if not staff: return await msg.answer("👥 Нет персонала")
        text = "👥 ПЕРСОНАЛ:\n\n"
        for uid, role in staff:
            try: name = await get_user_name(uid)
            except: name = f"ID {uid}"
            s = "🚫" if is_user_banned(msg.peer_id, uid) else "🔇" if is_user_muted(msg.peer_id, uid) else "✅"
            text += f"{s} @id{uid} ({name}) - роль {role}\n"
        await msg.answer(text)
    finally: conn.close()

# =========================
# WARN
# =========================
@bot.on.message(text="/warn")
async def warn_help(msg: Message):
    return await msg.answer("⚠️ /warn @user [причина]\n3 предупреждения = бан")

@bot.on.message(text="/warn <target> <reason>")
async def warn_cmd(msg: Message, target: str, reason: str = "Без причины"):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'warn')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя наказать равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("INSERT INTO users (user_id, peer_id, warn_count, warn_reasons) VALUES (%s,%s,1,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET warn_count=users.warn_count+1, warn_reasons=CASE WHEN users.warn_reasons='' THEN EXCLUDED.warn_reasons ELSE users.warn_reasons||' | '||EXCLUDED.warn_reasons END", (uid, msg.peer_id, reason))
        cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        warns = cur.fetchone()[0]
        user_name = await get_user_name(uid)
        if warns >= 3:
            cur.execute("INSERT INTO punishments (user_id, peer_id, type, reason) VALUES (%s,%s,'ban','Авто-бан (3 пред.)')", (uid, msg.peer_id))
            await kick_user(msg.peer_id, uid)
            return await msg.answer(f"🚫 АВТО-БАН\n👤 {user_name}\n3 предупреждения")
        await msg.answer(f"⚠️ ПРЕДУПРЕЖДЕНИЕ\n👤 {user_name}\n{warns}/3\n📝 {reason}")
    finally: conn.close()

# =========================
# MUTE
# =========================
@bot.on.message(text="/mute")
async def mute_help(msg: Message):
    return await msg.answer("🔇 /mute @user [время] [причина]\nФорматы: 10m, 1h, 1d")

@bot.on.message(text="/mute <target> <time_str> <reason>")
async def mute_full(msg: Message, target: str, time_str: str, reason: str):
    await process_mute_cmd(msg, target, time_str, reason)

@bot.on.message(text="/mute <target> <time_str>")
async def mute_simple(msg: Message, target: str, time_str: str):
    await process_mute_cmd(msg, target, time_str, "")

async def process_mute_cmd(msg: Message, target: str, time_str: str, reason: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'mute')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя наказать равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        duration = None
        final_reason = reason if reason else "Без причины"
        parsed = parse_time(time_str)
        if parsed: duration = parsed
        else: final_reason = time_str + (" " + reason if reason else "")
        end_time = datetime.now() + duration if duration else None
        ft = format_time(duration) if duration else "навсегда"
        cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason) VALUES (%s,%s,'mute',%s,%s)", (uid, msg.peer_id, end_time, final_reason))
        user_name = await get_user_name(uid)
        await msg.answer(f"🔇 МУТ\n👤 {user_name}\n⏰ {ft}\n📝 {final_reason}")
    finally: conn.close()

# =========================
# UNMUTE
# =========================
@bot.on.message(text="/unmute")
async def unmute_help(msg: Message):
    return await msg.answer("🔊 /unmute @user")

@bot.on.message(text="/unmute <target>")
async def unmute_cmd(msg: Message, target: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'unmute')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя снять мут равному или высшему\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='mute'", (uid, msg.peer_id))
        await msg.answer("🔊 Мут снят")
    finally: conn.close()

# =========================
# BAN
# =========================
@bot.on.message(text="/ban")
async def ban_help(msg: Message):
    return await msg.answer("🚫 /ban @user [время] [причина]\nФорматы: 1h, 1d, permanent")

@bot.on.message(text="/ban <target> <time_str> <reason>")
async def ban_full(msg: Message, target: str, time_str: str, reason: str):
    await process_ban_cmd(msg, target, time_str, reason)

@bot.on.message(text="/ban <target> <time_str>")
async def ban_simple(msg: Message, target: str, time_str: str):
    await process_ban_cmd(msg, target, time_str, "")

async def process_ban_cmd(msg: Message, target: str, time_str: str, reason: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'ban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя наказать равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        duration = None
        final_reason = reason if reason else "Без причины"
        if time_str.lower() != "permanent":
            parsed = parse_time(time_str)
            if parsed: duration = parsed
            else: final_reason = time_str + (" " + reason if reason else "")
        end_time = datetime.now() + duration if duration else None
        ft = format_time(duration) if duration else "навсегда"
        cur.execute("INSERT INTO punishments (user_id, peer_id, type, end_at, reason) VALUES (%s,%s,'ban',%s,%s)", (uid, msg.peer_id, end_time, final_reason))
        await kick_user(msg.peer_id, uid)
        user_name = await get_user_name(uid)
        await msg.answer(f"🚫 БАН\n👤 {user_name}\n⏰ {ft}\n📝 {final_reason}")
    finally: conn.close()

# =========================
# UNBAN
# =========================
@bot.on.message(text="/unban")
async def unban_help(msg: Message):
    return await msg.answer("✅ /unban @user")

@bot.on.message(text="/unban <target>")
async def unban_cmd(msg: Message, target: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'unban')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя разбанить равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("DELETE FROM punishments WHERE user_id=%s AND peer_id=%s AND type='ban'", (uid, msg.peer_id))
        await msg.answer("✅ Бан снят")
    finally: conn.close()

# =========================
# KICK
# =========================
@bot.on.message(text="/kick")
async def kick_help(msg: Message):
    return await msg.answer("👢 /kick @user")

@bot.on.message(text="/kick <target>")
async def kick_cmd(msg: Message, target: str):
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'kick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        uid = get_target_id(msg)
        if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
        if not uid: return await msg.answer("❌ Пользователь не найден")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя кикнуть равного или высшего\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        await kick_user(msg.peer_id, uid)
        await msg.answer("👢 Исключён")
    finally: conn.close()

# =========================
# SNICK
# =========================
@bot.on.message(text="/snick")
async def snick_help(msg: Message):
    return await msg.answer("🏷 /snick @user [ник]\nМакс. 50 символов")

@bot.on.message(text="/snick <target> <nick>")
async def snick_cmd(msg: Message, target: str, nick: str):
    if len(nick) > 50: return await msg.answer("❌ Макс. 50 символов")
    uid = get_target_id(msg)
    if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
    if not uid: return await msg.answer("❌ Пользователь не найден")
    conn, cur = db()
    try:
        ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'snick')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
        if uid == msg.from_id:
            cur.execute("INSERT INTO users (user_id, peer_id, nickname) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET nickname=%s", (uid, msg.peer_id, nick, nick))
            return await msg.answer(f"🏷 НИК: {nick}")
        if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ Нельзя установить ник равному или высшему\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("INSERT INTO users (user_id, peer_id, nickname) VALUES (%s,%s,%s) ON CONFLICT (user_id, peer_id) DO UPDATE SET nickname=%s", (uid, msg.peer_id, nick, nick))
        user_name = await get_user_name(uid)
        await msg.answer(f"🏷 НИК УСТАНОВЛЕН\n👤 {user_name}\n🏷 {nick}")
    finally: conn.close()

# =========================
# RNICK
# =========================
@bot.on.message(text="/rnick")
async def rnick_help(msg: Message):
    return await msg.answer("🧹 /rnick @user")

@bot.on.message(text="/rnick <target>")
async def rnick_cmd(msg: Message, target: str = ""):
    uid = get_target_id(msg)
    if not uid: uid = await resolve_user_id(target.replace("@", "").strip())
    if not uid: uid = msg.from_id
    conn, cur = db()
    try:
        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        if not res or not res[0]: return await msg.answer(f"❌ Ник не установлен")
        old_nick = res[0]
        if uid != msg.from_id:
            ok, user_role, req = check_permission(cur, msg.peer_id, msg.from_id, 'rnick')
            if not ok: return await msg.answer(f"❌ Требуется роль {req}+ (у вас {user_role})")
            if not can_punish(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
                target_role = get_user_role(cur, msg.peer_id, uid)
                return await msg.answer(f"❌ Нельзя удалить ник равному или высшему\n📊 Ваш приоритет: {user_role}\n📊 Приоритет цели: {target_role}")
        cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        user_name = await get_user_name(uid)
        await msg.answer(f"🧹 НИК УДАЛЁН\n👤 {user_name}\n❌ Был: {old_nick}")
    finally: conn.close()

# =========================
# NLIST / CLEARNICKS / STATS
# =========================
@bot.on.message(text="/nlist")
async def nlist(msg: Message):
    conn, cur = db()
    try:
        cur.execute("SELECT user_id, nickname FROM users WHERE peer_id=%s AND nickname IS NOT NULL ORDER BY nickname", (msg.peer_id,))
        nicks = cur.fetchall()
        if not nicks: return await msg.answer("🏷 Нет ников")
        text = f"🏷 НИКИ ({len(nicks)})\n\n"
        for uid, nick in nicks:
            try: name = await get_user_name(uid)
            except: name = f"ID {uid}"
            text += f"👤 @id{uid} ({name}) - {nick}\n"
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                await msg.answer(text[i:i+4000])
        else:
            await msg.answer(text)
    finally: conn.close()

@bot.on.message(text="/clearnicks")
async def clearnicks(msg: Message):
    conn, cur = db()
    try:
        ok, _, req = check_permission(cur, msg.peer_id, msg.from_id, 'clearnicks')
        if not ok: return await msg.answer(f"❌ Требуется роль {req}+")
        try:
            members = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
            member_ids = [m.member_id for m in members.items]
        except:
            return await msg.answer("❌ Не удалось получить список")
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
    return await msg.answer("📊 /stats @user")

@bot.on.message(text="/stats <target>")
async def stats_cmd(msg: Message, target: str = ""):
    uid = get_target_id(msg)
    if not uid and target: uid = await resolve_user_id(target.replace("@", "").strip())
    if not uid: uid = msg.from_id
    conn, cur = db()
    try:
        cur.execute("SELECT role, msgs, warn_count, nickname FROM users WHERE user_id=%s AND peer_id=%s", (uid, msg.peer_id))
        res = cur.fetchone()
        if not res: return await msg.answer("❌ Нет данных")
        role, msgs, warns, nick = res
        name = await get_user_name(uid)
        status = "🚫 БАН" if is_user_banned(msg.peer_id, uid) else "🔇 МУТ" if is_user_muted(msg.peer_id, uid) else "✅ Ок"
        text = f"📊 {name}\n📊 {status}\n🎖️ Роль: {role}\n💬 Сообщений: {msgs}\n⚠️ Варнов: {warns}/3"
        if nick: text += f"\n🏷 Ник: {nick}"
        await msg.answer(text)
    finally: conn.close()

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()
    try:
        if not msg.text: return
        uid, pid = msg.from_id, msg.peer_id
        
        # Авто-кик при выходе + снятие роли
        if msg.action and msg.action.type == 'chat_kick_user':
            if msg.action.member_id == uid:
                cur.execute("UPDATE users SET role=0, nickname=NULL WHERE user_id=%s AND peer_id=%s", (uid, pid))
                await kick_user(pid, uid)
                name = await get_user_name(uid)
                await msg.answer(f"👢 @id{uid} ({name}) вышел из чата\n📊 Роль сброшена\n\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}")
                return
        
        # Авто-кик забаненных
        if is_user_banned(pid, uid):
            await kick_user(pid, uid)
            return
        
        # Авто-удаление сообщений замученных
        if is_user_muted(pid, uid):
            if hasattr(msg, 'conversation_message_id'):
                await delete_message(pid, msg.conversation_message_id)
            return
        
        # Защита от приглашений обычными пользователями
        if msg.action and msg.action.type == 'chat_invite_user':
            inviter_role = get_user_role(cur, pid, uid)
            if inviter_role <= 0 and uid != OWNER_ID:
                invited_uid = msg.action.member_id
                await kick_user(pid, invited_uid)
                await msg.answer("🛡 Обычные пользователи не могут приглашать\nПриглашённый исключён")
                return
        
        # Обновление статистики
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
