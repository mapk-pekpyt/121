import sqlite3
from datetime import datetime, timedelta
from aiogram import Router, types, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions
from core.security import check_admin_level
from services.moderator import Moderator
from config import DB_PATH

router = Router()

# === Глобальные настройки автомодерации ===
AUTO_MOD_SETTINGS = {}  # chat_id: {"antimat": bool, "antiflood": bool}

# === КОМАНДЫ АДМИНИСТРИРОВАНИЯ ===

@router.message(Command("мут"))
async def cmd_mute(message: types.Message, command: CommandObject):
    """Мут пользователя (требуется уровень 1+)"""
    if not await check_admin_level(message.from_user.id, 1):
        await message.reply("❌ Недостаточно прав (нужен уровень 1).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    args = command.args.split() if command.args else []
    duration = 5  # минут по умолчанию
    reason = ""
    
    if args:
        try:
            duration = int(args[0])
            reason = " ".join(args[1:]) if len(args) > 1 else ""
        except ValueError:
            reason = " ".join(args)
    
    moderator = Moderator(message.bot)
    await moderator.mute_user(message.chat.id, target.id, duration, reason)
    
    await message.reply(
        f"🔇 {target.full_name} получил мут на {duration} минут.\n"
        f"Причина: {reason if reason else 'не указана'}"
    )

@router.message(Command("варн"))
async def cmd_warn(message: types.Message, command: CommandObject):
    """Выдать предупреждение (требуется уровень 2+)"""
    if not await check_admin_level(message.from_user.id, 2):
        await message.reply("❌ Недостаточно прав (нужен уровень 2).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    reason = command.args or "не указана"
    
    moderator = Moderator(message.bot)
    warn_count = await moderator.warn_user(message.chat.id, target.id, reason)
    
    if warn_count >= 3:
        await message.reply(f"⚠️ {target.full_name} получил 3-е предупреждение и забанен.")
    else:
        await message.reply(
            f"⚠️ {target.full_name} получил предупреждение ({warn_count}/3).\n"
            f"Причина: {reason}"
        )

@router.message(Command("бан"))
async def cmd_ban(message: types.Message, command: CommandObject):
    """Бан пользователя (требуется уровень 3+)"""
    if not await check_admin_level(message.from_user.id, 3):
        await message.reply("❌ Недостаточно прав (нужен уровень 3).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    reason = command.args or "не указана"
    
    await message.bot.ban_chat_member(message.chat.id, target.id)
    
    # Логируем в БД
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO moderations (chat_id, user_id, type, reason)
                 VALUES (?, ?, 'ban', ?)""",
              (message.chat.id, target.id, reason))
    conn.commit()
    conn.close()
    
    await message.reply(f"⛔ {target.full_name} забанен.\nПричина: {reason}")

@router.message(Command("разбан"))
async cmd_unban(message: types.Message):
    """Разбан пользователя (требуется уровень 3+)"""
    if not await check_admin_level(message.from_user.id, 3):
        await message.reply("❌ Недостаточно прав (нужен уровень 3).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    try:
        await message.bot.unban_chat_member(message.chat.id, target.id)
        await message.reply(f"✅ {target.full_name} разбанен.")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@router.message(Command("снять_варн"))
async def cmd_unwarn(message: types.Message):
    """Снять предупреждение (требуется уровень 2+)"""
    if not await check_admin_level(message.from_user.id, 2):
        await message.reply("❌ Недостаточно прав (нужен уровень 2).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""DELETE FROM moderations 
                 WHERE chat_id=? AND user_id=? AND type='warn'
                 ORDER BY id DESC LIMIT 1""",
              (message.chat.id, target.id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    if deleted:
        await message.reply(f"✅ Снято одно предупреждение у {target.full_name}.")
    else:
        await message.reply("❌ Нет предупреждений для снятия.")

# === НАСТРОЙКИ АВТОМОДЕРАЦИИ ===

@router.message(Command("антимат"))
async def cmd_antimat(message: types.Message):
    """Включить/выключить антимат (требуется уровень 2+)"""
    if not await check_admin_level(message.from_user.id, 2):
        await message.reply("❌ Недостаточно прав (нужен уровень 2).")
        return
    
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ["вкл", "выкл"]:
        await message.reply("❌ Использование: /антимат вкл/выкл")
        return
    
    chat_id = message.chat.id
    if chat_id not in AUTO_MOD_SETTINGS:
        AUTO_MOD_SETTINGS[chat_id] = {"antimat": False, "antiflood": False}
    
    AUTO_MOD_SETTINGS[chat_id]["antimat"] = (args[1].lower() == "вкл")
    status = "включен" if AUTO_MOD_SETTINGS[chat_id]["antimat"] else "выключен"
    await message.reply(f"✅ Антимат {status}.")

@router.message(Command("антифлуд"))
async def cmd_antiflood(message: types.Message):
    """Включить/выключить антифлуд (требуется уровень 2+)"""
    if not await check_admin_level(message.from_user.id, 2):
        await message.reply("❌ Недостаточно прав (нужен уровень 2).")
        return
    
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ["вкл", "выкл"]:
        await message.reply("❌ Использование: /антифлуд вкл/выкл")
        return
    
    chat_id = message.chat.id
    if chat_id not in AUTO_MOD_SETTINGS:
        AUTO_MOD_SETTINGS[chat_id] = {"antimat": False, "antiflood": False}
    
    AUTO_MOD_SETTINGS[chat_id]["antiflood"] = (args[1].lower() == "вкл")
    status = "включен" if AUTO_MOD_SETTINGS[chat_id]["antiflood"] else "выключен"
    await message.reply(f"✅ Антифлуд {status}.")

@router.message(Command("посадить_в_угол"))
async def cmd_ignore_mode(message: types.Message, command: CommandObject):
    """Режим игнора (удаление сообщений N минут) (требуется уровень 1+)"""
    if not await check_admin_level(message.from_user.id, 1):
        await message.reply("❌ Недостаточно прав (нужен уровень 1).")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    args = command.args.split() if command.args else []
    duration = 10  # минут по умолчанию
    if args:
        try:
            duration = int(args[0])
        except ValueError:
            pass
    
    # Сохраняем в БД
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    expires = (datetime.now() + timedelta(minutes=duration)).isoformat()
    c.execute("""INSERT INTO moderations (chat_id, user_id, type, expires, reason)
                 VALUES (?, ?, 'ignore', ?, 'посажен в угол')""",
              (message.chat.id, target.id, expires))
    conn.commit()
    conn.close()
    
    await message.reply(
        f"🙊 {target.full_name} посажен в угол на {duration} минут.\n"
        f"Его сообщения будут автоматически удаляться."
    )

# === КОМАНДА НАЗНАЧЕНИЯ АДМИНОВ ===

@router.message(Command("назначить"))
async def cmd_promote(message: types.Message, command: CommandObject):
    """Назначить админа (только создатель чата)"""
    if message.from_user.id != message.chat.creator.id:
        await message.reply("❌ Только создатель чата может назначать админов.")
        return
    
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        await message.reply("❌ Ответьте на сообщение пользователя.")
        return
    
    args = command.args.split() if command.args else []
    if len(args) < 1 or args[0] not in ["1", "2", "3"]:
        await message.reply("❌ Использование: /назначить 1|2|3\n1 - стажер, 2 - новичок, 3 - почти босс")
        return
    
    level = int(args[0])
    level_names = {1: "стажер", 2: "новичок", 3: "почти босс"}
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO admins (user_id, chat_id, level)
                 VALUES (?, ?, ?)""",
              (target.id, message.chat.id, level))
    conn.commit()
    conn.close()
    
    await message.reply(
        f"👑 {target.full_name} назначен на уровень {level} ({level_names[level]}).\n"
        f"Права: {get_level_permissions(level)}"
    )

def get_level_permissions(level: int) -> str:
    """Описание прав уровня"""
    if level == 1:
        return "Мут"
    elif level == 2:
        return "Мут + Варн"
    elif level == 3:
        return "Мут + Варн + Бан"
    return "Нет прав"