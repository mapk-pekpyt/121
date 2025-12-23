# main.py - ПОЛНЫЙ КОД С ПРАВИЛЬНОЙ БАЗОЙ ДАННЫХ
import os
import asyncio
import logging
import json
import random
import string
import qrcode
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh
import aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
ADMIN_CHAT_ID = -1003542769962
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"

# ПУТЬ К БАЗЕ ДАННЫХ В ПЕРСИСТЕНТНОМ ХРАНИЛИЩЕ
DB_PATH = "/data/database.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_db():
    """Инициализация базы данных"""
    logger.info(f"Инициализация БД по пути: {DB_PATH}")
    
    # Создаем папку /data если ее нет
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")
        
        # Серверы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                server_type TEXT DEFAULT 'vpn',
                country TEXT,
                city TEXT,
                max_users INTEGER DEFAULT 30,
                current_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                server_ip TEXT,
                public_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # VPN пользователи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                server_id INTEGER,
                vpn_type TEXT DEFAULT 'wireguard',
                device_type TEXT,
                config_data TEXT,
                subscription_end TIMESTAMP,
                trial_used BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
            )
        """)
        
        # Индексы для VPN пользователей
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_user_id ON vpn_users(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_active ON vpn_users(is_active)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_subscription ON vpn_users(subscription_end)")
        
        # Платежи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_stars INTEGER NOT NULL,
                period TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                telegram_payment_id TEXT,
                invoice_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индексы для платежей
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        
        # Telegram боты
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_name TEXT NOT NULL,
                bot_token TEXT UNIQUE,
                server_id INTEGER,
                status TEXT DEFAULT 'stopped',
                container_id TEXT,
                subscription_end TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
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
        
        # Проверяем существуют ли уже настройки
        cursor = await db.execute("SELECT COUNT(*) FROM price_settings")
        count = await cursor.fetchone()
        
        if count[0] == 0:
            # Инициализируем цены только если таблица пустая
            await db.execute(
                "INSERT INTO price_settings (service_type, week_price, month_price) VALUES (?, ?, ?)",
                ("vpn", 50, 150)
            )
            await db.execute(
                "INSERT INTO price_settings (service_type, week_price, month_price) VALUES (?, ?, ?)",
                ("bot", 100, 300)
            )
            logger.info("Инициализированы цены по умолчанию")
        
        await db.commit()
        logger.info("База данных инициализирована успешно")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    """Проверяет является ли пользователь админом"""
    if chat_id:
        return user_id == ADMIN_ID or str(chat_id) == str(ADMIN_CHAT_ID)
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    """Получает цены VPN из БД"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT week_price, month_price FROM price_settings WHERE service_type = 'vpn'"
            )
            prices = await cursor.fetchone()
            if prices:
                return {
                    "trial": {"days": 3, "stars": 0},
                    "week": {"days": 7, "stars": prices[0]},
                    "month": {"days": 30, "stars": prices[1]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен VPN: {e}")
    
    # Возвращаем значения по умолчанию
    return {
        "trial": {"days": 3, "stars": 0},
        "week": {"days": 7, "stars": 50},
        "month": {"days": 30, "stars": 150}
    }

async def get_bot_prices() -> Dict:
    """Получает цены ботов из БД"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT week_price, month_price FROM price_settings WHERE service_type = 'bot'"
            )
            prices = await cursor.fetchone()
            if prices:
                return {
                    "week": {"days": 7, "stars": prices[0]},
                    "month": {"days": 30, "stars": prices[1]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен ботов: {e}")
    
    # Возвращаем значения по умолчанию
    return {
        "week": {"days": 7, "stars": 100},
        "month": {"days": 30, "stars": 300}
    }

async def update_vpn_prices(week_price: int, month_price: int):
    """Обновляет цены VPN"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE price_settings 
                SET week_price = ?, month_price = ? 
                WHERE service_type = 'vpn'""",
                (week_price, month_price)
            )
            await db.commit()
            logger.info(f"Обновлены цены VPN: неделя={week_price}, месяц={month_price}")
    except Exception as e:
        logger.error(f"Ошибка обновления цен VPN: {e}")

async def update_bot_prices(week_price: int, month_price: int):
    """Обновляет цены ботов"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE price_settings 
                SET week_price = ?, month_price = ? 
                WHERE service_type = 'bot'""",
                (week_price, month_price)
            )
            await db.commit()
            logger.info(f"Обновлены цены ботов: неделя={week_price}, месяц={month_price}")
    except Exception as e:
        logger.error(f"Ошибка обновления цен ботов: {e}")

async def get_available_vpn_server() -> Optional[int]:
    """Находит доступный VPN сервер"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM servers 
                WHERE server_type = 'vpn' 
                AND is_active = TRUE 
                AND current_users < max_users
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска VPN сервера: {e}")
        return None

async def get_available_bot_server() -> Optional[int]:
    """Находит доступный сервер для ботов"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM servers 
                WHERE server_type = 'bot' 
                AND is_active = TRUE
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска сервера для ботов: {e}")
        return None

async def execute_ssh_command(server_id: int, command: str) -> Tuple[str, str]:
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
            
            # Подключаемся по SSH
            async with asyncssh.connect(
                host,
                username=user,
                port=port,
                client_keys=[asyncssh.import_private_key(ssh_key)],
                known_hosts=None,
                connect_timeout=10
            ) as conn:
                result = await conn.run(command)
                return result.stdout, result.stderr
                
    except Exception as e:
        logger.error(f"Ошибка SSH: {e}")
        return "", f"Ошибка SSH: {str(e)}"

async def setup_wireguard_server(server_id: int) -> bool:
    """Настраивает WireGuard на сервере"""
    try:
        # Проверяем установлен ли WireGuard
        stdout, stderr = await execute_ssh_command(server_id, "which wg-quick")
        if "which:" in stderr or "not found" in stderr:
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y wireguard")
        
        # Настраиваем
        commands = [
            "sysctl -w net.ipv4.ip_forward=1",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
            "mkdir -p /etc/wireguard",
            "cd /etc/wireguard && umask 077 && wg genkey | tee server.private | wg pubkey > server.public",
            """cat > /etc/wireguard/wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/server.private)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF""",
            "wg-quick up wg0 2>/dev/null || true",
            "systemctl enable wg-quick@wg0 2>/dev/null || true"
        ]
        
        for cmd in commands:
            await execute_ssh_command(server_id, cmd)
        
        # Получаем публичный ключ
        stdout, _ = await execute_ssh_command(server_id, "cat /etc/wireguard/server.public")
        if stdout:
            public_key = stdout.strip()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET public_key = ? WHERE id = ?",
                    (public_key, server_id)
                )
                await db.commit()
            return True
        
    except Exception as e:
        logger.error(f"Ошибка настройки WG: {e}")
    
    return False

async def create_wireguard_client(server_id: int, user_id: int) -> Optional[Dict]:
    """Создает клиента WireGuard"""
    try:
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        # Создаем ключи
        await execute_ssh_command(server_id, f"cd /etc/wireguard && umask 077 && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public")
        
        # Получаем ключи
        priv_stdout, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.private")
        pub_stdout, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.public")
        
        private_key = priv_stdout.strip() if priv_stdout else ""
        public_key = pub_stdout.strip() if pub_stdout else ""
        
        if not private_key or not public_key:
            return None
        
        # Получаем следующий IP
        stdout, _ = await execute_ssh_command(server_id, "grep -c '^\\[Peer\\]' /etc/wireguard/wg0.conf || echo 0")
        peer_count = int(stdout.strip())
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # Добавляем пира
        add_cmd = f"""echo '' >> /etc/wireguard/wg0.conf
echo '[Peer]' >> /etc/wireguard/wg0.conf
echo '# Client {user_id}' >> /etc/wireguard/wg0.conf
echo 'PublicKey = {public_key}' >> /etc/wireguard/wg0.conf
echo 'AllowedIPs = {client_ip}/32' >> /etc/wireguard/wg0.conf"""
        
        await execute_ssh_command(server_id, add_cmd)
        
        # Перезапускаем
        await execute_ssh_command(server_id, "wg-quick down wg0 2>/dev/null; sleep 1; wg-quick up wg0 2>/dev/null || true")
        
        # Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT public_key, server_ip FROM servers WHERE id = ?", 
                (server_id,)
            )
            server_data = await cursor.fetchone()
            server_pub_key = server_data[0] if server_data else ""
            server_ip = server_data[1] if server_data else ""
        
        return {
            "private_key": private_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания клиента WG: {e}")
        return None

async def create_vpn_for_user(user_id: int, device_type: str, period_days: int) -> bool:
    """Создает VPN для пользователя"""
    server_id = await get_available_vpn_server()
    if not server_id:
        return False
    
    # Настраиваем сервер если нужно
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT public_key FROM servers WHERE id = ?", (server_id,))
        server = await cursor.fetchone()
        
        if not server[0]:  # Сервер не настроен
            if not await setup_wireguard_server(server_id):
                return False
    
    vpn_config = None
    
    if device_type == "wireguard":
        vpn_config = await create_wireguard_client(server_id, user_id)
        config_type = "wireguard"
    else:
        # Для Android/iOS - используем WireGuard
        vpn_config = await create_wireguard_client(server_id, user_id)
        config_type = "wireguard"
    
    if not vpn_config:
        return False
    
    # Сохраняем пользователя
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO vpn_users 
            (user_id, server_id, vpn_type, device_type, config_data, subscription_end, is_active) 
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, server_id, config_type, device_type, 
             json.dumps(vpn_config, ensure_ascii=False),
             (datetime.now() + timedelta(days=period_days)).isoformat(),
             True)
        )
        
        await db.execute(
            "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
            (server_id,)
        )
        
        await db.commit()
    
    # Отправляем конфиг
    await send_vpn_config_to_user(user_id, vpn_config, device_type, period_days)
    return True

async def send_vpn_config_to_user(user_id: int, config: Dict, device_type: str, period_days: int):
    """Отправляет конфиг VPN пользователю"""
    end_date = datetime.now() + timedelta(days=period_days)
    
    if "private_key" in config:
        # WireGuard конфиг
        config_text = f"""[Interface]
PrivateKey = {config['private_key']}
Address = {config['client_ip']}
DNS = 1.1.1.1

[Peer]
PublicKey = {config['server_public_key']}
AllowedIPs = 0.0.0.0/0
Endpoint = {config['server_ip']}:51820
PersistentKeepalive = 25"""
        
        # Генерируем QR код
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(config_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        
        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Ваш VPN доступ активирован на {period_days} дней!</b>\n\n"
                f"📅 Подписка до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"📱 Установите WireGuard и отсканируйте QR код:",
                parse_mode=ParseMode.HTML
            )
            
            await bot.send_photo(
                user_id,
                types.BufferedInputFile(img_bytes.read(), filename="vpn_qr.png"),
                caption="QR код для WireGuard"
            )
            
            await bot.send_message(
                user_id,
                f"📝 <b>Текстовый конфиг:</b>\n\n<code>{config_text}</code>\n\n"
                "Скопируйте этот конфиг в приложение WireGuard.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки конфига: {e}")

async def create_bot_for_user(user_id: int, period_days: int) -> bool:
    """Создает бота для пользователя"""
    server_id = await get_available_bot_server()
    if not server_id:
        return False
    
    # Генерируем имя бота
    bot_name = f"bot_{user_id}_{random.randint(1000, 9999)}"
    
    # Создаем контейнер
    try:
        # Устанавливаем Docker если нужно
        stdout, stderr = await execute_ssh_command(server_id, "which docker")
        if "which:" in stderr or "not found" in stderr:
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y docker.io")
        
        # Создаем контейнер с Python ботом
        container_cmd = f"""docker run -d \
--name {bot_name} \
--restart unless-stopped \
-e BOT_TOKEN=YOUR_TOKEN_HERE \
python:3.11-slim sh -c "pip install aiogram && python -c 'print(\"Bot container ready\")' && sleep infinity"
"""
        
        await execute_ssh_command(server_id, container_cmd)
        
        # Получаем ID контейнера
        stdout, _ = await execute_ssh_command(server_id, f"docker ps -qf 'name={bot_name}'")
        container_id = stdout.strip() if stdout else ""
        
        # Сохраняем бота
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO user_bots 
                (user_id, bot_name, server_id, container_id, subscription_end, status) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, bot_name, server_id, container_id,
                 (datetime.now() + timedelta(days=period_days)).isoformat(),
                 'running')
            )
            await db.commit()
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Ваш бот активирован на {period_days} дней!</b>\n\n"
                f"📅 Подписка до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"🤖 Имя бота: {bot_name}\n"
                f"🆔 Контейнер: {container_id[:12] if container_id else 'не найден'}\n\n"
                f"Для настройки бота отправьте токен бота в ответном сообщении.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка создания бота: {e}")
        return False

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    """Главное меню для пользователя"""
    buttons = [
        [types.KeyboardButton(text="🔐 Получить VPN")],
        [types.KeyboardButton(text="🤖 Создать бота")],
        [types.KeyboardButton(text="🆘 Помощь")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    """Главное меню для админа"""
    buttons = [
        [types.KeyboardButton(text="🖥️ Серверы")],
        [types.KeyboardButton(text="👤 Пользователи")],
        [types.KeyboardButton(text="💰 Управление ценами")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def servers_menu():
    """Меню серверов"""
    buttons = [
        [types.KeyboardButton(text="🛡️ VPN серверы")],
        [types.KeyboardButton(text="🤖 Серверы для ботов")],
        [types.KeyboardButton(text="➕ Добавить сервер")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_period_keyboard(show_trial: bool = True):
    """Клавиатура периодов VPN"""
    buttons = []
    
    if show_trial:
        buttons.append([types.KeyboardButton(text="🎁 3 дня (пробный)")])
    
    buttons.append([types.KeyboardButton(text="💎 Неделя")])
    buttons.append([types.KeyboardButton(text="💎 Месяц")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_device_keyboard():
    buttons = [
        [types.KeyboardButton(text="📱 Android")],
        [types.KeyboardButton(text="🍎 iOS")],
        [types.KeyboardButton(text="💻 WireGuard (рекомендуется)")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_period_keyboard():
    """Клавиатура периодов для бота"""
    buttons = [
        [types.KeyboardButton(text="🤖 Неделя")],
        [types.KeyboardButton(text="🤖 Месяц")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_users_menu():
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="🤖 Выдать бота")],
        [types.KeyboardButton(text="📋 Список пользователей")],
        [types.KeyboardButton(text="⏹️ Отключить VPN")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_period_keyboard(service: str = "vpn"):
    """Клавиатура периодов для админа"""
    buttons = [
        [types.KeyboardButton(text="7 дней")],
        [types.KeyboardButton(text="30 дней")],
        [types.KeyboardButton(text="♾️ Безлимит")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_prices_menu():
    buttons = [
        [types.KeyboardButton(text="💰 Цены VPN")],
        [types.KeyboardButton(text="🤖 Цены ботов")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def confirm_keyboard():
    buttons = [
        [types.KeyboardButton(text="✅ Подтвердить")],
        [types.KeyboardButton(text="❌ Отменить")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_device = State()

class UserBotStates(StatesGroup):
    waiting_for_period = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()
    waiting_for_service = State()

class AdminPriceStates(StatesGroup):
    waiting_for_service = State()
    waiting_for_week_price = State()
    waiting_for_confirm = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    user_id = message.from_user.id
    
    # Сохраняем информацию о пользователе в VPN таблице
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем существует ли пользователь
            cursor = await db.execute(
                "SELECT id FROM vpn_users WHERE user_id = ? LIMIT 1",
                (user_id,)
            )
            existing = await cursor.fetchone()
            
            if not existing:
                # Добавляем нового пользователя
                await db.execute(
                    """INSERT INTO vpn_users (user_id, username, first_name)
                    VALUES (?, ?, ?)""",
                    (user_id, message.from_user.username, message.from_user.first_name)
                )
                await db.commit()
                logger.info(f"Добавлен новый пользователь: {user_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя: {e}")
    
    if is_admin(user_id, message.chat.id):
        await message.answer(
            "👑 <b>Админ-панель</b>",
            reply_markup=admin_main_menu(),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "🚀 <b>Добро пожаловать!</b>\n\n"
            "Выберите услугу:",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    """Начало получения VPN"""
    user_id = message.from_user.id
    
    # Проверяем использовал ли пробный
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT trial_used FROM vpn_users WHERE user_id = ?",
                (user_id,)
            )
            user_data = await cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка проверки пробного периода: {e}")
        user_data = None
    
    has_used_trial = user_data and user_data[0]
    
    await state.set_state(UserVPNStates.waiting_for_period)
    
    if has_used_trial:
        await message.answer(
            "Выберите период подписки:",
            reply_markup=vpn_period_keyboard(show_trial=False)
        )
    else:
        await message.answer(
            "🎁 <b>Бесплатный пробный период на 3 дня!</b>\n\n"
            "Или выберите платную подписку:",
            reply_markup=vpn_period_keyboard(show_trial=True),
            parse_mode=ParseMode.HTML
        )

@dp.message(UserVPNStates.waiting_for_period)
async def process_vpn_period(message: Message, state: FSMContext):
    """Обработка выбора периода VPN"""
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
    
    # Проверяем если это повторный выбор пробного
    if period == "trial":
        user_id = message.from_user.id
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT trial_used FROM vpn_users WHERE user_id = ?",
                    (user_id,)
                )
                user_data = await cursor.fetchone()
            
            if user_data and user_data[0]:
                await message.answer(
                    "❌ Вы уже использовали пробный период!\n\n"
                    "Выберите платную подписку:",
                    reply_markup=vpn_period_keyboard(show_trial=False)
                )
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
        
        # Создаем инвойс для оплаты
        payload = f"vpn:{message.from_user.id}:{period}:{int(datetime.now().timestamp())}"
        
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN на {days} дней",
                description=f"Доступ к VPN серверам",
                payload=payload,
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label=f"VPN {days} дней", amount=stars * 100)],
                start_parameter="vpn_subscription"
            )
            
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """INSERT INTO payments (user_id, amount_stars, period, status)
                        VALUES (?, ?, ?, 'pending')""",
                        (message.from_user.id, stars, period)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка сохранения платежа: {e}")
            
            await state.clear()
            return
            
        except Exception as e:
            logger.error(f"Ошибка создания инвойса: {e}")
            await message.answer(
                "❌ Ошибка создания счета.\n\n"
                "Для других способов оплаты напишите в @vpnbothost"
            )
            await state.clear()
            return
    
    # Для пробного - сразу запрашиваем устройство
    await state.set_state(UserVPNStates.waiting_for_device)
    await message.answer(
        "Выберите тип устройства:",
        reply_markup=vpn_device_keyboard()
    )

@dp.message(UserVPNStates.waiting_for_device)
async def process_vpn_device(message: Message, state: FSMContext):
    """Обработка выбора устройства"""
    if message.text == "◀️ Назад":
        await state.set_state(UserVPNStates.waiting_for_period)
        
        user_id = message.from_user.id
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT trial_used FROM vpn_users WHERE user_id = ?",
                    (user_id,)
                )
                user_data = await cursor.fetchone()
            
            has_used_trial = user_data and user_data[0]
        except:
            has_used_trial = False
            
        await message.answer(
            "Выберите период:",
            reply_markup=vpn_period_keyboard(show_trial=not has_used_trial)
        )
        return
    
    device_map = {
        "📱 Android": "android",
        "🍎 iOS": "ios",
        "💻 WireGuard (рекомендуется)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите устройство из списка:")
        return
    
    device_type = device_map[message.text]
    data = await state.get_data()
    
    # Создаем VPN
    await message.answer("🔄 Создаю ваш VPN доступ...")
    
    success = await create_vpn_for_user(
        message.from_user.id,
        device_type,
        data.get('days', 3)
    )
    
    if success:
        if data.get('period') == 'trial':
            # Помечаем пробный как использованный
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?",
                        (message.from_user.id,)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка обновления trial_used: {e}")
        
        await message.answer(
            "✅ <b>VPN доступ успешно создан!</b>\n\n"
            "Конфигурация отправлена вам в чат.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ <b>Ошибка создания VPN!</b>\n\n"
            "Пожалуйста, напишите в @vpnbothost",
            parse_mode=ParseMode.HTML
        )
    
    await state.clear()
    await cmd_start(message)

@dp.message(F.text == "🤖 Создать бота")
async def create_bot_start(message: Message, state: FSMContext):
    """Начало создания бота"""
    await state.set_state(UserBotStates.waiting_for_period)
    await message.answer(
        "🤖 <b>Создание Telegram бота</b>\n\n"
        "Выберите период:",
        reply_markup=bot_period_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(UserBotStates.waiting_for_period)
async def process_bot_period(message: Message, state: FSMContext):
    """Обработка периода для бота"""
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
    
    # Показываем цены
    try:
        prices = await get_bot_prices()
        stars = prices.get(period, {}).get("stars", 100)
    except:
        stars = 100 if period == "week" else 300
    
    # Создаем инвойс для оплаты
    payload = f"bot:{message.from_user.id}:{period}:{int(datetime.now().timestamp())}"
    
    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=f"Бот на {days} дней",
            description=f"Запуск Telegram бота в контейнере",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=[LabeledPrice(label=f"Бот {days} дней", amount=stars * 100)],
            start_parameter="bot_hosting"
        )
        
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO payments (user_id, amount_stars, period, status)
                    VALUES (?, ?, ?, 'pending')""",
                    (message.from_user.id, stars, period)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка сохранения платежа: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        await message.answer(
            "❌ Ошибка создания счета.\n\n"
            "Для других способов оплаты напишите в @vpnbothost"
        )
    
    await state.clear()

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    """Команда помощи"""
    await message.answer(
        "🆘 <b>Помощь и поддержка</b>\n\n"
        "• VPN не работает: @vpnbothost\n"
        "• Проблемы с оплатой: @vpnbothost\n"
        "• Техподдержка: @vpnbothost\n\n"
        "Мы всегда готовы помочь!",
        parse_mode=ParseMode.HTML
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    """Подтверждение платежа"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Успешный платеж"""
    payment = message.successful_payment
    user_id = message.from_user.id
    
    logger.info(f"Успешный платеж: {payment.total_amount} stars от {user_id}")
    
    # Парсим payload
    payload_parts = payment.invoice_payload.split(':')
    if len(payload_parts) >= 3:
        service_type = payload_parts[0]  # 'vpn' или 'bot'
        period = payload_parts[2]
        
        # Обновляем статус платежа
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """UPDATE payments 
                    SET status = 'completed', telegram_payment_id = ?
                    WHERE user_id = ? AND period = ? AND status = 'pending'""",
                    (payment.telegram_payment_charge_id, user_id, period)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка обновления платежа: {e}")
        
        await message.answer(
            f"✅ <b>Оплата получена!</b>\n\n"
            f"{payment.total_amount // 100} stars успешно списаны.\n"
            f"Сейчас активирую услугу...",
            parse_mode=ParseMode.HTML
        )
        
        if service_type == "vpn":
            # Для VPN - запрашиваем устройство
            await message.answer(
                "Выберите тип устройства:",
                reply_markup=vpn_device_keyboard()
            )
            
            # Сохраняем в состоянии
            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=user_id)
            
            await state.set_state(UserVPNStates.waiting_for_device)
            await state.update_data(
                period=period, 
                days=30 if period == "month" else 7
            )
        
        elif service_type == "bot":
            # Для бота - сразу создаем
            days = 30 if period == "month" else 7
            await message.answer(f"🔄 Создаю бота на {days} дней...")
            
            success = await create_bot_for_user(user_id, days)
            
            if success:
                await message.answer(
                    f"✅ <b>Бот успешно создан на {days} дней!</b>\n\n"
                    "Инструкция отправлена вам в чат.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(
                    "❌ <b>Ошибка создания бота!</b>\n\n"
                    "Пожалуйста, напишите в @vpnbothost",
                    parse_mode=ParseMode.HTML
                )
    else:
        await message.answer("❌ Ошибка обработки платежа. Обратитесь в @vpnbothost")

# ========== АДМИН ФУНКЦИИ ==========
@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message):
    """Меню серверов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "🖥️ <b>Управление серверами</b>",
        reply_markup=servers_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server(message: Message, state: FSMContext):
    """Добавление сервера"""
    if not is_admin(message.from_user.id, message.chat.id):
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
    """Обработка типа сервера"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
        return
    
    server_type = "vpn" if "🛡️" in message.text else "bot"
    await state.update_data(server_type=server_type)
    
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer(
        "Введите имя сервера:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    """Обработка имени сервера"""
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_key)
    await message.answer("Отправьте файл с SSH-ключом:")

@dp.message(AdminAddServerStates.waiting_for_key, F.document)
async def process_ssh_key(message: Message, state: FSMContext, bot: Bot):
    """Обработка SSH ключа"""
    file = await bot.get_file(message.document.file_id)
    file_path = f"temp_{message.document.file_name}"
    await bot.download_file(file.file_path, file_path)
    
    with open(file_path, 'r') as f:
        ssh_key = f.read().strip()
    
    os.remove(file_path)
    
    if not ssh_key.startswith('-----BEGIN'):
        ssh_key = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{ssh_key}\n-----END OPENSSH PRIVATE KEY-----"
    
    await state.update_data(ssh_key=ssh_key)
    await state.set_state(AdminAddServerStates.waiting_for_connection)
    
    await message.answer(
        "Введите строку подключения:\n"
        "Формат: user@host:port\n"
        "Пример: opc@193.122.8.29",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection(message: Message, state: FSMContext):
    """Обработка подключения"""
    data = await state.get_data()
    
    try:
        if ':' in message.text:
            user_host, port = message.text.rsplit(':', 1)
            user, host = user_host.split('@')
            port = int(port)
        else:
            user, host = message.text.split('@')
            port = 22
    except ValueError as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        return
    
    # Сохраняем сервер
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO servers 
                (name, ssh_key, connection_string, server_type, server_ip) 
                VALUES (?, ?, ?, ?, ?)""",
                (data['server_name'], data['ssh_key'], message.text, 
                 data['server_type'], host)
            )
            await db.commit()
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения: {str(e)}")
        await state.clear()
        return
    
    server_type_name = "VPN" if data['server_type'] == 'vpn' else "ботов"
    await message.answer(
        f"✅ Сервер для {server_type_name} <b>{data['server_name']}</b> добавлен!",
        parse_mode=ParseMode.HTML
    )
    
    await state.clear()
    await message.answer("Админ-панель:", reply_markup=admin_main_menu())

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message):
    """Управление пользователями"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "👤 <b>Управление пользователями</b>",
        reply_markup=admin_users_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_give_vpn(message: Message, state: FSMContext):
    """Выдача VPN от админа"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminUserStates.waiting_for_username)
    await state.update_data(service="vpn")
    await message.answer(
        "Введите @username или ID пользователя:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.text == "🤖 Выдать бота")
async def admin_give_bot(message: Message, state: FSMContext):
    """Выдача бота от админа"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminUserStates.waiting_for_username)
    await state.update_data(service="bot")
    await message.answer(
        "Введите @username или ID пользователя:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminUserStates.waiting_for_username)
async def admin_process_username(message: Message, state: FSMContext):
    """Обработка username от админа"""
    username = message.text.strip()
    user_id = None
    
    if username.isdigit():
        user_id = int(username)
        await state.update_data(user_id=user_id)
    elif username.startswith('@'):
        # Сохраняем username
        await state.update_data(username=username)
    else:
        await message.answer("Введите @username или ID:")
        return
    
    data = await state.get_data()
    service = data.get('service', 'vpn')
    
    await state.set_state(AdminUserStates.waiting_for_period)
    await message.answer(
        f"Выберите период для {service}:",
        reply_markup=admin_period_keyboard(service)
    )

@dp.message(AdminUserStates.waiting_for_period)
async def admin_process_period(message: Message, state: FSMContext):
    """Обработка периода от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_username)
        await message.answer("Введите @username или ID:")
        return
    
    period_map = {
        "7 дней": 7,
        "30 дней": 30,
        "♾️ Безлимит": 36500
    }
    
    if message.text not in period_map:
        await message.answer("Выберите период из списка:")
        return
    
    days = period_map[message.text]
    data = await state.get_data()
    user_id = data.get('user_id')
    service = data.get('service', 'vpn')
    username = data.get('username')
    
    if not user_id and username:
        # Нужно найти пользователя по username
        await message.answer(
            f"Пользователь {username} найден.\n"
            f"Будет выдан {service} на {days} дней.\n\n"
            f"Отправьте пользователю ссылку: https://t.me/{(await bot.get_me()).username}\n"
            f"Он должен написать боту, затем вы сможете выдать услугу."
        )
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
        return
    
    # Создаем услугу
    await message.answer(f"🔄 Выдаю {service} пользователю {user_id}...")
    
    if service == "vpn":
        # Для VPN отправляем пользователю сообщение
        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Вам выдан VPN доступ на {days} дней!</b>\n\n"
                f"Нажмите кнопку ниже чтобы выбрать тип устройства:",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                    types.InlineKeyboardButton(
                        text="🔐 Выбрать устройство",
                        url=f"https://t.me/{(await bot.get_me()).username}?start=getvpn_{user_id}_{days}"
                    )
                ]]),
                parse_mode=ParseMode.HTML
            )
            
            await message.answer(
                f"✅ Пользователю {user_id} отправлено приглашение!\n"
                f"Он должен нажать кнопку и выбрать устройство.",
                reply_markup=admin_main_menu()
            )
            
        except Exception as e:
            await message.answer(
                f"❌ Не удалось отправить сообщение пользователю {user_id}\n"
                f"Ошибка: {str(e)}",
                reply_markup=admin_main_menu()
            )
    
    elif service == "bot":
        # Создаем бота
        success = await create_bot_for_user(user_id, days)
        
        if success:
            await message.answer(
                f"✅ Бот успешно выдан пользователю {user_id} на {days} дней!",
                reply_markup=admin_main_menu()
            )
        else:
            await message.answer(
                f"❌ Ошибка выдачи бота пользователю {user_id}",
                reply_markup=admin_main_menu()
            )
    
    await state.clear()

@dp.message(F.text == "💰 Управление ценами")
async def admin_prices(message: Message):
    """Управление ценами"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        vpn_prices = await get_vpn_prices()
        bot_prices = await get_bot_prices()
    except:
        vpn_prices = {"week": {"stars": 50}, "month": {"stars": 150}}
        bot_prices = {"week": {"stars": 100}, "month": {"stars": 300}}
    
    text = "💰 <b>Текущие цены:</b>\n\n"
    text += "<b>VPN:</b>\n"
    text += f"• Неделя: {vpn_prices['week']['stars']} stars\n"
    text += f"• Месяц: {vpn_prices['month']['stars']} stars (3x недели)\n\n"
    text += "<b>Боты:</b>\n"
    text += f"• Неделя: {bot_prices['week']['stars']} stars\n"
    text += f"• Месяц: {bot_prices['month']['stars']} stars (3x недели)\n\n"
    text += "Выберите что изменить:"
    
    await message.answer(text, reply_markup=admin_prices_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "💰 Цены VPN")
async def admin_vpn_prices(message: Message, state: FSMContext):
    """Изменение цен VPN"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await state.update_data(service_type="vpn")
    
    try:
        vpn_prices = await get_vpn_prices()
        current_price = vpn_prices["week"]["stars"]
    except:
        current_price = 50
    
    await message.answer(
        f"Текущая цена за неделю: {current_price} stars\n\n"
        "Введите новую цену за неделю (в stars):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.text == "🤖 Цены ботов")
async def admin_bot_prices(message: Message, state: FSMContext):
    """Изменение цен ботов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await state.update_data(service_type="bot")
    
    try:
        bot_prices = await get_bot_prices()
        current_price = bot_prices["week"]["stars"]
    except:
        current_price = 100
    
    await message.answer(
        f"Текущая цена за неделю: {current_price} stars\n\n"
        "Введите новую цену за неделю (в stars):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminPriceStates.waiting_for_week_price)
async def admin_process_week_price(message: Message, state: FSMContext):
    """Обработка цены за неделю"""
    try:
        week_price = int(message.text)
        await state.update_data(week_price=week_price)
        await state.set_state(AdminPriceStates.waiting_for_confirm)
        
        data = await state.get_data()
        service_type = data.get('service_type')
        
        service_name = "VPN" if service_type == "vpn" else "ботов"
        
        await message.answer(
            f"<b>Новые цены для {service_name}:</b>\n\n"
            f"• Неделя: {week_price} stars\n"
            f"• Месяц: {week_price * 3} stars (3x недели)\n\n"
            f"Подтвердите изменение:",
            reply_markup=confirm_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await message.answer("Введите число:")

@dp.message(AdminPriceStates.waiting_for_confirm)
async def admin_confirm_prices(message: Message, state: FSMContext):
    """Подтверждение изменения цен"""
    if message.text == "✅ Подтвердить":
        data = await state.get_data()
        service_type = data.get('service_type')
        week_price = data.get('week_price')
        
        if service_type == "vpn":
            await update_vpn_prices(week_price, week_price * 3)
            service_name = "VPN"
        elif service_type == "bot":
            await update_bot_prices(week_price, week_price * 3)
            service_name = "ботов"
        else:
            service_name = "услуги"
        
        await message.answer(
            f"✅ Цены {service_name} обновлены!\n\n"
            f"• Неделя: {week_price} stars\n"
            f"• Месяц: {week_price * 3} stars",
            reply_markup=admin_main_menu()
        )
    
    elif message.text == "❌ Отменить":
        await message.answer("Изменение цен отменено", reply_markup=admin_main_menu())
    
    elif message.text == "◀️ Назад":
        await message.answer("Управление ценами:", reply_markup=admin_prices_menu())
    
    await state.clear()

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message):
    """Список пользователей"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # VPN пользователи
            cursor = await db.execute("""
                SELECT user_id, username, COUNT(*) as vpn_count,
                       SUM(CASE WHEN is_active = 1 AND subscription_end > datetime('now') THEN 1 ELSE 0 END) as active_vpn
                FROM vpn_users 
                GROUP BY user_id
                ORDER BY MAX(created_at) DESC
                LIMIT 20
            """)
            vpn_users = await cursor.fetchall()
            
            # Боты
            cursor = await db.execute("""
                SELECT user_id, COUNT(*) as bot_count,
                       SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as active_bots
                FROM user_bots 
                GROUP BY user_id
                LIMIT 20
            """)
            bot_users = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    text = "📋 <b>Пользователи (последние 20):</b>\n\n"
    
    for user in vpn_users[:10]:
        user_id, username, vpn_count, active_vpn = user
        username_display = f"@{username}" if username else f"ID: {user_id}"
        text += f"👤 {username_display}\n"
        text += f"   VPN: {active_vpn}/{vpn_count} активных\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "⏹️ Отключить VPN")
async def admin_disable_vpn(message: Message):
    """Отключение VPN"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "Введите ID пользователя для отключения VPN:",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Здесь нужно добавить обработчик

@dp.message(F.text == "◀️ Назад")
async def back_handler(message: Message, state: FSMContext):
    """Обработчик кнопки назад"""
    await state.clear()
    
    if is_admin(message.from_user.id, message.chat.id):
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
    else:
        await message.answer("Главное меню:", reply_markup=user_main_menu())

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_expired_subscriptions():
    """Проверка истекающих подписок"""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Находим подписки которые истекают через 24 часа
                cursor = await db.execute("""
                    SELECT user_id, subscription_end
                    FROM vpn_users 
                    WHERE is_active = 1 
                    AND subscription_end BETWEEN datetime('now') AND datetime('now', '+1 day')
                """)
                expiring_vpn = await cursor.fetchall()
                
                for user in expiring_vpn:
                    user_id = user[0]
                    end_date = datetime.fromisoformat(user[1]).strftime("%d.%m.%Y")
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"⚠️ <b>Ваша VPN подписка истекает через 24 часа!</b>\n\n"
                            f"Дата окончания: {end_date}\n\n"
                            f"Продлите подписку чтобы не потерять доступ!",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                # Отключаем истекшие VPN
                cursor = await db.execute("""
                    SELECT user_id FROM vpn_users 
                    WHERE is_active = 1 
                    AND subscription_end < datetime('now')
                """)
                expired_vpn = await cursor.fetchall()
                
                for user in expired_vpn:
                    user_id = user[0]
                    await db.execute(
                        "UPDATE vpn_users SET is_active = 0 WHERE user_id = ?",
                        (user_id,)
                    )
                    
                    try:
                        await bot.send_message(
                            user_id,
                            "⏰ <b>Ваша VPN подписка истекла!</b>\n\n"
                            "Для продления нажмите /start",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Ошибка проверки подписок: {e}")
        
        await asyncio.sleep(3600)  # Каждый час

# ========== ЗАПУСК ==========
async def main():
    """Основная функция запуска"""
    try:
        # Инициализируем БД перед запуском
        await init_db()
        
        # Проверяем что БД работает
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cursor.fetchall()
            logger.info(f"Таблицы в БД: {[t[0] for t in tables]}")
        
        # Запускаем фоновые задачи
        asyncio.create_task(check_expired_subscriptions())
        
        me = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{me.username}")
        logger.info(f"👑 Admin ID: {ADMIN_ID}")
        logger.info(f"💬 Admin chat: {ADMIN_CHAT_ID}")
        logger.info(f"🗄️ База данных: {DB_PATH}")
        
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Критическая ошибка запуска: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())