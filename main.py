# main.py - ЧИСТЫЙ И РАБОЧИЙ КОД
import os
import asyncio
import logging
import json
import random
import qrcode
import io
import sys
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh
import aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"

# Создаем директорию для данных
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    """Инициализация базы данных"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Серверы
            await db.execute("""
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ssh_key TEXT NOT NULL,
                    connection_string TEXT NOT NULL,
                    server_type TEXT DEFAULT 'vpn',
                    server_ip TEXT,
                    public_key TEXT,
                    wireguard_configured BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # VPN пользователи
            await db.execute("""
                CREATE TABLE IF NOT EXISTS vpn_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    server_id INTEGER,
                    config_data TEXT,
                    subscription_end TIMESTAMP,
                    trial_used BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    gifted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Платежи
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_stars INTEGER NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    invoice_payload TEXT,
                    service_type TEXT DEFAULT 'vpn',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Telegram боты
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_bots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bot_name TEXT NOT NULL,
                    bot_token TEXT UNIQUE,
                    server_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    container_id TEXT,
                    subscription_end TIMESTAMP,
                    gifted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Настройки цен
            await db.execute("""
                CREATE TABLE IF NOT EXISTS price_settings (
                    service_type TEXT PRIMARY KEY,
                    week_price INTEGER DEFAULT 50,
                    month_price INTEGER DEFAULT 150
                )
            """)
            
            # Инициализируем цены
            await db.execute("INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES ('vpn', 50, 150)")
            await db.execute("INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES ('bot', 100, 300)")
            
            await db.commit()
            logger.info("✅ База данных инициализирована")
            
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price FROM price_settings WHERE service_type = 'vpn'")
            prices = await cursor.fetchone()
            if prices:
                return {
                    "trial": {"days": 3, "stars": 0},
                    "week": {"days": 7, "stars": prices[0]},
                    "month": {"days": 30, "stars": prices[1]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен VPN: {e}")
    
    return {
        "trial": {"days": 3, "stars": 0},
        "week": {"days": 7, "stars": 50},
        "month": {"days": 30, "stars": 150}
    }

async def get_bot_prices() -> Dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price FROM price_settings WHERE service_type = 'bot'")
            prices = await cursor.fetchone()
            if prices:
                return {
                    "week": {"days": 7, "stars": prices[0]},
                    "month": {"days": 30, "stars": prices[1]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен ботов: {e}")
    
    return {
        "week": {"days": 7, "stars": 100},
        "month": {"days": 30, "stars": 300}
    }

async def get_available_vpn_server() -> Optional[int]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM servers 
                WHERE server_type = 'vpn' 
                AND wireguard_configured = TRUE
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска VPN сервера: {e}")
        return None

async def get_available_bot_server() -> Optional[int]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM servers 
                WHERE server_type = 'bot'
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска сервера для ботов: {e}")
        return None

async def execute_ssh_command(server_id: int, command: str, timeout: int = 30) -> Tuple[str, str]:
    """Выполняет команду на сервере через SSH"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT connection_string, ssh_key FROM servers WHERE id = ?", 
                (server_id,)
            )
            server = await cursor.fetchone()
            
            if not server:
                return "", "Сервер не найден"
            
            conn_str, ssh_key = server
            
            # Парсим строку подключения
            if ':' in conn_str:
                user_host, port = conn_str.rsplit(':', 1)
                user, host = user_host.split('@')
                port = int(port)
            else:
                user, host = conn_str.split('@')
                port = 22
            
            # Подготавливаем SSH ключ
            ssh_key_clean = ssh_key.strip()
            if not ssh_key_clean.startswith('-----BEGIN'):
                ssh_key_clean = f"-----BEGIN PRIVATE KEY-----\n{ssh_key_clean}\n-----END PRIVATE KEY-----"
            
            # Сохраняем ключ во временный файл
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean)
                temp_key_path = f.name
            
            # Устанавливаем правильные права доступа
            import stat
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            # Подключаемся по SSH
            try:
                async with asyncssh.connect(
                    host,
                    username=user,
                    port=port,
                    client_keys=[temp_key_path],
                    known_hosts=None,
                    connect_timeout=timeout
                ) as conn:
                    result = await conn.run(command, timeout=timeout)
                    
                    os.unlink(temp_key_path)
                    return result.stdout, result.stderr
                    
            except Exception as e:
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", f"SSH ошибка: {str(e)}"
                
    except Exception as e:
        logger.error(f"Ошибка SSH: {e}")
        return "", f"Ошибка: {str(e)}"

async def setup_wireguard_server(server_id: int) -> bool:
    """Настраивает WireGuard на сервере - ПРОБУЕМ ВСЕ ВАРИАНТЫ"""
    logger.info(f"Настраиваем WireGuard на сервере {server_id}")
    
    try:
        # Проверяем подключение
        stdout, stderr = await execute_ssh_command(server_id, "echo 'Connection test'")
        if stderr:
            return False
        
        # Вариант 1: Проверяем установлен ли WireGuard
        stdout, stderr = await execute_ssh_command(server_id, "which wg 2>/dev/null || echo 'not found'")
        
        if "not found" in stdout:
            # Пробуем установить
            await execute_ssh_command(server_id, "apt-get update -y && apt-get install -y wireguard-tools")
        
        # Вариант 2: Создаем директорию и генерируем ключи
        await execute_ssh_command(server_id, "mkdir -p /etc/wireguard")
        
        # Пробуем разные команды для генерации ключей
        keygen_commands = [
            "cd /etc/wireguard && umask 077 && wg genkey | tee private.key | wg pubkey > public.key",
            "cd /etc/wireguard && wg genkey > private.key 2>/dev/null && wg pubkey < private.key > public.key 2>/dev/null",
        ]
        
        public_key = None
        for cmd in keygen_commands:
            stdout, stderr = await execute_ssh_command(server_id, cmd)
            if not stderr:
                # Пробуем получить ключ
                stdout, _ = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key 2>/dev/null || echo 'no key'")
                if "no key" not in stdout and stdout.strip():
                    public_key = stdout.strip()
                    break
        
        if not public_key:
            logger.error("Не удалось сгенерировать ключи WireGuard")
            return False
        
        # Сохраняем публичный ключ в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET public_key = ?, wireguard_configured = TRUE WHERE id = ?",
                (public_key, server_id)
            )
            await db.commit()
        
        logger.info(f"✅ WireGuard настроен, ключ: {public_key[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка настройки WireGuard: {e}")
        return False

async def create_wireguard_client(server_id: int, user_id: int) -> Optional[Dict]:
    """Создает клиента WireGuard"""
    try:
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        # 1. Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT public_key, server_ip FROM servers WHERE id = ?", 
                (server_id,)
            )
            server_data = await cursor.fetchone()
            server_pub_key = server_data[0] if server_data else ""
            server_ip = server_data[1] if server_data else ""
        
        if not server_pub_key:
            return None
        
        # 2. Генерируем ключи клиента на сервере
        keygen_cmd = f"""
        cd /etc/wireguard
        umask 077
        wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public
        cat {client_name}.private
        """
        
        stdout, stderr = await execute_ssh_command(server_id, keygen_cmd)
        if not stdout.strip():
            return None
            
        private_key = stdout.strip()
        
        # 3. Получаем публичный ключ клиента
        stdout, stderr = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.public")
        public_key = stdout.strip() if stdout else ""
        
        # 4. Определяем IP адрес
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM vpn_users WHERE server_id = ?",
                (server_id,)
            )
            peer_count = (await cursor.fetchone())[0]
        
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # 5. Добавляем пира в конфиг
        add_peer_cmd = f"""
        cd /etc/wireguard
        echo "" >> wg0.conf
        echo "[Peer]" >> wg0.conf
        echo "# Client {user_id}" >> wg0.conf
        echo "PublicKey = {public_key}" >> wg0.conf
        echo "AllowedIPs = {client_ip}/32" >> wg0.conf
        """
        
        await execute_ssh_command(server_id, add_peer_cmd)
        
        return {
            "private_key": private_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания клиента: {e}")
        return None

async def create_vpn_for_user(user_id: int, days: int, gifted: bool = False) -> bool:
    """Создает VPN для пользователя"""
    logger.info(f"Создаем VPN для {user_id} на {days} дней")
    
    server_id = await get_available_vpn_server()
    if not server_id:
        logger.error("Нет доступных VPN серверов")
        return False
    
    # Настраиваем сервер если нужно
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT wireguard_configured FROM servers WHERE id = ?", 
            (server_id,)
        )
        server = await cursor.fetchone()
        
        if not server or not server[0]:
            if not await setup_wireguard_server(server_id):
                logger.error("Не удалось настроить WireGuard")
                return False
    
    # Создаем клиента
    vpn_config = await create_wireguard_client(server_id, user_id)
    if not vpn_config:
        logger.error("Не удалось создать конфиг")
        return False
    
    # Сохраняем в БД
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, config_data, subscription_end, is_active, gifted) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 
                 json.dumps(vpn_config, ensure_ascii=False),
                 (datetime.now() + timedelta(days=days)).isoformat(),
                 True, gifted)
            )
            await db.commit()
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения VPN: {e}")
        return False

async def send_vpn_config(user_id: int, config: Dict, days: int, gifted: bool = False):
    """Отправляет конфиг VPN пользователю"""
    try:
        end_date = datetime.now() + timedelta(days=days)
        
        # WireGuard конфиг
        config_text = f"""[Interface]
PrivateKey = {config['private_key']}
Address = {config['client_ip']}/24
DNS = 1.1.1.1

[Peer]
PublicKey = {config['server_public_key']}
Endpoint = {config['server_ip']}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"""
        
        # Генерируем QR код
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(config_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        
        if gifted:
            message_text = f"🎁 <b>Вам выдан VPN на {days} дней!</b>\n\n"
        else:
            message_text = f"✅ <b>Ваш VPN на {days} дней создан!</b>\n\n"
        
        message_text += f"📅 До: {end_date.strftime('%d.%m.%Y')}\n"
        
        await bot.send_message(user_id, message_text, parse_mode=ParseMode.HTML)
        await bot.send_photo(user_id, types.BufferedInputFile(img_bytes.read(), filename="vpn_qr.png"))
        await bot.send_message(user_id, f"<code>{config_text}</code>", parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Ошибка отправки конфига: {e}")

async def create_bot_for_user(user_id: int, bot_token: str, days: int, gifted: bool = False) -> Dict:
    """Создает бота для пользователя на удаленном сервере"""
    logger.info(f"Создаем бота для {user_id} на {days} дней")
    
    server_id = await get_available_bot_server()
    if not server_id:
        return {"success": False, "error": "Нет серверов для ботов"}
    
    try:
        # Проверяем Docker
        stdout, stderr = await execute_ssh_command(server_id, "which docker")
        if "which:" in stderr or "not found" in stderr:
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y docker.io")
        
        # Создаем простого бота
        bot_name = f"bot_{user_id}_{random.randint(1000, 9999)}"
        bot_code = f'''import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

BOT_TOKEN = "{bot_token}"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🤖 Бот создан через VPN & Bot Hosting!")

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("🏓 Pong!")

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Вы: {{message.text}}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
        
        # Создаем файлы на сервере
        await execute_ssh_command(server_id, f"mkdir -p /tmp/{bot_name}")
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo '{bot_code}' > bot.py")
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo 'aiogram>=3.0.0' > requirements.txt")
        
        # Создаем Dockerfile
        dockerfile = f'''FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "bot.py"]
'''
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo '{dockerfile}' > Dockerfile")
        
        # Собираем и запускаем контейнер
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && docker build -t {bot_name} .")
        stdout, _ = await execute_ssh_command(server_id, f"docker run -d --name {bot_name} --restart unless-stopped {bot_name}")
        container_id = stdout.strip()
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO user_bots 
                (user_id, bot_name, bot_token, server_id, container_id, subscription_end, status, gifted) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, bot_name, bot_token, server_id, container_id,
                 (datetime.now() + timedelta(days=days)).isoformat(),
                 'running', gifted)
            )
            await db.commit()
        
        return {
            "success": True,
            "bot_name": bot_name,
            "container_id": container_id[:12]
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания бота: {e}")
        return {"success": False, "error": str(e)}

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [
        [types.KeyboardButton(text="🔐 Получить VPN")],
        [types.KeyboardButton(text="🤖 Создать бота")],
        [types.KeyboardButton(text="🆘 Помощь")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    buttons = [
        [types.KeyboardButton(text="🖥️ Серверы")],
        [types.KeyboardButton(text="👤 Пользователи")],
        [types.KeyboardButton(text="🤖 Создать бота")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_period_keyboard(show_trial: bool = True):
    buttons = []
    if show_trial:
        buttons.append([types.KeyboardButton(text="🎁 3 дня (пробный)")])
    buttons.append([types.KeyboardButton(text="💎 Неделя")])
    buttons.append([types.KeyboardButton(text="💎 Месяц")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_period_keyboard():
    buttons = [
        [types.KeyboardButton(text="🤖 Неделя")],
        [types.KeyboardButton(text="🤖 Месяц")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_period_keyboard():
    buttons = [
        [types.KeyboardButton(text="7 дней")],
        [types.KeyboardButton(text="30 дней")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def yes_no_keyboard():
    buttons = [
        [types.KeyboardButton(text="✅ Да")],
        [types.KeyboardButton(text="❌ Нет")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()

class UserBotStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_token = State()

class AdminBotStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_period = State()

class AdminVPNStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_period = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Сохраняем пользователя
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id FROM vpn_users WHERE user_id = ? LIMIT 1", (user_id,))
            if not await cursor.fetchone():
                await db.execute(
                    "INSERT INTO vpn_users (user_id, username) VALUES (?, ?)",
                    (user_id, message.from_user.username)
                )
                await db.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя: {e}")
    
    if is_admin(user_id):
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else:
        await message.answer("🚀 <b>Добро пожаловать!</b>\n\nВыберите услугу:", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем пробный период
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user_data = await cursor.fetchone()
    except:
        user_data = None
    
    has_used_trial = user_data and user_data[0]
    await state.set_state(UserVPNStates.waiting_for_period)
    
    if has_used_trial:
        await message.answer("Выберите период:", reply_markup=vpn_period_keyboard(show_trial=False))
    else:
        await message.answer("🎁 <b>3 дня бесплатно!</b>\n\nИли выберите платный период:", reply_markup=vpn_period_keyboard(show_trial=True), parse_mode=ParseMode.HTML)

@dp.message(UserVPNStates.waiting_for_period)
async def process_vpn_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await cmd_start(message)
        return
    
    # Определяем период
    if "🎁" in message.text:
        period = "trial"
        days = 3
    elif "Неделя" in message.text:
        period = "week"
        days = 7
    elif "Месяц" in message.text:
        period = "month"
        days = 30
    else:
        await message.answer("Выберите период из списка:")
        return
    
    # Проверяем пробный период
    if period == "trial":
        user_id = message.from_user.id
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
                user_data = await cursor.fetchone()
            
            if user_data and user_data[0]:
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=vpn_period_keyboard(show_trial=False))
                return
        except Exception as e:
            logger.error(f"Ошибка проверки пробного: {e}")
    
    await state.update_data(period=period, days=days)
    
    if period != "trial":
        # Показываем цены
        try:
            prices = await get_vpn_prices()
            stars = prices.get(period, {}).get("stars", 50)
        except:
            stars = 50 if period == "week" else 150
        
        # Создаем инвойс
        timestamp = int(datetime.now().timestamp())
        payload = f"vpn:{message.from_user.id}:{period}:{timestamp}"
        
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN на {days} дней",
                description=f"VPN доступ на {days} дней",
                payload=payload,
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label=f"VPN {days} дней", amount=stars)],
                start_parameter="vpn_subscription"
            )
            
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """INSERT INTO payments (user_id, amount_stars, period, status, invoice_payload, service_type)
                        VALUES (?, ?, ?, 'pending', ?, 'vpn')""",
                        (message.from_user.id, stars, period, payload)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка сохранения платежа: {e}")
            
            await state.clear()
            return
            
        except Exception as e:
            logger.error(f"Ошибка создания инвойса: {e}")
            await message.answer("❌ Ошибка создания счета.", reply_markup=user_main_menu())
            await state.clear()
            return
    
    # Для пробного - создаем VPN
    await message.answer("🔄 Создаю VPN...")
    
    success = await create_vpn_for_user(message.from_user.id, days, gifted=False)
    
    if success:
        if period == 'trial':
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?", (message.from_user.id,))
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка обновления trial_used: {e}")
        
        # Получаем конфиг для отправки
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT config_data FROM vpn_users WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (message.from_user.id,)
            )
            vpn_data = await cursor.fetchone()
            
            if vpn_data:
                config = json.loads(vpn_data[0])
                await send_vpn_config(message.from_user.id, config, days)
        
        await message.answer("✅ <b>VPN создан!</b>", parse_mode=ParseMode.HTML, reply_markup=user_main_menu())
    else:
        await message.answer("❌ <b>Ошибка создания VPN!</b>", parse_mode=ParseMode.HTML, reply_markup=user_main_menu())
    
    await state.clear()

@dp.message(F.text == "🤖 Создать бота")
async def create_bot_start(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.set_state(AdminBotStates.waiting_for_token)
        await message.answer("🔑 <b>Отправьте токен бота:</b>\n\n(получите у @BotFather)", parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
    else:
        await state.set_state(UserBotStates.waiting_for_period)
        await message.answer("🤖 <b>Создание бота</b>\n\nВыберите период:", reply_markup=bot_period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(UserBotStates.waiting_for_period)
async def process_bot_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await cmd_start(message)
        return
    
    if "Неделя" in message.text:
        period = "week"
        days = 7
    elif "Месяц" in message.text:
        period = "month"
        days = 30
    else:
        await message.answer("Выберите период из списка:")
        return
    
    await state.update_data(period=period, days=days)
    
    # Показываем цены
    try:
        prices = await get_bot_prices()
        stars = prices.get(period, {}).get("stars", 100)
    except:
        stars = 100 if period == "week" else 300
    
    # Создаем инвойс
    timestamp = int(datetime.now().timestamp())
    payload = f"bot:{message.from_user.id}:{period}:{timestamp}"
    
    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=f"Бот на {days} дней",
            description=f"Хостинг бота на {days} дней",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=[LabeledPrice(label=f"Бот {days} дней", amount=stars)],
            start_parameter="bot_hosting"
        )
        
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO payments (user_id, amount_stars, period, status, invoice_payload, service_type)
                    VALUES (?, ?, ?, 'pending', ?, 'bot')""",
                    (message.from_user.id, stars, period, payload)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка сохранения платежа: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        await message.answer("❌ Ошибка создания счета.", reply_markup=user_main_menu())
    
    await state.clear()

@dp.message(AdminBotStates.waiting_for_token)
async def admin_process_bot_token(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await cmd_start(message)
        return
    
    bot_token = message.text.strip()
    
    if not bot_token or len(bot_token) < 30:
        await message.answer("❌ Неверный токен!\n\nОтправьте правильный токен:", reply_markup=back_keyboard())
        return
    
    await state.update_data(bot_token=bot_token)
    await state.set_state(AdminBotStates.waiting_for_period)
    await message.answer("✅ Токен принят!\n\nВыберите период:", reply_markup=admin_period_keyboard())

@dp.message(AdminBotStates.waiting_for_period)
async def admin_process_bot_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AdminBotStates.waiting_for_token)
        await message.answer("Отправьте токен бота:", reply_markup=back_keyboard())
        return
    
    if "7 дней" in message.text:
        days = 7
    elif "30 дней" in message.text:
        days = 30
    else:
        await message.answer("Выберите период из списка:")
        return
    
    data = await state.get_data()
    bot_token = data.get('bot_token')
    
    await message.answer(f"🔄 Создаю бота на {days} дней...")
    
    result = await create_bot_for_user(message.from_user.id, bot_token, days, gifted=False)
    
    if result["success"]:
        await message.answer(
            f"✅ <b>Бот создан!</b>\n\n"
            f"Имя: {result['bot_name']}\n"
            f"Контейнер: {result['container_id']}\n\n"
            f"Бот отвечает на /start и /ping",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания бота!</b>\n\n"
            f"Ошибка: {result['error']}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        "🆘 <b>Помощь</b>\n\n"
        "• VPN не работает: @vpnbothost\n"
        "• Проблемы с оплатой: @vpnbothost\n"
        "• Техподдержка: @vpnbothost",
        parse_mode=ParseMode.HTML,
        reply_markup=user_main_menu()
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    
    logger.info(f"Успешный платеж: {payment.total_amount} stars от {user_id}")
    
    # Парсим payload
    payload_parts = payment.invoice_payload.split(':')
    if len(payload_parts) >= 3:
        service_type = payload_parts[0]
        period = payload_parts[2]
        
        # Обновляем статус платежа
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """UPDATE payments 
                    SET status = 'completed', telegram_payment_id = ?
                    WHERE user_id = ? AND invoice_payload = ? AND status = 'pending'""",
                    (payment.telegram_payment_charge_id, user_id, payment.invoice_payload)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка обновления платежа: {e}")
        
        await message.answer("✅ <b>Оплата принята!</b>", parse_mode=ParseMode.HTML)
        
        if service_type == "vpn":
            days = 30 if period == "month" else 7
            await message.answer(f"🔄 Создаю VPN на {days} дней...")
            
            success = await create_vpn_for_user(user_id, days, gifted=False)
            
            if success:
                # Получаем конфиг для отправки
                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute(
                        "SELECT config_data FROM vpn_users WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                        (user_id,)
                    )
                    vpn_data = await cursor.fetchone()
                    
                    if vpn_data:
                        config = json.loads(vpn_data[0])
                        await send_vpn_config(user_id, config, days)
                
                await message.answer("✅ <b>VPN создан!</b>", parse_mode=ParseMode.HTML, reply_markup=user_main_menu())
            else:
                await message.answer("❌ <b>Ошибка создания VPN!</b>", parse_mode=ParseMode.HTML, reply_markup=user_main_menu())
        
        elif service_type == "bot":
            days = 30 if period == "month" else 7
            await message.answer(
                f"✅ <b>Оплата за бота принята!</b>\n\n"
                f"Теперь отправьте токен бота для создания:",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard()
            )
            
            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=user_id)
            await state.set_state(UserBotStates.waiting_for_token)
            await state.update_data(days=days, payment_completed=True)
    else:
        await message.answer("❌ Ошибка обработки платежа.")

@dp.message(UserBotStates.waiting_for_token)
async def user_process_bot_token(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await cmd_start(message)
        return
    
    bot_token = message.text.strip()
    
    if not bot_token or len(bot_token) < 30:
        await message.answer("❌ Неверный токен!\n\nОтправьте правильный токен:", reply_markup=back_keyboard())
        return
    
    data = await state.get_data()
    days = data.get('days', 7)
    
    await message.answer(f"🔄 Создаю бота на {days} дней...")
    
    result = await create_bot_for_user(message.from_user.id, bot_token, days, gifted=False)
    
    if result["success"]:
        await message.answer(
            f"✅ <b>Бот создан!</b>\n\n"
            f"Имя: {result['bot_name']}\n"
            f"Контейнер: {result['container_id']}\n\n"
            f"Бот отвечает на /start и /ping",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания бота!</b>\n\n"
            f"Ошибка: {result['error']}",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    
    await state.clear()

# ========== АДМИН ФУНКЦИИ ==========
@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, server_type, server_ip, wireguard_configured FROM servers")
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения серверов: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет")
        return
    
    text = "🖥️ <b>Серверы:</b>\n\n"
    
    for server in servers:
        id_, name, server_type, ip, wg_configured = server
        wg_status = "✅" if wg_configured else "❌"
        type_icon = "🛡️" if server_type == 'vpn' else "🤖"
        
        text += f"{type_icon} <b>{name}</b> (ID: {id_})\n"
        text += f"IP: {ip or 'не указан'}\n"
        if server_type == 'vpn':
            text += f"WireGuard: {wg_status}\n"
        text += "\n"
    
    keyboard = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="➕ Добавить сервер")],
        [types.KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminAddServerStates.waiting_for_type)
    
    keyboard = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="🛡️ VPN сервер")],
        [types.KeyboardButton(text="🤖 Сервер для ботов")],
        [types.KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)
    
    await message.answer("Выберите тип сервера:", reply_markup=keyboard)

@dp.message(AdminAddServerStates.waiting_for_type)
async def process_server_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
        return
    
    server_type = "vpn" if "🛡️" in message.text else "bot"
    await state.update_data(server_type=server_type)
    
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=ReplyKeyboardRemove())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_key)
    await message.answer("Отправьте SSH-ключ (текстом или файлом):")

@dp.message(AdminAddServerStates.waiting_for_key)
async def process_ssh_key_text(message: Message, state: FSMContext):
    ssh_key = message.text.strip()
    
    if not ssh_key.startswith('-----BEGIN'):
        ssh_key = f"-----BEGIN PRIVATE KEY-----\n{ssh_key}\n-----END PRIVATE KEY-----"
    
    await state.update_data(ssh_key=ssh_key)
    await state.set_state(AdminAddServerStates.waiting_for_connection)
    
    await message.answer(
        "✅ Ключ принят!\n\n"
        "Введите строку подключения:\n"
        "Формат: <code>user@host:port</code>\n"
        "Пример: <code>opc@123.456.7.89</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection(message: Message, state: FSMContext):
    data = await state.get_data()
    
    try:
        connection_string = message.text.strip()
        
        # Парсим строку подключения
        if ':' in connection_string:
            user_host, port = connection_string.rsplit(':', 1)
            user, host = user_host.split('@')
            port = int(port)
        else:
            user, host = connection_string.split('@')
            port = 22
        
        # Сохраняем сервер
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO servers 
                (name, ssh_key, connection_string, server_type, server_ip) 
                VALUES (?, ?, ?, ?, ?)""",
                (data['server_name'], data['ssh_key'], connection_string, 
                 data['server_type'], host)
            )
            await db.commit()
            
            # Получаем ID добавленного сервера
            cursor = await db.execute("SELECT last_insert_rowid()")
            server_id = (await cursor.fetchone())[0]
        
        server_type_name = "VPN" if data['server_type'] == 'vpn' else "ботов"
        
        # Если это VPN сервер, настраиваем WireGuard
        if data['server_type'] == 'vpn':
            await message.answer(
                f"✅ VPN сервер добавлен! ID: {server_id}\n\n"
                f"🔄 Настраиваю WireGuard...",
                parse_mode=ParseMode.HTML
            )
            
            # Пробуем разные варианты настройки
            success = await setup_wireguard_server(server_id)
            
            if success:
                await message.answer(
                    f"✅ <b>WireGuard успешно настроен!</b>\n\n"
                    f"Сервер готов к использованию.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_main_menu()
                )
            else:
                await message.answer(
                    f"⚠️ <b>Сервер добавлен, но WireGuard не настроен!</b>\n\n"
                    f"Проверьте SSH доступ.\n"
                    f"Настройте вручную: <code>wg genkey | tee private.key | wg pubkey > public.key</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_main_menu()
                )
        else:
            await message.answer(
                f"✅ Сервер для {server_type_name} добавлен!\n\n"
                f"ID: {server_id}\n"
                f"IP: {host}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_menu()
            )
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка формата: {str(e)}")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    keyboard = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="🤖 Выдать бота")],
        [types.KeyboardButton(text="📋 Список")],
        [types.KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)
    
    await message.answer("👤 <b>Пользователи</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_give_vpn(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminVPNStates.waiting_for_user)
    await message.answer("Введите ID пользователя:", reply_markup=ReplyKeyboardRemove())

@dp.message(AdminVPNStates.waiting_for_user)
async def admin_process_vpn_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await state.update_data(user_id=user_id)
        await state.set_state(AdminVPNStates.waiting_for_period)
        await message.answer("Выберите период:", reply_markup=admin_period_keyboard())
    except ValueError:
        await message.answer("❌ Введите числовой ID:")

@dp.message(AdminVPNStates.waiting_for_period)
async def admin_process_vpn_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AdminVPNStates.waiting_for_user)
        await message.answer("Введите ID пользователя:")
        return
    
    if "7 дней" in message.text:
        days = 7
    elif "30 дней" in message.text:
        days = 30
    else:
        await message.answer("Выберите период из списка:")
        return
    
    data = await state.get_data()
    user_id = data.get('user_id')
    
    await message.answer(f"🔄 Выдаю VPN пользователю {user_id}...")
    
    success = await create_vpn_for_user(user_id, days, gifted=True)
    
    if success:
        try:
            await bot.send_message(
                user_id,
                f"🎁 <b>Вам выдан VPN на {days} дней!</b>\n\n"
                f"Конфигурация отправлена ниже.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление: {e}")
        
        # Получаем конфиг для отправки
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT config_data FROM vpn_users WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            vpn_data = await cursor.fetchone()
            
            if vpn_data:
                config = json.loads(vpn_data[0])
                await send_vpn_config(user_id, config, days, gifted=True)
        
        await message.answer(f"✅ VPN выдан пользователю {user_id}!", reply_markup=admin_main_menu())
    else:
        await message.answer(f"❌ Ошибка выдачи VPN пользователю {user_id}", reply_markup=admin_main_menu())
    
    await state.clear()

@dp.message(F.text == "🤖 Выдать бота")
async def admin_give_bot(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    await message.answer(
        "🤖 <b>Выдать бота</b>\n\n"
        "Для выдачи бота пользователю:\n"
        "1. Попросите пользователя создать бота у @BotFather\n"
        "2. Получите токен бота от пользователя\n"
        "3. Используйте кнопку '🤖 Создать бота' в админ-меню\n"
        "4. Укажите токен и период\n\n"
        "Бот будет создан на удаленном сервере и отвечать на /start и /ping",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_menu()
    )

@dp.message(F.text == "📋 Список")
async def admin_list_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id, username, 
                       COUNT(*) as vpn_count,
                       SUM(CASE WHEN is_active = 1 AND subscription_end > datetime('now') THEN 1 ELSE 0 END) as active_vpn
                FROM vpn_users 
                GROUP BY user_id
                ORDER BY MAX(created_at) DESC
                LIMIT 10
            """)
            users = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    text = "📋 <b>Пользователи (последние 10):</b>\n\n"
    
    for user in users:
        user_id, username, vpn_count, active_vpn = user
        username_display = f"@{username}" if username else f"ID: {user_id}"
        text += f"👤 {username_display}\n"
        text += f"   VPN: {active_vpn}/{vpn_count} активных\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "🤖 Создать бота")
async def admin_create_bot(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminBotStates.waiting_for_token)
    await message.answer("🔑 <b>Отправьте токен бота:</b>\n\n(получите у @BotFather)", parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(F.text == "◀️ Назад")
async def back_handler(message: Message, state: FSMContext):
    await state.clear()
    
    if is_admin(message.from_user.id):
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
    else:
        await message.answer("Главное меню:", reply_markup=user_main_menu())

# ========== ЗАПУСК ==========
async def main():
    """Основная функция запуска"""
    try:
        print("=" * 50)
        print("🚀 ЗАПУСК VPN & BOT HOSTING БОТА")
        print("=" * 50)
        
        # Инициализируем базу данных
        await init_database()
        
        # Получаем информацию о боте
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username}")
        print(f"👑 Admin ID: {ADMIN_ID}")
        print(f"🗄️ База данных: {DB_PATH}")
        
        # Запускаем опрос
        print("🔄 Запускаем опрос...")
        await dp.start_polling(bot)
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)