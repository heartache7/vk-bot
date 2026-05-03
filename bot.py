import os
import re
import asyncio
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

        cur.execute("""
        CREATE TABLE IF NOT EXISTS commands_settings(
            id SERIAL PRIMARY KEY,
            peer_id BIGINT,
            command TEXT,
            required_role INT DEFAULT 0,
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(peer_id, command)
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

def check_cmd_permission(cur, peer_id, cmd_name, user_role):
    """Проверяет, может ли пользователь использовать команду"""
    cur.execute("""
    SELECT required_role, enabled FROM commands_settings
    WHERE peer_id=%s AND command=%s
    """, (peer_id, cmd_name))
    
    res = cur.fetchone()
    if not res:
        return True
    
    required_role, enabled = res
    if not enabled:
        return False
    
    return user_role >= required_role

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
        'addrole': 60,
    }
    return defaults.get(cmd_name, 0)

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
                "🔧 Я бот для модерации чатов с множеством полезных функций:\n\n"
                "🏷 НИКИ - устанавливайте красивые ники\n"
                "⚠️ МОДЕРАЦИЯ - система предупреждений и наказаний\n"
                "🔇 МУТ - запрет на написание сообщений\n"
                "🚫 БАН - блокировка пользователей\n"
                "👢 КИК - исключение из беседы\n"
                "🎖️ РОЛИ - система ролей и приоритетов\n"
                "⚙️ НАСТРОЙКИ - управление командами\n\n"
                "⚠️ ОШИБКА ПРАВ ДОСТУПА\n\n"
                "🔧 Что нужно сделать:\n"
                "1️⃣ Откройте настройки беседы\n"
                "2️⃣ Перейдите в раздел 'Управление ботами'\n"
                "3️⃣ Выдайте боту 'FLEX BOT' права администратора\n"
                "4️⃣ Убедитесь, что включены права на:\n"
                "   • Редактирование сообщений\n"
                "   • Удаление сообщений\n"
                "   • Исключение пользователей\n"
                "   • Просмотр списка участников\n\n"
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
                    "✅ БОТ УСПЕШНО ИНИЦИАЛИЗИРОВАН\n\n"
                    "👑 FLEX BOT активирован в вашей беседе!\n\n"
                    f"🎖️ Создателю беседы @id{m.member_id} ({owner_name})\n"
                    "выдана роль с приоритетом 100\n\n"
                    "🎯 Основной функционал:\n"
                    "• 🏷 Установка и удаление ников\n"
                    "• ⚠️ Система предупреждений (3 = бан)\n"
                    "• 🔇 Мут (запрет на отправку сообщений)\n"
                    "• 🚫 Блокировка пользователей\n"
                    "• 👢 Исключение из беседы\n"
                    "• 📊 Система ролей\n"
                    "• ⚙️ Управление командами\n\n"
                    "📖 Введите /help чтобы увидеть все команды"
                )

    except Exception as e:
        print(f"ERROR in start: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# SYSTEM COMMANDS (ONLY FOR BOT OWNER)
# =========================
@bot.on.message(text="/sysrole")
async def sysrole_help(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН\n\nЭта команда доступна только владельцу бота")
    
    return await msg.answer(
        "⚙️ СИСТЕМНАЯ КОМАНДА: /sysrole\n\n"
        "🔒 ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА БОТА\n\n"
        "📝 Синтаксис:\n"
        "/sysrole info - информация о системе\n"
        "/sysrole [беседа] @пользователь [приоритет] - выдать роль в любой беседе\n\n"
        "⚙️ Примеры:\n"
        "• /sysrole info\n"
        "• /sysrole 2000000001 @Ivan 50\n"
        "• /sysrole 2000000002 @Maria 100"
    )

@bot.on.message(text="/sysrole info")
async def sysrole_info(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    return await msg.answer(
        "ℹ️ ИНФОРМАЦИЯ О СИСТЕМЕ РОЛЕЙ\n\n"
        "👤 Владелец бота: @id676081199\n\n"
        "📊 СИСТЕМА ПРИОРИТЕТОВ:\n"
        "• 0-9 - Обычный пользователь\n"
        "• 10-49 - Модератор\n"
        "• 50-59 - Администратор\n"
        "• 60+ - Главный администратор\n"
        "• 100 - Создатель беседы\n\n"
        "🔧 УПРАВЛЕНИЕ РОЛЯМИ:\n"
        "• /sysrole [беседа] @user [приоритет] - выдать роль в любой беседе\n"
        "• /giverole @user [приоритет] - выдать роль в текущей беседе\n"
        "• /setcmd - управление командами\n\n"
        "📋 ДОСТУПНЫЕ КОМАНДЫ:\n"
        "warn, mute, unmute, ban, unban, kick, snick, rnick, giverole, addrole"
    )

@bot.on.message(text="/sysrole <peer_id> <user_info> <priority>")
async def sysrole_set(msg: Message, peer_id: str, user_info: str, priority: str):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
    conn, cur = db()
    try:
        try:
            peer_id = int(peer_id)
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: беседа и приоритет должны быть числами\n\nПример: /sysrole 2000000001 @Ivan 50")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: неверный формат пользователя\n\nПример: /sysrole 2000000001 @Ivan 50")
        
        if priority_int < 0 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 0 до 1000")
        
        user_name = await get_user_name(uid)
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, peer_id, priority_int, priority_int))
        
        # Получаем имя роли если существует
        cur.execute("""
        SELECT role_name FROM roles
        WHERE peer_id=%s AND role_priority=%s
        """, (peer_id, priority_int))
        
        role_res = cur.fetchone()
        role_name = role_res[0] if role_res else f"Уровень {priority_int}"
        
        await msg.answer(
            f"🎖️ РОЛЬ ВЫДАНА (СИСТЕМА)\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"🏘 Беседа: {peer_id}\n"
            f"📋 Роль: {role_name}\n"
            f"📊 Приоритет: {priority_int}\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👨‍💼 Владелец: @id{msg.from_id}"
        )
    
    except Exception as e:
        print(f"ERROR in sysrole_set: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# ROLES COMMANDS
# =========================
@bot.on.message(text="/addrole")
async def addrole_help(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН\n\nЭта команда доступна только владельцу бота")
    
    return await msg.answer(
        "📋 КОМАНДА: ДОБАВЛЕНИЕ РОЛИ\n\n"
        "🔒 ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА БОТА\n\n"
        "📝 Синтаксис:\n"
        "/addrole [приоритет] [имя роли]\n\n"
        "📊 ПРИОРИТЕТЫ:\n"
        "• 1-9 - Низкий\n"
        "• 10-49 - Средний (Модератор)\n"
        "• 50-59 - Высокий (Администратор)\n"
        "• 60+ - Главный администратор\n"
        "• 100 - Создатель\n\n"
        "⚙️ Примеры:\n"
        "• /addrole 50 Админ\n"
        "• /addrole 10 Модератор\n"
        "• /addrole 100 Создатель"
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
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /addrole 50 Админ")
        
        if priority < 1 or priority > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 1 до 1000")
        
        if len(role_name) > 30:
            return await msg.answer("❌ ОШИБКА: имя роли слишком длинное (макс. 30 символов)")
        
        peer_id = msg.peer_id
        
        cur.execute("""
        INSERT INTO roles (peer_id, role_priority, role_name)
        VALUES (%s, %s, %s)
        """, (peer_id, priority, role_name))
        
        return await msg.answer(
            f"✅ РОЛЬ ДОБАВЛЕНА\n\n"
            f"📋 Имя: {role_name}\n"
            f"📊 Приоритет: {priority}\n"
            f"🏘 Беседа: {peer_id}\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        if "unique" in str(e).lower():
            return await msg.answer(f"❌ ОШИБКА: приоритет {priority} уже используется в этой беседе")
        print(f"ERROR in addrole: {e}")
        traceback.print_exc()
    finally:
        conn.close()

@bot.on.message(text="/listroles")
async def listroles(msg: Message):
    if msg.from_id != OWNER_ID:
        return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
    
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
                "❌ В этой беседе ещё нет добавленных ролей\n\n"
                "🆕 Добавьте роль командой:\n"
                "/addrole 50 Админ"
            )
        
        roles_text = "📊 СПИСОК РОЛЕЙ В БЕСЕДЕ\n\n"
        for priority, name in roles:
            roles_text += f"  {priority:3d} - {name}\n"
        
        return await msg.answer(roles_text)
    
    except Exception as e:
        print(f"ERROR in listroles: {e}")
        traceback.print_exc()
    finally:
        conn.close()

@bot.on.message(text="/giverole")
async def giverole_help(msg: Message):
    return await msg.answer(
        "🎖️ КОМАНДА: ВЫДАЧА РОЛИ\n\n"
        "📝 Синтаксис:\n"
        "/giverole @пользователь [приоритет]\n"
        "или ответьте на сообщение: /giverole [приоритет]\n\n"
        "📊 ТРЕБУЕМЫЙ ПРИОРИТЕТ: минимум 60\n\n"
        "⚙️ Примеры:\n"
        "• /giverole @Ivan 10\n"
        "• /giverole @Maria 50\n"
        "• (ответить на сообщение) /giverole 100"
    )

@bot.on.message(text="/giverole <user_info> <priority>")
async def giverole(msg: Message, user_info: str, priority: str):
    conn, cur = db()
    try:
        # Проверяем права выдающего роль
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        # Проверяем настройки команды
        default_role = get_default_cmd_role('giverole')
        if not check_cmd_permission(cur, msg.peer_id, 'giverole', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет роли: {default_role}\n"
                f"Ваш приоритет: {sender_role_val}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ\n\nИспользуйте: /giverole @username приоритет")
        
        try:
            priority_int = int(priority)
        except:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть числом\n\nПример: /giverole @Ivan 50")
        
        if priority_int < 0 or priority_int > 1000:
            return await msg.answer("❌ ОШИБКА: приоритет должен быть от 0 до 1000")
        
        user_name = await get_user_name(uid)
        
        cur.execute("""
        INSERT INTO users (user_id, peer_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET role=%s
        """, (uid, msg.peer_id, priority_int, priority_int))
        
        # Получаем имя роли если существует
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
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Выдал: @id{msg.from_id}"
        )
    
    except Exception as e:
        print(f"ERROR in giverole: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# COMMAND SETTINGS
# =========================
@bot.on.message(text="/setcmd")
async def setcmd_help(msg: Message):
    conn, cur = db()
    try:
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        if sender_role_val < 50:
            return await msg.answer(
                "❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                "Требуемый приоритет роли: 50 (Администратор)"
            )
        
        return await msg.answer(
            "⚙️ КОМАНДА: НАСТРОЙКА КОМАНД\n\n"
            "📝 Синтаксис:\n"
            "/setcmd список - показать все команды\n"
            "/setcmd [команда] [роль] - установить минимальную роль для команды\n"
            "/setcmd [команда] вкл/выкл - включить/выключить команду\n\n"
            "🔧 Примеры:\n"
            "• /setcmd warn 10 - warn требует роль 10+\n"
            "• /setcmd ban выкл - отключить команду ban\n"
            "• /setcmd warn вкл - включить команду warn\n\n"
            "📋 ДОСТУПНЫЕ КОМАНДЫ:\n"
            "warn, mute, unmute, ban, unban, kick, snick, rnick, giverole, addrole"
        )
    finally:
        conn.close()

@bot.on.message(text="/setcmd список")
async def setcmd_list(msg: Message):
    conn, cur = db()
    try:
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        if sender_role_val < 50:
            return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
        
        peer_id = msg.peer_id
        cur.execute("""
        SELECT command, required_role, enabled FROM commands_settings
        WHERE peer_id=%s
        ORDER BY command
        """, (peer_id,))
        
        settings = cur.fetchall()
        
        if not settings:
            text = "⚙️ НАСТРОЙКИ КОМАНД (СТАНДАРТНЫЕ)\n\n"
            for cmd in ['warn', 'mute', 'unmute', 'ban', 'unban', 'kick', 'snick', 'rnick', 'giverole', 'addrole']:
                role = get_default_cmd_role(cmd)
                text += f"{cmd:10} - ✅ ВКЛ - роль {role}+\n"
            
            text += "\nДля изменения используйте /setcmd [команда] [роль]"
            return await msg.answer(text)
        
        text = "⚙️ ТЕКУЩИЕ НАСТРОЙКИ КОМАНД\n\n"
        for cmd, role, enabled in settings:
            status = "✅ ВКЛ" if enabled else "❌ ВЫКЛ"
            text += f"{cmd:10} - {status} - роль {role}+\n"
        
        return await msg.answer(text)
    finally:
        conn.close()

@bot.on.message(text="/setcmd <command> <param>")
async def setcmd_set(msg: Message, command: str, param: str):
    conn, cur = db()
    try:
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        if sender_role_val < 50:
            return await msg.answer("❌ ДОСТУП ЗАПРЕЩЁН")
        
        valid_commands = ['warn', 'mute', 'unmute', 'ban', 'unban', 'kick', 'snick', 'rnick', 'giverole', 'addrole']
        command = command.lower()
        
        if command not in valid_commands:
            return await msg.answer(f"❌ КОМАНДА НЕ НАЙДЕНА\n\nДоступные: {', '.join(valid_commands)}")
        
        peer_id = msg.peer_id
        
        if param.lower() in ['вкл', 'выкл']:
            enabled = param.lower() == 'вкл'
            cur.execute("""
            INSERT INTO commands_settings (peer_id, command, enabled)
            VALUES (%s, %s, %s)
            ON CONFLICT (peer_id, command)
            DO UPDATE SET enabled=%s
            """, (peer_id, command, enabled, enabled))
            
            status = "✅ ВКЛЮЧЕНА" if enabled else "❌ ОТКЛЮЧЕНА"
            return await msg.answer(
                f"⚙️ КОМАНДА ИЗМЕНЕНА\n\n"
                f"📋 Команда: /{command}\n"
                f"📊 Статус: {status}\n\n"
                f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        
        else:
            try:
                role = int(param)
                if role < 0 or role > 1000:
                    return await msg.answer("❌ ОШИБКА: роль должна быть от 0 до 1000")
                
                cur.execute("""
                INSERT INTO commands_settings (peer_id, command, required_role, enabled)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (peer_id, command)
                DO UPDATE SET required_role=%s
                """, (peer_id, command, role, role))
                
                return await msg.answer(
                    f"⚙️ КОМАНДА ИЗМЕНЕНА\n\n"
                    f"📋 Команда: /{command}\n"
                    f"📊 Минимальная роль: {role}\n\n"
                    f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
            except:
                return await msg.answer("❌ ОШИБКА: параметр должен быть числом или 'вкл'/'выкл'")
    
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
        "Выдать пользователю предупреждение. После 3 предупреждений пользователь автоматически будет забанен.\n\n"
        "⚙️ Примеры:\n"
        "• /warn @Ivan спам в чате\n"
        "• (ответить на сообщение) /warn флуд"
    )

@bot.on.message(text="/warn <user_info> <reason>")
async def warn(msg: Message, user_info: str, reason: str = "Без указанной причины"):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        # Проверяем настройки команды
        default_role = get_default_cmd_role('warn')
        if not check_cmd_permission(cur, msg.peer_id, 'warn', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(
                f"❌ ДОСТУП ЗАПРЕЩЁН\n\n"
                f"Требуемый приоритет роли: {default_role}\n"
                f"Ваш приоритет: {sender_role_val}"
            )
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer(
                    "❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ\n\n"
                    "✅ Используйте один из этих форматов:\n"
                    "• /warn @username причина\n"
                    "• /warn id123456789 причина\n"
                    "• Ответьте на сообщение: /warn причина"
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
            """, (uid, pid, f"Автоматический бан (3 предупреждения)"))
            
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            except:
                pass
            
            return await msg.answer(
                f"🚫 ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n"
                f"👤 {user_name} (id{uid})\n"
                f"📋 Причина: Превышено максимальное количество предупреждений (3/3)\n\n"
                f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"👮 Модератор: @id{msg.from_id}"
            )

        warn_text = "предупреждение" if warns == 1 else "предупреждения" if warns == 2 else "предупреждений"
        warn_msg = "⚠️ ВНИМАНИЕ! ЕЩЁ 2 ПРЕДУПРЕЖДЕНИЯ И ПРОИЗОЙДЁТ БАН!" if warns == 1 else "⚠️⚠️ ОСТОРОЖНО! ЕЩЁ 1 ПРЕДУПРЕЖДЕНИЕ И ВЫ БУДЕТЕ ЗАБАНЕНЫ!" if warns == 2 else ""
        
        await msg.answer(
            f"⚠️ ПРЕДУПРЕЖДЕНИЕ ВЫДАНО\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"📊 Статус: {warns}/3 {warn_text}\n"
            f"📝 Причина: «{reason}»\n\n"
            f"{warn_msg}\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Модератор: @id{msg.from_id}"
        )

    except Exception as e:
        print(f"ERROR in warn: {e}")
        traceback.print_exc()
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
        "/mute @пользователь [причина] - время: навсегда\n\n"
        "⏱️ Форматы времени:\n"
        "• 10m - 10 минут\n"
        "• 1h - 1 час\n"
        "• 1d - 1 день\n\n"
        "⚙️ Примеры:\n"
        "• /mute @Ivan 30m спам в чате\n"
        "• /mute @Maria флуд"
    )

@bot.on.message(text="/mute <user_info> <time_or_reason>")
async def mute(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('mute')
        if not check_cmd_permission(cur, msg.peer_id, 'mute', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(f"❌ ДОСТУП ЗАПРЕЩЁН\n\nТребуемый приоритет: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ\n\nИспользуйте: /mute @username [время] причина")
        
        pid = msg.peer_id
        
        # Определяем время и причину
        duration = None
        reason = "Без указанной причины"
        
        if time_or_reason:
            parsed_time = parse_time(time_or_reason)
            if parsed_time:
                duration = parsed_time
            else:
                reason = time_or_reason
        
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
        """, (uid, pid, end_time, reason))

        end_info = f"🔚 Снимется: {end_time.strftime('%d.%m.%Y в %H:%M')}" if end_time else "⏰ Навсегда (вечный мут)"

        await msg.answer(
            f"🔇 МУТ НАЛОЖЕН\n\n"
            f"👤 {user_name} (id{uid})\n"
            f"⏰ Длительность: {formatted_time}\n"
            f"{end_info}\n"
            f"📝 Причина: «{reason}»\n\n"
            f"🚫 Все сообщения будут автоматически удалены\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Модератор: @id{msg.from_id}"
        )

    except Exception as e:
        print(f"ERROR in mute: {e}")
        traceback.print_exc()
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
        "или ответьте на сообщение: /unmute\n\n"
        "⚙️ Примеры:\n"
        "• /unmute @Ivan\n"
        "• (ответить на сообщение) /unmute"
    )

@bot.on.message(text="/unmute <user_info>")
async def unmute(msg: Message, user_info: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('unmute')
        if not check_cmd_permission(cur, msg.peer_id, 'unmute', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(f"❌ ДОСТУП ЗАПРЕЩЁН\n\nТребуемый приоритет: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        # Удаляем активные муты
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        """, (uid, pid))
        
        await msg.answer(
            f"🔊 МУТ СНЯТ\n\n"
            f"👤 {user_name} (id{uid})\n\n"
            f"✅ Пользователь может писать в чат\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Модератор: @id{msg.from_id}"
        )

    except Exception as e:
        print(f"ERROR in unmute: {e}")
        traceback.print_exc()
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
        "/ban @пользователь [причина] - время: навсегда\n\n"
        "⏱️ Форматы времени:\n"
        "• 1h - 1 час\n"
        "• 1d - 1 день\n"
        "• permanent - навсегда\n\n"
        "⚙️ Примеры:\n"
        "• /ban @Ivan 1d спам\n"
        "• /ban @John тролль"
    )

@bot.on.message(text="/ban <user_info> <time_or_reason>")
async def ban(msg: Message, user_info: str, time_or_reason: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('ban')
        if not check_cmd_permission(cur, msg.peer_id, 'ban', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(f"❌ ДОСТУП ЗАПРЕЩЁН\n\nТребуемый прио��итет: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        pid = msg.peer_id
        
        # Определяем время и причину
        duration = None
        reason = "Без указанной причины"
        
        if time_or_reason:
            if time_or_reason.lower() == "permanent":
                duration = None
            else:
                parsed_time = parse_time(time_or_reason)
                if parsed_time:
                    duration = parsed_time
                else:
                    reason = time_or_reason
        
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
        """, (uid, pid, end_time, reason))

        try:
            await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
        except:
            pass

        duration_info = f"⏰ Длительность: {formatted_time}\n🔚 Снимется: {end_time.strftime('%d.%m.%Y в %H:%M')}" if end_time else "⏰ Вечный банн"

        await msg.answer(
            f"🚫 ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n"
            f"👤 {user_name}\n"
            f"🆔 ID: {uid}\n\n"
            f"{duration_info}\n"
            f"📝 Причина: «{reason}»\n\n"
            f"✅ Пользователь исключён из беседы\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Администратор: @id{msg.from_id}"
        )

    except Exception as e:
        print(f"ERROR in ban: {e}")
        traceback.print_exc()
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
        "или ответьте на сообщение: /unban\n\n"
        "⚙️ Примеры:\n"
        "• /unban @Ivan\n"
        "• (ответить на сообщение) /unban"
    )

@bot.on.message(text="/unban <user_info>")
async def unban(msg: Message, user_info: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('unban')
        if not check_cmd_permission(cur, msg.peer_id, 'unban', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(f"❌ ДОСТУП ЗАПРЕЩЁН\n\nТребуемый приоритет: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        pid = msg.peer_id
        user_name = await get_user_name(uid)
        
        # Удаляем активные баны
        cur.execute("""
        DELETE FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban'
        """, (uid, pid))
        
        await msg.answer(
            f"✅ БАН СНЯТ\n\n"
            f"👤 {user_name} (id{uid})\n\n"
            f"🔓 Пользователь может присоединиться к беседе\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👮 Администратор: @id{msg.from_id}"
        )

    except Exception as e:
        print(f"ERROR in unban: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# KICK COMMAND
# =========================
@bot.on.message(text="/kick <user_info>")
async def kick(msg: Message, user_info: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('kick')
        if not check_cmd_permission(cur, msg.peer_id, 'kick', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        if sender_role_val < default_role:
            return await msg.answer(f"❌ ДОСТУП ЗАПРЕЩЁН\n\nТребуемый приоритет: {default_role}+")
        
        uid = extract(msg)
        if not uid:
            try:
                uid = int(user_info.replace("@", "").replace("id", ""))
            except:
                return await msg.answer("❌ ОШИБКА: НЕВЕРНЫЙ ФОРМАТ")
        
        user_name = await get_user_name(uid)
        
        try:
            await bot.api.messages.remove_chat_user(chat_id=msg.peer_id-2000000000, user_id=uid)
            await msg.answer(
                f"👢 ПОЛЬЗОВАТЕЛЬ ИСКЛЮЧЕН\n\n"
                f"👤 {user_name} (id{uid})\n\n"
                f"✅ Удалён из беседы\n"
                f"💬 Может присоединиться обратно\n\n"
                f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"👮 Модератор: @id{msg.from_id}"
            )
        except Exception as e:
            await msg.answer(f"❌ НЕ УДАЛОСЬ ИСКЛЮЧИТЬ\n\n👤 {user_name}")

    except Exception as e:
        print(f"ERROR in kick: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# SNICK COMMAND
# =========================
@bot.on.message(text="/snick")
async def snick_help(msg: Message):
    return await msg.answer(
        "🏷 КОМАНДА: НИК\n\n"
        "📝 Синтаксис:\n"
        "/snick [ник]\n"
        "или ответьте на сооб��ение: /snick [ник]\n\n"
        "⚙️ Примеры:\n"
        "• /snick ★ King ★\n"
        "• /snick 👑 Admin"
    )

@bot.on.message(text="/snick <nick>")
async def snick(msg: Message, nick: str):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('snick')
        if not check_cmd_permission(cur, msg.peer_id, 'snick', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        target = extract(msg) or msg.from_id
        pid = msg.peer_id
        
        if len(nick) > 50:
            return await msg.answer("❌ НИК СЛИШКОМ ДЛИННЫЙ (макс. 50 символов)")

        cur.execute("""
        INSERT INTO users (user_id, peer_id, nickname)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET nickname=%s
        """, (target, pid, nick, nick))

        return await msg.answer(
            f"✅ НИК УСТАНОВЛЕН\n\n"
            f"👤 @id{target}\n"
            f"🏷 Ник: {nick}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        print(f"ERROR in snick: {e}")
        traceback.print_exc()
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
        "/rnick\n"
        "или ответьте на сообщение: /rnick"
    )

@bot.on.message(text="/rnick")
async def rnick(msg: Message):
    conn, cur = db()
    try:
        # Проверяем права
        cur.execute("SELECT role FROM users WHERE user_id=%s AND peer_id=%s", (msg.from_id, msg.peer_id))
        sender_role = cur.fetchone()
        sender_role_val = sender_role[0] if sender_role else 0
        
        default_role = get_default_cmd_role('rnick')
        if not check_cmd_permission(cur, msg.peer_id, 'rnick', sender_role_val):
            return await msg.answer(f"❌ КОМАНДА ОТКЛЮЧЕНА или у вас нет прав\n\nТребуемый приоритет: {default_role}+")
        
        target = extract(msg) or msg.from_id
        pid = msg.peer_id

        cur.execute("SELECT nickname FROM users WHERE user_id=%s AND peer_id=%s", (target, pid))
        res = cur.fetchone()
        old_nick = res[0] if res else None

        cur.execute("UPDATE users SET nickname=NULL WHERE user_id=%s AND peer_id=%s", (target, pid))

        return await msg.answer(
            f"🧹 НИК УДАЛЁН\n\n"
            f"👤 @id{target}\n"
            f"❌ Был: {old_nick if old_nick else '(не установлен)'}\n\n"
            f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    
    except Exception as e:
        print(f"ERROR in rnick: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
# MAIN HANDLER
# =========================
@bot.on.message()
async def handler(msg: Message):
    conn, cur = db()

    try:
        if not msg.text:
            return

        uid, pid = msg.from_id, msg.peer_id
        text = msg.text.strip()

        # ===== AUTO KICK BAN =====
        cur.execute("""
        SELECT end_at FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='ban' 
        AND (end_at IS NULL OR end_at > NOW())
        """, (uid, pid))

        if cur.fetchone():
            try:
                await bot.api.messages.remove_chat_user(chat_id=pid-2000000000, user_id=uid)
            except:
                pass
            return

        # ===== AUTO DELETE MUTE =====
        cur.execute("""
        SELECT end_at FROM punishments
        WHERE user_id=%s AND peer_id=%s AND type='mute'
        AND (end_at IS NULL OR end_at > NOW())
        """, (uid, pid))

        if cur.fetchone():
            try:
                await bot.api.messages.delete(message_ids=[msg.id], delete_for_all=True)
            except:
                pass
            return

        # ===== UPDATE USER =====
        cur.execute("""
        INSERT INTO users (user_id, peer_id, msgs)
        VALUES (%s, %s, 1)
        ON CONFLICT (user_id, peer_id)
        DO UPDATE SET msgs = users.msgs + 1
        """, (uid, pid))

        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0][1:].lower()

        # ===== HELP =====
        if cmd == "help":
            return await msg.answer(
                "💠 FLEX BOT - КОМАНДЫ\n\n"
                "🏷 НИКИ:\n"
                "/snick [ник] - Установить ник\n"
                "/rnick - Удалить ник\n\n"
                "⚠️ МОДЕРАЦИЯ:\n"
                "/warn [id] [причина] - Предупреждение\n"
                "/mute [id] [время] [причина] - Мут\n"
                "/unmute [id] - Снять мут\n"
                "/ban [id] [время] [причина] - Бан\n"
                "/unban [id] - Разбанить\n"
                "/kick [id] - Исключить\n\n"
                "🎖️ РОЛИ:\n"
                "/giverole [id] [приоритет] - Выдать роль (роль 60+)\n\n"
                "⚙️ НАСТРОЙКИ:\n"
                "/setcmd - управление командами (роль 50+)\n"
                "/sysrole - системные команды (только владелец)\n\n"
                "📖 Введите команду без параметров для справки"
            )

    except Exception as e:
        print(f"ERROR in handler: {e}")
        traceback.print_exc()
    finally:
        conn.close()

# =========================
if __name__ == "__main__":
    print(">>> BOT START")
    bot.run_forever()
