import asyncio
import random
from aiogram import Router, types, Bot
from aiogram.filters import Command
from services.ai_client import ask_groq
from services.memory import memory
from services.analytics import detect_conflict, log_message
from services.moderator import Moderator
from config import ACTIVITY_THRESHOLD

router = Router()

# === Глобальные счетчики ===
message_counters = {}  # chat_id: count

# === Обработка всех сообщений ===
@router.message()
async def handle_all_messages(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    msg_type = "sticker" if message.sticker else "gif" if message.animation else "text"

    # Логируем
    log_message(chat_id, user_id, text[:500])

    # Автомодерация
    moderator = Moderator(message.bot)
    if await moderator.auto_moderate(chat_id, user_id, text, msg_type):
        return

    # Счётчик для прожарки
    message_counters[chat_id] = message_counters.get(chat_id, 0) + 1
    if message_counters[chat_id] >= ACTIVITY_THRESHOLD:
        await roast_chat(message.bot, chat_id)
        message_counters[chat_id] = 0

    # Детектор конфликта
    if detect_conflict(chat_id):
        await escalate_conflict(message)
        return

    # Ответ на вопросы (без упоминания)
    if "?" in text and len(text.split()) < 25:
        await answer_question(message)
        return

    # Упоминание бота
    bot_username = (await message.bot.me()).username
    if bot_username and f"@{bot_username}" in text:
        await reply_to_mention(message)
        return

    # Случайная провокация при тишине (5% шанс если сообщений < 3 за 10 мин)
    if random.random() < 0.05 and await is_chat_quiet(chat_id):
        await provoke_chat(message.bot, chat_id)

# === Функции ===
async def roast_chat(bot: Bot, chat_id: int):
    """Прожарка чата каждые 1000 сообщений"""
    messages = memory.get_chat_messages(chat_id, limit=100)
    if not messages:
        return
    prompt = [{
        "role": "user",
        "content": f"""Сгенерируй жёсткую, саркастичную прожарку чата на основе последних сообщений. 
        Используй чёрный юмор, подмечай глупости, передразнивай участников. 
        Максимально конкретно и язвительно. Сообщения для анализа: {messages}"""
    }]
    try:
        roast = await ask_groq(prompt, temperature=0.9)
        await bot.send_message(chat_id, f"🔥 ПРОЖАРКА ЧАТА (1000 сообщений):\n\n{roast}")
    except Exception as e:
        print(f"Ошибка прожарки: {e}")

async def escalate_conflict(message: types.Message):
    """Вмешательство в конфликт с агрессией"""
    chat_id = message.chat.id
    context = memory.get_context(chat_id, limit=15)
    prompt = [{
        "role": "user",
        "content": f"""Ты — язвительный участник конфликта. Твоя цель — максимально жёстко унизить всех спорщиков, 
        используя их же аргументы против них. Будь саркастичным, используй факты из истории. 
        Ответь коротко (3-4 предложения), но метко. Контекст: {context}"""
    }]
    try:
        reply = await ask_groq(prompt, temperature=0.95)
        await message.reply(reply[:500])
    except Exception as e:
        print(f"Ошибка эскалации: {e}")

async def answer_question(message: types.Message):
    """Ответ на вопрос в чате"""
    prompt = [{
        "role": "user",
        "content": f"""Дай максимально точный и краткий ответ на вопрос. 
        Добавь лёгкую язвительность, если вопрос простой. 
        Вопрос: {message.text}"""
    }]
    try:
        answer = await ask_groq(prompt, temperature=0.7)
        await message.reply(answer[:300])
    except Exception:
        pass

async def reply_to_mention(message: types.Message):
    """Ответ при упоминании бота"""
    chat_id = message.chat.id
    context = memory.get_context(chat_id, limit=10)
    prompt = [{
        "role": "user",
        "content": f"""Ответь на сообщение в том же стиле, но с сарказмом. 
        Если это вопрос — дай жёсткий, но точный ответ. 
        Контекст: {context}\nСообщение: {message.text}"""
    }]
    try:
        reply = await ask_groq(prompt, temperature=0.85)
        await message.reply(reply[:400])
    except Exception as e:
        print(f"Ошибка ответа: {e}")

async def is_chat_quiet(chat_id: int) -> bool:
    """Проверка, тихий ли чат (менее 3 сообщений за 10 минут)"""
    # Упрощённая проверка — в реальности нужен запрос к БД
    return random.choice([True, False])  # Заглушка

async def provoke_chat(bot: Bot, chat_id: int):
    """Провокация при тишине"""
    users = memory.get_chat_messages(chat_id, limit=5)
    if not users:
        return
    target = users[-1]["user_id"]  # Последний писавший
    prompt = [{
        "role": "user",
        "content": f"""Придумай провокационное сообщение, чтобы разжечь дискуссию в чате. 
        Нацелься на пользователя {target}. Будь язвительным, но умным."""
    }]
    try:
        provocation = await ask_groq(prompt, temperature=0.9)
        await bot.send_message(chat_id, provocation[:350])
    except Exception as e:
        print(f"Ошибка провокации: {e}")

# === Команда персональной прожарки ===
@router.message(Command("прожарь"))
async def personal_roast(message: types.Message):
    """Прожарка конкретного пользователя"""
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    chat_id = message.chat.id
    user_messages = memory.get_user_messages(target.id, chat_id, limit=30)
    if not user_messages:
        await message.reply("Недостаточно данных для прожарки.")
        return
    prompt = [{
        "role": "user",
        "content": f"""Унизи пользователя {target.full_name} на основе его сообщений. 
        Будь максимально жёстким, используй конкретные цитаты, высмеивай противоречия. 
        Сообщения пользователя: {user_messages}"""
    }]
    try:
        roast = await ask_groq(prompt, temperature=0.95)
        await message.reply(f"🔥 Прожарка для {target.mention}:\n\n{roast}")
        # Кэшируем
        memory.cache_roast(target.id, chat_id, roast)
    except Exception as e:
        await message.reply("Не удалось прожарить, попробуй позже.")
        print(f"Ошибка персональной прожарки: {e}")