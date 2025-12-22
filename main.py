# main.py - РАСШИРЕННАЯ ВЕРСИЯ
import os
import asyncio
import logging
import json
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
ADMIN_ID = 5791171535
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

# Типы ботов и их настройки установки
BOT_TYPES = {
    "python": {
        "name": "Python бот",
        "setup_commands": [
            "cd {path} && [ -f requirements.txt ] && pip3 install -r requirements.txt || true"
        ],
        "start_command": "cd {path} && nohup python3 -u main.py > bot.log 2>&1 &",
        "stop_command": "pkill -f 'python3.*{bot_name}' || true"
    },
    "docker": {
        "name": "Docker контейнер",
        "setup_commands": [
            "cd {path} && [ -f Dockerfile ] && docker build -t {bot_name} . || true",
            "cd {path} && [ -f docker-compose.yml ] && docker-compose up -d || true"
        ],
        "start_command": "cd {path} && [ -f docker-compose.yml ] && docker-compose up -d || docker run -d --name {bot_name} {bot_name}",
        "stop_command": "cd {path} && [ -f docker-compose.yml ] && docker-compose down || docker stop {bot_name} || true"
    },
    "vpn_wireguard": {
        "name": "WireGuard VPN",
        "setup_commands": [
            "apt-get update && apt-get install -y wireguard qrencode",
            "sysctl -w net.ipv4.ip_forward=1",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
            "mkdir -p /etc/wireguard",
            "cd {path} && [ -f setup.sh ] && chmod +x setup.sh && ./setup.sh || true"
        ],
        "start_command": "cd {path} && wg-quick up wg0 2>/dev/null || true",
        "stop_command": "wg-quick down wg0 2>/dev/null || true"
    },
    "nodejs": {
        "name": "Node.js бот",
        "setup_commands": [
            "cd {path} && [ -f package.json ] && npm install || true"
        ],
        "start_command": "cd {path} && nohup npm start > bot.log 2>&1 &",
        "stop_command": "pkill -f 'node.*{bot_name}' || true"
    },
    "custom": {
        "name": "Кастомная установка",
        "setup_commands": [],
        "start_command": "cd {path} && [ -f start.sh ] && chmod +x start.sh && ./start.sh || echo 'Нет start.sh'",
        "stop_command": "cd {path} && [ -f stop.sh ] && chmod +x stop.sh && ./stop.sh || true"
    }
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                token TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                server_id INTEGER NOT NULL,
                bot_type TEXT DEFAULT 'python',
                env_vars TEXT DEFAULT '{}',
                setup_commands TEXT DEFAULT '[]',
                start_command TEXT DEFAULT '',
                stop_command TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE
            )
        """)
        await db.commit()

# ========== FSM ==========
class AddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AddBotStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_token = State()
    waiting_for_repo = State()
    waiting_for_env = State()
    waiting_for_custom_setup = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    buttons = [
        [types.KeyboardButton(text="📋 Список серверов")],
        [types.KeyboardButton(text="➕ Добавить сервер")],
        [types.KeyboardButton(text="⚙️ Настройки")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_menu():
    buttons = [
        [types.KeyboardButton(text="🤖 Список ботов")],
        [types.KeyboardButton(text="➕ Добавить бота")],
        [types.KeyboardButton(text="📊 Статус сервера")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_menu():
    buttons = [
        [types.KeyboardButton(text="▶️ Запустить")],
        [types.KeyboardButton(text="⏹️ Остановить")],
        [types.KeyboardButton(text="🔄 Обновить")],
        [types.KeyboardButton(text="📝 Логи")],
        [types.KeyboardButton(text="⚙️ Настройки")],
        [types.KeyboardButton(text="❌ Удалить")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_type_keyboard():
    buttons = []
    row = []
    for key, value in BOT_TYPES.items():
        row.append(types.KeyboardButton(text=value["name"]))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== SSH ФУНКЦИИ ==========
def parse_connection_string(conn_str: str):
    """Разбирает строку подключения на компоненты"""
    try:
        if ':' in conn_str:
            user_host, port = conn_str.rsplit(':', 1)
            user, host = user_host.split('@')
            port = int(port)
        else:
            user, host = conn_str.split('@')
            port = 22
        return user, host, port
    except ValueError:
        raise ValueError("Неправильный формат. Используйте: user@host:port или user@host")

async def execute_ssh_command(server_id: int, command: str, sudo=False) -> tuple[str, str]:
    """Выполняет команду на сервере через SSH"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
        
        if not server:
            return "", "Сервер не найден"
        
        user, host, port = parse_connection_string(server['connection_string'])
        
        if sudo and user != 'root':
            command = f"sudo {command}"
        
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

async def deploy_bot_on_server(bot_data: dict):
    """Разворачивает бота на сервере"""
    server_id = bot_data['server_id']
    bot_name = bot_data['name']
    bot_type = bot_data.get('bot_type', 'python')
    repo_url = bot_data['repo_url']
    token = bot_data['token']
    env_vars = json.loads(bot_data.get('env_vars', '{}'))
    
    path = f"/home/bots/{bot_name}"
    
    # Базовые команды
    commands = [
        f"mkdir -p /home/bots",
        f"cd /home/bots && rm -rf {bot_name}",
        f"cd /home/bots && git clone {repo_url} {bot_name}",
        f"chmod -R 755 {path}"
    ]
    
    # Добавляем токен в env
    env_content = f"BOT_TOKEN={token}\n"
    for key, value in env_vars.items():
        env_content += f"{key}={value}\n"
    
    commands.append(f"cd {path} && echo '{env_content}' > .env")
    
    # Команды для типа бота
    bot_config = BOT_TYPES.get(bot_type, BOT_TYPES['python'])
    
    # Кастомные setup команды
    custom_setup = json.loads(bot_data.get('setup_commands', '[]'))
    if custom_setup:
        for cmd in custom_setup:
            commands.append(cmd.format(path=path, bot_name=bot_name))
    else:
        for cmd_template in bot_config['setup_commands']:
            commands.append(cmd_template.format(path=path, bot_name=bot_name))
    
    # Выполняем команды
    results = []
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd, sudo=True)
        if stderr and "already exists" not in stderr.lower() and "warning" not in stderr.lower():
            results.append(f"❌ {cmd[:50]}...: {stderr[:200]}")
    
    # Запускаем бота
    start_cmd = bot_data.get('start_command') or bot_config['start_command']
    if start_cmd:
        stdout, stderr = await execute_ssh_command(
            server_id, 
            start_cmd.format(path=path, bot_name=bot_name),
            sudo=True
        )
        if stderr:
            results.append(f"❌ Запуск: {stderr[:200]}")
    
    return results

# ========== ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "👋 <b>Бот-менеджер серверов PRO</b>\n\n"
        "Поддержка: Python, Docker, VPN (WireGuard), Node.js, кастомные установки",
        reply_markup=main_menu()
    )

@dp.message(F.text == "📋 Список серверов")
async def list_servers(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers ORDER BY name")
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("📭 Серверы не добавлены")
        return
    
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
    server_id = int(callback.data.split("_")[1])
    await state.update_data(server_id=server_id)
    
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
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AddServerStates.waiting_for_name)
    await message.answer(
        "Введите имя для сервера (например: Oracle-VPS):",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="◀️ Назад")]],
            resize_keyboard=True
        )
    )

# [Код добавления сервера остается таким же как в предыдущей версии]
# ... (пропускаю для краткости, он не изменился)

@dp.message(F.text == "➕ Добавить бота")
async def add_bot_start(message: Message, state: FSMContext):
    data = await state.get_data()
    server_id = data.get('server_id')
    
    if not server_id:
        await message.answer("Сначала выберите сервер")
        return
    
    await state.set_state(AddBotStates.waiting_for_name)
    await state.update_data(bot_server_id=server_id)
    await message.answer(
        "Введите имя для нового бота (латиница, без пробелов):",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="◀️ Назад")]],
            resize_keyboard=True
        )
    )

@dp.message(AddBotStates.waiting_for_name)
async def process_bot_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(None)
        await message.answer("Меню сервера:", reply_markup=server_menu())
        return
    
    await state.update_data(bot_name=message.text)
    await state.set_state(AddBotStates.waiting_for_type)
    await message.answer("Выберите тип бота:", reply_markup=bot_type_keyboard())

@dp.message(AddBotStates.waiting_for_type)
async def process_bot_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddBotStates.waiting_for_name)
        await message.answer("Введите имя для нового бота:")
        return
    
    # Находим ключ типа по имени
    bot_type_key = None
    for key, value in BOT_TYPES.items():
        if value["name"] == message.text:
            bot_type_key = key
            break
    
    if not bot_type_key:
        await message.answer("Выберите тип из списка:")
        return
    
    await state.update_data(bot_type=bot_type_key)
    
    if bot_type_key == "custom":
        await state.set_state(AddBotStates.waiting_for_custom_setup)
        await message.answer(
            "Введите кастомные команды setup (каждая с новой строки):\n"
            "Пример:\n"
            "apt-get install -y docker.io\n"
            "systemctl start docker\n"
            "docker pull nginx"
        )
    else:
        await state.set_state(AddBotStates.waiting_for_token)
        await message.answer("Введите токен бота (если нет - введите 'no'):")

@dp.message(AddBotStates.waiting_for_custom_setup)
async def process_custom_setup(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddBotStates.waiting_for_type)
        await message.answer("Выберите тип бота:", reply_markup=bot_type_keyboard())
        return
    
    setup_commands = [cmd.strip() for cmd in message.text.split('\n') if cmd.strip()]
    await state.update_data(setup_commands=json.dumps(setup_commands))
    await state.set_state(AddBotStates.waiting_for_token)
    await message.answer("Введите токен бота (если нет - введите 'no'):")

@dp.message(AddBotStates.waiting_for_token)
async def process_bot_token(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        data = await state.get_data()
        if data.get('bot_type') == 'custom':
            await state.set_state(AddBotStates.waiting_for_custom_setup)
            await message.answer("Введите кастомные команды setup:")
        else:
            await state.set_state(AddBotStates.waiting_for_type)
            await message.answer("Выберите тип бота:", reply_markup=bot_type_keyboard())
        return
    
    token = message.text if message.text.lower() != 'no' else ''
    await state.update_data(bot_token=token)
    await state.set_state(AddBotStates.waiting_for_repo)
    await message.answer("Введите URL Git-репозитория с кодом бота:")

@dp.message(AddBotStates.waiting_for_repo)
async def process_bot_repo(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddBotStates.waiting_for_token)
        await message.answer("Введите токен бота (если нет - введите 'no'):")
        return
    
    data = await state.get_data()
    
    # Сохраняем бота в БД
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO bots (name, token, repo_url, server_id, bot_type, setup_commands) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data['bot_name'], 
                data['bot_token'], 
                message.text, 
                data['bot_server_id'],
                data.get('bot_type', 'python'),
                data.get('setup_commands', '[]')
            )
        )
        bot_id = cursor.lastrowid
        
        # Получаем данные бота для деплоя
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
        bot_data = await cursor.fetchone()
        await db.commit()
    
    await message.answer("🚀 Начинаю развертывание бота на сервере...")
    
    # Конвертируем Row в dict
    bot_dict = dict(bot_data)
    
    errors = await deploy_bot_on_server(bot_dict)
    
    if errors:
        error_msg = "\n".join(errors[:3])
        await message.answer(f"⚠️ Бот добавлен, но были ошибки:\n{error_msg}")
    else:
        bot_type_name = BOT_TYPES.get(data.get('bot_type', 'python'), {}).get('name', 'Python бот')
        await message.answer(f"✅ Бот <b>{data['bot_name']}</b> ({bot_type_name}) успешно развернут!")
    
    await state.clear()
    await state.update_data(server_id=data['bot_server_id'])
    await message.answer("Меню сервера:", reply_markup=server_menu())

@dp.message(F.text == "🤖 Список ботов")
async def list_bots(message: Message, state: FSMContext):
    data = await state.get_data()
    server_id = data.get('server_id')
    
    if not server_id:
        await message.answer("Сначала выберите сервер")
        return
    
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
    
    text = "📋 <b>Список ботов на сервере:</b>\n\n"
    for bot in bots:
        bot_type = BOT_TYPES.get(bot['bot_type'], {}).get('name', 'Python')
        text += f"• <b>{bot['name']}</b> ({bot_type})\n"
        text += f"  Репозиторий: {bot['repo_url'][:30]}...\n"
        text += f"  ID: {bot['id']}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📊 Статус сервера")
async def server_status(message: Message, state: FSMContext):
    data = await state.get_data()
    server_id = data.get('server_id')
    
    if not server_id:
        await message.answer("Сначала выберите сервер")
        return
    
    await message.answer("🔄 Проверяю статус сервера...")
    
    commands = [
        "uptime",
        "free -h | head -2",
        "df -h / | tail -1",
        "docker ps --format 'table {{.Names}}\\t{{.Status}}' | head -5"
    ]
    
    results = []
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd)
        if stdout:
            results.append(f"<b>{cmd}:</b>\n{stdout}")
    
    if results:
        await message.answer("\n".join(results), parse_mode=ParseMode.HTML)
    else:
        await message.answer("Не удалось получить статус сервера")

# ========== УПРАВЛЕНИЕ БОТАМИ ==========
async def manage_bot_action(callback: types.CallbackQuery, state: FSMContext, action: str):
    """Управление ботом (запуск/остановка/обновление)"""
    data = await state.get_data()
    bot_id = data.get('selected_bot_id')
    bot_name = data.get('selected_bot_name')
    server_id = data.get('server_id')
    
    if not bot_id:
        await callback.answer("Сначала выберите бота")
        return
    
    # Получаем данные бота
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
        bot_data = await cursor.fetchone()
    
    if not bot_data:
        await callback.answer("Бот не найден")
        return
    
    bot_config = BOT_TYPES.get(bot_data['bot_type'], BOT_TYPES['python'])
    path = f"/home/bots/{bot_name}"
    
    if action == "start":
        start_cmd = bot_data.get('start_command') or bot_config['start_command']
        if start_cmd:
            stdout, stderr = await execute_ssh_command(
                server_id, 
                start_cmd.format(path=path, bot_name=bot_name),
                sudo=True
            )
            msg = f"✅ Бот {bot_name} запущен" if not stderr else f"❌ Ошибка: {stderr[:200]}"
            await callback.message.answer(msg)
    
    elif action == "stop":
        stop_cmd = bot_data.get('stop_command') or bot_config['stop_command']
        if stop_cmd:
            stdout, stderr = await execute_ssh_command(
                server_id,
                stop_cmd.format(path=path, bot_name=bot_name),
                sudo=True
            )
            msg = f"⏹️ Бот {bot_name} остановлен" if not stderr else f"❌ Ошибка: {stderr[:200]}"
            await callback.message.answer(msg)
    
    elif action == "update":
        await callback.message.answer(f"🔄 Обновляю бота {bot_name}...")
        
        # Git pull
        stdout, stderr = await execute_ssh_command(
            server_id,
            f"cd {path} && git pull",
            sudo=True
        )
        
        if stderr:
            await callback.message.answer(f"❌ Ошибка git pull: {stderr[:200]}")
            return
        
        # Перезапуск
        stop_cmd = bot_data.get('stop_command') or bot_config['stop_command']
        start_cmd = bot_data.get('start_command') or bot_config['start_command']
        
        if stop_cmd:
            await execute_ssh_command(
                server_id,
                stop_cmd.format(path=path, bot_name=bot_name),
                sudo=True
            )
        
        if start_cmd:
            stdout, stderr = await execute_ssh_command(
                server_id,
                start_cmd.format(path=path, bot_name=bot_name),
                sudo=True
            )
            
            if stderr:
                await callback.message.answer(f"❌ Ошибка запуска: {stderr[:200]}")
            else:
                await callback.message.answer(f"✅ Бот {bot_name} обновлен и перезапущен")
    
    await callback.answer()

# Привязка обработчиков к кнопкам
@dp.message(F.text == "▶️ Запустить")
async def start_bot(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get('selected_bot_id'):
        await message.answer("Сначала выберите бота из списка")
        return
    
    # Создаем фейковый callback для использования функции
    class FakeCallback:
        def __init__(self, msg):
            self.message = msg
            self.data = "fake"
        
        async def answer(self, text=""):
            pass
    
    fake_callback = FakeCallback(message)
    await manage_bot_action(fake_callback, state, "start")

@dp.message(F.text == "⏹️ Остановить")
async def stop_bot(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get('selected_bot_id'):
        await message.answer("Сначала выберите бота из списка")
        return
    
    class FakeCallback:
        def __init__(self, msg):
            self.message = msg
            self.data = "fake"
        
        async def answer(self, text=""):
            pass
    
    fake_callback = FakeCallback(message)
    await manage_bot_action(fake_callback, state, "stop")

@dp.message(F.text == "🔄 Обновить")
async def update_bot(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get('selected_bot_id'):
        await message.answer("Сначала выберите бота из списка")
        return
    
    class FakeCallback:
        def __init__(self, msg):
            self.message = msg
            self.data = "fake"
        
        async def answer(self, text=""):
            pass
    
    fake_callback = FakeCallback(message)
    await manage_bot_action(fake_callback, state, "update")

@dp.message(F.text == "📝 Логи")
async def show_logs(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_name = data.get('selected_bot_name')
    server_id = data.get('server_id')
    
    if not bot_name:
        await message.answer("Сначала выберите бота")
        return
    
    await message.answer("📥 Загружаю логи...")
    
    # Пробуем разные пути к логам
    log_paths = [
        f"/home/bots/{bot_name}/bot.log",
        f"/var/log/{bot_name}.log",
        f"/home/bots/{bot_name}/logs/app.log"
    ]
    
    logs_found = False
    for log_path in log_paths:
        stdout, stderr = await execute_ssh_command(
            server_id,
            f"tail -50 {log_path} 2>/dev/null || echo 'Лог не найден: {log_path}'"
        )
        
        if stdout and "Лог не найден" not in stdout:
            logs_found = True
            log_text = stdout[:3000]  # Ограничиваем размер
            await message.answer(f"<b>Логи {bot_name}:</b>\n<code>{log_text}</code>", 
                               parse_mode=ParseMode.HTML)
            break
    
    if not logs_found:
        await message.answer(f"📭 Логи для бота {bot_name} не найдены")

# ========== ЗАПУСК ==========
async def main():
    await init_db()
    me = await bot.get_me()
    logger.info(f"Бот запущен: @{me.username}")
    logger.info(f"Admin ID: {ADMIN_ID}")
    
    # Проверяем доступность SSH
    try:
        import asyncssh
        logger.info("✅ AsyncSSH доступен")
    except ImportError as e:
        logger.error(f"❌ AsyncSSH не установлен: {e}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())