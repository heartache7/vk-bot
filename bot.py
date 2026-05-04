import os
import re
import traceback
import logging
import asyncio
import psycopg2
from datetime import datetime, timedelta
from vkbottle.bot import Bot, Message
from vkbottle import API

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OWNER_ID = 676081199
VK_TOKEN = os.getenv("VK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not VK_TOKEN or not DATABASE_URL:
    raise ValueError("VK_TOKEN and DATABASE_URL must be set in environment variables")

bot = Bot(token=VK_TOKEN)
api = API(token=VK_TOKEN)

# =========================
# DB
# =========================
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

            cur.execute("""
            CREATE TABLE IF NOT EXISTS cmd_permissions(
                id SERIAL PRIMARY KEY,
                peer_id BIGINT,
                cmd_name TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(peer_id, cmd_name)
            );
            """)
            
            cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='cmd_permissions' AND column_name='required_role'
            """)
            
            if not cur.fetchone():
                cur.execute("""
                ALTER TABLE cmd_permissions 
                ADD COLUMN required_role INT DEFAULT 10
                """)
                logger.info(">>> Added required_role column to cmd_permissions")

            default_permissions = [
                ('warn', 10), ('mute', 10), ('unmute', 10),
                ('ban', 50), ('unban', 50), ('kick', 10),
                ('snick', 0), ('rnick', 0), ('giverole', 60),
                ('stats', 0), ('addrole', 50)
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
    
    r = re.search(r"\[id(\d+)\|", msg.text)
    if r:
        return int(r.group(1))
    
    r = re.search(r"id(\d+)", msg.text)
    if r:
        return int(r.group(1))
    
    r = re.search(r"@([a-zA-Z0-9_.]+)", msg.text)
    if r:
        return r.group(1)
    
    return None

async def get_user_name(uid):
    try:
        user = await bot.api.users.get(user_ids=uid, name_case='nom')
        return f"{user[0].first_name} {user[0].last_name}"
    except:
        return f"Пользователь {uid}"

async def resolve_user_id(uid_or_username):
    """Преобразует username в user_id если нужно"""
    if isinstance(uid_or_username, int):
        return uid_or_username
    
    if isinstance(uid_or_username, str) and not uid_or_username.isdigit():
        try:
            users = await bot.api.users.get(user_ids=[uid_or_username])
            if users:
                return users[0].id
        except:
            pass
    elif isinstance(uid_or_username, str) and uid_or_username.isdigit():
        return int(uid_or_username)
    
    return None

def is_user_banned(peer_id, user_id):
    """Проверяет, забанен ли пользователь в этой беседе"""
    conn, cur = db()
    try:
        cur.execute("""
        SELECT end_at FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban' 
        AND (end_at IS NULL OR end_at > NOW())
        """, (user_id, peer_id))
        return cur.fetchone() is not None
    finally:
        conn.close()

def is_user_muted(peer_id, user_id):
    """Проверяет, замучен ли пользователь в этой беседе"""
    conn, cur = db()
    try:
        cur.execute("""
        SELECT end_at FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        AND (end_at IS NULL OR end_at > NOW())
        """, (user_id, peer_id))
        return cur.fetchone() is not None
    finally:
        conn.close()

def can_punish_user(cur, peer_id, punisher_id, target_id):
    """Проверяет, может ли punisher наказать target'а"""
    if punisher_id == OWNER_ID:
        return True
    
    cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (punisher_id, peer_id))
    punisher_role = cur.fetchone()
    punisher_role_val = punisher_role[0] if punisher_role else 0
    
    cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (target_id, peer_id))
    target_role = cur.fetchone()
    target_role_val = target_role[0] if target_role else 0
    
    return punisher_role_val > target_role_val

def get_user_role(cur, peer_id, user_id):
    """Получает роль пользователя"""
    cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (user_id, peer_id))
    res = cur.fetchone()
    return res[0] if res else 0

def get_cmd_required_role(cur, peer_id, cmd_name):
    """Получает требуемую роль для команды в конкретной беседе"""
    cur.execute("""
    SELECT required_role FROM cmd_permissions
    WHERE peer_id=%s AND cmd_name=%s
    """, (peer_id, cmd_name))
    res = cur.fetchone()
    
    if res:
        return res[0]
    
    cur.execute("""
    SELECT required_role FROM cmd_permissions
    WHERE peer_id=0 AND cmd_name=%s
    """, (cmd_name,))
    res = cur.fetchone()
    
    return res[0] if res else 0

def check_permission(cur, peer_id, user_id, cmd_name):
    """Проверяет права на выполнение команды"""
    if user_id == OWNER_ID:
        return True, 0, 0
    
    user_role = get_user_role(cur, peer_id, user_id)
    required_role = get_cmd_required_role(cur, peer_id, cmd_name)
    
    return user_role >= required_role, user_role, required_role

async def kick_user(peer_id, user_id):
    """Кикает пользователя из беседы"""
    try:
        await bot.api.messages.remove_chat_user(
            chat_id=peer_id - 2000000000,
            user_id=user_id
        )
        return True
    except Exception as e:
        logger.error(f"Failed to kick user {user_id}: {e}")
        return False

async def delete_message(peer_id, conversation_message_id):
    """Удаляет сообщение"""
    try:
        await bot.api.messages.delete(
            peer_id=peer_id,
            conversation_message_ids=[conversation_message_id],
            delete_for_all=True
        )
        return True
    except:
        return False

# =========================
# HELP COMMAND
# =========================
@bot.on.message(text="/help")
async def help_cmd(msg: Message):
    return await msg.answer(
        "💠 FLEX BOT - ПОЛНОЕ РУКОВОДСТВО\n\n"
        "🤖 Я бот для модерации чатов ВКонтакте\n"
        "Поддерживаю систему ролей, наказаний и никнеймов\n\n"
        "📋 ОСНОВНЫЕ КОМАНДЫ:\n\n"
        "🏷 НИКИ:\n"
        "/snick [ник] - Установить ник\n"
        "/rnick - Удалить ник\n\n"
        "⚠️ МОДЕРАЦИЯ:\n"
        "/warn [пользователь] [причина] - Предупреждение\n"
        "/mute [пользователь] [время] [причина] - Мут\n"
        "/unmute [пользователь] - Снять мут\n"
        "/ban [пользователь] [время] [причина] - Бан\n"
        "/unban [пользователь] - Разбанить\n"
        "/kick [пользователь] - Исключить\n\n"
        "🎖️ РОЛИ:\n"
        "/giverole [пользователь] [приоритет] - Выдать роль\n"
        "/addrole [приоритет] [имя] - Создать/изменить роль\n"
        "/roles - Список ролей беседы\n"
        "/staff - Список персонала\n\n"
        "📊 СТАТИСТИКА:\n"
        "/stats [пользователь] - Статистика\n\n"
        "⚙️ НАСТРОЙКИ:\n"
        "/setcmd - Настройка прав команд\n\n"
        "👑 ВЛАДЕЛЕЦ БОТА:\n"
        "/sysrole [пользователь] [приоритет] - Системная роль\n\n"
        "💡 СОВЕТ: Вы можете отвечать на сообщение пользователя командой\n"
        "Например: ответьте на сообщение и напишите /mute 30m спам\n\n"
        "📖 Для подробной инструкции по команде введите её без параметров\n"
        "Например: /ban"
    )

# =========================
# START COMMAND
# =========================
@bot.on.message(text="/start")
async def start(msg: Message):
    conn, cur = db()
    try:
        try:
            res = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
        except Exception as e:
            logger.error(f"Cannot get conversation members: {e}")
            return await msg.answer(
                "👋 ПРИВЕТ, Я FLEX BOT!\n\n"
                "🎉 Спасибо за приглашение в вашу беседу!\n\n"
                "🔧 Я бот для модерации чатов с множеством полезных функций:\n\n"
                "🏷 НИКИ - устанавливайте красивые ники\n"
                "⚠️ МОДЕРАЦИЯ - система предупреждений и наказаний\n"
                "🔇 МУТ - запрет на написание сообщений\n"
                "🚫 БАН - блокировка пользователей\n"
                "👢 КИК - исключение из беседы\n"
                "🎖️ РОЛИ - система ролей и приоритетов\n\n"
                "⚠️ ОШИБКА ПРАВ ДОСТУПА\n\n"
                "🔧 Что нужно сделать:\n"
                "1️⃣ Откройте настройки беседы\n"
                "2️⃣ Перейдите в раздел 'Управление ботами'\n"
                "3️⃣ Выдайте боту 'FLEX BOT' права администратора\n"
                "4️⃣ Убедитесь, что включены права на удаление\n\n"
                "После этого напишите /start ещё раз ⭐"
            )

        owner_found = False
        for m in res.items:
            if getattr(m, "is_owner", False):
                owner_found = True
                cur.execute("""
                INSERT INTO users (user_id, peer_id, role)
                VALUES (%s, %s, 100)
                ON CONFLICT (user_id, peer_id)
                DO UPDATE SET role=100
                """, (m.member_id, msg.peer_id))

                cur.execute("""
                INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
                SELECT %s, cmd_name, required_role
                FROM cmd_permissions
                WHERE peer_id = 0
                ON CONFLICT (peer_id, cmd_name) DO NOTHING
                """, (msg.peer_id,))

                owner_name = await get_user_name(m.member_id)

                return await msg.answer(
                    "✅ БОТ УСПЕШНО ИНИЦИАЛИЗИРОВАН\n\n"
                    "👑 FLEX BOT активирован!\n\n"
                    f"🎖️ @id{m.member_id} ({owner_name}) получил роль 100\n\n"
                    "📖 Введите /help чтобы увидеть все команды"
                )

        if not owner_found:
            return await msg.answer("❌ Не удалось найти создателя беседы")

    except Exception as e:
        logger.error(f"ERROR in start: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при инициализации")
    finally:
        conn.close()

# =========================
# SETCMD COMMAND
# =========================
@bot.on.message(text="/setcmd")
async def setcmd_help(msg: Message):
    conn, cur = db()
    try:
        peer_id = msg.peer_id
        
        cur.execute("""
        SELECT DISTINCT ON (cmd_name) cmd_name, required_role
        FROM cmd_permissions
        WHERE peer_id=%s OR peer_id=0
        ORDER BY cmd_name, peer_id DESC
        """, (peer_id,))
        
        perms = cur.fetchall()
        
        perms_text = "📊 ТЕКУЩИЕ ПРАВА КОМАНД:\n\n"
        for cmd, role in perms:
            if cmd != 'sysrole':
                perms_text += f"📝 /{cmd} - роль {role}+\n"
        
        return await msg.answer(
            "⚙️ КОМАНДА: НАСТРОЙКА ПРАВ\n\n"
            "📝 Синтаксис:\n"
            "/setcmd [команда] [приоритет]\n\n"
            "📋 Описание:\n"
            "Устанавливает минимальный приоритет роли\n"
            "для использования указанной команды\n\n"
            "⚙️ Примеры:\n"
            "• /setcmd ban 30\n"
            "• /setcmd mute 20\n"
            "• /setcmd warn 5\n\n"
            "❗ Особые условия:\n"
            "• Нельзя изменить права /sysrole\n"
            "• Нельзя установить приоритет выше вашего\n"
            "• Только создатель беседы может менять права\n\n"
            f"{perms_text}\n"
            "💡 Введите /setcmd без параметров чтобы увидеть это сообщение"
        )
    finally:
        conn.close()

@bot.on.message(text="/setcmd <cmd_name> <priority>")
async def setcmd(msg: Message, cmd_name: str, priority: str):
    conn, cur = db()
    try:
        try:
            res = await bot.api.messages.get_conversation_members(peer_id=msg.peer_id)
            is_owner = False
            for m in res.items:
                if m.member_id == msg.from_id and getattr(m, "is_owner", False):
                    is_owner = True
                    break
            
            if not is_owner and msg.from_id != OWNER_ID:
                return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН\n\nТолько создатель беседы может менять права команд")
        except:
            if msg.from_id != OWNER_ID:
                return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН\n\nТолько создатель беседы может менять права команд")
        
        if cmd_name.lower() == "sysrole":
            return await msg.answer("❌ НЕЛЬЗЯ ИЗМЕНИТЬ\n\nКоманда /sysrole доступна только владельцу бота")
        
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /setcmd ban 30")
        
        if priority_int < 0 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 0 до 1000")
        
        user_role = get_user_role(cur, msg.peer_id, msg.from_id)
        if priority_int > user_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ НЕЛЬЗЯ УСТАНОВИТЬ ПРИОРИТЕТ ВЫШЕ ВАШЕГО\n\nВаш приоритет: {user_role}")
        
        peer_id = msg.peer_id
        
        cur.execute("""
        INSERT INTO cmd_permissions (peer_id, cmd_name, required_role)
        VALUES (%s, %s, %s)
        ON CONFLICT (peer_id, cmd_name)
        DO UPDATE SET required_role = %s
        """, (peer_id, cmd_name.lower(), priority_int, priority_int))
        
        await msg.answer(
            f"✅ ПРАВА ОБНОВЛЕНЫ\n\n"
            f"📝 Команда: /{cmd_name.lower()}\n"
            f"📊 Требуемая роль: {priority_int}+\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in setcmd: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при настройке прав")
    finally:
        conn.close()

# =========================
# SYSTEM COMMANDS (OWNER ONLY)
# =========================
@bot.on.message(text="/sysrole")
async def sysrole_help(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН\n\nЭта команда только для владельца бота")
    
    return await msg.answer(
        "⚙️ СИСТЕМНАЯ КОМАНДА: /sysrole\n\n"
        "🔒 ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА БОТА\n\n"
        "📝 Синтаксис:\n"
        "/sysrole @пользователь [приоритет]\n"
        "Или ответьте на сообщение: /sysrole [приоритет]\n\n"
        "⚙️ Примеры:\n"
        "• /sysrole @Ivan 50\n"
        "• /sysrole @Maria 100\n"
        "• (ответить на сообщение) /sysrole 60\n\n"
        "📋 Описание:\n"
        "Выдаёт указанный приоритет роли пользователю\n"
        "в обход всех проверок прав"
    )

@bot.on.message(text="/sysrole <priority>")
async def sysrole_reply(msg: Message, priority: str):
    """Выдача роли через ответ на сообщение"""
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    if not msg.reply_message:
        return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его ID\n\nПример: /sysrole @Ivan 50")
    
    uid = msg.reply_message.from_id
    await process_sysrole(msg, uid, priority)

@bot.on.message(text="/sysrole <user_info> <priority>")
async def sysrole_set(msg: Message, user_info: str, priority: str):
    """Выдача роли с указанием пользователя"""
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    # Извлекаем ID из упоминания или username
    uid = None
    
    # Проверяем reply_message сначала
    if msg.reply_message:
        uid = msg.reply_message.from_id
    else:
        # Пробуем извлечь из текста
        r = re.search(r"\[id(\d+)\|", msg.text)
        if r:
            uid = int(r.group(1))
        else:
            r = re.search(r"id(\d+)", msg.text)
            if r:
                uid = int(r.group(1))
            else:
                # Пробуем как username
                clean_user = user_info.replace("@", "").replace("[", "").replace("]", "")
                if clean_user.isdigit():
                    uid = int(clean_user)
                else:
                    uid = await resolve_user_id(clean_user)
    
    if not uid:
        return await msg.answer("❌ ОШИБКА: Не удалось определить пользователя\n\nУкажите @username или ID пользователя")
    
    await process_sysrole(msg, uid, priority)

async def process_sysrole(msg: Message, uid: int, priority: str):
    """Обработка команды sysrole"""
    conn, cur = db()
    try:
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /sysrole @Ivan 50")
        
        if priority_int < 0 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 0 до 1000")
        
        peer_id = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, peer_id, priority_int, priority_int))
        
        cur.execute("""
        SELECT role_name FROM roles
        WHERE peer_id=%s AND role_priority=%s
        """, (peer_id, priority_int))
        
        role_res = cur.fetchone()
        role_name = role_res[0] if role_res else f"Уровень {priority_int}"
        
        await msg.answer(
            f"🎖️ РОЛЬ ВЫДАНА\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"📋 Роль: {role_name}\n"
            f"📊 Приоритет: {priority_int}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in sysrole_set: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при выдаче роли")
    finally:
        conn.close()

# =========================
# ADDROLE COMMAND
# =========================
@bot.on.message(text="/addrole")
async def addrole_help(msg: Message):
    return await msg.answer(
        "📋 КОМАНДА: ДОБАВЛЕНИЕ/ИЗМЕНЕНИЕ РОЛИ\n\n"
        "📝 Синтаксис:\n"
        "/addrole [приоритет] [имя роли]\n\n"
        "⚙️ Примеры:\n"
        "• /addrole 50 Модератор\n"
        "• /addrole 10 VIP\n"
        "• /addrole 100 Создатель\n\n"
        "📋 Описание:\n"
        "Создаёт новую роль или изменяет существующую\n"
        "Роль с указанным приоритетом будет иметь красивое имя\n"
        "Максимальная длина имени - 30 символов\n\n"
        "💡 После создания роли, вы можете выдать её пользователю\n"
        "с помощью команды /giverole @user [приоритет]"
    )

@bot.on.message(text="/addrole <priority> <role_name>")
async def addrole(msg: Message, priority: str, role_name: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'addrole')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет роли: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /addrole 50 Модератор")
        
        if priority_int < 1 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 1 до 1000")
        
        if len(role_name) > 30:
            return await msg.answer("❌ ОШИБКА: имя роли слишком длинное (макс. 30 символов)")
        
        if priority_int > user_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ НЕЛЬЗЯ СОЗДАТЬ РОЛЬ ВЫШЕ ВАШЕГО ПРИОРИТЕТА\n\nВаш приоритет: {user_role}")
        
        peer_id = msg.peer_id
        
        cur.execute("""
        SELECT role_name FROM roles
        WHERE peer_id=%s AND role_priority=%s
        """, (peer_id, priority_int))
        
        existing_role = cur.fetchone()
        
        cur.execute("""
        INSERT INTO roles (peer_id, role_priority, role_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (peer_id, role_priority) 
        DO UPDATE SET role_name = EXCLUDED.role_name
        """, (peer_id, priority_int, role_name))
        
        if existing_role:
            return await msg.answer(
                f"✅ РОЛЬ ИЗМЕНЕНА\n\n"
                f"📋 Было: {existing_role[0]}\n"
                f"📋 Стало: {role_name}\n"
                f"📊 Приоритет: {priority_int}\n\n"
                f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            return await msg.answer(
                f"✅ РОЛЬ СОЗДАНА\n\n"
                f"📋 Имя: {role_name}\n"
                f"📊 Приоритет: {priority_int}\n\n"
                f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
    
    except Exception as e:
        logger.error(f"ERROR in addrole: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при работе с ролью")
    finally:
        conn.close()

# =========================
# GIVEROLE COMMAND
# =========================
@bot.on.message(text="/giverole")
async def giverole_help(msg: Message):
    return await msg.answer(
        "🎖️ КОМАНДА: ВЫДАЧА РОЛИ\n\n"
        "📝 Синтаксис:\n"
        "/giverole @пользователь [приоритет]\n"
        "или ответьте на сообщение: /giverole [приоритет]\n\n"
        "📊 ТРЕБУЕМЫЙ ПРИОРИТЕТ: зависит от настроек беседы\n\n"
        "⚙️ Примеры:\n"
        "• /giverole @Ivan 10\n"
        "• /giverole @Maria 50\n"
        "• (ответить на сообщение) /giverole 100\n\n"
        "📋 Описание:\n"
        "Выдаёт роль пользователю\n"
        "Можно выдавать только роли ниже вашей\n"
        "и только пользователям с меньшим приоритетом\n\n"
        "💡 Используйте /roles чтобы увидеть список доступных ролей"
    )

@bot.on.message(text="/giverole <priority>")
async def giverole_reply(msg: Message, priority: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'giverole')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет роли: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /giverole @Ivan 50")
        
        uid = msg.reply_message.from_id
        await process_giverole(msg, uid, priority, user_role)
    finally:
        conn.close()

@bot.on.message(text="/giverole <user_info> <priority>")
async def giverole(msg: Message, user_info: str, priority: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'giverole')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет роли: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ\n\nИспользуйте: /giverole @username приоритет")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ\n\nИспользуйте: /giverole @username приоритет")
        
        await process_giverole(msg, uid, priority, user_role)
    finally:
        conn.close()

async def process_giverole(msg: Message, uid: int, priority: str, sender_role: int = 0):
    conn, cur = db()
    try:
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /giverole @Ivan 50")
        
        if priority_int < 0 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 0 до 1000")
        
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            return await msg.answer("❌ НЕЛЬЗЯ ВЫДАТЬ РОЛЬ\n\nМожно выдавать роли только пользователям ниже по приоритету")
        
        if priority_int >= sender_role and msg.from_id != OWNER_ID:
            return await msg.answer(f"❌ НЕЛЬЗЯ ВЫДАТЬ РОЛЬ ВЫШЕ ИЛИ РАВНУЮ ВАШЕЙ\n\nВаш приоритет: {sender_role}")
        
        user_name = await get_user_name(uid)
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, msg.peer_id, priority_int, priority_int))
        
        cur.execute("""
        SELECT role_name FROM roles
        WHERE peer_id=%s AND role_priority=%s
        """, (msg.peer_id, priority_int))
        
        role_res = cur.fetchone()
        role_name = role_res[0] if role_res else f"Уровень {priority_int}"
        
        await msg.answer(
            f"🎖️ РОЛЬ ВЫДАНА\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"📋 Роль: {role_name}\n"
            f"📊 Приоритет: {priority_int}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Выдал: @id{msg.from_id}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in giverole: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при выдаче роли")
    finally:
        conn.close()

# =========================
# ROLES LIST COMMAND
# =========================
@bot.on.message(text="/roles")
async def list_roles(msg: Message):
    conn, cur = db()
    try:
        peer_id = msg.peer_id
        cur.execute("""
        SELECT role_priority, role_name FROM roles
        WHERE peer_id=%s
        ORDER BY role_priority DESC
        """, (peer_id,))
        
        roles = cur.fetchall()
        
        if not roles:
            return await msg.answer(
                "📊 СПИСОК РОЛЕЙ\n\n"
                "❌ В этой беседе ещё нет созданных ролей\n\n"
                "🆕 Создайте новую роль: /addrole [приоритет] [имя]\n"
                "Например: /addrole 50 Модератор\n\n"
                "📖 По умолчанию используются числовые уровни"
            )
        
        roles_text = "📊 СПИСОК РОЛЕЙ В БЕСЕДЕ\n\n"
        for priority, name in roles:
            roles_text += f"📋 {priority:3d} - {name}\n"
        
        roles_text += "\n💡 Используйте /giverole чтобы выдать роль пользователю"
        
        return await msg.answer(roles_text)
    
    except Exception as e:
        logger.error(f"ERROR in list_roles: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при получении списка ролей")
    finally:
        conn.close()

# =========================
# STAFF COMMAND
# =========================
@bot.on.message(text="/staff")
async def staff(msg: Message):
    conn, cur = db()
    try:
        peer_id = msg.peer_id
        
        cur.execute("""
        SELECT user_id, role FROM users
        WHERE peer_id=%s AND role > 0
        ORDER BY role DESC
        """, (peer_id,))
        
        staff_list = cur.fetchall()
        
        if not staff_list:
            return await msg.answer(
                "👥 СПИСОК ПЕРСОНАЛА\n\n"
                "❌ В этой беседе нет пользователей с ролями\n\n"
                "Используйте /giverole чтобы выдать роль"
            )
        
        text = "👥 СПИСОК ПЕРСОНАЛА БЕСЕДЫ\n\n"
        
        cur.execute("""
        SELECT role_priority, role_name FROM roles
        WHERE peer_id=%s
        """, (peer_id,))
        roles_dict = {role[0]: role[1] for role in cur.fetchall()}
        
        for user_id, role_priority in staff_list:
            user_name = await get_user_name(user_id)
            role_name = roles_dict.get(role_priority, f"Уровень {role_priority}")
            
            if is_user_banned(peer_id, user_id):
                status = "🚫 ЗАБАНЕН"
            elif is_user_muted(peer_id, user_id):
                status = "🔇 ЗАМУЧЕН"
            else:
                status = "✅ Активен"
            
            text += f"👤 {user_name} ({status})\n"
            text += f"   📊 {role_name} (приоритет: {role_priority})\n"
            
            cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (user_id, peer_id))
            nick_res = cur.fetchone()
            if nick_res and nick_res[0]:
                text += f"   🏷 Ник: {nick_res[0]}\n"
            
            cur.execute("SELECT warn_count FROM users WHERE user_id=%s AND peer_id=%s", (user_id, peer_id))
            warn_res = cur.fetchone()
            if warn_res and warn_res[0] > 0:
                text += f"   ⚠️ Предупреждений: {warn_res[0]}/3\n"
            
            try:
                res = await bot.api.messages.get_conversation_members(peer_id=peer_id)
                for m in res.items:
                    if m.member_id == user_id and getattr(m, "is_owner", False):
                        text += "   👑 Создатель беседы\n"
                        break
            except:
                pass
            
            text += "\n"
        
        text += f"👥 Всего пользователей с ролями: {len(staff_list)}"
        
        return await msg.answer(text)
    
    except Exception as e:
        logger.error(f"ERROR in staff: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при получении списка персонала")
    finally:
        conn.close()

# =========================
# WARN COMMAND
# =========================
@bot.on.message(text="/warn")
async def warn_help(msg: Message):
    return await msg.answer(
        "⚠️ КОМАНДА: ПРЕДУПРЕЖДЕНИЕ\n\n"
        "📝 Синтаксис:\n"
        "/warn @пользователь [причина]\n"
        "или ответьте на сообщение: /warn [причина]\n\n"
        "📋 Описание:\n"
        "Выдаёт предупреждение пользователю\n"
        "После 3 предупреждений - автоматический бан\n\n"
        "⚙️ Примеры:\n"
        "• /warn @Ivan спам\n"
        "• /warn @Maria флуд\n"
        "• (ответить на сообщение) /warn оскорбление"
    )

@bot.on.message(text="/warn <reason>")
async def warn_reply(msg: Message, reason: str = "Без указанной причины"):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'warn')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /warn @Ivan спам")
        
        uid = msg.reply_message.from_id
        await process_warn(msg, uid, reason, user_role)
    finally:
        conn.close()

@bot.on.message(text="/warn <user_info> <reason>")
async def warn(msg: Message, user_info: str, reason: str = "Без указанной причины"):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'warn')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_warn(msg, uid, reason, user_role)
    finally:
        conn.close()

async def process_warn(msg: Message, uid: int, reason: str, sender_role: int):
    conn, cur = db()
    try:
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_name = await get_user_name(uid)
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(
                f"❌ НЕЛЬЗЯ НАКАЗАТЬ\n\n"
                f"👤 {target_name}\n"
                f"📊 Его приоритет: {target_role}\n"
                f"📊 Ваш приоритет: {sender_role}"
            )
        
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
            """, (uid, pid, "Автоматический бан (3 предупреждения)"))
            
            await kick_user(pid, uid)
            
            return await msg.answer(
                f"🚫 АВТОМАТИЧЕСКИЙ БАН\n\n"
                f"👤 {user_name} (id{uid})\n"
                f"📋 Причина: 3 предупреждения\n\n"
                f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )

        await msg.answer(
            f"⚠️ ПРЕДУПРЕЖДЕНИЕ ВЫДАНО\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"📊 Статус: {warns}/3\n"
            f"📝 Причина: «{reason}»\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    except Exception as e:
        logger.error(f"ERROR in warn: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при выдаче предупреждения")
    finally:
        conn.close()

# =========================
# MUTE COMMAND
# =========================
@bot.on.message(text="/mute")
async def mute_help(msg: Message):
    return await msg.answer(
        "🔇 КОМАНДА: МУТ\n\n"
        "📝 Синтаксис:\n"
        "/mute @пользователь [время] [причина]\n"
        "или ответьте на сообщение: /mute [время] [причина]\n\n"
        "⏱️ Форматы времени:\n"
        "• 10m - 10 минут\n"
        "• 1h - 1 час\n"
        "• 1d - 1 день\n"
        "• permanent - навсегда\n\n"
        "⚙️ Примеры:\n"
        "• /mute @Ivan 30m спам\n"
        "• /mute @Maria 1h флуд\n"
        "• (ответить на сообщение) /mute 2h оскорбление\n\n"
        "📋 Примечание:\n"
        "Все сообщения замученного пользователя будут удаляться"
    )

@bot.on.message(text="/mute <time_or_reason>")
async def mute_reply(msg: Message, time_or_reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'mute')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /mute @Ivan 30m спам")
        
        uid = msg.reply_message.from_id
        await process_mute(msg, uid, time_or_reason, "", user_role)
    finally:
        conn.close()

@bot.on.message(text="/mute <user_info> <time_or_reason>")
async def mute(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'mute')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_mute(msg, uid, time_or_reason, "", user_role)
    finally:
        conn.close()

@bot.on.message(text="/mute <user_info> <time_or_reason> <reason>")
async def mute_with_reason(msg: Message, user_info: str, time_or_reason: str, reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'mute')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_mute(msg, uid, time_or_reason, reason, user_role)
    finally:
        conn.close()

async def process_mute(msg: Message, uid: int, time_or_reason: str, reason: str, sender_role: int):
    conn, cur = db()
    try:
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_name = await get_user_name(uid)
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(
                f"❌ НЕЛЬЗЯ НАКАЗАТЬ\n\n"
                f"👤 {target_name}\n"
                f"📊 Его приоритет: {target_role}\n"
                f"📊 Ваш приоритет: {sender_role}"
            )
        
        pid = msg.peer_id
        
        duration = None
        final_reason = reason if reason else "Без указанной причины"
        
        parsed_time = parse_time(time_or_reason)
        if parsed_time:
            duration = parsed_time
        else:
            if time_or_reason.lower() != "permanent":
                final_reason = time_or_reason + (" " + reason if reason else "")
        
        if duration:
            end_time = datetime.now() + duration
            formatted_time = format_time(duration)
        else:
            end_time = None
            formatted_time = "навсегда"

        user_name = await get_user_name(uid)

        cur.execute("""
        INSERT INTO punishments (user_id, peer_id, type, end_at, reason)
        VALUES (%s, %s, 'mute', %s, %s)
        """, (uid, pid, end_time, final_reason))

        end_info = f"🔚 До: {end_time.strftime('%d.%m в %H:%M')}" if end_time else "⏰ Навсегда"

        await msg.answer(
            f"🔇 МУТ НАЛОЖЕН\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"⏰ {formatted_time}\n"
            f"{end_info}\n"
            f"📝 Причина: «{final_reason}»\n\n"
            f"📋 Все сообщения пользователя будут удаляться\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    except Exception as e:
        logger.error(f"ERROR in mute: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при выдаче мута")
    finally:
        conn.close()

# =========================
# UNMUTE COMMAND
# =========================
@bot.on.message(text="/unmute")
async def unmute_help(msg: Message):
    return await msg.answer(
        "🔊 КОМАНДА: СНЯТЬ МУТ\n\n"
        "📝 Синтаксис:\n"
        "/unmute @пользователь\n"
        "или ответьте на сообщение: /unmute"
    )

@bot.on.message(text="/unmute")
async def unmute_reply(msg: Message):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'unmute')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /unmute @Ivan")
        
        uid = msg.reply_message.from_id
        await process_unmute(msg, uid)
    finally:
        conn.close()

@bot.on.message(text="/unmute <user_info>")
async def unmute(msg: Message, user_info: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'unmute')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_unmute(msg, uid)
    finally:
        conn.close()

async def process_unmute(msg: Message, uid: int):
    conn, cur = db()
    try:
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        """, (uid, pid))
        
        await msg.answer(
            f"🔊 МУТ СНЯТ\n\n"
            f"👤 {user_name} (id{uid})\n\n"
            f"✅ Может писать в чат\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    except Exception as e:
        logger.error(f"ERROR in unmute: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при снятии мута")
    finally:
        conn.close()

# =========================
# BAN COMMAND
# =========================
@bot.on.message(text="/ban")
async def ban_help(msg: Message):
    return await msg.answer(
        "🚫 КОМАНДА: БАН\n\n"
        "📝 Синтаксис:\n"
        "/ban @пользователь [время] [причина]\n"
        "или ответьте на сообщение: /ban [время] [причина]\n\n"
        "⏱️ Форматы времени:\n"
        "• 1h - 1 час\n"
        "• 1d - 1 день\n"
        "• permanent - навсегда\n\n"
        "⚙️ Примеры:\n"
        "• /ban @Ivan 1d спам\n"
        "• /ban @John permanent тролль\n"
        "• (ответить на сообщение) /ban 30m флуд\n\n"
        "📋 Примечание:\n"
        "Пользователь будет исключён из беседы\n"
        "При попытке вернуться - будет снова исключён"
    )

@bot.on.message(text="/ban <time_or_reason>")
async def ban_reply(msg: Message, time_or_reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'ban')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /ban @Ivan 1d спам")
        
        uid = msg.reply_message.from_id
        await process_ban(msg, uid, time_or_reason, "", user_role)
    finally:
        conn.close()

@bot.on.message(text="/ban <user_info> <time_or_reason>")
async def ban(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'ban')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_ban(msg, uid, time_or_reason, "", user_role)
    finally:
        conn.close()

@bot.on.message(text="/ban <user_info> <time_or_reason> <reason>")
async def ban_with_reason(msg: Message, user_info: str, time_or_reason: str, reason: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'ban')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_ban(msg, uid, time_or_reason, reason, user_role)
    finally:
        conn.close()

async def process_ban(msg: Message, uid: int, time_or_reason: str, reason: str, sender_role: int):
    conn, cur = db()
    try:
        if not can_punish_user(cur, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            target_name = await get_user_name(uid)
            target_role = get_user_role(cur, msg.peer_id, uid)
            return await msg.answer(
                f"❌ НЕЛЬЗЯ НАКАЗАТЬ\n\n"
                f"👤 {target_name}\n"
                f"📊 Его приоритет: {target_role}\n"
                f"📊 Ваш приоритет: {sender_role}"
            )
        
        pid = msg.peer_id
        
        duration = None
        final_reason = reason if reason else "Без указанной причины"
        
        if time_or_reason.lower() == "permanent":
            duration = None
        else:
            parsed_time = parse_time(time_or_reason)
            if parsed_time:
                duration = parsed_time
            else:
                final_reason = time_or_reason + (" " + reason if reason else "")
        
        if duration:
            end_time = datetime.now() + duration
            formatted_time = format_time(duration)
        else:
            end_time = None
            formatted_time = "навсегда"

        user_name = await get_user_name(uid)

        cur.execute("""
        INSERT INTO punishments (user_id, peer_id, type, end_at, reason)
        VALUES (%s, %s, 'ban', %s, %s)
        """, (uid, pid, end_time, final_reason))

        kicked = await kick_user(pid, uid)
        
        kick_status = "✅ Исключён из беседы" if kicked else "⚠️ Не удалось исключить"

        duration_info = f"⏰ {formatted_time}" if formatted_time else "⏰ Вечный бан"

        await msg.answer(
            f"🚫 БАН НАЛОЖЕН\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"{duration_info}\n"
            f"📝 Причина: «{final_reason}»\n"
            f"{kick_status}\n\n"
            f"📋 При попытке вернуться будет снова исключён\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    except Exception as e:
        logger.error(f"ERROR in ban: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при бане")
    finally:
        conn.close()

# =========================
# UNBAN COMMAND
# =========================
@bot.on.message(text="/unban")
async def unban_help(msg: Message):
    return await msg.answer(
        "✅ КОМАНДА: РАЗБАНИТЬ\n\n"
        "📝 Синтаксис:\n"
        "/unban @пользователь\n"
        "или ответьте на сообщение: /unban"
    )

@bot.on.message(text="/unban")
async def unban_reply(msg: Message):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'unban')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /unban @Ivan")
        
        uid = msg.reply_message.from_id
        await process_unban(msg, uid)
    finally:
        conn.close()

@bot.on.message(text="/unban <user_info>")
async def unban(msg: Message, user_info: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'unban')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_unban(msg, uid)
    finally:
        conn.close()

async def process_unban(msg: Message, uid: int):
    conn, cur = db()
    try:
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
        """, (uid, pid))
        
        await msg.answer(
            f"✅ БАН СНЯТ\n\n"
            f"👤 {user_name} (id{uid})\n\n"
            f"🔓 Может присоединиться к беседе\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

    except Exception as e:
        logger.error(f"ERROR in unban: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при разбане")
    finally:
        conn.close()

# =========================
# KICK COMMAND
# =========================
@bot.on.message(text="/kick")
async def kick_help(msg: Message):
    return await msg.answer(
        "👢 КОМАНДА: ИСКЛЮЧЕНИЕ\n\n"
        "📝 Синтаксис:\n"
        "/kick @пользователь\n"
        "или ответьте на сообщение: /kick\n\n"
        "📋 Примечание:\n"
        "Пользователь сможет вернуться обратно"
    )

@bot.on.message(text="/kick")
async def kick_reply(msg: Message):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'kick')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        if not msg.reply_message:
            return await msg.answer("❌ ОШИБКА: Ответьте на сообщение пользователя или укажите его\n\nПример: /kick @Ivan")
        
        uid = msg.reply_message.from_id
        await process_kick(msg, uid, user_role)
    finally:
        conn.close()

@bot.on.message(text="/kick <user_info>")
async def kick(msg: Message, user_info: str):
    conn, cur = db()
    try:
        has_perm, user_role, required_role = check_permission(cur, msg.peer_id, msg.from_id, 'kick')
        if not has_perm:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет: {required_role}\n"
                f"Ваш приоритет: {user_role}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        await process_kick(msg, uid, user_role)
    finally:
        conn.close()

async def process_kick(msg: Message, uid: int, sender_role: int):
    try:
        if not can_punish_user(None, msg.peer_id, msg.from_id, uid) and msg.from_id != OWNER_ID:
            user_name = await get_user_name(uid)
            return await msg.answer(f"❌ НЕЛЬЗЯ ИСКЛЮЧИТЬ\n\n👤 {user_name}")
        
        user_name = await get_user_name(uid)
        
        kicked = await kick_user(msg.peer_id, uid)
        
        if kicked:
            await msg.answer(
                f"👢 ИСКЛЮЧЕН\n\n"
                f"👤 {user_name} (id{uid})\n\n"
                f"💬 Может присоединиться обратно\n\n"
                f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            await msg.answer(f"❌ НЕ УДАЛОСЬ ИСКЛЮЧИТЬ\n\n👤 {user_name}")

    except Exception as e:
        logger.error(f"ERROR in kick: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при исключении")

# =========================
# SNICK COMMAND
# =========================
@bot.on.message(text="/snick")
async def snick_help(msg: Message):
    return await msg.answer(
        "🏷 КОМАНДА: УСТАНОВКА НИКА\n\n"
        "📝 Синтаксис:\n"
        "/snick [ник]\n\n"
        "⚙️ Примеры:\n"
        "• /snick Король\n"
        "• /snick Модератор\n\n"
        "📋 Примечание:\n"
        "Максимальная длина ника - 50 символов"
    )

@bot.on.message(text="/snick <nick>")
async def snick(msg: Message, nick: str):
    conn, cur = db()
    try:
        if len(nick) > 50:
            return await msg.answer("❌ НИК СЛИШКОМ ДЛИННЫЙ (макс. 50 символов)")

        target = msg.from_id
        pid = msg.peer_id
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, nickname)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET nickname=%s
        """, (target, pid, nick, nick))

        return await msg.answer(
            f"✅ НИК УСТАНОВЛЕН\n\n"
            f"👤 @id{target}\n"
            f"🏷 {nick}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in snick: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при установке ника")
    finally:
        conn.close()

# =========================
# RNICK COMMAND
# =========================
@bot.on.message(text="/rnick")
async def rnick_help(msg: Message):
    return await msg.answer(
        "🧹 КОМАНДА: УДАЛИТЬ НИК\n\n"
        "📝 Синтаксис:\n"
        "/rnick\n\n"
        "📋 Примечание:\n"
        "Удаляет ваш текущий ник"
    )

@bot.on.message(text="/rnick")
async def rnick(msg: Message):
    conn, cur = db()
    try:
        target = msg.from_id
        pid = msg.peer_id

        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (target, pid))
        res = cur.fetchone()
        old_nick = res[0] if res else None

        if not old_nick:
            return await msg.answer(
                f"❌ НИК НЕ УСТАНОВЛЕН\n\n"
                f"👤 @id{target}"
            )

        cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (target, pid))

        user_name = await get_user_name(target)

        return await msg.answer(
            f"🧹 НИК УДАЛЁН\n\n"
            f"👤 {user_name} (id{target})\n"
            f"❌ Был: {old_nick}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in rnick: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при удалении ника")
    finally:
        conn.close()

# =========================
# STATS COMMAND
# =========================
@bot.on.message(text="/stats")
async def stats_help(msg: Message):
    return await msg.answer(
        "📊 КОМАНДА: СТАТИСТИКА\n\n"
        "📝 Синтаксис:\n"
        "/stats [@пользователь]\n"
        "или ответьте на сообщение: /stats"
    )

@bot.on.message(text="/stats")
async def stats_reply(msg: Message):
    conn, cur = db()
    try:
        uid = msg.reply_message.from_id if msg.reply_message else msg.from_id
        await process_stats(msg, uid)
    finally:
        conn.close()

@bot.on.message(text="/stats <user_info>")
async def stats(msg: Message, user_info: str):
    conn, cur = db()
    try:
        uid = extract(msg)
        if not uid:
            try:
                uid = await resolve_user_id(user_info)
                if not uid:
                    uid = msg.from_id
            except:
                uid = msg.from_id
        
        await process_stats(msg, uid)
    finally:
        conn.close()

async def process_stats(msg: Message, uid: int):
    conn, cur = db()
    try:
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        cur.execute("""
        SELECT role, msgs, warn_count, nickname FROM users
        WHERE user_id=%s AND peer_id=%s
        """, (uid, pid))
        
        res = cur.fetchone()
        
        if not res:
            return await msg.answer(
                f"📊 СТАТИСТИКА\n\n"
                f"👤 {user_name} (id{uid})\n\n"
                f"❌ Нет данных"
            )
        
        role, msgs, warn_count, nickname = res
        
        cur.execute("""
        SELECT role_name FROM roles
        WHERE peer_id=%s AND role_priority=%s
        """, (pid, role))
        
        role_res = cur.fetchone()
        role_name = role_res[0] if role_res else f"Уровень {role}"
        
        if is_user_banned(pid, uid):
            status = "🚫 ЗАБАНЕН"
        elif is_user_muted(pid, uid):
            status = "🔇 ЗАМУЧЕН"
        else:
            status = "✅ Активен"
        
        nick_info = f"🏷 Ник: {nickname}\n" if nickname else ""
        
        return await msg.answer(
            f"📊 СТАТИСТИКА\n\n"
            f"👤 {user_name}\n"
            f"🆔 ID: {uid}\n"
            f"📊 Статус: {status}\n\n"
            f"🎖️ Роль: {role_name}\n"
            f"📊 Приоритет: {role}\n"
            f"💬 Сообщений: {msgs}\n"
            f"⚠️ Предупреждений: {warn_count}/3\n"
            f"{nick_info}\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        logger.error(f"ERROR in stats: {e}")
        traceback.print_exc()
        return await msg.answer("❌ Произошла ошибка при получении статистики")
    finally:
        conn.close()

# =========================
# MAIN MESSAGE HANDLER
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()

    try:
        if not msg.text:
            return

        uid, pid = msg.from_id, msg.peer_id
        text = msg.text.strip()

        # Проверяем, забанен ли пользователь
        if is_user_banned(pid, uid):
            await kick_user(pid, uid)
            return

        # Проверяем, замучен ли пользователь
        if is_user_muted(pid, uid):
            if hasattr(msg, 'conversation_message_id'):
                await delete_message(pid, msg.conversation_message_id)
            return

        # Обновляем статистику
        cur.execute("""
        INSERT INTO users (user_id, peer_id, msgs)
        VALUES (%s, %s, 1)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET msgs = users.msgs + 1
        """, (uid, pid))

        if not text.startswith("/"):
            return

    except Exception as e:
        logger.error(f"ERROR in handler: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# AUTO KICK BANNED USERS
# =========================
# Авто-кик забаненных работает через главный обработчик сообщений (handler)
# Пользователь будет кикнут при попытке отправить любое сообщение

# =========================
# PERIODIC CHECK FOR EXPIRED PUNISHMENTS
# =========================
async def check_expired_punishments():
    """Периодически проверяет истёкшие наказания"""
    while True:
        try:
            conn, cur = db()
            try:
                # Получаем истёкшие баны до удаления
                cur.execute("""
                SELECT user_id, peer_id FROM punishments
                WHERE type='ban' AND end_at IS NOT NULL AND end_at < NOW()
                """)
                expired_bans = cur.fetchall()
                
                # Удаляем все истёкшие наказания
                cur.execute("""
                DELETE FROM punishments
                WHERE end_at IS NOT NULL AND end_at < NOW()
                """)
                
                # Уведомляем о разбане
                for user_id, peer_id in expired_bans:
                    logger.info(f"Ban expired for user {user_id} in chat {peer_id}")
                    try:
                        await bot.api.messages.send(
                            peer_id=peer_id,
                            message=f"✅ СРОК БАНА ИСТЁК\n\n"
                                   f"👤 @id{user_id} может снова присоединиться к беседе\n\n"
                                   f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                            random_id=0
                        )
                    except Exception as e:
                        logger.error(f"Failed to send unban notification: {e}")
                
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"ERROR in check_expired_punishments: {e}")
        
        await asyncio.sleep(60)

# =========================
# STARTUP
# =========================
if __name__ == "__main__":
    logger.info(">>> BOT STARTING...")
    print(">>> FLEX BOT STARTED")
    print(f">>> Owner ID: {OWNER_ID}")
    print(">>> Waiting for messages...")
    
    loop = asyncio.get_event_loop()
    loop.create_task(check_expired_punishments())
    
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        print("\n>>> Bot stopped by user")
    except Exception as e:
        print(f">>> FATAL ERROR: {e}")
        traceback.print_exc()
