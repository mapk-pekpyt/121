import json
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from services.ai_client import ask_groq
from services.memory import memory
from config import CREATOR_ID

router = Router()

# === СОСТОЯНИЯ ДЛЯ АНКЕТЫ ===
class ProfileStates(StatesGroup):
    language = State()
    country = State()
    interests = State()
    expertise = State()
    style = State()
    banned_topics = State()
    timezone = State()
    final = State()

# === СТАРТ АНКЕТЫ ===
@router.message(Command("start", "profile"))
async def cmd_start_personal(message: types.Message, state: FSMContext):
    """Начать настройку профиля в ЛС"""
    if message.chat.type != "private":
        return
    
    welcome = (
        "👤 Настройка персонального ассистента\n\n"
        "Я задам несколько вопросов для кастомизации. "
        "Отвечай кратко. Можно пропустить вопрос, отправив 'пропустить'.\n\n"
        "1. Основной язык общения (например: русский, английский):"
    )
    await message.answer(welcome)
    await state.set_state(ProfileStates.language)

# === ШАГИ АНКЕТЫ ===
@router.message(ProfileStates.language)
async def process_language(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(language=None)
    else:
        await state.update_data(language=message.text[:50])
    
    await message.answer("2. Страна проживания:")
    await state.set_state(ProfileStates.country)

@router.message(ProfileStates.country)
async def process_country(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(country=None)
    else:
        await state.update_data(country=message.text[:100])
    
    await message.answer("3. Основные интересы (через запятую, до 5):")
    await state.set_state(ProfileStates.interests)

@router.message(ProfileStates.interests)
async def process_interests(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(interests=None)
    else:
        interests = [i.strip() for i in message.text.split(',')[:5]]
        await state.update_data(interests=interests)
    
    await message.answer("4. Уровень знаний в этих интересах (новичок, продвинутый, эксперт):")
    await state.set_state(ProfileStates.expertise)

@router.message(ProfileStates.expertise)
async def process_expertise(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(expertise=None)
    else:
        await state.update_data(expertise=message.text[:30])
    
    await message.answer("5. Стиль ответов (саркастичный, нейтральный, поддерживающий):")
    await state.set_state(ProfileStates.style)

@router.message(ProfileStates.style)
async def process_style(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(style=None)
    else:
        await state.update_data(style=message.text[:30])
    
    await message.answer("6. Запретные темы (через запятую):")
    await state.set_state(ProfileStates.banned_topics)

@router.message(ProfileStates.banned_topics)
async def process_banned_topics(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(banned_topics=None)
    else:
        topics = [t.strip() for t in message.text.split(',')]
        await state.update_data(banned_topics=topics)
    
    await message.answer("7. Часовой пояс (например: Europe/Berlin или GMT+3):")
    await state.set_state(ProfileStates.timezone)

@router.message(ProfileStates.timezone)
async def process_timezone(message: types.Message, state: FSMContext):
    if message.text.lower() == 'пропустить':
        await state.update_data(timezone=None)
    else:
        await state.update_data(timezone=message.text[:50])
    
    # Финальный шаг
    data = await state.get_data()
    
    # Сохраняем профиль
    memory.save_profile(message.from_user.id, data)
    
    # Формируем сводку
    summary = "✅ Профиль сохранён!\n\nСводка:\n"
    for key, value in data.items():
        if value:
            summary += f"• {key}: {value}\n"
    
    summary += "\nТеперь я буду учитывать эти данные в ответах.\n"
    summary += "Изменить профиль: /profile\n"
    summary += "Режим ассистента: просто пиши мне сообщения."
    
    await message.answer(summary)
    await state.clear()

# === ОБРАБОТКА ЛЮБЫХ СООБЩЕНИЙ В ЛС (ассистент) ===
@router.message(F.chat.type == "private")
async def handle_personal_assistant(message: types.Message, state: FSMContext):
    """Обработка всех сообщений в ЛС как запросов к ассистенту"""
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    
    # Загружаем профиль
    profile = memory.load_profile(user_id)
    
    # Если профиля нет — предлагаем создать
    if not profile:
        await message.answer(
            "⚠️ Профиль не настроен. Для персонализации выполните /profile\n"
            "Но я всё равно отвечу в общем режиме."
        )
        profile = {}
    
    # Формируем системный промпт с учётом профиля
    system_prompt = {
        "role": "system",
        "content": f"""Ты персональный ассистент. Учитывай профиль пользователя:
        - Язык: {profile.get('language', 'русский')}
        - Интересы: {profile.get('interests', 'не указаны')}
        - Стиль ответов: {profile.get('style', 'нейтральный')}
        - Запретные темы: {profile.get('banned_topics', 'нет')}
        
        Отвечай на языке пользователя. Будь полезным, но добавляй лёгкую иронию если стиль 'саркастичный'.
        Избегай запретных тем. Отвечай кратко, по делу."""
    }
    
    # Загружаем историю диалога
    chat_context = memory.get_context(user_id, limit=15)  # user_id как chat_id для ЛС
    
    # Формируем сообщения для ИИ
    messages = [system_prompt] + chat_context + [{"role": "user", "content": text}]
    
    try:
        # Отправляем запрос
        response = await ask_groq(messages, temperature=0.7)
        
        # Сохраняем в историю
        memory.add_context(user_id, "user", text)
        memory.add_context(user_id, "assistant", response)
        
        # Отправляем ответ
        await message.answer(response[:4000])
        
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {str(e)}")
        print(f"Ошибка ассистента ЛС: {e}")

# === КОМАНДА ПРОСМОТРА ПРОФИЛЯ ===
@router.message(Command("my_profile", "мой_профиль"))
async def cmd_show_profile(message: types.Message):
    """Показать текущий профиль"""
    if message.chat.type != "private":
        await message.answer("Эта команда работает только в личных сообщениях.")
        return
    
    profile = memory.load_profile(message.from_user.id)
    if not profile:
        await message.answer("Профиль не настроен. Используйте /profile")
        return
    
    text = "📋 Ваш профиль:\n\n"
    for key, value in profile.items():
        if value:
            text += f"• {key}: {value}\n"
    
    text += "\nИзменить: /profile"
    await message.answer(text)

# === КОМАНДА ОЧИСТКИ ИСТОРИИ ===
@router.message(Command("clear_history", "очистить_историю"))
async def cmd_clear_history(message: types.Message):
    """Очистить историю диалога в ЛС"""
    if message.chat.type != "private":
        return
    
    memory.clear_context(message.from_user.id)
    await message.answer("🗑 История диалога очищена.")

# === СПЕЦИАЛЬНАЯ КОМАНДА ДЛЯ СОЗДАТЕЛЯ ===
@router.message(Command("inspect_profile"))
async def cmd_inspect_profile(message: types.Message, command: CommandObject):
    """Посмотреть чужой профиль (только создатель)"""
    if message.from_user.id != CREATOR_ID:
        return
    
    args = command.args
    if not args or not args.isdigit():
        await message.answer("Использование: /inspect_profile <user_id>")
        return
    
    user_id = int(args)
    profile = memory.load_profile(user_id)
    
    if not profile:
        await message.answer(f"Профиль для ID {user_id} не найден.")
        return
    
    text = f"🔍 Профиль пользователя {user_id}:\n\n"
    for key, value in profile.items():
        if value:
            text += f"{key}: {value}\n"
    
    await message.answer(text[:4000])