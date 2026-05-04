import os
import re
import traceback
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message

OWNER_ID = 676081199
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=VK_TOKEN)

# =========================
# DB
# =========================
def db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init():
    conn, cur = db()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
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
        CREATE TABLE IF NOT EXISTS punishments(
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
        CREATE TABLE IF NOT EXISTS roles(
            id SERIAL PRIMARY KEY,
            peer_id BIGINT,
            role_priority INT,
            role_name TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(peer_id, role_priority)
        );
        """)

        print(">>> DB OK")

    except Exception as e:
        print("DB ERROR:", e)
    finally:
        conn.close()

init()

# =========================
# UTILS
# =========================
def parse_time(t):
    if not t:
        return None
    m = re.match(r"(\d+)([mhd])", t.lower())
    if not m:
        return None
    v, u = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=v), "h": timedelta(hours=v), "d": timedelta(days=v)}[u]

def format_time(td):
    if not td:
        return "навсегда"
    
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} м.")
    
    return " ".join(parts) if parts else "менее минуты"

def extract(msg: Message):
    """Извлекает ID пользователя из сообщения или reply"""
    if msg.reply_message:
        return msg.reply_message.from_id
    if not msg.text:
        return None
    r = re.search(r"id(\d+)|\[id(\d+)\|", msg.text)
    return int(r.group(1) or r.group(2)) if r else None

async def get_user_name(uid):
    try:
        user = await bot.api.users.get(user_ids=uid, name_case='nom')
        return f"{user[0].first_name} {user[0].last_name}"
    except:
        return f"Пользователь {uid}"

def get_user_role(cur, peer_id, user_id):
    """Получает роль пользователя в беседе"""
    if user_id == OWNER_ID:
        return 1000
    cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (user_id, peer_id))
    res = cur.fetchone()
    return res[0] if res else 0

def get_ban_info(cur, peer_id, user_id):
    """Получает информацию о бане пользователя"""
    cur.execute("""
    SELECT reason, end_at FROM punishments
    WHERE user_id=%s AND peer_id=%s AND type='ban' 
    AND (end_at IS NULL OR end_at > NOW())
    """, (user_id, peer_id))
    return cur.fetchone()

def can_punish_user(cur, peer_id, punisher_id, target_id):
    """Проверяет, может ли punisher наказать target'а"""
    punisher_role = get_user_role(cur, peer_id, punisher_id)
    target_role = get_user_role(cur, peer_id, target_id)
    return punisher_role > target_role

def get_default_cmd_role(cmd_name):
    """Возвращает роль по умолчанию для команды"""
    defaults = {
        'warn': 10,
        'mute': 10,
        'unmute': 10,
        'ban': 50,
        'unban': 50,
        'kick': 10,
        'snick': 0,
        'rnick': 0,
        'giverole': 60,
        'stats': 0,
    }
    return defaults.get(cmd_name, 0)

def get_role_name(cur, peer_id, priority):
    """Получает имя роли по приоритету"""
    cur.execute("""
    SELECT role_name FROM roles
    WHERE peer_id=%s AND role_priority=%s
    """, (peer_id, priority))
    res = cur.fetchone()
    return res[0] if res else f"Уровень {priority}"

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
            return await msg.answer(
                "👋 ПРИВЕТ, Я FLEX BOT!\n\n"
                "🎉 Спасибо за приглашение в вашу беседу!\n\n"
                "⚠️ ОШИБКА ПРАВ ДОСТУПА\n\n"
                "🔧 Что нужно сделать:\n"
                "1️⃣ Откройте настройки беседы\n"
                "2️⃣ Перейдите в 'Управление ботами'\n"
                "3️⃣ Выдайте FLEX BOT права администратора\n"
                "4️⃣ Убедитесь, что включено удаление сообщений\n\n"
                "После этого напишите /start ещё раз ⭐"
            )

        for m in res.items:
            if getattr(m, "is_owner", False):
                cur.execute("""
                INSERT INTO users (user_id, peer_id, role)
                VALUES (%s, %s, 100)
                ON CONFLICT (user_id, peer_id)
                DO UPDATE SET role=100
                """, (m.member_id, msg.peer_id))

                owner_name = await get_user_name(m.member_id)

                return await msg.answer(
                    "✅ БОТ ИНИЦИАЛИЗИРОВАН\n\n"
                    "👑 FLEX BOT активирован!\n\n"
                    f"🎖️ @id{m.member_id} ({owner_name}) - роль 100\n\n"
                    "📖 /help для списка команд"
                )

    except Exception as e:
        print(f"ERROR in start: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# HELP
# =========================
@bot.on.message(text="/help")
async def help_cmd(msg: Message):
    conn, cur = db()
    try:
        user_role = get_user_role(cur, msg.peer_id, msg.from_id)
        is_owner = msg.from_id == OWNER_ID
        
        text = "💠 FLEX BOT - КОМАНДЫ\n\n"
        
        text += "🏷 НИКИ (всегда):\n"
        text += "/snick [ник] - Установить ник\n"
        text += "/rnick [@user] - Удалить ник\n\n"
        
        text += "📊 СТАТИСТИКА (всегда):\n"
        text += "/stats [@user] - Статистика\n"
        text += "/roles - Список ролей\n"
        text += "/staff - Список модераторов\n\n"
        
        if user_role >= 10 or is_owner:
            text += "⚠️ МОДЕРАЦИЯ (роль 10+):\n"
            text += "/warn [id] [причина] - Предупреждение\n"
            text += "/kick [id] - Исключить\n"
            text += "/mute [id] [время] [причина] - Мут\n"
            text += "/unmute [id] - Снять мут\n\n"
        
        if user_role >= 50 or is_owner:
            text += "🚫 БАН (роль 50+):\n"
            text += "/ban [id] [время] [причина] - Бан\n"
            text += "/unban [id] - Разбан\n\n"
        
        if user_role >= 60 or is_owner:
            text += "🎖️ ВЫДАЧА РОЛЕЙ (роль 60+):\n"
            text += "/giverole [@user] [приоритет] - Выдать роль\n\n"
        
        if is_owner:
            text += "⚙️ ТОЛЬКО ВЛАДЕЛЕЦ:\n"
            text += "/sysrole @user [приоритет] - Выдать роль\n"
            text += "/addrole [приоритет] [имя] - Добавить роль\n"
        
        text += f"\n📊 Ваш приоритет: {user_role}"
        
        return await msg.answer(text)
    finally:
        conn.close()

# =========================
# SYSTEM COMMANDS
# =========================
@bot.on.message(text="/sysrole")
async def sysrole_help(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    return await msg.answer(
        "⚙️ /sysrole @user [приоритет]\n\n"
        "Примеры:\n"
        "• /sysrole @Ivan 50\n"
        "• (ответить) /sysrole 60"
    )

@bot.on.message(text="/sysrole <user_info> <priority>")
async def sysrole_set(msg: Message, user_info: str, priority: str):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    conn, cur = db()
    try:
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ Приоритет должен быть числом")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        if not (0 <= priority_int <= 1000):
            return await msg.answer("❌ Приоритет от 0 до 1000")
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, msg.peer_id, priority_int, priority_int))
        
        user_name = await get_user_name(uid)
        role_name = get_role_name(cur, msg.peer_id, priority_int)
        
        await msg.answer(f"✅ РОЛЬ ВЫДАНА\n👤 {user_name}\n📋 {role_name} ({priority_int})")
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

@bot.on.message(text="/addrole")
async def addrole_help(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    return await msg.answer(
        "📋 /addrole [приоритет] [имя]\n\n"
        "Примеры:\n"
        "• /addrole 50 Модератор\n"
        "• /addrole 10 VIP"
    )

@bot.on.message(text="/addrole <priority> <role_name>")
async def addrole(msg: Message, priority: str, role_name: str):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    conn, cur = db()
    try:
        try:
            priority = int(priority)
        except:
            return await msg.answer("❌ Приоритет должен быть числом")
        
        if not (1 <= priority <= 1000):
            return await msg.answer("❌ Приоритет от 1 до 1000")
        
        if len(role_name) > 30:
            return await msg.answer("❌ Имя слишком длинное (макс. 30)")
        
        cur.execute("""
        INSERT INTO roles (peer_id, role_priority, role_name)
        VALUES (%s, %s, %s)
        """, (msg.peer_id, priority, role_name))
        
        await msg.answer(f"✅ РОЛЬ ДОБАВЛЕНА\n📋 {role_name} ({priority})")
    
    except Exception as e:
        if "unique" in str(e).lower():
            return await msg.answer(f"❌ Приоритет {priority} уже используется")
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# ROLES
# =========================
@bot.on.message(text="/giverole")
async def giverole_help(msg: Message):
    return await msg.answer(
        "🎖️ /giverole [@user] [приоритет]\n\n"
        "Требуемый приоритет: 60+\n\n"
        "Примеры:\n"
        "• /giverole @Ivan 10\n"
        "• (ответить) /giverole 50"
    )

@bot.on.message(text="/giverole <user_info> <priority>")
async def giverole(msg: Message, user_info: str, priority: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('giverole')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ Приоритет должен быть числом")
        
        if not (0 <= priority_int <= 1000):
            return await msg.answer("❌ Приоритет от 0 до 1000")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(f"❌ НЕ МОЖЕШЬ ВЫДАТЬ\nЕго: {target_role}, Твой: {sender_role}")
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, msg.peer_id, priority_int, priority_int))
        
        user_name = await get_user_name(uid)
        role_name = get_role_name(cur, msg.peer_id, priority_int)
        
        await msg.answer(f"✅ РОЛЬ ВЫДАНА\n👤 {user_name}\n📋 {role_name} ({priority_int})")
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

@bot.on.message(text="/roles")
async def list_roles(msg: Message):
    conn, cur = db()
    try:
        cur.execute("""
        SELECT role_priority, role_name FROM roles
        WHERE peer_id=%s ORDER BY role_priority DESC
        """, (msg.peer_id,))
        
        roles = cur.fetchall()
        
        if not roles:
            return await msg.answer("📊 СПИСОК РОЛЕЙ\n❌ Нет добавленных ролей")
        
        text = "📊 РОЛИ В БЕСЕДЕ\n\n"
        for priority, name in roles:
            text += f"{priority:3d} - {name}\n"
        
        return await msg.answer(text)
    finally:
        conn.close()

@bot.on.message(text="/staff")
async def staff(msg: Message):
    conn, cur = db()
    try:
        cur.execute("""
        SELECT user_id, role FROM users
        WHERE peer_id=%s AND role > 0
        ORDER BY role DESC
        """, (msg.peer_id,))
        
        staff_list = cur.fetchall()
        
        if not staff_list:
            return await msg.answer("👥 МОДЕРАЦИЯ\n❌ Нет модераторов")
        
        text = "👥 МОДЕРАЦИЯ\n\n"
        for user_id, role_priority in staff_list:
            user_name = await get_user_name(user_id)
            role_name = get_role_name(cur, msg.peer_id, role_priority)
            text += f"👤 {user_name} - {role_name} ({role_priority})\n"
        
        return await msg.answer(text)
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# WARN
# =========================
@bot.on.message(text="/warn")
async def warn_help(msg: Message):
    return await msg.answer(
        "⚠️ /warn [@user] [причина]\n\n"
        "После 3 - автобан\n\n"
        "Примеры:\n"
        "• /warn @Ivan спам\n"
        "• (ответить) /warn флуд"
    )

@bot.on.message(text="/warn <user_info> <reason>")
async def warn(msg: Message, user_info: str, reason: str = "Без причины"):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('warn')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ НЕ МОЖЕШЬ НАКАЗАТЬ")
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)

        cur.execute("""
        INSERT INTO users (user_id, peer_id, warn_count, warn_reasons)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET 
            warn_count = users.warn_count + 1,
            warn_reasons = CASE 
                WHEN users.warn_reasons = '' THEN EXCLUDED.warn_reasons
                ELSE users.warn_reasons || ' | ' || EXCLUDED.warn_reasons
            END
        """, (uid, pid, reason))

        cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (uid, pid))
        warns = cur.fetchone()[0]

        if warns >= 3:
            cur.execute("""
            INSERT INTO punishments (user_id, peer_id, type, reason)
            VALUES (%s, %s, 'ban', %s)
            """, (uid, pid, "Автобан (3 предупреждения)"))
            
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            except:
                pass
            
            return await msg.answer(f"🚫 АВТОБАН\n👤 {user_name}\n📋 3 предупреждения")

        await msg.answer(f"⚠️ ПРЕДУПРЕЖДЕНИЕ\n👤 {user_name}\n📊 {warns}/3\n📝 {reason}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# MUTE
# =========================
@bot.on.message(text="/mute")
async def mute_help(msg: Message):
    return await msg.answer(
        "🔇 /mute [@user] [время] [причина]\n\n"
        "Форматы: 10m, 1h, 1d\n\n"
        "Примеры:\n"
        "• /mute @Ivan 30m спам\n"
        "• (ответить) /mute 1h"
    )

@bot.on.message(text="/mute <user_info> <time_or_reason>")
async def mute(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('mute')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ НЕ МОЖЕШЬ НАКАЗАТЬ")
        
        pid = msg.peer_id
        
        duration = None
        reason = "Без причины"
        
        if time_or_reason:
            parsed_time = parse_time(time_or_reason)
            if parsed_time:
                duration = parsed_time
            else:
                reason = time_or_reason
        
        end_time = datetime.now() + duration if duration else None
        formatted_time = format_time(duration) if duration else "навсегда"

        user_name = await get_user_name(uid)

        cur.execute("""
        INSERT INTO punishments (user_id, peer_id, type, end_at, reason)
        VALUES (%s, %s, 'mute', %s, %s)
        """, (uid, pid, end_time, reason))

        await msg.answer(f"🔇 МУТ\n👤 {user_name}\n⏰ {formatted_time}\n📝 {reason}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# UNMUTE
# =========================
@bot.on.message(text="/unmute")
async def unmute_help(msg: Message):
    return await msg.answer(
        "🔊 /unmute [@user]\n\n"
        "Примеры:\n"
        "• /unmute @Ivan\n"
        "• (ответить) /unmute"
    )

@bot.on.message(text="/unmute <user_info>")
async def unmute(msg: Message, user_info: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('unmute')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        """, (uid, pid))
        
        await msg.answer(f"✅ МУТ СНЯТ\n👤 {user_name}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# BAN
# =========================
@bot.on.message(text="/ban")
async def ban_help(msg: Message):
    return await msg.answer(
        "🚫 /ban [@user] [время] [причина]\n\n"
        "Форматы: 1h, 1d\n\n"
        "Примеры:\n"
        "• /ban @Ivan 1d спам\n"
        "• (ответить) /ban тролль"
    )

@bot.on.message(text="/ban <user_info> <time_or_reason>")
async def ban(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('ban')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ НЕ МОЖЕШЬ НАКАЗАТЬ")
        
        pid = msg.peer_id
        
        duration = None
        reason = "Без причины"
        
        if time_or_reason:
            if time_or_reason.lower() == "permanent":
                duration = None
            else:
                parsed_time = parse_time(time_or_reason)
                if parsed_time:
                    duration = parsed_time
                else:
                    reason = time_or_reason
        
        end_time = datetime.now() + duration if duration else None
        formatted_time = format_time(duration) if duration else "навсегда"

        user_name = await get_user_name(uid)

        cur.execute("""
        INSERT INTO punishments (user_id, peer_id, type, end_at, reason)
        VALUES (%s, %s, 'ban', %s, %s)
        """, (uid, pid, end_time, reason))

        try:
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
        except:
            pass

        await msg.answer(f"🚫 БАН\n👤 {user_name}\n⏰ {formatted_time}\n📝 {reason}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# UNBAN
# =========================
@bot.on.message(text="/unban")
async def unban_help(msg: Message):
    return await msg.answer(
        "✅ /unban [@user]\n\n"
        "Примеры:\n"
        "• /unban @Ivan\n"
        "• (ответить) /unban"
    )

@bot.on.message(text="/unban <user_info>")
async def unban(msg: Message, user_info: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('unban')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
        """, (uid, pid))
        
        await msg.answer(f"✅ БАН СНЯТ\n👤 {user_name}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# KICK
# =========================
@bot.on.message(text="/kick")
async def kick_help(msg: Message):
    return await msg.answer(
        "👢 /kick [@user]\n\n"
        "Примеры:\n"
        "• /kick @Ivan\n"
        "• (ответить) /kick"
    )

@bot.on.message(text="/kick <user_info>")
async def kick(msg: Message, user_info: str):
    conn, cur = db()
    try:
        sender_role = get_user_role(cur, msg.peer_id, msg.from_id)
        default_role = get_default_cmd_role('kick')
        
        if sender_role < default_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ ТРЕБУЕМЫЙ ПРИОРИТЕТ: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ НЕВЕРНЫЙ ФОРМАТ")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ НЕ МОЖЕШЬ НАКАЗАТЬ")
        
        user_name = await get_user_name(uid)
        
        try:
            await bot.api.messages.remove_chat_user(chat_id=msg.peer_id-2000000000, user_id=uid)
            await msg.answer(f"👢 ИСКЛЮЧЕН\n👤 {user_name}")
        except Exception as e:
            await msg.answer(f"❌ НЕ УДАЛОСЬ\n👤 {user_name}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# SNICK
# =========================
@bot.on.message(text="/snick")
async def snick_help(msg: Message):
    return await msg.answer(
        "🏷 /snick [ник]\n\n"
        "Примеры:\n"
        "• /snick ★ King ★\n"
        "• (ответить) /snick 👑 Admin"
    )

@bot.on.message(text="/snick <nick>")
async def snick(msg: Message, nick: str):
    conn, cur = db()
    try:
        if len(nick) > 50:
            return await msg.answer("❌ Ник слишком длинный (макс. 50)")

        target = extract(msg) or msg.from_id
        pid = msg.peer_id
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, nickname)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET nickname=%s
        """, (target, pid, nick, nick))

        return await msg.answer(f"✅ НИК УСТАНОВЛЕН\n🏷 {nick}")
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# RNICK
# =========================
@bot.on.message(text="/rnick")
async def rnick_help(msg: Message):
    return await msg.answer(
        "🧹 /rnick [@user]\n\n"
        "Примеры:\n"
        "• /rnick @Ivan\n"
        "• (ответить) /rnick"
    )

@bot.on.message(text="/rnick <user_info>")
async def rnick(msg: Message, user_info: str = None):
    conn, cur = db()
    try:
        target = extract(msg)
        if not target and user_info:
            try:
                target = int(user_info.replace("@", "").replace("id", ""))
            except:
                target = msg.from_id
        elif not target:
            target = msg.from_id
        
        pid = msg.peer_id

        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (target, pid))
        res = cur.fetchone()
        old_nick = res[0] if res else None

        if not old_nick:
            return await msg.answer("❌ НИК НЕ УСТАНОВЛЕН")

        cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (target, pid))

        return await msg.answer(f"🧹 НИК УДАЛЁН\n❌ Был: {old_nick}")
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# STATS
# =========================
@bot.on.message(text="/stats")
async def stats_help(msg: Message):
    return await msg.answer(
        "📊 /stats [@user]\n\n"
        "Примеры:\n"
        "• /stats @Ivan\n"
        "• (ответить) /stats"
    )

@bot.on.message(text="/stats <user_info>")
async def stats(msg: Message, user_info: str = None):
    conn, cur = db()
    try:
        uid = extract(msg)
        if not uid and user_info:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                uid = msg.from_id
        elif not uid:
            uid = msg.from_id
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        SELECT role, msgs, warn_count, nickname FROM users
        WHERE user_id=%s AND peer_id=%s
        """, (uid, pid))
        
        res = cur.fetchone()
        
        if not res:
            return await msg.answer(f"📊 СТАТИСТИКА\n👤 {user_name}\n❌ Нет данных")
        
        role, msgs, warn_count, nickname = res
        role_name = get_role_name(cur, pid, role)
        nick_info = f"🏷 {nickname}\n" if nickname else ""
        
        return await msg.answer(
            f"📊 СТАТИСТИКА\n\n"
            f"👤 {user_name}\n"
            f"📋 {role_name} ({role})\n"
            f"💬 {msgs} сообщений\n"
            f"⚠️ {warn_count}/3 предупреждений\n"
            f"{nick_info}"
        )
    
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()


@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()

    try:
        uid, pid = msg.from_id, msg.peer_id

        # ПРОВЕРКА НА БАН
        ban_info = get_ban_info(cur, pid, uid)
        if ban_info:
            reason, end_at = ban_info
            duration_text = format_time(end_at - datetime.now()) if end_at else "навсегда"
            
            # Кикаем пользователя
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            except:
                pass
            
            # Отправляем информацию о бане в беседу
            try:
                user_name = await get_user_name(uid)
                await bot.api.messages.send(
                    peer_id=pid,
                    message=f"🚫 ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН И ИСКЛЮЧЕН\n\n"
                            f"👤 {user_name} (id{uid})\n"
                            f"⏰ Бан на: {duration_text}\n"
                            f"📝 Причина: {reason}\n\n"
                            f"📌 ИНСТРУКЦИЯ:\n"
                            f"Для обжалования напишите владельцу беседы"
                )
            except:
                pass
            
            print(f">>> AUTO KICKED BANNED USER {uid} FROM CHAT {pid}")
            return

        if not msg.text:
            return

        # ПРОВЕРКА НА МУТ
        cur.execute("""
        SELECT reason FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        AND (end_at IS NULL OR end_at > NOW())
        """, (uid, pid))

        if cur.fetchone():
            try:
                await bot.api.messages.delete(message_ids=[msg.id], delete_for_all=True)
            except:
                pass
            return

        # ОБНОВЛЯЕМ СТАТИСТИКУ
        cur.execute("""
        INSERT INTO users (user_id, peer_id, msgs)
        VALUES (%s, %s, 1)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET msgs = users.msgs + 1
        """, (uid, pid))

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
if __name__ == "__main__":
    print(">>> BOT START")
    bot.run_forever()
