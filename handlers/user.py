from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.markdown import hlink
from services.analytics import get_chat_stats
from services.memory import memory
from services.ai_client import ask_groq
from config import CREATOR_ID

router = Router()

# === Команда АКТИВНОСТЬ ===
@router.message(Command("актив"))
async def cmd_activity(message: types.Message, command: CommandObject):
    """Показать статистику активности"""
    args = command.args.split() if command.args else []
    chat_id = message.chat.id
    
    # Определяем период
    periods = {'сутки': 'day', 'неделя': 'week', 'месяц': 'month', 'весь': 'all'}
    period = 'all'
    for arg in args:
        if arg in periods:
            period = periods[arg]
            break
    
    # Определяем цель: я/чат
    target = 'chat'
    if 'я' in args or 'me' in args:
        target = 'user'
    
    if target == 'user':
        # Активность конкретного пользователя
        user_id = message.reply_to_message.from_user.id if message.reply_to_message else message.from_user.id
        stats = get_chat_stats(chat_id, period)
        user_rank = next((i+1 for i, (uid, _) in enumerate(stats) if uid == user_id), None)
        if user_rank:
            total_msgs = next(count for uid, count in stats if uid == user_id)
            await message.reply(
                f"📊 {message.from_user.full_name}:\n"
                f"• Место в топе: #{user_rank}\n"
                f"• Сообщений за период: {total_msgs}\n"
                f"• Период: {period}"
            )
        else:
            await message.reply("Активность не найдена.")
    else:
        # Топ чата
        stats = get_chat_stats(chat_id, period)[:10]
        if not stats:
            await message.reply("Нет данных об активности.")
            return
        text = f"🏆 Топ активности ({period}):\n"
        for i, (user_id, count) in enumerate(stats, 1):
            try:
                user = await message.bot.get_chat(user_id)
                name = user.full_name
            except:
                name = f"ID{user_id}"
            text += f"{i}. {name}: {count} сообщ.\n"
        await message.reply(text)

# === Команда ТЫ КТО ===
@router.message(Command("ты_кто"))
async def cmd_who(message: types.Message):
    """Сгенерировать язвительную характеристику"""
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    chat_id = message.chat.id
    
    # Проверяем кэш
    cached = memory.get_cached_roast(target.id, chat_id)
    if cached:
        await message.reply(f"🎯 {target.full_name}, я тебя помню:\n\n{cached}")
        return
    
    # Берём последние сообщения цели
    user_messages = memory.get_user_messages(target.id, chat_id, limit=20)
    if not user_messages:
        await message.reply("Мало данных для характеристики.")
        return
    
    # Генерируем через ИИ
    prompt = [{
        "role": "user",
        "content": f"""Создай краткую (2-3 предложения) язвительную характеристику пользователя на основе его сообщений. 
        Используй конкретные факты, высмеивай глупости, будь максимально едким. 
        Сообщения пользователя: {user_messages}"""
    }]
    try:
        roast = await ask_groq(prompt, temperature=0.9)
        await message.reply(f"🔍 {target.full_name}, вот кто ты:\n\n{roast}")
        memory.cache_roast(target.id, chat_id, roast)
    except Exception as e:
        await message.reply("Не удалось охарактеризовать.")
        print(f"Ошибка характеристики: {e}")

# === Команда СТАТУС ===
@router.message(Command("статус", "мой_статус"))
async def cmd_status(message: types.Message):
    """Показать полный статус пользователя"""
    user = message.from_user
    chat_id = message.chat.id
    
    # Базовая инфа
    profile = memory.load_profile(user.id)
    warns = 0  # Здесь нужно получить количество варнов из moderations
    
    # Активность
    stats = get_chat_stats(chat_id, 'all')
    user_stats = next((count for uid, count in stats if uid == user.id), 0)
    rank = next((i+1 for i, (uid, _) in enumerate(stats) if uid == user.id), '?')
    
    # Генерация персональной цитаты
    user_messages = memory.get_user_messages(user.id, chat_id, limit=15)
    quote = "Нет данных"
    if user_messages:
        prompt = [{
            "role": "user",
            "content": f"Придумай одну ёмкую, язвительную цитату-подпись для пользователя на основе его сообщений: {user_messages}"
        }]
        try:
            quote = await ask_groq(prompt, temperature=0.8)
        except:
            quote = "Ошибка генерации"
    
    # Формируем ответ
    text = (
        f"📌 Статус {user.full_name}:\n"
        f"• ID: {user.id}\n"
        f"• Цитата: «{quote}»\n"
        f"• Сообщений всего: {user_stats}\n"
        f"• Место в топе: #{rank}\n"
        f"• Предупреждений: {warns}/3\n"
    )
    if profile:
        text += f"• Язык: {profile.get('language', 'не указан')}\n"
        text += f"• Страна: {profile.get('country', 'не указана')}"
    
    await message.reply(text)

# === Команда для создателя: полная статистика ===
@router.message(Command("full_stats"))
async def cmd_full_stats(message: types.Message):
    """Полная статистика (только создатель)"""
    if message.from_user.id != CREATOR_ID:
        return
    
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(DISTINCT chat_id) FROM chat_history")
    total_chats = c.fetchone()[0]
    
    c.execute("SELECT SUM(messages) FROM activity")
    total_messages = c.fetchone()[0] or 0
    
    c.execute("SELECT COUNT(*) FROM moderations WHERE type='warn'")
    total_warns = c.fetchone()[0]
    
    text = (
        f"📈 ГЛОБАЛЬНАЯ СТАТИСТИКА:\n"
        f"• Пользователей: {total_users}\n"
        f"• Чатов: {total_chats}\n"
        f"• Сообщений всего: {total_messages}\n"
        f"• Выдано предупреждений: {total_warns}\n"
        f"• Прожарок закэшировано: {memory.conn.execute('SELECT COUNT(*) FROM roast_cache').fetchone()[0]}"
    )
    conn.close()
    await message.reply(text)

# === Команда помощи ===
@router.message(Command("help", "помощь"))
async def cmd_help(message: types.Message):
    """Справка по командам"""
    help_text = """
🤖 Доступные команды:

👤 Для всех:
• /актив [я/чат] [сутки/неделя/месяц/весь] — статистика активности
• /ты_кто [ответ на сообщение] — характеристика пользователя
• /статус — ваш полный статус
• /прожарь [ответ на сообщение] — прожарка пользователя

🛠 Для админов:
• /мут [время] [причина] — мут пользователя
• /варн [причина] — предупреждение
• /разбан — разбан
• /антимат вкл/выкл — авто-модерация

📢 Для создателя:
• /add_ad — добавить рекламу
• /ad_stats — статистика по рекламе
• /full_stats — полная статистика

Бот также отвечает на вопросы, вступает в конфликты и прожаривает чат каждые 1000 сообщений.
    """
    await message.reply(help_text)