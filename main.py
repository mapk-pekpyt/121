# main.py - ИСПРАВЛЕННЫЙ VPN + БОТ МЕНЕДЖЕР
import os
import asyncio
import logging
import json
import random
import string
import qrcode
import io
import subprocess
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
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

# VPN цены в Stars (неделя = X, месяц = 3X)
VPN_PRICES = {
    "trial": {"days": 3, "stars": 0},
    "week": {"days": 7, "stars": 50},
    "month": {"days": 30, "stars": 150}  # 3x недели
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
                config_data TEXT,  -- JSON с конфигом
                subscription_end TIMESTAMP,
                trial_used BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Платежи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_stars INTEGER,
                period TEXT,
                status TEXT DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Telegram боты (созданные пользователями)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_name TEXT NOT NULL,
                bot_token TEXT UNIQUE,
                server_id INTEGER,
                status TEXT DEFAULT 'stopped',
                container_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Настройки цен
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_settings (
                service_type TEXT PRIMARY KEY,  -- 'vpn' или 'bot'
                week_price INTEGER DEFAULT 50,
                month_price INTEGER DEFAULT 150
            )
        """)
        
        # Инициализируем цены
        await db.execute(
            "INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES (?, ?, ?)",
            ("vpn", 50, 150)
        )
        await db.execute(
            "INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES (?, ?, ?)",
            ("bot", 100, 300)
        )
        
        await db.commit()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    return user_id == ADMIN_ID or (chat_id and str(chat_id) == str(ADMIN_CHAT_ID))

async def get_vpn_prices() -> Dict:
    """Получает цены VPN из БД"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT week_price, month_price FROM price_settings WHERE service_type = 'vpn'"
        )
        prices = await cursor.fetchone()
        return {
            "trial": {"days": 3, "stars": 0},
            "week": {"days": 7, "stars": prices[0] if prices else 50},
            "month": {"days": 30, "stars": prices[1] if prices else 150}
        }

async def get_bot_prices() -> Dict:
    """Получает цены ботов из БД"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT week_price, month_price FROM price_settings WHERE service_type = 'bot'"
        )
        prices = await cursor.fetchone()
        return {
            "week": {"days": 7, "stars": prices[0] if prices else 100},
            "month": {"days": 30, "stars": prices[1] if prices else 300}
        }

async def update_vpn_prices(week_price: int, month_price: int):
    """Обновляет цены VPN"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE price_settings 
            SET week_price = ?, month_price = ? 
            WHERE service_type = 'vpn'""",
            (week_price, month_price)
        )
        await db.commit()

async def update_bot_prices(week_price: int, month_price: int):
    """Обновляет цены ботов"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE price_settings 
            SET week_price = ?, month_price = ? 
            WHERE service_type = 'bot'""",
            (week_price, month_price)
        )
        await db.commit()

async def get_available_vpn_server() -> Optional[int]:
    """Находит доступный VPN сервер"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT id FROM servers 
            WHERE server_type = 'vpn' 
            AND is_active = TRUE 
            AND current_users < max_users
            ORDER BY current_users ASC 
            LIMIT 1
        """)
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_available_bot_server() -> Optional[int]:
    """Находит доступный сервер для ботов"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT id FROM servers 
            WHERE server_type = 'bot' 
            AND is_active = TRUE
            LIMIT 1
        """)
        result = await cursor.fetchone()
        return result[0] if result else None

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
            user, host, port = parse_connection_string(conn_str)
            
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
        logger.error(f"SSH error: {e}")
        return "", f"Ошибка SSH: {str(e)}"

def parse_connection_string(conn_str: str) -> Tuple[str, str, int]:
    """Парсит строку подключения"""
    if ':' in conn_str:
        user_host, port = conn_str.rsplit(':', 1)
        user, host = user_host.split('@')
        port = int(port)
    else:
        user, host = conn_str.split('@')
        port = 22
    return user, host, port

async def check_server_status(server_id: int) -> Dict:
    """Проверяет статус сервера"""
    result = {
        "online": False,
        "ping": None,
        "load": None,
        "memory": None,
        "disk": None
    }
    
    try:
        # Проверяем доступность
        stdout, stderr = await execute_ssh_command(server_id, "echo 'ping'")
        if stdout.strip() == "ping":
            result["online"] = True
        
        # Получаем нагрузку
        stdout, _ = await execute_ssh_command(server_id, "uptime | awk -F'[a-z]:' '{print $2}' | awk '{print $1}'")
        if stdout:
            result["load"] = stdout.strip()
        
        # Получаем память
        stdout, _ = await execute_ssh_command(server_id, "free -m | awk 'NR==2{printf \"%.1f%%\", $3*100/$2}'")
        if stdout:
            result["memory"] = stdout.strip()
        
        # Получаем диск
        stdout, _ = await execute_ssh_command(server_id, "df -h / | awk 'NR==2{print $5}'")
        if stdout:
            result["disk"] = stdout.strip()
        
        # Простой пинг
        import time
        start = time.time()
        await execute_ssh_command(server_id, "true")
        result["ping"] = f"{(time.time() - start) * 1000:.0f}ms"
        
    except Exception as e:
        logger.error(f"Ошибка проверки статуса: {e}")
    
    return result

async def setup_wireguard_server(server_id: int) -> bool:
    """Настраивает WireGuard на сервере"""
    commands = [
        "which wg-quick || (apt-get update && apt-get install -y wireguard)",
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
        "wg-quick up wg0",
        "systemctl enable wg-quick@wg0"
    ]
    
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd)
        if stderr and "already exists" not in stderr.lower():
            logger.error(f"Ошибка настройки WG: {stderr}")
    
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
    
    return False

async def create_wireguard_client(server_id: int, user_id: int) -> Optional[Dict]:
    """Создает клиента WireGuard"""
    try:
        # Генерируем ключи
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        # Получаем количество существующих пиров для определения IP
        stdout, _ = await execute_ssh_command(
            server_id, 
            "grep -c '^\\[Peer\\]' /etc/wireguard/wg0.conf || echo 0"
        )
        peer_count = int(stdout.strip()) if stdout else 0
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # Создаем ключи клиента
        key_gen_cmds = [
            f"cd /etc/wireguard && umask 077 && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public",
            f"PRIVATE_KEY=$(cat /etc/wireguard/{client_name}.private)",
            f"PUBLIC_KEY=$(cat /etc/wireguard/{client_name}.public)"
        ]
        
        for cmd in key_gen_cmds:
            await execute_ssh_command(server_id, cmd)
        
        # Добавляем пира в конфиг
        add_peer_cmd = f"""echo '' >> /etc/wireguard/wg0.conf &&
echo '[Peer]' >> /etc/wireguard/wg0.conf &&
echo '# Client {user_id}' >> /etc/wireguard/wg0.conf &&
echo 'PublicKey = $(cat /etc/wireguard/{client_name}.public)' >> /etc/wireguard/wg0.conf &&
echo 'AllowedIPs = {client_ip}/32' >> /etc/wireguard/wg0.conf"""
        
        await execute_ssh_command(server_id, add_peer_cmd)
        
        # Получаем ключи
        priv_stdout, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.private")
        pub_stdout, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.public")
        
        private_key = priv_stdout.strip() if priv_stdout else ""
        public_key = pub_stdout.strip() if pub_stdout else ""
        
        # Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT public_key, server_ip FROM servers WHERE id = ?", 
                (server_id,)
            )
            server_data = await cursor.fetchone()
            server_pub_key = server_data[0] if server_data else ""
            server_ip = server_data[1] if server_data else ""
        
        # Обновляем WireGuard
        await execute_ssh_command(server_id, "wg-quick down wg0; sleep 1; wg-quick up wg0")
        
        return {
            "private_key": private_key,
            "public_key": public_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip,
            "client_name": client_name
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания клиента WG: {e}")
        return None

async def create_l2tp_client(server_id: int, user_id: int) -> Optional[Dict]:
    """Создает L2TP подключение"""
    try:
        username = f"vpn{user_id}{random.randint(1000, 9999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        psk = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        
        # Настраиваем L2TP если нужно
        setup_cmds = [
            "which xl2tpd || (apt-get update && apt-get install -y xl2tpd strongswan)",
            f"echo '{username} l2tpd {password} *' >> /etc/ppp/chap-secrets",
            "systemctl restart xl2tpd"
        ]
        
        for cmd in setup_cmds:
            await execute_ssh_command(server_id, cmd)
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT server_ip FROM servers WHERE id = ?", 
                (server_id,)
            )
            server_ip = await cursor.fetchone()
        
        return {
            "type": "l2tp",
            "username": username,
            "password": password,
            "psk": psk,
            "server_ip": server_ip[0] if server_ip else ""
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания L2TP: {e}")
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
        vpn_config = await create_l2tp_client(server_id, user_id)
        config_type = "l2tp"
    
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
    
    if device_type == "wireguard" and "private_key" in config:
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
    
    elif config.get("type") == "l2tp":
        # L2TP конфиг
        if device_type == "android":
            config_text = f"""Имя: VPN Service
Тип: L2TP/IPSec PSK
Адрес сервера: {config['server_ip']}
Общий ключ IPSec: {config['psk']}
Имя пользователя: {config['username']}
Пароль: {config['password']}"""
            
            instructions = "В настройках VPN выберите тип L2TP/IPSec PSK"
        
        else:  # iOS
            config_text = f"""Описание: VPN Service
Сервер: {config['server_ip']}
Учетная запись: {config['username']}
Пароль: {config['password']}
Общий ключ: {config['psk']}"""
            
            instructions = "В настройках VPN добавьте новую конфигурацию"
        
        await bot.send_message(
            user_id,
            f"✅ <b>Ваш VPN доступ активирован на {period_days} дней!</b>\n\n"
            f"📅 Подписка до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"{instructions}\n\n"
            f"<code>{config_text}</code>",
            parse_mode=ParseMode.HTML
        )

# ========== КЛАВИАТУРЫ ==========
def user_main_menu(has_active_vpn: bool = False, has_active_bot: bool = False):
    """Главное меню для пользователя"""
    buttons = [[types.KeyboardButton(text="🔐 Получить VPN")]]
    
    if has_active_vpn:
        buttons.append([types.KeyboardButton(text="📱 Мои VPN")])
    
    buttons.append([types.KeyboardButton(text="🤖 Создать бота")])
    
    if has_active_bot:
        buttons.append([types.KeyboardButton(text="⚙️ Мои боты")])
    
    buttons.append([types.KeyboardButton(text="🆘 Помощь")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    """Главное меню для админа"""
    buttons = [
        [types.KeyboardButton(text="🖥️ Серверы")],
        [types.KeyboardButton(text="👤 Пользователи")],
        [types.KeyboardButton(text="💰 Управление ценами")],
        [types.KeyboardButton(text="📊 Статистика")]
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
    
    prices = asyncio.run(get_vpn_prices())
    buttons.append([types.KeyboardButton(text=f"💎 Неделя - {prices['week']['stars']} stars")])
    buttons.append([types.KeyboardButton(text=f"💎 Месяц - {prices['month']['stars']} stars")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_device_keyboard():
    buttons = [
        [types.KeyboardButton(text="📱 Android (L2TP)")],
        [types.KeyboardButton(text="🍎 iOS (L2TP)")],
        [types.KeyboardButton(text="💻 WireGuard (рекомендуется)")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def bot_period_keyboard():
    """Клавиатура периодов для бота"""
    prices = asyncio.run(get_bot_prices())
    buttons = [
        [types.KeyboardButton(text=f"🤖 Неделя - {prices['week']['stars']} stars")],
        [types.KeyboardButton(text=f"🤖 Месяц - {prices['month']['stars']} stars")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_users_menu():
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="⏹️ Отключить VPN")],
        [types.KeyboardButton(text="📋 Список пользователей")],
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

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_device = State()

class UserBotStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_token = State()
    waiting_for_name = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()
    waiting_for_device = State()

class AdminPriceStates(StatesGroup):
    waiting_for_service = State()
    waiting_for_week_price = State()
    waiting_for_month_price = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    user_id = message.from_user.id
    
    # Проверяем активные подписки
    async with aiosqlite.connect(DB_PATH) as db:
        # VPN
        cursor = await db.execute("""
            SELECT COUNT(*) FROM vpn_users 
            WHERE user_id = ? AND is_active = 1 
            AND subscription_end > datetime('now')
        """, (user_id,))
        has_vpn = await cursor.fetchone()
        
        # Боты
        cursor = await db.execute("""
            SELECT COUNT(*) FROM user_bots 
            WHERE user_id = ? AND status = 'running'
        """, (user_id,))
        has_bot = await cursor.fetchone()
    
    if is_admin(user_id, message.chat.id):
        await message.answer(
            "👑 <b>Админ-панель</b>",
            reply_markup=admin_main_menu(),
            parse_mode=ParseMode.HTML
        )
    else:
        has_active_vpn = has_vpn[0] > 0 if has_vpn else False
        has_active_bot = has_bot[0] > 0 if has_bot else False
        
        await message.answer(
            "🚀 <b>Добро пожаловать!</b>\n\n"
            "Выберите услугу:",
            reply_markup=user_main_menu(has_active_vpn, has_active_bot),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    """Начало получения VPN"""
    user_id = message.from_user.id
    
    # Проверяем использовал ли пробный
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT trial_used FROM vpn_users WHERE user_id = ?",
            (user_id,)
        )
        user_data = await cursor.fetchone()
    
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
    
    prices = await get_vpn_prices()
    
    if "🎁" in message.text:
        period = "trial"
        days = 3
        stars = 0
    elif "Неделя" in message.text:
        period = "week"
        days = 7
        stars = prices["week"]["stars"]
    elif "Месяц" in message.text:
        period = "month"
        days = 30
        stars = prices["month"]["stars"]
    else:
        await message.answer("Выберите период из списка:")
        return
    
    # Сохраняем в состоянии
    await state.update_data(period=period, days=days, stars=stars)
    
    # Проверяем если это повторный выбор пробного
    if period == "trial":
        user_id = message.from_user.id
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
    
    await state.set_state(UserVPNStates.waiting_for_device)
    
    if stars > 0:
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
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO payments (user_id, amount_stars, period, status)
                    VALUES (?, ?, ?, 'pending')""",
                    (message.from_user.id, stars, period)
                )
                await db.commit()
            
        except Exception as e:
            logger.error(f"Ошибка создания инвойса: {e}")
            await message.answer(
                "❌ Ошибка создания счета.\n\n"
                "Для других способов оплаты напишите в @vpnbothost"
            )
            await state.clear()
    else:
        # Бесплатный пробный
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
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT trial_used FROM vpn_users WHERE user_id = ?",
                (user_id,)
            )
            user_data = await cursor.fetchone()
        
        has_used_trial = user_data and user_data[0]
        await message.answer(
            "Выберите период:",
            reply_markup=vpn_period_keyboard(show_trial=not has_used_trial)
        )
        return
    
    device_map = {
        "📱 Android (L2TP)": "android",
        "🍎 iOS (L2TP)": "ios",
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
        data['days']
    )
    
    if success:
        if data['period'] == 'trial':
            # Помечаем пробный как использованный
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?",
                    (message.from_user.id,)
                )
                await db.commit()
        
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
        "Ваш бот будет запущен в изолированном контейнере.\n"
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
    
    prices = await get_bot_prices()
    
    if "Неделя" in message.text:
        period = "week"
        days = 7
        stars = prices["week"]["stars"]
    elif "Месяц" in message.text:
        period = "month"
        days = 30
        stars = prices["month"]["stars"]
    else:
        await message.answer("Выберите период из списка:")
        return
    
    await state.update_data(period=period, days=days, stars=stars)
    await state.set_state(UserBotStates.waiting_for_name)
    
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
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO payments (user_id, amount_stars, period, status)
                VALUES (?, ?, ?, 'pending')""",
                (message.from_user.id, stars, period)
            )
            await db.commit()
        
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        await message.answer(
            "❌ Ошибка создания счета.\n\n"
            "Для других способов оплаты напишите в @vpnbothost"
        )
        await state.clear()

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
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE payments 
                SET status = 'completed' 
                WHERE user_id = ? AND period = ? AND status = 'pending'""",
                (user_id, period)
            )
            await db.commit()
        
        await message.answer(
            f"✅ <b>Оплата получена!</b>\n\n"
            f"{payment.total_amount // 100} stars успешно списаны.\n"
            f"Сейчас активирую услугу...",
            parse_mode=ParseMode.HTML
        )
        
        if service_type == "vpn":
            # Для VPN - запрашиваем устройство
            prices = await get_vpn_prices()
            days = prices.get(period, {}).get("days", 7)
            
            await message.answer(
                f"Вы оплатили VPN на {days} дней.\n"
                "Теперь выберите устройство:",
                reply_markup=vpn_device_keyboard()
            )
            
            # Сохраняем в состоянии
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.memory import MemoryStorage
            
            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=user_id)
            
            await state.set_state(UserVPNStates.waiting_for_device)
            await state.update_data(period=period, days=days, stars=payment.total_amount // 100)
        
        elif service_type == "bot":
            # Для бота - запрашиваем имя
            await message.answer(
                "Введите имя для вашего бота:",
                reply_markup=ReplyKeyboardRemove()
            )
            
            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=user_id)
            
            await state.set_state(UserBotStates.waiting_for_name)
            await state.update_data(
                period=period, 
                days=30 if period == "month" else 7,
                stars=payment.total_amount // 100
            )
    else:
        await message.answer("❌ Ошибка обработки платежа. Обратитесь в @vpnbothost")

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

@dp.message(F.text == "🛡️ VPN серверы")
async def admin_vpn_servers(message: Message):
    """Список VPN серверов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT id, name, server_ip, current_users, max_users, is_active 
            FROM servers WHERE server_type = 'vpn'
        """)
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("VPN серверы не добавлены")
        return
    
    text = "🛡️ <b>VPN серверы:</b>\n\n"
    
    for server in servers:
        server_id, name, ip, current, max_users, active = server
        status = "✅" if active else "⛔"
        
        # Проверяем статус
        status_info = await check_server_status(server_id)
        
        text += f"{status} <b>{name}</b>\n"
        text += f"   IP: {ip or 'нет'}\n"
        text += f"   Пользователи: {current}/{max_users}\n"
        
        if status_info["online"]:
            text += f"   🟢 Онлайн | Ping: {status_info['ping']}\n"
            text += f"   📊 Нагрузка: {status_info['load'] or '?'}\n"
        else:
            text += f"   🔴 Оффлайн\n"
        
        text += f"   ID: {server_id}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "🤖 Серверы для ботов")
async def admin_bot_servers(message: Message):
    """Список серверов для ботов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT s.id, s.name, s.server_ip, s.is_active, COUNT(ub.id) as bot_count
            FROM servers s
            LEFT JOIN user_bots ub ON s.id = ub.server_id
            WHERE s.server_type = 'bot'
            GROUP BY s.id
        """)
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("Серверы для ботов не добавлены")
        return
    
    text = "🤖 <b>Серверы для ботов:</b>\n\n"
    
    for server in servers:
        server_id, name, ip, active, bot_count = server
        status = "✅" if active else "⛔"
        
        status_info = await check_server_status(server_id)
        
        text += f"{status} <b>{name}</b>\n"
        text += f"   IP: {ip or 'нет'}\n"
        text += f"   Ботов: {bot_count}\n"
        
        if status_info["online"]:
            text += f"   🟢 Онлайн | Ping: {status_info['ping']}\n"
            text += f"   💾 Память: {status_info['memory'] or '?'}\n"
        else:
            text += f"   🔴 Оффлайн\n"
        
        text += f"   ID: {server_id}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

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
    await message.answer(
        "Введите @username или ID пользователя:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminUserStates.waiting_for_username)
async def admin_process_username(message: Message, state: FSMContext):
    """Обработка username от админа"""
    username = message.text.strip()
    user_id = None
    
    # Пытаемся найти пользователя
    if username.isdigit():
        user_id = int(username)
    elif username.startswith('@'):
        # Пока просто сохраняем
        await state.update_data(username=username)
        await message.answer(
            "Пользователь найден по username.\n"
            "Введите количество дней (3, 7, 30 или любое другое число):"
        )
        await state.set_state(AdminUserStates.waiting_for_period)
        return
    else:
        await message.answer("Введите @username или ID:")
        return
    
    if user_id:
        await state.update_data(user_id=user_id)
        await state.set_state(AdminUserStates.waiting_for_period)
        
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="3 дня")],
            [types.KeyboardButton(text="7 дней")],
            [types.KeyboardButton(text="30 дней")],
            [types.KeyboardButton(text="Другое")],
            [types.KeyboardButton(text="◀️ Назад")]
        ], resize_keyboard=True)
        
        await message.answer("Выберите период:", reply_markup=keyboard)

@dp.message(AdminUserStates.waiting_for_period)
async def admin_process_period(message: Message, state: FSMContext):
    """Обработка периода от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_username)
        await message.answer("Введите @username или ID:")
        return
    
    if message.text == "Другое":
        await message.answer("Введите количество дней:")
        return
    
    if message.text.endswith("дня") or message.text.endswith("дней"):
        days = int(message.text.split()[0])
    elif message.text.isdigit():
        days = int(message.text)
    else:
        await message.answer("Введите количество дней:")
        return
    
    await state.update_data(days=days)
    await state.set_state(AdminUserStates.waiting_for_device)
    
    await message.answer(
        "Выберите тип устройства:",
        reply_markup=vpn_device_keyboard()
    )

@dp.message(AdminUserStates.waiting_for_device)
async def admin_process_device(message: Message, state: FSMContext):
    """Обработка устройства от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_period)
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="3 дня")],
            [types.KeyboardButton(text="7 дней")],
            [types.KeyboardButton(text="30 дней")],
            [types.KeyboardButton(text="Другое")],
            [types.KeyboardButton(text="◀️ Назад")]
        ], resize_keyboard=True)
        await message.answer("Выберите период:", reply_markup=keyboard)
        return
    
    device_map = {
        "📱 Android (L2TP)": "android",
        "🍎 iOS (L2TP)": "ios",
        "💻 WireGuard (рекомендуется)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите устройство из списка:")
        return
    
    data = await state.get_data()
    user_id = data.get('user_id')
    days = data['days']
    device_type = device_map[message.text]
    
    if not user_id and 'username' in data:
        # Нужно найти пользователя по username
        username = data['username']
        await message.answer(f"Ищу пользователя {username}...")
        # Показываем инструкцию
        await message.answer(
            f"Для выдачи VPN пользователю {username}:\n\n"
            f"1. Напишите ему в ЛС\n"
            f"2. Попросите его написать этому боту @{(await bot.get_me()).username}\n"
            f"3. Затем выдайте VPN через меню"
        )
        await state.clear()
        return
    
    # Создаем VPN
    await message.answer(f"🔄 Выдаю VPN пользователю {user_id}...")
    
    success = await create_vpn_for_user(user_id, device_type, days)
    
    if success:
        await message.answer(
            f"✅ VPN успешно выдан пользователю {user_id} на {days} дней!",
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ Ошибка выдачи VPN пользователю {user_id}",
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "💰 Управление ценами")
async def admin_prices(message: Message):
    """Управление ценами"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    vpn_prices = await get_vpn_prices()
    bot_prices = await get_bot_prices()
    
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
    
    vpn_prices = await get_vpn_prices()
    
    await message.answer(
        f"Текущая цена за неделю: {vpn_prices['week']['stars']} stars\n\n"
        "Введите новую цену за неделю (в stars):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminPriceStates.waiting_for_week_price)
async def admin_process_week_price(message: Message, state: FSMContext):
    """Обработка цены за неделю"""
    try:
        week_price = int(message.text)
        await state.update_data(week_price=week_price)
        await state.set_state(AdminPriceStates.waiting_for_month_price)
        
        await message.answer(
            f"Цена за неделю: {week_price} stars\n"
            f"Цена за месяц будет автоматически: {week_price * 3} stars (3x недели)\n\n"
            "Подтвердите изменение цен (да/нет):"
        )
    except ValueError:
        await message.answer("Введите число:")

@dp.message(AdminPriceStates.waiting_for_month_price)
async def admin_process_month_price(message: Message, state: FSMContext):
    """Подтверждение изменения цен"""
    if message.text.lower() in ["да", "yes", "ok", "подтвердить"]:
        data = await state.get_data()
        service_type = data.get('service_type')
        week_price = data.get('week_price')
        
        if service_type == "vpn":
            await update_vpn_prices(week_price, week_price * 3)
            await message.answer(
                f"✅ Цены VPN обновлены!\n\n"
                f"Неделя: {week_price} stars\n"
                f"Месяц: {week_price * 3} stars",
                reply_markup=admin_main_menu()
            )
        elif service_type == "bot":
            await update_bot_prices(week_price, week_price * 3)
            await message.answer(
                f"✅ Цены ботов обновлены!\n\n"
                f"Неделя: {week_price} stars\n"
                f"Месяц: {week_price * 3} stars",
                reply_markup=admin_main_menu()
            )
    else:
        await message.answer("Изменение цен отменено", reply_markup=admin_main_menu())
    
    await state.clear()

@dp.message(F.text == "🤖 Цены ботов")
async def admin_bot_prices(message: Message, state: FSMContext):
    """Изменение цен ботов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await state.update_data(service_type="bot")
    
    bot_prices = await get_bot_prices()
    
    await message.answer(
        f"Текущая цена за неделю: {bot_prices['week']['stars']} stars\n\n"
        "Введите новую цену за неделю (в stars):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    """Статистика"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        # VPN статистика
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total_users,
                COUNT(CASE WHEN is_active = 1 AND subscription_end > datetime('now') THEN 1 END) as active_users,
                COUNT(CASE WHEN trial_used = 1 THEN 1 END) as trial_used
            FROM vpn_users
        """)
        vpn_stats = await cursor.fetchone()
        
        # Платежи
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total_payments,
                SUM(amount_stars) as total_stars,
                SUM(CASE WHEN period = 'week' THEN amount_stars ELSE 0 END) as week_stars,
                SUM(CASE WHEN period = 'month' THEN amount_stars ELSE 0 END) as month_stars
            FROM payments WHERE status = 'completed'
        """)
        payment_stats = await cursor.fetchone()
        
        # Серверы
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total_servers,
                COUNT(CASE WHEN server_type = 'vpn' THEN 1 END) as vpn_servers,
                COUNT(CASE WHEN server_type = 'bot' THEN 1 END) as bot_servers
            FROM servers
        """)
        server_stats = await cursor.fetchone()
    
    text = "📊 <b>Статистика системы:</b>\n\n"
    
    text += "<b>VPN:</b>\n"
    text += f"• Всего пользователей: {vpn_stats[0] or 0}\n"
    text += f"• Активных подписок: {vpn_stats[1] or 0}\n"
    text += f"• Использовали пробный: {vpn_stats[2] or 0}\n\n"
    
    text += "<b>Платежи:</b>\n"
    text += f"• Всего платежей: {payment_stats[0] or 0}\n"
    text += f"• Всего stars: {payment_stats[1] or 0}\n"
    text += f"• За недели: {payment_stats[2] or 0} stars\n"
    text += f"• За месяцы: {payment_stats[3] or 0} stars\n\n"
    
    text += "<b>Серверы:</b>\n"
    text += f"• Всего серверов: {server_stats[0] or 0}\n"
    text += f"• VPN серверов: {server_stats[1] or 0}\n"
    text += f"• Серверов для ботов: {server_stats[2] or 0}\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_handler(message: Message, state: FSMContext):
    """Обработчик кнопки назад"""
    await state.clear()
    
    if is_admin(message.from_user.id, message.chat.id):
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
    else:
        await cmd_start(message)

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_expired_subscriptions():
    """Проверка истекающих подписок"""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # VPN подписки
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
    await init_db()
    
    # Запускаем фоновые задачи
    asyncio.create_task(check_expired_subscriptions())
    
    me = await bot.get_me()
    logger.info(f"✅ Бот запущен: @{me.username}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"💬 Admin chat: {ADMIN_CHAT_ID}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())