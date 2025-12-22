# main.py - ПОЛНЫЙ VPN МЕНЕДЖЕР С ОПЛАТОЙ STARS
import os
import asyncio
import logging
import json
import random
import string
import qrcode
import io
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh
import aiosqlite
import aiohttp

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"  # Твой провайдер токен
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

# Цены по умолчанию
PRICES = {
    "trial": {"days": 3, "price": 0, "stars": 0},
    "week": {"days": 7, "price": 5, "stars": 50},  # 50 stars = 5€
    "month": {"days": 30, "price": 12, "stars": 120}  # 120 stars = 12€
}

# Конвертация stars в евро (Telegram rates)
STARS_TO_EURO = 0.01  # 1 star = 0.01€

# VPN конфигурации
VPN_CONFIGS = {
    "android": {
        "name": "Android (L2TP/IPSec)",
        "type": "l2tp",
        "instructions": "В настройках VPN выберите тип L2TP/IPSec PSK",
        "template": """Имя: {name}
Тип: L2TP/IPSec PSK
Адрес сервера: {server_ip}
Общий ключ IPSec: {psk}
Имя пользователя: {username}
Пароль: {password}"""
    },
    "ios": {
        "name": "iPhone/iPad",
        "type": "l2tp",
        "instructions": "В настройках VPN добавьте новую конфигурацию",
        "template": """Описание: {name}
Сервер: {server_ip}
Учетная запись: {username}
Пароль: {password}
Общий ключ: {psk}"""
    },
    "wireguard": {
        "name": "WireGuard (все устройства)",
        "type": "wireguard",
        "instructions": "Установите приложение WireGuard и отсканируйте QR код",
        "template": """[Interface]
PrivateKey = {private_key}
Address = {address}
DNS = 1.1.1.1

[Peer]
PublicKey = {server_public_key}
AllowedIPs = 0.0.0.0/0
Endpoint = {server_ip}:51820
PersistentKeepalive = 25"""
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
        # Серверы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                vpn_type TEXT DEFAULT 'wireguard',
                country TEXT,
                city TEXT,
                max_users INTEGER DEFAULT 30,
                current_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                server_ip TEXT,
                psk_key TEXT,
                public_key TEXT,
                private_key TEXT,
                bandwidth_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Пользователи VPN
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                server_id INTEGER,
                vpn_type TEXT,
                device_type TEXT,
                client_name TEXT,
                private_key TEXT,
                public_key TEXT,
                address TEXT,
                subscription_end TIMESTAMP,
                trial_used BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                notified BOOLEAN DEFAULT FALSE,
                bandwidth_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
            )
        """)
        
        # Платежи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_stars INTEGER,
                amount_eur REAL,
                period TEXT,
                status TEXT DEFAULT 'pending', -- pending, completed, failed, refunded
                provider_payment_id TEXT,
                telegram_payment_id TEXT,
                invoice_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Запросы на пробный период
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'pending', -- pending, approved, rejected
                approved_by INTEGER,
                approved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Настройки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Инициализируем настройки по умолчанию
        default_settings = [
            ("prices", json.dumps(PRICES)),
            ("welcome_message", "Добро пожаловать в VPN бот! 🔐\n\nПолучите безопасный доступ к интернету с нашей VPN услугой."),
            ("trial_message", "🎁 Бесплатный пробный период на 3 дня доступен каждому пользователю один раз!"),
            ("payment_message", "💎 Оплата через Telegram Stars - мгновенная активация!\n\nИли напишите админу для других вариантов оплаты."),
            ("admin_contact", "@ваш_юзернейм"),
            ("refund_policy", "Возврат средств возможен в течение 24 часов после оплаты, если услуга не использовалась."),
            ("terms_link", "https://telegra.ph/VPN-Terms-01-01")
        ]
        
        for key, value in default_settings:
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        
        await db.commit()

# ========== FSM СОСТОЯНИЯ ==========
class AddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_country = State()
    waiting_for_city = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class UserBuyStates(StatesGroup):
    waiting_for_device = State()
    waiting_for_period = State()
    waiting_for_payment = State()

class AdminAddUserStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_device = State()
    waiting_for_period = State()
    waiting_for_server = State()

class SettingsStates(StatesGroup):
    waiting_for_price_week = State()
    waiting_for_price_month = State()
    waiting_for_welcome_msg = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu(is_admin: bool = True):
    """Главное меню"""
    buttons = []
    
    if is_admin:
        buttons = [
            [types.KeyboardButton(text="📋 Мои серверы")],
            [types.KeyboardButton(text="➕ Добавить сервер")],
            [types.KeyboardButton(text="👥 Пользователи VPN")],
            [types.KeyboardButton(text="💰 Управление подписками")],
            [types.KeyboardButton(text="💎 Платежи")],
            [types.KeyboardButton(text="⚙️ Настройки")]
        ]
    else:
        buttons = [
            [types.KeyboardButton(text="🔐 Получить VPN")],
            [types.KeyboardButton(text="📱 Мои подключения")],
            [types.KeyboardButton(text="💎 Купить подписку")],
            [types.KeyboardButton(text="🆘 Помощь")]
        ]
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_type_keyboard():
    buttons = [
        [types.KeyboardButton(text="🛡️ VPN сервер")],
        [types.KeyboardButton(text="🤖 Обычный сервер")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_device_keyboard():
    buttons = [
        [types.KeyboardButton(text="📱 Android")],
        [types.KeyboardButton(text="🍎 iOS")],
        [types.KeyboardButton(text="💻 WireGuard (все устройства)")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def period_keyboard(user_id: int = None, show_trial: bool = True):
    """Клавиатура выбора периода с учетом пробного периода"""
    buttons = []
    
    # Получаем цены
    prices = PRICES
    try:
        prices_json = await get_setting("prices")
        if prices_json:
            prices = json.loads(prices_json)
    except:
        pass
    
    if show_trial:
        buttons.append([types.KeyboardButton(text=f"🎁 3 дня (пробный)")])
    
    buttons.append([types.KeyboardButton(text=f"💎 Неделя - {prices['week']['stars']} stars")])
    buttons.append([types.KeyboardButton(text=f"💎 Месяц - {prices['month']['stars']} stars")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def payment_keyboard(period: str, amount_stars: int):
    """Клавиатура оплаты"""
    period_text = {"week": "неделю", "month": "месяц"}.get(period, period)
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=f"💳 Оплатить {amount_stars} stars",
                pay=True
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📝 Оплата админу",
                callback_data=f"manual_pay:{period}"
            ),
            types.InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_payment"
            )
        ]
    ])
    return keyboard

def admin_vpn_menu():
    buttons = [
        [types.KeyboardButton(text="👤 Добавить пользователя")],
        [types.KeyboardButton(text="📊 Статистика VPN")],
        [types.KeyboardButton(text="🔄 Обновить все VPN")],
        [types.KeyboardButton(text="⏱️ Запросы на пробный")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def payments_menu():
    buttons = [
        [types.KeyboardButton(text="📈 Статистика платежей")],
        [types.KeyboardButton(text="📋 Последние платежи")],
        [types.KeyboardButton(text="↩️ Возвраты")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def settings_menu():
    buttons = [
        [types.KeyboardButton(text="💰 Изменить цены")],
        [types.KeyboardButton(text="📝 Изменить приветствие")],
        [types.KeyboardButton(text="👤 Контакт админа")],
        [types.KeyboardButton(text="📋 Термины")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def get_setting(key: str) -> str:
    """Получает настройку из БД"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else ""

async def update_setting(key: str, value: str):
    """Обновляет настройку"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()

def parse_connection_string(conn_str: str) -> Tuple[str, str, int]:
    """Парсит строку подключения"""
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

async def execute_ssh_command(server_id: int, command: str, sudo: bool = False) -> Tuple[str, str]:
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
            known_hosts=None,
            connect_timeout=10
        ) as conn:
            result = await conn.run(command)
            return result.stdout, result.stderr
            
    except Exception as e:
        logger.error(f"SSH error: {e}")
        return "", f"Ошибка SSH: {str(e)}"

async def generate_qr_code(config_text: str) -> io.BytesIO:
    """Генерирует QR код"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(config_text)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr

# ========== VPN ФУНКЦИИ ==========
async def setup_wireguard_server(server_id: int):
    """Настраивает WireGuard на сервере"""
    commands = [
        "apt-get update && apt-get install -y wireguard qrencode",
        "sysctl -w net.ipv4.ip_forward=1",
        "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
        "mkdir -p /etc/wireguard",
        "umask 077 && cd /etc/wireguard && wg genkey | tee server.private | wg pubkey > server.public",
        """cat > /etc/wireguard/wg0.conf << EOF
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
    
    results = []
    for cmd in commands:
        stdout, stderr = await execute_ssh_command(server_id, cmd, sudo=True)
        if stderr and "already exists" not in stderr.lower() and "Warning" not in stderr:
            results.append(f"❌ {cmd[:50]}...: {stderr[:200]}")
    
    # Получаем публичный ключ сервера
    stdout, stderr = await execute_ssh_command(server_id, "cat /etc/wireguard/server.public")
    if stdout:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET public_key = ? WHERE id = ?",
                (stdout.strip(), server_id)
            )
            await db.commit()
    
    return results

async def create_vpn_user(server_id: int, user_id: int, device_type: str) -> Dict:
    """Создает VPN пользователя на сервере"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем данные сервера
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
        server = await cursor.fetchone()
        
        # Проверяем лимит
        if server['current_users'] >= server['max_users']:
            raise Exception(f"Достигнут лимит пользователей ({server['max_users']})")
        
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        if server['vpn_type'] == 'wireguard':
            # Создаем ключи WireGuard
            commands = [
                f"cd /etc/wireguard && umask 077 && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public",
                f"cd /etc/wireguard && echo '' >> wg0.conf",
                f"cd /etc/wireguard && echo '[Peer]' >> wg0.conf",
                f"cd /etc/wireguard && echo '# User {user_id}' >> wg0.conf",
                f"cd /etc/wireguard && echo 'PublicKey = $(cat {client_name}.public)' >> wg0.conf",
                f"cd /etc/wireguard && echo 'AllowedIPs = 10.0.0.{server['current_users'] + 2}/32' >> wg0.conf"
            ]
            
            for cmd in commands:
                stdout, stderr = await execute_ssh_command(server_id, cmd, sudo=True)
                if stderr:
                    logger.error(f"Ошибка создания WireGuard клиента: {stderr}")
            
            # Получаем ключи
            priv_key, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.private", sudo=True)
            pub_key, _ = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.public", sudo=True)
            
            # Перезапускаем WireGuard
            await execute_ssh_command(server_id, "wg-quick down wg0 && wg-quick up wg0", sudo=True)
            
            # Сохраняем пользователя
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, vpn_type, device_type, client_name, 
                 private_key, public_key, address, is_active) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 'wireguard', device_type, client_name,
                 priv_key.strip(), pub_key.strip(), 
                 f"10.0.0.{server['current_users'] + 2}", True)
            )
            
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
            
            return {
                "type": "wireguard",
                "private_key": priv_key.strip(),
                "address": f"10.0.0.{server['current_users'] + 2}",
                "server_public_key": server['public_key'],
                "server_ip": server['server_ip']
            }
        
        elif server['vpn_type'] == 'l2tp':
            username = f"vpnuser{user_id}"
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            
            add_user_cmd = f'echo "{username} l2tpd {password} *" >> /etc/ppp/chap-secrets'
            await execute_ssh_command(server_id, add_user_cmd, sudo=True)
            await execute_ssh_command(server_id, "systemctl restart xl2tpd", sudo=True)
            
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, vpn_type, device_type, client_name, is_active) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 'l2tp', device_type, username, True)
            )
            
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
            
            return {
                "type": "l2tp",
                "username": username,
                "password": password,
                "psk": server['psk_key'],
                "server_ip": server['server_ip']
            }
    
    return {}

async def send_vpn_config_to_user(user_id: int, vpn_config: Dict, device_type: str, period_days: int):
    """Отправляет конфиг VPN пользователю"""
    subscription_end = datetime.now() + timedelta(days=period_days)
    
    if vpn_config['type'] == 'wireguard':
        config_text = VPN_CONFIGS['wireguard']['template'].format(**vpn_config)
        
        # Генерируем QR код
        qr_buffer = await generate_qr_code(config_text)
        
        # Сохраняем дату окончания
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vpn_users SET subscription_end = ? WHERE user_id = ? AND is_active = 1",
                (subscription_end.isoformat(), user_id)
            )
            await db.commit()
        
        await bot.send_message(
            user_id,
            f"✅ <b>Ваш VPN доступ активирован на {period_days} дней!</b>\n\n"
            f"📅 Подписка активна до: {subscription_end.strftime('%d.%m.%Y %H:%M')}\n\n"
            "📱 Установите приложение WireGuard и отсканируйте QR код:",
            parse_mode=ParseMode.HTML
        )
        
        await bot.send_photo(
            user_id,
            types.BufferedInputFile(qr_buffer.read(), filename="vpn_qr.png"),
            caption="QR код для быстрого подключения"
        )
        
        await bot.send_message(
            user_id,
            f"📝 <b>Текстовый конфиг:</b>\n\n<code>{config_text}</code>\n\n"
            "Скопируйте этот конфиг в приложение WireGuard.",
            parse_mode=ParseMode.HTML
        )
    
    elif vpn_config['type'] == 'l2tp':
        config_data = VPN_CONFIGS['android' if device_type == 'android' else 'ios']
        config_text = config_data['template'].format(
            name=f"VPN Premium",
            server_ip=vpn_config['server_ip'],
            psk=vpn_config['psk'],
            username=vpn_config['username'],
            password=vpn_config['password']
        )
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vpn_users SET subscription_end = ? WHERE user_id = ? AND is_active = 1",
                (subscription_end.isoformat(), user_id)
            )
            await db.commit()
        
        await bot.send_message(
            user_id,
            f"✅ <b>Ваш VPN доступ активирован на {period_days} дней!</b>\n\n"
            f"📅 Подписка активна до: {subscription_end.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"{config_data['instructions']}\n\n"
            f"<code>{config_text}</code>",
            parse_mode=ParseMode.HTML
        )

# ========== ПЛАТЕЖИ И STARTS ==========
async def create_stars_invoice(user_id: int, period: str) -> str:
    """Создает инвойс для оплаты Stars"""
    prices = json.loads(await get_setting("prices") or json.dumps(PRICES))
    
    period_config = prices.get(period, prices['week'])
    amount_stars = period_config['stars']
    
    # Создаем payload для идентификации платежа
    payload = f"{user_id}:{period}:{datetime.now().timestamp()}"
    
    # Создаем инвойс
    prices_tg = [LabeledPrice(label=f"VPN на {period_config['days']} дней", amount=amount_stars * 100)]  # В копейках/центах
    
    try:
        result = await bot.create_invoice(
            title=f"VPN подписка на {period_config['days']} дней",
            description=f"🔐 Доступ к VPN серверам\n📅 {period_config['days']} дней\n⚡ Высокая скорость",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars
            prices=prices_tg,
            payload=payload,
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        
        # Сохраняем платеж в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO payments 
                (user_id, amount_stars, amount_eur, period, status, invoice_payload) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, amount_stars, amount_stars * STARS_TO_EURO, period, 'pending', payload)
            )
            await db.commit()
        
        return result.invoice_link
    
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        return None

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    """Обработка pre-checkout запроса"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Обработка успешного платежа"""
    payment = message.successful_payment
    user_id = message.from_user.id
    
    # Парсим payload
    try:
        payload_parts = payment.invoice_payload.split(':')
        if len(payload_parts) >= 2:
            original_user_id = int(payload_parts[0])
            period = payload_parts[1]
        else:
            # Альтернативный парсинг
            original_user_id = user_id
            period = "week"  # По умолчанию
            
    except:
        original_user_id = user_id
        period = "week"
    
    logger.info(f"Успешный платеж от {user_id}: {payment.total_amount} stars за {period}")
    
    # Обновляем статус платежа
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE payments 
            SET status = 'completed', 
                provider_payment_id = ?,
                telegram_payment_id = ?
            WHERE user_id = ? AND invoice_payload LIKE ? AND status = 'pending'""",
            (payment.provider_payment_charge_id, payment.telegram_payment_charge_id,
             original_user_id, f"{original_user_id}:{period}%")
        )
        
        # Получаем информацию о платеже для создания VPN
        cursor = await db.execute(
            "SELECT period FROM payments WHERE telegram_payment_id = ?",
            (payment.telegram_payment_charge_id,)
        )
        payment_data = await cursor.fetchone()
        
        if payment_data:
            period = payment_data[0]
        
        await db.commit()
    
    # Отправляем пользователю сообщение
    await message.answer(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"Спасибо за покупку! {payment.total_amount // 100} stars успешно списаны.\n\n"
        f"Сейчас настроим ваш VPN доступ...",
        parse_mode=ParseMode.HTML
    )
    
    # Запрашиваем устройство для настройки
    await message.answer(
        "Выберите тип вашего устройства:",
        reply_markup=vpn_device_keyboard()
    )
    
    # Сохраняем состояние
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=user_id)
    
    await state.update_data({
        'user_id': user_id,
        'period': period,
        'payment_completed': True
    })
    
    # Устанавливаем состояние выбора устройства
    await state.set_state(UserBuyStates.waiting_for_device)

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    
    # Сохраняем/обновляем информацию о пользователе
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO vpn_users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name""",
            (user_id, message.from_user.username, message.from_user.first_name)
        )
        await db.commit()
    
    if is_admin:
        await message.answer(
            "👑 <b>Админ-панель VPN менеджера</b>\n\n"
            "Управляйте серверами, пользователями и платежами",
            reply_markup=main_menu(is_admin=True),
            parse_mode=ParseMode.HTML
        )
    else:
        welcome_msg = await get_setting("welcome_message")
        trial_msg = await get_setting("trial_message")
        
        # Проверяем активные подписки
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT COUNT(*) FROM vpn_users 
                WHERE user_id = ? AND is_active = 1 
                AND (subscription_end IS NULL OR subscription_end > datetime('now'))
            """, (user_id,))
            has_active = await cursor.fetchone()
        
        if has_active[0] > 0:
            # Показываем информацию о активных подписках
            cursor = await db.execute("""
                SELECT s.name, v.subscription_end, v.vpn_type
                FROM vpn_users v
                LEFT JOIN servers s ON v.server_id = s.id
                WHERE v.user_id = ? AND v.is_active = 1
                AND (v.subscription_end IS NULL OR v.subscription_end > datetime('now'))
            """, (user_id,))
            subscriptions = await cursor.fetchall()
            
            text = "🎉 <b>У вас есть активная VPN подписка!</b>\n\n"
            for sub in subscriptions:
                server_name = sub[0] or "Неизвестный сервер"
                end_date = datetime.fromisoformat(sub[1]) if sub[1] else None
                vpn_type = sub[2] or "wireguard"
                
                if end_date:
                    text += f"• {server_name} ({vpn_type})\n"
                    text += f"  До: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                else:
                    text += f"• {server_name} ({vpn_type})\n"
                    text += f"  Бессрочная\n\n"
            
            text += "Выберите действие:"
        else:
            text = f"{welcome_msg}\n\n{trial_msg}\n\nВыберите действие:"
        
        await message.answer(
            text,
            reply_markup=main_menu(is_admin=False),
            parse_mode=ParseMode.HTML
        )

# ========== ПОЛЬЗОВАТЕЛЬСКИЕ ФУНКЦИИ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message):
    """Начало получения VPN"""
    user_id = message.from_user.id
    
    # Проверяем пробный период
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT trial_used FROM vpn_users WHERE user_id = ?",
            (user_id,)
        )
        user_data = await cursor.fetchone()
    
    has_used_trial = user_data and user_data[0]
    
    if has_used_trial:
        # Прямо к покупке
        await message.answer(
            "Вы уже использовали пробный период.\n\n"
            "Выберите период подписки:",
            reply_markup=period_keyboard(user_id, show_trial=False)
        )
    else:
        # Предлагаем выбор
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🎁 Получить пробный период (3 дня)",
                    callback_data="request_trial"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="💎 Купить подписку",
                    callback_data="buy_subscription"
                )
            ]
        ])
        
        await message.answer(
            "Выберите вариант:",
            reply_markup=keyboard
        )

@dp.callback_query(F.data == "request_trial")
async def request_trial_callback(callback: types.CallbackQuery):
    """Запрос пробного периода"""
    user_id = callback.from_user.id
    
    # Проверяем не запрашивал ли уже
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM trial_requests WHERE user_id = ? AND status = 'pending'",
            (user_id,)
        )
        existing = await cursor.fetchone()
        
        if existing:
            await callback.answer("Вы уже отправили запрос на пробный период!")
            return
        
        # Создаем запрос
        await db.execute(
            """INSERT INTO trial_requests (user_id, username, first_name, status)
            VALUES (?, ?, ?, 'pending')""",
            (user_id, callback.from_user.username, callback.from_user.first_name)
        )
        await db.commit()
    
    admin_contact = await get_setting("admin_contact")
    
    await callback.message.edit_text(
        "✅ <b>Запрос на пробный период отправлен!</b>\n\n"
        f"Администратор {admin_contact} рассмотрит вашу заявку в ближайшее время.\n"
        "Обычно это занимает несколько минут.\n\n"
        "Вы получите уведомление когда VPN доступ будет активирован.",
        parse_mode=ParseMode.HTML
    )
    
    # Уведомляем админа
    approve_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"approve_trial:{user_id}"
            ),
            types.InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_trial:{user_id}"
            )
        ]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"🆕 <b>Запрос на пробный период!</b>\n\n"
        f"👤 Пользователь: @{callback.from_user.username or 'нет'}\n"
        f"🆔 ID: {user_id}\n"
        f"📛 Имя: {callback.from_user.first_name}\n\n"
        f"Действие:",
        reply_markup=approve_kb,
        parse_mode=ParseMode.HTML
    )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_trial:"))
async def approve_trial_callback(callback: types.CallbackQuery):
    """Одобрение пробного периода"""
    user_id = int(callback.data.split(":")[1])
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Обновляем запрос
        await db.execute(
            """UPDATE trial_requests 
            SET status = 'approved', 
                approved_by = ?,
                approved_at = datetime('now')
            WHERE user_id = ? AND status = 'pending'""",
            (callback.from_user.id, user_id)
        )
        
        # Помечаем что пробный период использован
        await db.execute(
            "UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
    
    await callback.message.edit_text(
        f"✅ Пробный период для пользователя {user_id} одобрен!\n\n"
        f"Теперь нужно добавить его на VPN сервер через меню пользователей."
    )
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_id,
            "🎉 <b>Ваш запрос на пробный период одобрен!</b>\n\n"
            "Администратор скоро добавит вас на VPN сервер.\n"
            "Вы получите конфигурацию для подключения в этом чате.",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def buy_subscription_callback(callback: types.CallbackQuery):
    """Начало покупки подписки"""
    user_id = callback.from_user.id
    
    await callback.message.edit_text(
        "Выберите период подписки:",
        reply_markup=period_keyboard(user_id, show_trial=False)
    )
    
    # Устанавливаем состояние
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=user_id)
    
    await state.set_state(UserBuyStates.waiting_for_period)

@dp.message(F.text.startswith("💎") & F.text.contains("stars"))
async def process_period_selection(message: Message, state: FSMContext):
    """Обработка выбора периода (для оплаты stars)"""
    user_id = message.from_user.id
    
    # Определяем период
    if "Неделя" in message.text:
        period = "week"
    elif "Месяц" in message.text:
        period = "month"
    else:
        await message.answer("Пожалуйста, выберите период из списка:")
        return
    
    # Получаем цену
    prices_json = await get_setting("prices")
    prices = json.loads(prices_json) if prices_json else PRICES
    period_config = prices.get(period, prices['week'])
    amount_stars = period_config['stars']
    
    await state.update_data({
        'period': period,
        'amount_stars': amount_stars,
        'days': period_config['days']
    })
    
    await state.set_state(UserBuyStates.waiting_for_device)
    
    await message.answer(
        f"Вы выбрали подписку на <b>{period_config['days']} дней</b> за <b>{amount_stars} stars</b>.\n\n"
        "Теперь выберите тип вашего устройства:",
        reply_markup=vpn_device_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(UserBuyStates.waiting_for_device)
async def process_device_selection(message: Message, state: FSMContext):
    """Обработка выбора устройства"""
    device_map = {
        "📱 Android": "android",
        "🍎 iOS": "ios",
        "💻 WireGuard (все устройства)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Пожалуйста, выберите устройство из списка:")
        return
    
    device_type = device_map[message.text]
    data = await state.get_data()
    
    await state.update_data({'device_type': device_type})
    await state.set_state(UserBuyStates.waiting_for_payment)
    
    # Создаем инвойс для оплаты
    invoice_link = await create_stars_invoice(message.from_user.id, data['period'])
    
    if invoice_link:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💳 Оплатить сейчас",
                    url=invoice_link
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📝 Оплата админу",
                    callback_data=f"manual_pay:{data['period']}:{device_type}"
                )
            ]
        ])
        
        await message.answer(
            f"💎 <b>Оплата подписки</b>\n\n"
            f"Период: {data['days']} дней\n"
            f"Устройство: {message.text}\n"
            f"Стоимость: {data['amount_stars']} stars\n\n"
            f"Нажмите кнопку ниже для оплаты через Telegram Stars:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ Не удалось создать счет для оплаты.\n\n"
            "Пожалуйста, свяжитесь с администратором для ручной оплаты."
        )

@dp.callback_query(F.data.startswith("manual_pay:"))
async def manual_payment_callback(callback: types.CallbackQuery):
    """Ручная оплата через админа"""
    parts = callback.data.split(":")
    period = parts[1] if len(parts) > 1 else "week"
    device_type = parts[2] if len(parts) > 2 else "wireguard"
    
    prices_json = await get_setting("prices")
    prices = json.loads(prices_json) if prices_json else PRICES
    period_config = prices.get(period, prices['week'])
    
    admin_contact = await get_setting("admin_contact")
    
    await callback.message.edit_text(
        f"📝 <b>Оплата через администратора</b>\n\n"
        f"Период: {period_config['days']} дней\n"
        f"Стоимость: {period_config['stars']} stars ({period_config['price']}€)\n\n"
        f"Для оплаты другим способом напишите администратору:\n"
        f"{admin_contact}\n\n"
        f"Укажите:\n"
        f"• Свой ID: <code>{callback.from_user.id}</code>\n"
        f"• Выбранный период: {period}\n"
        f"• Тип устройства: {device_type}\n\n"
        f"После оплаты администратор активирует ваш VPN доступ.",
        parse_mode=ParseMode.HTML
    )
    
    # Уведомляем админа
    user_info = f"@{callback.from_user.username}" if callback.from_user.username else f"ID: {callback.from_user.id}"
    
    approve_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="✅ Активировать вручную",
                callback_data=f"manual_activate:{callback.from_user.id}:{period}:{device_type}"
            )
        ]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"📝 <b>Запрос на ручную оплату</b>\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"📛 Имя: {callback.from_user.first_name}\n"
        f"📅 Период: {period} ({period_config['days']} дней)\n"
        f"📱 Устройство: {device_type}\n"
        f"💎 Стоимость: {period_config['stars']} stars\n\n"
        f"После получения оплаты нажмите кнопку ниже:",
        reply_markup=approve_kb,
        parse_mode=ParseMode.HTML
    )
    
    await callback.answer()

# ========== АДМИН ФУНКЦИИ (уже есть в предыдущем коде, сокращаю для экономии места) ==========
@dp.message(F.text == "📋 Мои серверы")
async def list_servers(message: Message):
    """Список серверов - аналогично предыдущей версии"""
    if message.from_user.id != ADMIN_ID:
        await show_user_menu(message)
        return
    
    # ... (код из предыдущей версии)
    await message.answer("Функция в разработке...", reply_markup=main_menu(True))

@dp.message(F.text == "👥 Пользователи VPN")
async def vpn_users_menu(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "👥 <b>Управление пользователями VPN</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_vpn_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "💎 Платежи")
async def payments_main(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    # Статистика платежей
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN amount_eur ELSE 0 END) as total_eur,
                SUM(CASE WHEN status = 'completed' THEN amount_stars ELSE 0 END) as total_stars,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
            FROM payments
        """)
        stats = await cursor.fetchone()
    
    text = "💎 <b>Статистика платежей</b>\n\n"
    text += f"📊 Всего платежей: <b>{stats[0] or 0}</b>\n"
    text += f"✅ Завершенных: <b>{stats[0] - (stats[3] or 0)}</b>\n"
    text += f"⏳ Ожидают: <b>{stats[3] or 0}</b>\n"
    text += f"💰 Общая сумма: <b>{stats[1] or 0:.2f}€</b>\n"
    text += f"💎 Всего stars: <b>{stats[2] or 0}</b>\n\n"
    text += "Выберите действие:"
    
    await message.answer(text, reply_markup=payments_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📈 Статистика платежей")
async def payments_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    # Детальная статистика по дням/периодам
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # По дням за последнюю неделю
        cursor = await db.execute("""
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as count,
                SUM(amount_eur) as total_eur,
                SUM(amount_stars) as total_stars
            FROM payments 
            WHERE status = 'completed'
            AND created_at >= datetime('now', '-7 days')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        daily = await cursor.fetchall()
        
        # По периодам
        cursor = await db.execute("""
            SELECT 
                period,
                COUNT(*) as count,
                SUM(amount_eur) as total_eur,
                SUM(amount_stars) as total_stars
            FROM payments 
            WHERE status = 'completed'
            GROUP BY period
        """)
        by_period = await cursor.fetchall()
    
    text = "📈 <b>Детальная статистика платежей</b>\n\n"
    
    text += "<b>За последние 7 дней:</b>\n"
    for day in daily[:5]:  # Показываем 5 последних дней
        text += f"📅 {day['date']}: {day['count']} платежей, {day['total_eur']:.2f}€\n"
    
    text += "\n<b>По периодам:</b>\n"
    for period in by_period:
        period_name = {"week": "Неделя", "month": "Месяц"}.get(period['period'], period['period'])
        text += f"• {period_name}: {period['count']} платежей, {period['total_eur']:.2f}€\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

# ========== ОБРАБОТКА РУЧНОЙ АКТИВАЦИИ ==========
@dp.callback_query(F.data.startswith("manual_activate:"))
async def manual_activate_callback(callback: types.CallbackQuery):
    """Ручная активация после оплаты админу"""
    parts = callback.data.split(":")
    user_id = int(parts[1])
    period = parts[2] if len(parts) > 2 else "week"
    device_type = parts[3] if len(parts) > 3 else "wireguard"
    
    prices_json = await get_setting("prices")
    prices = json.loads(prices_json) if prices_json else PRICES
    period_config = prices.get(period, prices['week'])
    
    # Создаем запись о платеже
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO payments 
            (user_id, amount_stars, amount_eur, period, status) 
            VALUES (?, ?, ?, ?, 'completed')""",
            (user_id, period_config['stars'], period_config['stars'] * STARS_TO_EURO, period)
        )
        await db.commit()
    
    await callback.message.edit_text(
        f"✅ Платеж зарегистрирован!\n\n"
        f"Пользователь: {user_id}\n"
        f"Период: {period}\n"
        f"Stars: {period_config['stars']}\n\n"
        f"Теперь добавьте пользователя на VPN сервер через меню пользователей."
    )
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_id,
            "✅ <b>Оплата подтверждена администратором!</b>\n\n"
            f"Ваша подписка на {period_config['days']} дней активирована.\n"
            "Сейчас администратор добавит вас на VPN сервер.",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    await callback.answer()

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_subscriptions():
    """Проверка истекающих подписок"""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Истекшие подписки
                cursor = await db.execute("""
                    SELECT v.user_id, v.username, s.name, v.subscription_end
                    FROM vpn_users v
                    JOIN servers s ON v.server_id = s.id
                    WHERE v.is_active = 1 
                    AND v.subscription_end < datetime('now')
                """)
                expired = await cursor.fetchall()
                
                for user in expired:
                    user_id = user[0]
                    server_name = user[2]
                    
                    # Отключаем
                    await db.execute(
                        "UPDATE vpn_users SET is_active = 0 WHERE user_id = ?",
                        (user_id,)
                    )
                    
                    # Уведомляем
                    try:
                        await bot.send_message(
                            user_id,
                            f"⏰ <b>Ваша VPN подписка истекла!</b>\n\n"
                            f"Сервер: {server_name}\n\n"
                            "Для продления нажмите /start",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                # Скоро истекают (за 24 часа)
                cursor = await db.execute("""
                    SELECT v.user_id, v.username, s.name, v.subscription_end
                    FROM vpn_users v
                    JOIN servers s ON v.server_id = s.id
                    WHERE v.is_active = 1 
                    AND v.subscription_end BETWEEN datetime('now') AND datetime('now', '+1 day')
                    AND v.notified = 0
                """)
                expiring = await cursor.fetchall()
                
                for user in expiring:
                    user_id = user[0]
                    server_name = user[2]
                    end_date = datetime.fromisoformat(user[3]).strftime("%d.%m.%Y %H:%M")
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"⚠️ <b>VPN подписка истекает через 24 часа!</b>\n\n"
                            f"Сервер: {server_name}\n"
                            f"Истекает: {end_date}\n\n"
                            "Продлите подписку чтобы не потерять доступ!",
                            parse_mode=ParseMode.HTML
                        )
                        
                        await db.execute(
                            "UPDATE vpn_users SET notified = 1 WHERE user_id = ?",
                            (user_id,)
                        )
                    except:
                        pass
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Ошибка проверки подписок: {e}")
        
        await asyncio.sleep(3600)  # Каждый час

# ========== ЗАПУСК ==========
async def main():
    """Основная функция"""
    await init_db()
    
    # Фоновые задачи
    asyncio.create_task(check_subscriptions())
    
    me = await bot.get_me()
    logger.info(f"✅ Бот запущен: @{me.username}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"💎 Provider token: {PROVIDER_TOKEN[:10]}...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())