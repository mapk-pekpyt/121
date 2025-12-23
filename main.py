# main.py - УПРОЩЕННЫЙ И ИСПРАВЛЕННЫЙ КОД
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"

# Создаем директорию /data если ее нет
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"✅ Создана директория: {DATA_DIR}")

# ПУТЬ К БАЗЕ ДАННЫХ
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Пытаемся добавить файловый логгер
try:
    file_handler = logging.FileHandler(os.path.join(DATA_DIR, "bot.log"))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    print(f"✅ Файл логов: {os.path.join(DATA_DIR, 'bot.log')}")
except Exception as e:
    print(f"⚠️ Не удалось создать файл логов: {e}")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    """Инициализация базы данных"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Включаем поддержку внешних ключей
            await db.execute("PRAGMA foreign_keys = ON")
            
            # 1. Серверы
            await db.execute("""
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ssh_key TEXT NOT NULL,
                    connection_string TEXT NOT NULL,
                    country TEXT,
                    city TEXT,
                    max_users INTEGER DEFAULT 50,
                    current_users INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    server_ip TEXT,
                    public_key TEXT,
                    wireguard_configured BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 2. VPN пользователи
            await db.execute("""
                CREATE TABLE IF NOT EXISTS vpn_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    server_id INTEGER,
                    config_data TEXT,
                    subscription_end TIMESTAMP,
                    trial_used BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    gifted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 3. Платежи
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
            
            # Создаем индексы
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_user_id ON vpn_users(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_active ON vpn_users(is_active)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_subscription ON vpn_users(subscription_end)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
            
            # Инициализируем цены по умолчанию
            await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY, week_price INTEGER DEFAULT 50, month_price INTEGER DEFAULT 150)")
            await db.execute("INSERT OR IGNORE INTO prices (id, week_price, month_price) VALUES (1, 50, 150)")
            
            await db.commit()
            logger.info("✅ База данных инициализирована")
            return True
            
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int) -> bool:
    """Проверяет является ли пользователь админом"""
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    """Получает цены VPN из БД"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices:
                return {
                    "week": {"days": 7, "stars": prices[0]},
                    "month": {"days": 30, "stars": prices[1]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен: {e}")
    
    return {
        "week": {"days": 7, "stars": 50},
        "month": {"days": 30, "stars": 150}
    }

async def update_vpn_prices(week_price: int, month_price: int):
    """Обновляет цены VPN"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE prices SET week_price = ?, month_price = ? WHERE id = 1", (week_price, month_price))
            await db.commit()
            logger.info(f"Обновлены цены VPN: неделя={week_price}, месяц={month_price}")
            return True
    except Exception as e:
        logger.error(f"Ошибка обновления цен: {e}")
        return False

async def get_available_server() -> Optional[int]:
    """Находит доступный сервер"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM servers 
                WHERE is_active = TRUE 
                AND current_users < max_users
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска сервера: {e}")
        return None

async def execute_ssh_command(server_id: int, command: str, timeout: int = 30) -> Tuple[str, str, bool]:
    """Выполняет команду на сервере через SSH с детальным логированием"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            
            if not server:
                logger.error(f"Сервер с ID {server_id} не найден")
                return "", f"Сервер {server_id} не найден", False
            
            conn_str, ssh_key = server
            
            # Парсим строку подключения
            try:
                if ':' in conn_str:
                    user_host, port = conn_str.rsplit(':', 1)
                    user, host = user_host.split('@')
                    port = int(port)
                else:
                    user, host = conn_str.split('@')
                    port = 22
            except ValueError as e:
                logger.error(f"Ошибка парсинга строки подключения '{conn_str}': {e}")
                return "", f"Неверный формат: {conn_str}", False
            
            # Подготавливаем SSH ключ
            import tempfile
            import stat
            
            ssh_key_clean = ssh_key.strip()
            if not ssh_key_clean.startswith('-----BEGIN'):
                ssh_key_clean = f"-----BEGIN PRIVATE KEY-----\n{ssh_key_clean}\n-----END PRIVATE KEY-----"
            
            # Сохраняем ключ во временный файл
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean)
                temp_key_path = f.name
            
            # Устанавливаем правильные права доступа
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            # Подключаемся по SSH
            try:
                logger.info(f"Подключаюсь к {host}:{port} как {user}")
                async with asyncssh.connect(
                    host,
                    username=user,
                    port=port,
                    client_keys=[temp_key_path],
                    known_hosts=None,
                    connect_timeout=timeout,
                    login_timeout=timeout
                ) as conn:
                    logger.info(f"Выполняю команду: {command[:100]}...")
                    result = await conn.run(command, timeout=timeout)
                    
                    # Удаляем временный файл ключа
                    try:
                        os.unlink(temp_key_path)
                    except:
                        pass
                    
                    logger.info(f"Команда выполнена успешно")
                    return result.stdout, result.stderr, True
                    
            except asyncssh.Error as e:
                error_msg = f"SSH ошибка: {str(e)}"
                logger.error(error_msg)
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", error_msg, False
            except asyncio.TimeoutError:
                error_msg = "Таймаут подключения"
                logger.error(error_msg)
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", error_msg, False
                
    except Exception as e:
        error_msg = f"Общая ошибка: {str(e)}"
        logger.error(error_msg)
        return "", error_msg, False

async def setup_wireguard_server(server_id: int, message: Message = None):
    """Настраивает WireGuard на сервере с пошаговым логированием"""
    steps = []
    
    async def log_step(text: str, success: bool = True):
        step_msg = f"{'✅' if success else '❌'} {text}"
        steps.append(step_msg)
        if message:
            try:
                await message.answer(step_msg)
            except:
                pass
        logger.info(step_msg)
    
    await log_step("Начинаю настройку WireGuard на сервере")
    
    try:
        # 1. Проверка подключения
        await log_step("Проверяю подключение к серверу...")
        stdout, stderr, success = await execute_ssh_command(server_id, "echo 'Connection test'")
        
        if not success:
            await log_step(f"Ошибка подключения: {stderr}", False)
            return False, steps
        
        await log_step("Подключение установлено")
        
        # 2. Проверка системы
        await log_step("Проверяю операционную систему...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/os-release | grep PRETTY_NAME")
        if success and stdout:
            await log_step(f"Система: {stdout.split('=')[1].strip('\"')}")
        
        # 3. Установка WireGuard
        await log_step("Обновляю пакеты системы...")
        stdout, stderr, success = await execute_ssh_command(server_id, "apt-get update -y", timeout=60)
        if not success:
            await log_step("Пробую другой способ обновления...", False)
            stdout, stderr, success = await execute_ssh_command(server_id, "apt update -y", timeout=60)
        
        if not success:
            await log_step("Ошибка обновления пакетов. Продолжаю установку...", False)
        
        await log_step("Устанавливаю WireGuard...")
        
        # Пробуем разные способы установки
        install_methods = [
            "apt-get install -y wireguard",
            "apt install -y wireguard",
            "yum install -y wireguard-tools 2>/dev/null || apt-get install -y wireguard"
        ]
        
        installed = False
        for method in install_methods:
            await log_step(f"Пробую: {method}")
            stdout, stderr, success = await execute_ssh_command(server_id, method, timeout=120)
            if success:
                installed = True
                await log_step("WireGuard установлен")
                break
        
        if not installed:
            await log_step("Не удалось установить WireGuard. Пробую установить из исходников...", False)
            
            # Установка зависимостей
            deps_cmd = "apt-get install -y build-essential git libmnl-dev libelf-dev linux-headers-$(uname -r)"
            stdout, stderr, success = await execute_ssh_command(server_id, deps_cmd, timeout=180)
            
            if success:
                # Компиляция из исходников
                source_cmd = """
                cd /tmp && git clone https://git.zx2c4.com/wireguard-tools && \
                cd wireguard-tools && make && make install
                """
                stdout, stderr, success = await execute_ssh_command(server_id, source_cmd, timeout=300)
                
                if success:
                    installed = True
                    await log_step("WireGuard установлен из исходников")
                else:
                    await log_step("Не удалось установить из исходников", False)
            else:
                await log_step("Не удалось установить зависимости", False)
        
        if not installed:
            return False, steps
        
        # 4. Создание директории
        await log_step("Создаю директорию для WireGuard...")
        await execute_ssh_command(server_id, "mkdir -p /etc/wireguard")
        
        # 5. Генерация ключей
        await log_step("Генерирую ключи...")
        keygen_cmd = """
        cd /etc/wireguard
        umask 077
        wg genkey | tee private.key | wg pubkey > public.key
        chmod 600 private.key public.key
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd)
        if not success:
            await log_step("Ошибка генерации ключей", False)
            return False, steps
        
        # 6. Получение публичного ключа
        await log_step("Получаю публичный ключ...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key")
        if not success or not stdout.strip():
            await log_step("Не удалось получить публичный ключ", False)
            return False, steps
        
        public_key = stdout.strip()
        
        # 7. Получение IP сервера
        await log_step("Определяю IP адрес сервера...")
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success and stdout.strip() else ""
        
        # 8. Сохранение данных в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET public_key = ?, wireguard_configured = TRUE, server_ip = ? WHERE id = ?",
                (public_key, server_ip, server_id)
            )
            await db.commit()
        
        await log_step(f"✅ WireGuard успешно настроен!")
        await log_step(f"Публичный ключ: {public_key[:30]}...")
        await log_step(f"IP сервера: {server_ip}")
        
        return True, steps
        
    except Exception as e:
        error_msg = f"Критическая ошибка: {str(e)}"
        await log_step(error_msg, False)
        return False, steps

async def create_wireguard_client(server_id: int, user_id: int, message: Message = None):
    """Создает клиента WireGuard с логированием"""
    async def log_step(text: str, success: bool = True):
        if message:
            try:
                await message.answer(text)
            except:
                pass
        logger.info(text)
    
    await log_step("🔄 Создаю VPN конфигурацию...")
    
    try:
        # 1. Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT public_key, server_ip FROM servers WHERE id = ?", (server_id,))
            server_data = await cursor.fetchone()
            
            if not server_data or not server_data[0]:
                await log_step("❌ У сервера нет публичного ключа", False)
                return None
            
            server_pub_key, server_ip = server_data
        
        # 2. Генерируем ключи клиента
        await log_step("Генерирую ключи для клиента...")
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        keygen_cmd = f"""
        cd /etc/wireguard
        umask 077
        wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public
        cat {client_name}.private
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd)
        if not success or not stdout.strip():
            await log_step("❌ Не удалось сгенерировать ключи клиента", False)
            return None
        
        private_key = stdout.strip()
        
        # 3. Получаем публичный ключ клиента
        stdout, stderr, success = await execute_ssh_command(server_id, f"cat /etc/wireguard/{client_name}.public")
        if not success or not stdout.strip():
            await log_step("❌ Не удалось получить публичный ключ клиента", False)
            return None
        
        public_key = stdout.strip()
        
        # 4. Определяем IP адрес клиента
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM vpn_users WHERE server_id = ?", (server_id,))
            peer_count = (await cursor.fetchone())[0]
        
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # 5. Создаем конфиг WireGuard если его нет
        await log_step("Настраиваю конфигурацию WireGuard...")
        check_config = "test -f /etc/wireguard/wg0.conf && echo 'exists' || echo 'not exists'"
        stdout, stderr, success = await execute_ssh_command(server_id, check_config)
        
        if success and 'not exists' in stdout:
            # Создаем базовый конфиг
            config_cmd = f"""
            cd /etc/wireguard
            cat > wg0.conf << EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

EOF
            """
            await execute_ssh_command(server_id, config_cmd)
        
        # 6. Добавляем пира в конфиг
        add_peer_cmd = f"""
        cd /etc/wireguard
        cat >> wg0.conf << EOF

[Peer]
# Client {user_id}
PublicKey = {public_key}
AllowedIPs = {client_ip}/32
EOF
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, add_peer_cmd)
        if not success:
            await log_step("❌ Не удалось добавить клиента в конфиг", False)
            return None
        
        # 7. Перезапускаем WireGuard
        await log_step("Перезапускаю WireGuard...")
        
        # Проверяем запущен ли сервис
        check_service = "systemctl is-active wg-quick@wg0 2>/dev/null || echo 'inactive'"
        stdout, stderr, success = await execute_ssh_command(server_id, check_service)
        
        if success and 'active' not in stdout:
            # Запускаем сервис
            start_cmd = """
            systemctl enable wg-quick@wg0
            systemctl start wg-quick@wg0
            """
            await execute_ssh_command(server_id, start_cmd)
        else:
            # Перезагружаем конфиг
            reload_cmd = "wg syncconf wg0 <(wg-quick strip wg0)"
            await execute_ssh_command(server_id, reload_cmd)
        
        await log_step(f"✅ Клиент создан: IP={client_ip}")
        
        return {
            "private_key": private_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip,
            "client_name": client_name
        }
        
    except Exception as e:
        await log_step(f"❌ Ошибка создания клиента: {str(e)}", False)
        return None

async def create_vpn_for_user(user_id: int, period_days: int = 7, gifted: bool = False, message: Message = None) -> bool:
    """Создает VPN для пользователя с подробным логированием"""
    if message:
        await message.answer(f"🔄 Начинаю создание VPN на {period_days} дней...")
    
    # Находим доступный сервер
    server_id = await get_available_server()
    if not server_id:
        if message:
            await message.answer("❌ Нет доступных серверов")
        return False
    
    # Проверяем настроен ли WireGuard
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT wireguard_configured FROM servers WHERE id = ?", (server_id,))
        server = await cursor.fetchone()
        
        if not server or not server[0]:
            if message:
                await message.answer("⚙️ WireGuard не настроен, начинаю настройку...")
            
            success, steps = await setup_wireguard_server(server_id, message)
            
            if not success:
                if message:
                    await message.answer("❌ Не удалось настроить WireGuard")
                return False
        
    # Создаем клиента
    vpn_config = await create_wireguard_client(server_id, user_id, message)
    
    if not vpn_config:
        if message:
            await message.answer("❌ Не удалось создать VPN конфигурацию")
        return False
    
    # Сохраняем в БД
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, config_data, subscription_end, is_active, gifted) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, json.dumps(vpn_config),
                 (datetime.now() + timedelta(days=period_days)).isoformat(),
                 True, gifted)
            )
            
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
        
        if message:
            await message.answer("✅ VPN успешно создан и сохранен в базе")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения VPN: {e}")
        if message:
            await message.answer(f"❌ Ошибка сохранения: {str(e)}")
        return False

async def send_vpn_config_to_user(user_id: int, config: Dict, period_days: int, gifted: bool = False):
    """Отправляет конфиг VPN пользователю"""
    try:
        end_date = datetime.now() + timedelta(days=period_days)
        
        # WireGuard конфиг
        config_text = f"""[Interface]
PrivateKey = {config['private_key']}
Address = {config['client_ip']}/24
DNS = 1.1.1.1, 8.8.8.8

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
        
        # Формируем сообщение
        if gifted:
            message_text = f"🎁 <b>Вам выдан VPN доступ на {period_days} дней!</b>\n\n"
        else:
            message_text = f"✅ <b>Ваш VPN доступ активирован на {period_days} дней!</b>\n\n"
        
        message_text += f"📅 Подписка до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
        message_text += f"📱 Установите WireGuard и отсканируйте QR код:"
        
        await bot.send_message(user_id, message_text, parse_mode=ParseMode.HTML)
        
        # Отправляем QR код
        await bot.send_photo(
            user_id,
            types.BufferedInputFile(img_bytes.read(), filename="vpn_qr.png"),
            caption="QR код для быстрой настройки"
        )
        
        # Отправляем текстовый конфиг
        await bot.send_message(
            user_id,
            f"📝 <b>Текстовый конфиг:</b>\n\n<code>{config_text}</code>\n\n"
            "Скопируйте этот конфиг в приложение WireGuard.",
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Конфиг VPN отправлен пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки конфига: {e}")
        await bot.send_message(user_id, f"❌ Ошибка отправки конфига: {str(e)}")

async def create_test_bot(server_id: int, bot_token: str, message: Message):
    """Создает простого тестового бота на сервере"""
    await message.answer("🤖 Создаю тестового бота...")
    
    try:
        # 1. Проверяем Docker
        await message.answer("Проверяю Docker...")
        stdout, stderr, success = await execute_ssh_command(server_id, "which docker")
        
        if not success or "not found" in stderr:
            await message.answer("Устанавливаю Docker...")
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y docker.io", timeout=120)
        
        # 2. Создаем простого бота
        bot_content = f"""import os
import time
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

BOT_TOKEN = '{bot_token}'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("✅ Бот создан, сервер подключен!\\n\\nНапишите /ping для проверки")

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    start_time = time.time()
    await message.answer("🏓 Pong!")
    end_time = time.time()
    response_time = round((end_time - start_time) * 1000, 2)
    await message.answer(f"⏱️ Время ответа: {response_time}ms")

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Вы сказали: {{message.text}}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
"""
        
        # 3. Создаем файлы на сервере
        await message.answer("Создаю файлы бота...")
        
        # Создаем директорию
        await execute_ssh_command(server_id, "mkdir -p /tmp/test_bot")
        
        # Создаем bot.py
        create_bot_cmd = f"cd /tmp/test_bot && echo '''{bot_content}''' > bot.py"
        await execute_ssh_command(server_id, create_bot_cmd)
        
        # Создаем requirements.txt
        await execute_ssh_command(server_id, "cd /tmp/test_bot && echo 'aiogram>=3.0.0' > requirements.txt")
        
        # 4. Создаем Dockerfile
        dockerfile = """
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "bot.py"]
"""
        
        create_dockerfile = f"cd /tmp/test_bot && echo '''{dockerfile}''' > Dockerfile"
        await execute_ssh_command(server_id, create_dockerfile)
        
        # 5. Собираем и запускаем контейнер
        await message.answer("Собираю Docker образ...")
        await execute_ssh_command(server_id, "cd /tmp/test_bot && docker build -t test_bot .", timeout=180)
        
        await message.answer("Запускаю бота...")
        stdout, stderr, success = await execute_ssh_command(
            server_id, 
            "docker run -d --name test_bot --restart unless-stopped test_bot"
        )
        
        if success and stdout:
            container_id = stdout.strip()[:12]
            await message.answer(f"✅ Тестовый бот запущен!\n\n🆔 Контейнер: {container_id}")
            
            # Получаем логи
            await asyncio.sleep(2)
            stdout, stderr, success = await execute_ssh_command(server_id, "docker logs test_bot --tail 10")
            if success:
                await message.answer(f"📋 Логи запуска:\n<code>{stdout[-500:] if stdout else 'Нет логов'}</code>", parse_mode=ParseMode.HTML)
            
            return True, "Бот успешно создан и запущен"
        else:
            await message.answer(f"❌ Ошибка запуска: {stderr}")
            return False, stderr
        
    except Exception as e:
        error_msg = f"❌ Ошибка создания бота: {str(e)}"
        await message.answer(error_msg)
        return False, error_msg

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [
        [types.KeyboardButton(text="🔐 Получить VPN")],
        [types.KeyboardButton(text="📱 Мои услуги")],
        [types.KeyboardButton(text="🆘 Помощь")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    buttons = [
        [types.KeyboardButton(text="🖥️ Серверы")],
        [types.KeyboardButton(text="👤 Пользователи")],
        [types.KeyboardButton(text="💰 Цены")],
        [types.KeyboardButton(text="🤖 Тест сервера")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def servers_menu():
    buttons = [
        [types.KeyboardButton(text="📋 Список серверов")],
        [types.KeyboardButton(text="➕ Добавить сервер")],
        [types.KeyboardButton(text="◀️ Назад")]
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

def admin_users_menu():
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="📋 Список пользователей")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def confirm_keyboard():
    buttons = [
        [types.KeyboardButton(text="✅ Подтвердить")],
        [types.KeyboardButton(text="❌ Отменить")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_list_keyboard(servers):
    """Создает клавиатуру со списком серверов"""
    buttons = []
    for server in servers:
        server_id, server_name, is_active = server
        status = "🟢" if is_active else "🔴"
        buttons.append([types.KeyboardButton(text=f"{status} {server_name} (ID: {server_id})")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()

class AdminPriceStates(StatesGroup):
    waiting_for_week_price = State()
    waiting_for_confirm = State()

class AdminTestBotStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_token = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    if is_admin(message.from_user.id):
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            "🚀 <b>Добро пожаловать в VPN Hosting!</b>\n\n"
            "Выберите услугу:",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    """Показать услуги пользователя"""
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT subscription_end, is_active, gifted
                FROM vpn_users 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (user_id,))
            vpn_services = await cursor.fetchall()
        
        text = "📱 <b>Ваши VPN подписки:</b>\n\n"
        
        if vpn_services:
            for i, vpn in enumerate(vpn_services[:5], 1):
                end_date, active, gifted = vpn
                if end_date:
                    end = datetime.fromisoformat(end_date)
                    end_str = end.strftime("%d.%m.%Y %H:%M")
                    status = "🟢" if active and end > datetime.now() else "🔴"
                    gift = " 🎁" if gifted else ""
                    remaining = (end - datetime.now()).days if end > datetime.now() else 0
                    text += f"{i}. {status} до {end_str}{gift}\n   ⏳ Осталось: {remaining} дней\n\n"
        else:
            text += "❌ Нет активных VPN подписок\n\n"
        
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=user_main_menu())
        
    except Exception as e:
        logger.error(f"Ошибка получения услуг: {e}")
        await message.answer("❌ Ошибка получения данных", reply_markup=user_main_menu())

@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    """Начало получения VPN"""
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
    
    # Проверяем пробный период
    if period == "trial":
        user_id = message.from_user.id
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
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
        
        # Создаем инвойс
        timestamp = int(datetime.now().timestamp())
        payload = f"vpn:{message.from_user.id}:{period}:{timestamp}"
        
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN на {days} дней",
                description=f"Доступ к VPN серверам на {days} дней",
                payload=payload,
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label=f"VPN {days} дней", amount=stars)],
                start_parameter="vpn_subscription"
            )
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO payments (user_id, amount_stars, period, invoice_payload)
                    VALUES (?, ?, ?, ?)""",
                    (message.from_user.id, stars, period, payload)
                )
                await db.commit()
            
            await state.clear()
            return
            
        except Exception as e:
            logger.error(f"Ошибка создания инвойса: {e}")
            await message.answer("❌ Ошибка создания счета.", reply_markup=user_main_menu())
            await state.clear()
            return
    
    # Для пробного - создаем VPN
    success = await create_vpn_for_user(message.from_user.id, days, False, message)
    
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
                await send_vpn_config_to_user(message.from_user.id, config, days)
        
        await message.answer("✅ VPN доступ успешно создан!", reply_markup=user_main_menu())
    else:
        await message.answer("❌ Ошибка создания VPN!", reply_markup=user_main_menu())
    
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
        parse_mode=ParseMode.HTML,
        reply_markup=user_main_menu()
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
        period = payload_parts[2]
        
        # Обновляем статус платежа
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """UPDATE payments 
                    SET status = 'completed', telegram_payment_id = ?
                    WHERE user_id = ? AND invoice_payload = ?""",
                    (payment.telegram_payment_charge_id, user_id, payment.invoice_payload)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка обновления платежа: {e}")
        
        await message.answer(f"✅ <b>Оплата получена!</b>\n\n{payment.total_amount} stars успешно списаны.", parse_mode=ParseMode.HTML)
        
        # Создаем VPN
        days = 30 if period == "month" else 7
        await message.answer(f"🔄 Создаю VPN на {days} дней...")
        
        success = await create_vpn_for_user(user_id, days, False, message)
        
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
                    await send_vpn_config_to_user(user_id, config, days)
            
            await message.answer("✅ VPN успешно создан!", reply_markup=user_main_menu())
        else:
            await message.answer("❌ Ошибка создания VPN!", reply_markup=user_main_menu())
    else:
        await message.answer("❌ Ошибка обработки платежа.")

# ========== АДМИН ФУНКЦИИ ==========
@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message):
    """Меню серверов"""
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список серверов")
async def admin_list_servers(message: Message):
    """Список серверов"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, server_ip, current_users, max_users, 
                       is_active, wireguard_configured
                FROM servers 
                ORDER BY created_at DESC
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка серверов: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет")
        return
    
    text = "🖥️ <b>Список серверов:</b>\n\n"
    
    for server in servers:
        id_, name, ip, current, max_users, active, wg_configured = server
        status = "🟢" if active else "🔴"
        wg_status = "✅" if wg_configured else "❌"
        
        text += f"{status} <b>{name}</b> (ID: {id_})\n"
        text += f"IP: {ip or 'не указан'}\n"
        text += f"Пользователи: {current}/{max_users}\n"
        text += f"WireGuard: {wg_status}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server(message: Message, state: FSMContext):
    """Добавление сервера"""
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=ReplyKeyboardRemove())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    """Обработка имени сервера"""
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_key)
    await message.answer("Отправьте SSH-ключ (текстом или файлом):")

@dp.message(AdminAddServerStates.waiting_for_key, F.document)
async def process_ssh_key_doc(message: Message, state: FSMContext, bot: Bot):
    """Обработка SSH ключа из файла"""
    try:
        file = await bot.get_file(message.document.file_id)
        file_path = f"/tmp/{message.document.file_name}"
        await bot.download_file(file.file_path, file_path)
        
        with open(file_path, 'r') as f:
            ssh_key = f.read().strip()
        
        os.remove(file_path)
        
        await state.update_data(ssh_key=ssh_key)
        await state.set_state(AdminAddServerStates.waiting_for_connection)
        
        await message.answer(
            "✅ SSH-ключ получен!\n\n"
            "Введите строку подключения:\n"
            "Формат: <code>user@host:port</code>\n"
            "Пример: <code>opc@123.456.7.89</code>\n\n"
            "Если порт стандартный (22), можно без порта: <code>user@host</code>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки файла: {str(e)}")
        logger.error(f"Ошибка обработки SSH-ключа: {e}")

@dp.message(AdminAddServerStates.waiting_for_key)
async def process_ssh_key_text(message: Message, state: FSMContext):
    """Обработка SSH ключа отправленного текстом"""
    ssh_key = message.text.strip()
    
    if not ssh_key:
        await message.answer("Отправьте SSH-ключ:")
        return
    
    # Проверяем формат ключа
    if not ssh_key.startswith('-----BEGIN'):
        ssh_key = f"-----BEGIN PRIVATE KEY-----\n{ssh_key}\n-----END PRIVATE KEY-----"
    
    await state.update_data(ssh_key=ssh_key)
    await state.set_state(AdminAddServerStates.waiting_for_connection)
    
    await message.answer(
        "✅ SSH-ключ получен!\n\n"
        "Введите строку подключения:\n"
        "Формат: <code>user@host:port</code>\n"
        "Пример: <code>opc@123.456.7.89</code>\n\n"
        "Если порт стандартный (22), можно без порта: <code>user@host</code>",
        parse_mode=ParseMode.HTML
    )

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection(message: Message, state: FSMContext):
    """Обработка подключения"""
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
                (name, ssh_key, connection_string, server_ip) 
                VALUES (?, ?, ?, ?)""",
                (data['server_name'], data['ssh_key'], connection_string, host)
            )
            await db.commit()
            
            # Получаем ID добавленного сервера
            cursor = await db.execute("SELECT last_insert_rowid()")
            server_id = (await cursor.fetchone())[0]
        
        await message.answer(
            f"✅ Сервер <b>{data['server_name']}</b> добавлен!\n\n"
            f"ID: {server_id}\n"
            f"IP: {host}\n\n"
            f"⚠️ <b>WireGuard не настроен!</b>\n"
            f"Настройте его через тест сервера.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка формата: {str(e)}\n\nВведите строку подключения в формате: user@host:port")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        logger.error(f"Ошибка сохранения сервера: {e}")
    
    await state.clear()

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message):
    """Управление пользователями"""
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("👤 <b>Управление пользователями</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_give_vpn(message: Message, state: FSMContext):
    """Выдача VPN от админа"""
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer("Введите @username или ID пользователя:", reply_markup=ReplyKeyboardRemove())

@dp.message(AdminUserStates.waiting_for_username)
async def admin_process_username(message: Message, state: FSMContext):
    """Обработка username от админа"""
    username = message.text.strip()
    user_id = None
    
    if username.isdigit():
        user_id = int(username)
    elif username.startswith('@'):
        username_clean = username[1:]
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT user_id FROM vpn_users WHERE username = ? LIMIT 1",
                    (username_clean,)
                )
                result = await cursor.fetchone()
                
                if result:
                    user_id = result[0]
                else:
                    # Пробуем получить пользователя по ID из Telegram
                    try:
                        chat = await bot.get_chat(username)
                        user_id = chat.id
                    except:
                        await message.answer(f"❌ Пользователь {username} не найден.", reply_markup=admin_main_menu())
                        await state.clear()
                        return
        except Exception as e:
            logger.error(f"Ошибка поиска пользователя: {e}")
            await message.answer("❌ Ошибка поиска пользователя")
            await state.clear()
            return
    else:
        await message.answer("Введите @username или ID:")
        return
    
    if user_id:
        await state.update_data(user_id=user_id, username=username)
        await state.set_state(AdminUserStates.waiting_for_period)
        
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="3 дня")],
            [types.KeyboardButton(text="7 дней")],
            [types.KeyboardButton(text="30 дней")],
            [types.KeyboardButton(text="◀️ Назад")]
        ], resize_keyboard=True)
        
        await message.answer(f"Пользователь найден: {username}\n\nВыберите период:", reply_markup=keyboard)
    else:
        await message.answer("Не удалось определить ID пользователя")
        await state.clear()

@dp.message(AdminUserStates.waiting_for_period)
async def admin_process_period(message: Message, state: FSMContext):
    """Обработка периода от админа"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminUserStates.waiting_for_username)
        await message.answer("Введите @username или ID:")
        return
    
    period_map = {
        "3 дня": 3,
        "7 дней": 7,
        "30 дней": 30
    }
    
    if message.text not in period_map:
        await message.answer("Выберите период из списка:")
        return
    
    days = period_map[message.text]
    data = await state.get_data()
    user_id = data.get('user_id')
    
    if not user_id:
        await message.answer("❌ Ошибка: ID пользователя не найден")
        await state.clear()
        return
    
    # Создаем VPN
    await message.answer(f"🔄 Выдаю VPN пользователю {user_id} на {days} дней...")
    
    success = await create_vpn_for_user(user_id, days, True, message)
    
    if success:
        # Отправляем уведомление пользователю
        try:
            await bot.send_message(
                user_id,
                f"🎁 <b>Вам выдан VPN доступ на {days} дней от администратора!</b>\n\n"
                f"Конфигурация уже отправлена вам.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление: {e}")
        
        await message.answer(f"✅ VPN успешно выдан!", reply_markup=admin_main_menu())
    else:
        await message.answer(f"❌ Ошибка выдачи VPN", reply_markup=admin_main_menu())
    
    await state.clear()

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message):
    """Список пользователей"""
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
                LIMIT 20
            """)
            vpn_users = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    text = "📋 <b>Пользователи (последние 20):</b>\n\n"
    
    for user in vpn_users:
        user_id, username, vpn_count, active_vpn = user
        username_display = f"@{username}" if username else f"ID: {user_id}"
        text += f"👤 {username_display}\n"
        text += f"   VPN: {active_vpn}/{vpn_count} активных\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "💰 Цены")
async def admin_prices(message: Message):
    """Управление ценами"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        vpn_prices = await get_vpn_prices()
    except:
        vpn_prices = {"week": {"stars": 50}, "month": {"stars": 150}}
    
    text = "💰 <b>Текущие цены VPN:</b>\n\n"
    text += f"• Неделя: {vpn_prices['week']['stars']} stars\n"
    text += f"• Месяц: {vpn_prices['month']['stars']} stars\n\n"
    text += "Выберите действие:"
    
    keyboard = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="📝 Изменить цены")],
        [types.KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)
    
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📝 Изменить цены")
async def admin_change_prices(message: Message, state: FSMContext):
    """Изменение цен"""
    if not is_admin(message.from_user.id):
        return
    
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    
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

@dp.message(AdminPriceStates.waiting_for_week_price)
async def admin_process_week_price(message: Message, state: FSMContext):
    """Обработка цена за неделю"""
    try:
        week_price = int(message.text)
        if week_price <= 0:
            await message.answer("Цена должна быть больше 0:")
            return
            
        await state.update_data(week_price=week_price)
        await state.set_state(AdminPriceStates.waiting_for_confirm)
        
        data = await state.get_data()
        month_price = week_price * 3
        
        await message.answer(
            f"<b>Новые цены VPN:</b>\n\n"
            f"• Неделя: {week_price} stars\n"
            f"• Месяц: {month_price} stars\n\n"
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
        week_price = data.get('week_price')
        month_price = week_price * 3
        
        success = await update_vpn_prices(week_price, month_price)
        
        if success:
            await message.answer(
                f"✅ Цены обновлены!\n\n"
                f"• Неделя: {week_price} stars\n"
                f"• Месяц: {month_price} stars",
                reply_markup=admin_main_menu()
            )
        else:
            await message.answer("❌ Ошибка обновления цен", reply_markup=admin_main_menu())
    
    elif message.text == "❌ Отменить":
        await message.answer("Изменение цен отменено", reply_markup=admin_main_menu())
    
    await state.clear()

@dp.message(F.text == "🤖 Тест сервера")
async def admin_test_server(message: Message, state: FSMContext):
    """Тестирование сервера - создание тестового бота"""
    if not is_admin(message.from_user.id):
        return
    
    # Получаем список серверов
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения серверов: {e}")
        await message.answer("❌ Ошибка получения списка серверов")
        return
    
    if not servers:
        await message.answer("📭 Нет серверов для тестирования")
        return
    
    await state.set_state(AdminTestBotStates.waiting_for_server)
    await state.update_data(servers=servers)
    
    await message.answer("Выберите сервер для тестирования:", reply_markup=server_list_keyboard(servers))

@dp.message(AdminTestBotStates.waiting_for_server)
async def process_test_server(message: Message, state: FSMContext):
    """Обработка выбора сервера для теста"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
        return
    
    # Парсим ID сервера из сообщения
    try:
        # Ищем ID в скобках
        import re
        match = re.search(r'\(ID:\s*(\d+)\)', message.text)
        if match:
            server_id = int(match.group(1))
        else:
            # Ищем цифры в тексте
            import re
            numbers = re.findall(r'\d+', message.text)
            if numbers:
                server_id = int(numbers[-1])
            else:
                await message.answer("Не удалось определить ID сервера. Выберите из списка:")
                return
    except:
        await message.answer("Не удалось определить ID сервера. Выберите из списка:")
        return
    
    data = await state.get_data()
    servers = data.get('servers', [])
    
    # Проверяем существование сервера
    server_exists = any(str(s[0]) == str(server_id) for s in servers)
    if not server_exists:
        await message.answer("Сервер не найден. Выберите из списка:")
        return
    
    await state.update_data(server_id=server_id)
    await state.set_state(AdminTestBotStates.waiting_for_token)
    
    await message.answer(
        f"Выбран сервер ID: {server_id}\n\n"
        "Отправьте токен бота для тестирования:\n"
        "(получите у @BotFather)",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(AdminTestBotStates.waiting_for_token)
async def process_test_bot_token(message: Message, state: FSMContext):
    """Обработка токена для тестового бота"""
    if message.text == "◀️ Назад":
        data = await state.get_data()
        servers = data.get('servers', [])
        await message.answer("Выберите сервер для тестирования:", reply_markup=server_list_keyboard(servers))
        await state.set_state(AdminTestBotStates.waiting_for_server)
        return
    
    bot_token = message.text.strip()
    
    if len(bot_token) < 30:
        await message.answer("Неверный формат токена. Отправьте токен бота:")
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    # Создаем тестового бота
    success, result = await create_test_bot(server_id, bot_token, message)
    
    if success:
        await message.answer(
            f"✅ <b>Тестовый бот успешно создан!</b>\n\n"
            f"Сервер работает корректно.\n\n"
            f"Теперь вы можете:\n"
            f"1. Настроить WireGuard на этом сервере\n"
            f"2. Использовать его для VPN пользователей",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания тестового бота!</b>\n\n"
            f"Ошибка: {result}\n\n"
            f"Проверьте:\n"
            f"• SSH доступ к серверу\n"
            f"• Доступность интернета на сервере\n"
            f"• Права пользователя",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "◀️ Назад")
async def back_handler(message: Message, state: FSMContext):
    """Обработчик кнопки назад"""
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
        print("🚀 ЗАПУСК VPN HOSTING БОТА")
        print("=" * 50)
        
        # Инициализируем базу данных
        logger.info("📊 Инициализация базы данных...")
        if not await init_database():
            logger.critical("❌ Не удалось инициализировать базу данных!")
            return
        
        # Получаем информацию о боте
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username}")
        print(f"👑 Admin ID: {ADMIN_ID}")
        print(f"🗄️ База данных: {DB_PATH}")
        
        # Запускаем опрос
        logger.info("🔄 Запускаем опрос...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка запуска: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.critical(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)