# main.py - ПОЛНОСТЬЮ АВТОМАТИЧЕСКИЙ VPN + БОТ МЕНЕДЖЕР
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
ADMIN_CHAT_ID = -1003542769962  # Твой админ чат
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

# VPN цены в Stars
VPN_PRICES = {
    "trial": {"days": 3, "stars": 0},
    "week": {"days": 7, "stars": 50},
    "month": {"days": 30, "stars": 120},
    "unlimited": {"days": 36500, "stars": 0}  # ~100 лет
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
        # Серверы (VPN и для ботов)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                server_type TEXT DEFAULT 'vpn',  -- 'vpn' или 'bot'
                country TEXT,
                city TEXT,
                max_users INTEGER DEFAULT 30,
                current_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                server_ip TEXT,
                public_key TEXT,  -- Для WireGuard
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
                client_name TEXT,
                private_key TEXT,
                public_key TEXT,
                address TEXT,
                subscription_end TIMESTAMP,
                trial_used BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id)
            )
        """)
        
        # Платежи VPN
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vpn_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_stars INTEGER,
                period TEXT,
                status TEXT DEFAULT 'completed',
                telegram_payment_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Боты (другие Telegram боты)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                server_id INTEGER,
                repo_url TEXT,
                status TEXT DEFAULT 'stopped',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id)
            )
        """)
        
        await db.commit()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    """Проверяет является ли пользователь админом"""
    return user_id == ADMIN_ID or (chat_id == ADMIN_CHAT_ID)

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

async def setup_wireguard_on_server(server_id: int):
    """Настраивает WireGuard на сервере если еще не настроен"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT public_key FROM servers WHERE id = ?", (server_id,))
        server = await cursor.fetchone()
        
        if server[0]:  # Уже настроен
            return True
    
    # Настраиваем WireGuard
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
    
    try:
        user, host, port = parse_connection_string(await get_server_connection(server_id))
        ssh_key = await get_server_ssh_key(server_id)
        
        for cmd in commands:
            async with asyncssh.connect(
                host,
                username=user,
                port=port,
                client_keys=[asyncssh.import_private_key(ssh_key)],
                known_hosts=None,
                connect_timeout=10
            ) as conn:
                await conn.run(cmd)
        
        # Получаем публичный ключ
        async with asyncssh.connect(
            host,
            username=user,
            port=port,
            client_keys=[asyncssh.import_private_key(ssh_key)],
            known_hosts=None,
            connect_timeout=10
        ) as conn:
            result = await conn.run("cat /etc/wireguard/server.public")
            public_key = result.stdout.strip()
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET public_key = ? WHERE id = ?",
                    (public_key, server_id)
                )
                await db.commit()
        
        return True
    except Exception as e:
        logger.error(f"Ошибка настройки WireGuard: {e}")
        return False

async def create_vpn_user_auto(user_id: int, device_type: str, period_days: int) -> bool:
    """Автоматически создает VPN пользователя"""
    server_id = await get_available_vpn_server()
    if not server_id:
        return False
    
    # Настраиваем сервер если нужно
    if not await setup_wireguard_on_server(server_id):
        return False
    
    # Создаем клиента WireGuard
    client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
    
    try:
        user, host, port = parse_connection_string(await get_server_connection(server_id))
        ssh_key = await get_server_ssh_key(server_id)
        
        # Создаем ключи
        async with asyncssh.connect(
            host,
            username=user,
            port=port,
            client_keys=[asyncssh.import_private_key(ssh_key)],
            known_hosts=None,
            connect_timeout=10
        ) as conn:
            # Генерируем ключи
            await conn.run(f"cd /etc/wireguard && umask 077 && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public")
            
            # Добавляем в конфиг
            add_peer_cmd = f"""echo '' >> /etc/wireguard/wg0.conf &&
echo '[Peer]' >> /etc/wireguard/wg0.conf &&
echo '# User {user_id}' >> /etc/wireguard/wg0.conf &&
echo 'PublicKey = $(cat {client_name}.public)' >> /etc/wireguard/wg0.conf &&
echo 'AllowedIPs = 10.0.0.$((2 + $(grep -c \"^\\[Peer\\]\" /etc/wireguard/wg0.conf)))/32' >> /etc/wireguard/wg0.conf"""
            
            await conn.run(add_peer_cmd)
            
            # Перезапускаем WireGuard
            await conn.run("wg-quick down wg0 && wg-quick up wg0")
            
            # Получаем ключи
            priv_result = await conn.run(f"cat /etc/wireguard/{client_name}.private")
            pub_result = await conn.run(f"cat /etc/wireguard/{client_name}.public")
            
            private_key = priv_result.stdout.strip()
            public_key = pub_result.stdout.strip()
            
            # Получаем адрес
            peer_count = await conn.run("grep -c \"^\\[Peer\\]\" /etc/wireguard/wg0.conf || echo 0")
            address = f"10.0.0.{int(peer_count.stdout.strip()) + 1}"
        
        # Получаем публичный ключ сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT public_key, server_ip FROM servers WHERE id = ?", (server_id,))
            server_data = await cursor.fetchone()
            server_public_key = server_data[0]
            server_ip = server_data[1]
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, vpn_type, device_type, client_name, 
                 private_key, public_key, address, subscription_end, is_active) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 'wireguard', device_type, client_name,
                 private_key, public_key, address, 
                 (datetime.now() + timedelta(days=period_days)).isoformat(),
                 True)
            )
            
            # Обновляем счетчик пользователей
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
        
        # Отправляем конфиг пользователю
        config_text = f"""[Interface]
PrivateKey = {private_key}
Address = {address}
DNS = 1.1.1.1

[Peer]
PublicKey = {server_public_key}
AllowedIPs = 0.0.0.0/0
Endpoint = {server_ip}:51820
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
            f"📅 Подписка активна до: {(datetime.now() + timedelta(days=period_days)).strftime('%d.%m.%Y %H:%M')}\n\n"
            f"📱 Установите приложение WireGuard и отсканируйте QR код:",
            parse_mode=ParseMode.HTML
        )
        
        await bot.send_photo(
            user_id,
            types.BufferedInputFile(img_bytes.read(), filename="vpn_qr.png"),
            caption="QR код для быстрого подключения"
        )
        
        await bot.send_message(
            user_id,
            f"📝 <b>Текстовый конфиг:</b>\n\n<code>{config_text}</code>\n\n"
            "Скопируйте этот конфиг в приложение WireGuard.",
            parse_mode=ParseMode.HTML
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка создания VPN пользователя: {e}")
        return False

async def get_server_connection(server_id: int) -> str:
    """Получает строку подключения сервера"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT connection_string FROM servers WHERE id = ?", (server_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_server_ssh_key(server_id: int) -> str:
    """Получает SSH ключ сервера"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT ssh_key FROM servers WHERE id = ?", (server_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

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
        raise ValueError("Неправильный формат подключения")

# ========== КЛАВИАТУРЫ ==========
def user_main_menu(has_active_vpn: bool = False):
    """Главное меню для пользователя"""
    buttons = [[types.KeyboardButton(text="🔐 Получить VPN")]]
    
    if has_active_vpn:
        buttons.append([types.KeyboardButton(text="📱 Мои подключения")])
    
    buttons.append([types.KeyboardButton(text="🆘 Помощь")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    """Главное меню для админа"""
    buttons = [
        [types.KeyboardButton(text="📋 Мои серверы")],
        [types.KeyboardButton(text="➕ Добавить сервер")],
        [types.KeyboardButton(text="👤 Управление пользователями")],
        [types.KeyboardButton(text="🤖 Управление ботами")],
        [types.KeyboardButton(text="💰 Платежи")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_period_keyboard(show_trial: bool = True):
    """Клавиатура выбора периода VPN"""
    buttons = []
    
    if show_trial:
        buttons.append([types.KeyboardButton(text="🎁 3 дня (пробный)")])
    
    buttons.append([types.KeyboardButton(text="💎 Неделя - 50 stars")])
    buttons.append([types.KeyboardButton(text="💎 Месяц - 120 stars")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_device_keyboard():
    """Клавиатура выбора устройства"""
    buttons = [
        [types.KeyboardButton(text="📱 Android (L2TP)")],
        [types.KeyboardButton(text="🍎 iOS (L2TP)")],
        [types.KeyboardButton(text="💻 WireGuard (все устройства)")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_user_management_keyboard():
    """Клавиатура управления пользователями для админа"""
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN бесплатно")],
        [types.KeyboardButton(text="⏹️ Отключить VPN")],
        [types.KeyboardButton(text="📊 Статистика")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_bot_management_keyboard():
    """Клавиатура управления ботами для админа"""
    buttons = [
        [types.KeyboardButton(text="🤖 Список ботов")],
        [types.KeyboardButton(text="➕ Добавить бота")],
        [types.KeyboardButton(text="🔄 Обновить бота")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_device = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()
    waiting_for_device = State()

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    user_id = message.from_user.id
    
    # Проверяем активные VPN
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT COUNT(*) FROM vpn_users 
            WHERE user_id = ? AND is_active = 1 
            AND subscription_end > datetime('now')
        """, (user_id,))
        has_active = await cursor.fetchone()
    
    if is_admin(user_id, message.chat.id):
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu())
    else:
        has_active_vpn = has_active[0] > 0 if has_active else False
        await message.answer(
            "🔐 <b>VPN Бот</b>\n\n"
            "Получите безопасный доступ к интернету!",
            reply_markup=user_main_menu(has_active_vpn),
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
            "Вы уже использовали пробный период.\nВыберите период подписки:",
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
        stars = 0
    elif "Неделя" in message.text:
        period = "week"
        days = 7
        stars = 50
    elif "Месяц" in message.text:
        period = "month"
        days = 30
        stars = 120
    else:
        await message.answer("Выберите период из списка:")
        return
    
    await state.update_data(period=period, days=days, stars=stars)
    await state.set_state(UserVPNStates.waiting_for_device)
    
    if stars > 0:
        # Создаем инвойс для оплаты
        payload = f"{message.from_user.id}:{period}:{int(datetime.now().timestamp())}"
        
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN на {days} дней",
                description=f"Доступ к VPN серверам на {days} дней",
                payload=payload,
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label=f"VPN {days} дней", amount=stars * 100)],
                start_parameter="vpn_subscription"
            )
            
            # Сохраняем платеж
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO vpn_payments (user_id, amount_stars, period)
                    VALUES (?, ?, ?)""",
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
        await message.answer("Выберите период:", reply_markup=vpn_period_keyboard(show_trial=True))
        return
    
    device_map = {
        "📱 Android (L2TP)": "android",
        "🍎 iOS (L2TP)": "ios",
        "💻 WireGuard (все устройства)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите устройство из списка:")
        return
    
    device_type = device_map[message.text]
    data = await state.get_data()
    
    # Создаем VPN пользователя
    await message.answer("🔄 Создаю ваш VPN доступ...")
    
    success = await create_vpn_user_auto(
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

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    """Команда помощи"""
    await message.answer(
        "🆘 <b>Помощь</b>\n\n"
        "• Для получения VPN нажмите '🔐 Получить VPN'\n"
        "• Пробный период: 3 дня (один раз)\n"
        "• Проблемы с оплатой: @vpnbothost\n"
        "• Техподдержка: @vpnbothost\n\n"
        "Мы всегда готовы помочь!",
        parse_mode=ParseMode.HTML
    )

# ========== ОБРАБОТКА ПЛАТЕЖЕЙ ==========
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    """Подтверждение платежа"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Успешный платеж - автоматическая активация"""
    payment = message.successful_payment
    user_id = message.from_user.id
    
    logger.info(f"Успешный платеж: {payment.total_amount} stars от {user_id}")
    
    # Определяем период из суммы
    stars = payment.total_amount // 100
    
    if stars == 50:
        period = "week"
        days = 7
    elif stars == 120:
        period = "month"
        days = 30
    else:
        period = "week"
        days = 7
    
    await message.answer(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"{stars} stars успешно списаны.\n"
        f"Сейчас создам ваш VPN доступ...",
        parse_mode=ParseMode.HTML
    )
    
    # Автоматически создаем VPN
    success = await create_vpn_user_auto(user_id, "wireguard", days)
    
    if success:
        await message.answer(
            "🎉 <b>VPN доступ успешно создан!</b>\n\n"
            "Конфигурация отправлена вам в чат.\n"
            "Спасибо за покупку!",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ <b>Ошибка создания VPN!</b>\n\n"
            "Пожалуйста, напишите в @vpnbothost\n"
            "Мы вернем средства или решим проблему.",
            parse_mode=ParseMode.HTML
        )

# ========== АДМИН ФУНКЦИИ ==========
@dp.message(F.text == "📋 Мои серверы")
async def admin_list_servers(message: Message):
    """Список серверов для админа"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM servers ORDER BY server_type, name")
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("Серверы не добавлены")
        return
    
    text = "📋 <b>Ваши серверы:</b>\n\n"
    
    vpn_servers = [s for s in servers if s[4] == 'vpn']
    bot_servers = [s for s in servers if s[4] == 'bot']
    
    if vpn_servers:
        text += "<b>VPN серверы:</b>\n"
        for server in vpn_servers:
            text += f"🛡️ <b>{server[1]}</b>\n"
            text += f"   Пользователи: {server[8]}/{server[7]}\n"
            text += f"   IP: {server[10] or 'нет'}\n"
            text += f"   ID: {server[0]}\n\n"
    
    if bot_servers:
        text += "<b>Серверы для ботов:</b>\n"
        for server in bot_servers:
            text += f"🤖 <b>{server[1]}</b>\n"
            text += f"   ID: {server[0]}\n"
            text += f"   Подключение: {server[3][:30]}...\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

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
        "Введите имя сервера:\n"
        f"Пример: {'VPS Германия/Франкфурт' if server_type == 'vpn' else 'Bot-Host-1'}",
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
        "Формат: <code>user@host:port</code>\n"
        "Пример: <code>opc@193.122.8.29</code>",
        parse_mode=ParseMode.HTML
    )

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection(message: Message, state: FSMContext):
    """Обработка подключения и сохранение сервера"""
    data = await state.get_data()
    
    try:
        user, host, port = parse_connection_string(message.text)
    except ValueError as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        return
    
    # Сохраняем сервер
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO servers 
            (name, ssh_key, connection_string, server_type, server_ip) 
            VALUES (?, ?, ?, ?, ?)""",
            (data['server_name'], data['ssh_key'], message.text, 
             data['server_type'], host)
        )
        await db.commit()
    
    server_type_name = "VPN" if data['server_type'] == 'vpn' else "ботов"
    await message.answer(
        f"✅ Сервер для {server_type_name} <b>{data['server_name']}</b> добавлен!",
        parse_mode=ParseMode.HTML
    )
    
    await state.clear()
    await message.answer("Админ-панель:", reply_markup=admin_main_menu())

@dp.message(F.text == "👤 Управление пользователями")
async def admin_user_management(message: Message):
    """Управление пользователями"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "👤 <b>Управление пользователями VPN</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_user_management_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "🎁 Выдать VPN бесплатно")
async def admin_give_vpn_free(message: Message, state: FSMContext):
    """Выдача VPN бесплатно"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer(
        "Введите @username пользователя или его ID:",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminUserStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    """Обработка username"""
    username = message.text.strip()
    user_id = None
    
    # Пытаемся определить ID
    if username.isdigit():
        user_id = int(username)
    elif username.startswith('@'):
        # Нужно будет искать в базе или использовать другой метод
        # Пока просто сохраняем как строку
        await state.update_data(username=username)
    else:
        await message.answer("Введите @username или ID:")
        return
    
    if user_id:
        await state.update_data(user_id=user_id)
        await state.set_state(AdminUserStates.waiting_for_period)
        
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="🎁 3 дня (пробный)")],
            [types.KeyboardButton(text="⏳ Неделя")],
            [types.KeyboardButton(text="📅 Месяц")],
            [types.KeyboardButton(text="♾️ Безлимит")],
            [types.KeyboardButton(text="◀️ Назад")]
        ], resize_keyboard=True)
        
        await message.answer("Выберите период:", reply_markup=keyboard)

@dp.message(AdminUserStates.waiting_for_period)
async def process_admin_period(message: Message, state: FSMContext):
    """Обработка периода от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_username)
        await message.answer("Введите @username или ID:")
        return
    
    period_map = {
        "🎁 3 дня (пробный)": 3,
        "⏳ Неделя": 7,
        "📅 Месяц": 30,
        "♾️ Безлимит": 36500
    }
    
    if message.text not in period_map:
        await message.answer("Выберите период из списка:")
        return
    
    days = period_map[message.text]
    await state.update_data(days=days)
    await state.set_state(AdminUserStates.waiting_for_device)
    
    await message.answer(
        "Выберите тип устройства:",
        reply_markup=vpn_device_keyboard()
    )

@dp.message(AdminUserStates.waiting_for_device)
async def process_admin_device(message: Message, state: FSMContext):
    """Обработка устройства от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_period)
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="🎁 3 дня (пробный)")],
            [types.KeyboardButton(text="⏳ Неделя")],
            [types.KeyboardButton(text="📅 Месяц")],
            [types.KeyboardButton(text="♾️ Безлимит")],
            [types.KeyboardButton(text="◀️ Назад")]
        ], resize_keyboard=True)
        await message.answer("Выберите период:", reply_markup=keyboard)
        return
    
    device_map = {
        "📱 Android (L2TP)": "android",
        "🍎 iOS (L2TP)": "ios",
        "💻 WireGuard (все устройства)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите устройство из списка:")
        return
    
    data = await state.get_data()
    user_id = data.get('user_id')
    days = data['days']
    device_type = device_map[message.text]
    
    if not user_id:
        await message.answer("❌ Не удалось определить ID пользователя")
        await state.clear()
        return
    
    # Создаем VPN
    await message.answer(f"🔄 Выдаю VPN пользователю {user_id}...")
    
    success = await create_vpn_user_auto(user_id, device_type, days)
    
    if success:
        await message.answer(
            f"✅ VPN успешно выдан пользователю {user_id} на {days} дней!",
            reply_markup=admin_main_menu()
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Вам выдан VPN доступ на {days} дней!</b>\n\n"
                "Конфигурация отправлена вам в чат.",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    else:
        await message.answer(
            f"❌ Ошибка выдачи VPN пользователю {user_id}",
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "⏹️ Отключить VPN")
async def admin_disable_vpn(message: Message):
    """Отключение VPN пользователя"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "Введите @username или ID пользователя для отключения VPN:",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Здесь нужно добавить FSM для обработки

@dp.message(F.text == "🤖 Управление ботами")
async def admin_bot_management(message: Message):
    """Управление ботами"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "🤖 <b>Управление ботами</b>\n\n"
        "Здесь можно добавлять и управлять другими Telegram ботами\n"
        "на ваших серверах.",
        reply_markup=admin_bot_management_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "💰 Платежи")
async def admin_payments(message: Message):
    """Статистика платежей"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT COUNT(*), SUM(amount_stars) 
            FROM vpn_payments WHERE status = 'completed'
        """)
        stats = await cursor.fetchone()
        
        cursor = await db.execute("""
            SELECT user_id, amount_stars, period, created_at 
            FROM vpn_payments 
            ORDER BY created_at DESC LIMIT 10
        """)
        recent = await cursor.fetchall()
    
    text = "💰 <b>Статистика платежей</b>\n\n"
    text += f"Всего платежей: <b>{stats[0] or 0}</b>\n"
    text += f"Всего stars: <b>{stats[1] or 0}</b>\n\n"
    
    text += "<b>Последние платежи:</b>\n"
    for payment in recent:
        date = datetime.fromisoformat(payment[3]).strftime("%d.%m %H:%M")
        text += f"• {payment[0]}: {payment[1]} stars ({payment[2]}) - {date}\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_to_main(message: Message, state: FSMContext):
    """Возврат в главное меню"""
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
                # Находим подписки которые истекают через 24 часа
                cursor = await db.execute("""
                    SELECT user_id, username, subscription_end
                    FROM vpn_users 
                    WHERE is_active = 1 
                    AND subscription_end BETWEEN datetime('now') AND datetime('now', '+1 day')
                """)
                expiring = await cursor.fetchall()
                
                for user in expiring:
                    user_id = user[0]
                    end_date = datetime.fromisoformat(user[2]).strftime("%d.%m.%Y %H:%M")
                    
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
                
                # Отключаем истекшие
                cursor = await db.execute("""
                    SELECT user_id FROM vpn_users 
                    WHERE is_active = 1 
                    AND subscription_end < datetime('now')
                """)
                expired = await cursor.fetchall()
                
                for user in expired:
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
    logger.info(f"💎 Provider token: {PROVIDER_TOKEN}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())