# main.py
import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh
import aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535  # Ваш Telegram ID
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен бота-менеджера (установить в переменных окружения)
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"  # Путь для bithost.ru

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица серверов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Таблица ботов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                token TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                server_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE
            )
        """)
        await db.commit()

# ========== FSM (СТАТУСЫ) ==========
class AddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AddBotStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_token = State()
    waiting_for_repo = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    """Главное меню"""
    buttons = [
        [types.KeyboardButton(text="📋 Список серверов")],
        [types.KeyboardButton(text="➕ Добавить сервер")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_menu():
    """Меню управления сервером"""
    buttons = [
        [types.KeyboardButton(text="🤖 Список ботов")],
        [types.KeyboardButton(text="➕ Добавить бота")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_menu():
    """Меню управления ботом"""
    buttons = [
        [types.KeyboardButton(text="🔄 Обновить из Git")],
        [types.KeyboardButton(text="❌ Удалить бота")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_button():
    """Кнопка назад"""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="◀️ Назад")]],
        resize_keyboard=True
    )

# ========== SSH ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def parse_connection_string(conn_str: str):
    """Разбирает строку подключения на компоненты"""
    try:
        if ':' in conn_str:
            # Формат user@host:port
            user_host, port = conn_str.rsplit(':', 1)
            user, host = user_host.split('@')
            port = int(port)
        else:
            # Формат user@host
            user, host = conn_str.split('@')
            port = 22
        return user, host, port
    except ValueError:
        raise ValueError("Неправильный формат. Используйте: user@host:port или user@host")

async def execute_ssh_command(server_id: int, command: str) -> tuple[str, str]:
    """Выполняет команду на сервере через SSH"""
    try:
        # Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
        
        if not server:
            return "", "Сервер не найден"
        
        # Разбираем строку подключения
        user, host, port = parse_connection_string(server['connection_string'])
        
        # Подключаемся по SSH
        async with asyncssh.connect(
            host,
            username=user,
            port=port,
            client_keys=[asyncssh.import_private_key(server['ssh_key'])],
            known_hosts=None
        ) as conn:
            result = await conn.run(command)
            return result.stdout, result.stderr
            
    except Exception as e:
        logger.error(f"SSH error: {e}")
        return "", f"Ошибка SSH: {str(e)}"

async def deploy_bot_on_server(server_id: int, bot_name: str, bot_token: str, repo_url: str):
    """Разворачивает бота на сервере"""
    commands = [
        f"cd /home && mkdir -p bots",
        f"cd /home/bots && rm -rf {bot_name}",
        f"cd /home/bots && git clone {repo_url} {bot_name}",
        f"cd /home/bots/{bot_name} && echo 'BOT_TOKEN={bot_token}' > .env",
        # Установка зависимостей (для Python)
        f"cd /home/bots/{bot_name} && [ -f requirements.txt ] && pip3 install -r requirements.txt || true",
        # Запуск через systemd (упрощенный вариант)
        f"cd /home/bots/{bot_name} && nohup python3 -u main.py > bot.log 2>&1 &"
    ]
    
    results = []
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd)
        if stderr and "already exists" not in stderr:
            results.append(f"Команда: {cmd}\nОшибка: {stderr}")
    
    return results

async def update_bot_from_git(server_id: int, bot_name: str):
    """Обновляет бота из Git репозитория"""
    commands = [
        f"cd /home/bots/{bot_name} && git pull",
        f"cd /home/bots/{bot_name} && [ -f requirements.txt ] && pip3 install -r requirements.txt || true",
        f"pkill -f 'python3.*{bot_name}' || true",
        f"cd /home/bots/{bot_name} && nohup python3 -u main.py > bot.log 2>&1 &"
    ]
    
    results = []
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd)
        if stderr:
            results.append(f"Команда: {cmd}\nОшибка: {stderr}")
    
    return results

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "👋 <b>Бот-менеджер серверов</b>\n\n"
        "Управляйте своими серверами и ботами через кнопки ниже:",
        reply_markup=main_menu()
    )

@dp.message(F.text == "◀️ Назад")
async def back_to_main(message: Message, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())

# ========== ОБРАБОТКА СЕРВЕРОВ ==========
@dp.message(F.text == "📋 Список серверов")
async def list_servers(message: Message):
    """Показать список серверов"""
    if message.from_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers ORDER BY name")
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("📭 Серверы не добавлены")
        return
    
    # Создаем инлайн-кнопки с серверами
    buttons = []
    for server in servers:
        buttons.append([types.InlineKeyboardButton(
            text=f"🖥️ {server['name']}",
            callback_data=f"server_{server['id']}"
        )])
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите сервер:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("server_"))
async def server_selected(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора сервера"""
    server_id = int(callback.data.split("_")[1])
    
    # Сохраняем server_id в состоянии
    await state.update_data(server_id=server_id)
    
    # Получаем имя сервера
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
        server_name = await cursor.fetchone()
    
    await callback.message.edit_text(
        f"🖥️ <b>Сервер:</b> {server_name[0]}\n\nВыберите действие:",
        reply_markup=server_menu()
    )
    await callback.answer()

@dp.message(F.text == "➕ Добавить сервер")
async def add_server_start(message: Message, state: FSMContext):
    """Начало добавления сервера"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AddServerStates.waiting_for_name)
    await message.answer(
        "Введите имя для сервера (например: VPS-1):",
        reply_markup=back_button()
    )

@dp.message(AddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    """Обработка имени сервера"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_menu())
        return
    
    await state.update_data(server_name=message.text)
    await state.set_state(AddServerStates.waiting_for_key)
    await message.answer(
        "Отправьте файл с SSH-ключом (формат: ssh-key-2025-12-21.key):"
    )

@dp.message(AddServerStates.waiting_for_key, F.document)
async def process_ssh_key(message: Message, state: FSMContext, bot: Bot):
    """Обработка SSH-ключа"""
    if not message.document:
        await message.answer("Пожалуйста, отправьте файл с ключом")
        return
    
    # Скачиваем файл
    file = await bot.get_file(message.document.file_id)
    file_path = f"temp_{message.document.file_name}"
    await bot.download_file(file.file_path, file_path)
    
    # Читаем содержимое ключа
    with open(file_path, 'r') as f:
        ssh_key = f.read()
    
    # Удаляем временный файл
    os.remove(file_path)
    
    await state.update_data(ssh_key=ssh_key)
    await state.set_state(AddServerStates.waiting_for_connection)
    await message.answer(
        "Введите строку подключения (формат: user@host:port или user@host):"
    )

@dp.message(AddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    """Обработка строки подключения и сохранение сервера"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_menu())
        return
    
    data = await state.get_data()
    
    # Парсим строку подключения
    try:
        user, host, port = parse_connection_string(message.text)
    except ValueError as e:
        await message.answer(f"❌ {str(e)}")
        return
    
    # Проверяем подключение
    await message.answer("🔍 Проверяем подключение к серверу...")
    
    try:
        async with asyncssh.connect(
            host,
            username=user,
            port=port,
            client_keys=[asyncssh.import_private_key(data['ssh_key'])],
            known_hosts=None,
            connect_timeout=10
        ) as conn:
            await conn.run("echo 'Connection test successful'")
    except Exception as e:
        await message.answer(f"❌ Ошибка подключения: {str(e)}\nПопробуйте снова.")
        return
    
    # Сохраняем сервер в БД
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string) VALUES (?, ?, ?)",
                (data['server_name'], data['ssh_key'], message.text)
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        await message.answer("❌ Сервер с таким именем уже существует")
        await state.clear()
        return
    
    await state.clear()
    await message.answer(
        f"✅ Сервер <b>{data['server_name']}</b> успешно добавлен!",
        reply_markup=main_menu()
    )

# ========== ОБРАБОТКА БОТОВ ==========
@dp.message(F.text == "🤖 Список ботов")
async def list_bots(message: Message, state: FSMContext):
    """Показать список ботов на текущем сервере"""
    data = await state.get_data()
    server_id = data.get('server_id')
    
    if not server_id:
        await message.answer("Сначала выберите сервер")
        return
    
    # Получаем ботов с этого сервера
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bots WHERE server_id = ? ORDER BY name",
            (server_id,)
        )
        bots = await cursor.fetchall()
    
    if not bots:
        await message.answer("🤖 На этом сервере нет ботов")
        return
    
    # Создаем инлайн-кнопки с ботами
    buttons = []
    for bot_data in bots:
        buttons.append([types.InlineKeyboardButton(
            text=f"🤖 {bot_data['name']}",
            callback_data=f"bot_{bot_data['id']}"
        )])
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите бота:", reply_markup=keyboard)

@dp.message(F.text == "➕ Добавить бота")
async def add_bot_start(message: Message, state: FSMContext):
    """Начало добавления бота"""
    data = await state.get_data()
    server_id = data.get('server_id')
    
    if not server_id:
        await message.answer("Сначала выберите сервер")
        return
    
    await state.set_state(AddBotStates.waiting_for_name)
    await state.update_data(bot_server_id=server_id)
    await message.answer(
        "Введите имя для нового бота (например: ShopBot):",
        reply_markup=back_button()
    )

@dp.message(AddBotStates.waiting_for_name)
async def process_bot_name(message: Message, state: FSMContext):
    """Обработка имени бота"""
    if message.text == "◀️ Назад":
        await state.set_state(None)
        await message.answer("Меню сервера:", reply_markup=server_menu())
        return
    
    await state.update_data(bot_name=message.text)
    await state.set_state(AddBotStates.waiting_for_token)
    await message.answer("Введите токен бота:")

@dp.message(AddBotStates.waiting_for_token)
async def process_bot_token(message: Message, state: FSMContext):
    """Обработка токена бота"""
    if message.text == "◀️ Назад":
        await state.set_state(AddBotStates.waiting_for_name)
        await message.answer("Введите имя для нового бота:")
        return
    
    await state.update_data(bot_token=message.text)
    await state.set_state(AddBotStates.waiting_for_repo)
    await message.answer("Введите URL Git-репозитория с кодом бота:")

@dp.message(AddBotStates.waiting_for_repo)
async def process_bot_repo(message: Message, state: FSMContext):
    """Обработка репозитория и развертывание бота"""
    if message.text == "◀️ Назад":
        await state.set_state(AddBotStates.waiting_for_token)
        await message.answer("Введите токен бота:")
        return
    
    data = await state.get_data()
    
    # Сохраняем бота в БД
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO bots (name, token, repo_url, server_id) 
               VALUES (?, ?, ?, ?)""",
            (data['bot_name'], data['bot_token'], message.text, data['bot_server_id'])
        )
        bot_id = cursor.lastrowid
        await db.commit()
    
    # Развертываем бота на сервере
    await message.answer("🚀 Начинаю развертывание бота на сервере...")
    
    errors = await deploy_bot_on_server(
        data['bot_server_id'],
        data['bot_name'],
        data['bot_token'],
        message.text
    )
    
    if errors:
        error_msg = "\n".join(errors[:3])  # Показываем первые 3 ошибки
        await message.answer(f"⚠️ Бот добавлен, но были ошибки:\n{error_msg}")
    else:
        await message.answer(f"✅ Бот <b>{data['bot_name']}</b> успешно развернут!")
    
    await state.clear()
    await state.update_data(server_id=data['bot_server_id'])
    await message.answer("Меню сервера:", reply_markup=server_menu())

@dp.callback_query(F.data.startswith("bot_"))
async def bot_selected(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора бота"""
    bot_id = int(callback.data.split("_")[1])
    
    # Получаем информацию о боте
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
        bot_data = await cursor.fetchone()
    
    if not bot_data:
        await callback.answer("Бот не найден")
        return
    
    # Сохраняем данные в состоянии
    await state.update_data(
        selected_bot_id=bot_id,
        selected_bot_name=bot_data['name'],
        server_id=bot_data['server_id']
    )
    
    await callback.message.edit_text(
        f"🤖 <b>Бот:</b> {bot_data['name']}\n\nВыберите действие:",
        reply_markup=bot_menu()
    )
    await callback.answer()

@dp.message(F.text == "🔄 Обновить из Git")
async def update_bot_git(message: Message, state: FSMContext):
    """Обновление бота из Git"""
    data = await state.get_data()
    bot_id = data.get('selected_bot_id')
    bot_name = data.get('selected_bot_name')
    server_id = data.get('server_id')
    
    if not all([bot_id, bot_name, server_id]):
        await message.answer("Сначала выберите бота")
        return
    
    await message.answer(f"🔄 Обновляю бота <b>{bot_name}</b> из Git...")
    
    errors = await update_bot_from_git(server_id, bot_name)
    
    if errors:
        error_msg = "\n".join(errors[:3])
        await message.answer(f"⚠️ Были ошибки при обновлении:\n{error_msg}")
    else:
        await message.answer(f"✅ Бот <b>{bot_name}</b> успешно обновлен!")
    
    await message.answer("Меню сервера:", reply_markup=server_menu())

@dp.message(F.text == "❌ Удалить бота")
async def delete_bot(message: Message, state: FSMContext):
    """Удаление бота"""
    data = await state.get_data()
    bot_id = data.get('selected_bot_id')
    bot_name = data.get('selected_bot_name')
    server_id = data.get('server_id')
    
    if not bot_id:
        await message.answer("Сначала выберите бота")
        return
    
    # Удаляем бота из БД
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        await db.commit()
    
    # Останавливаем бота на сервере
    stdout, stderr = await execute_ssh_command(
        server_id,
        f"pkill -f 'python3.*{bot_name}' || echo 'Bot not running'"
    )
    
    await message.answer(f"✅ Бот <b>{bot_name}</b> удален!")
    await state.clear()
    await state.update_data(server_id=server_id)
    await message.answer("Меню сервера:", reply_markup=server_menu())

# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция запуска"""
    await init_db()
    
    # Проверяем что мы админ
    me = await bot.get_me()
    logger.info(f"Бот запущен: @{me.username}")
    logger.info(f"Admin ID: {ADMIN_ID}")
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())