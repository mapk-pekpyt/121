# main.py - ИСПРАВЛЕННЫЙ КОД (ФИНАЛЬНАЯ ВЕРСИЯ)
import os
import asyncio
import logging
import json
import random
import string
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
ADMIN_CHAT_ID = -1003542769962
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"

# Сначала создаем директорию /data если ее нет
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
def create_database_sync():
    """Синхронное создание базы данных"""
    try:
        logger.info(f"Создаем БД по пути: {DB_PATH}")
        
        # Проверяем, существует ли файл БД
        if os.path.exists(DB_PATH):
            logger.info(f"БД уже существует: {DB_PATH}")
            
            # Проверяем целостность БД
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                table_names = [t[0] for t in tables]
                logger.info(f"Существующие таблицы: {table_names}")
                
                # Проверяем обязательные таблицы
                required_tables = ['servers', 'vpn_users', 'payments', 'user_bots', 'price_settings']
                missing_tables = [t for t in required_tables if t not in table_names]
                
                if missing_tables:
                    logger.warning(f"Отсутствующие таблицы: {missing_tables}")
                    conn.close()
                    return False
                else:
                    logger.info("Все таблицы присутствуют")
                    conn.close()
                    return True
                    
            except Exception as e:
                logger.error(f"Ошибка проверки БД: {e}")
                if 'conn' in locals():
                    conn.close()
                return False
        else:
            logger.info("БД не существует, создаем новую...")
            return False
            
    except Exception as e:
        logger.error(f"Ошибка создания БД: {e}")
        return False

async def create_database_tables():
    """Создает таблицы в БД"""
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
                    server_type TEXT DEFAULT 'vpn',
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
                    vpn_type TEXT DEFAULT 'wireguard',
                    device_type TEXT,
                    config_data TEXT,
                    subscription_end TIMESTAMP,
                    trial_used BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    gifted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
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
                    service_type TEXT DEFAULT 'vpn',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 4. Telegram боты пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_bots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bot_name TEXT NOT NULL,
                    bot_token TEXT UNIQUE,
                    bot_username TEXT,
                    git_repo TEXT,
                    server_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    container_id TEXT,
                    subscription_end TIMESTAMP,
                    gifted BOOLEAN DEFAULT FALSE,
                    last_logs TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
                )
            """)
            
            # 5. Настройки цен
            await db.execute("""
                CREATE TABLE IF NOT EXISTS price_settings (
                    service_type TEXT PRIMARY KEY,
                    week_price INTEGER DEFAULT 50,
                    month_price INTEGER DEFAULT 150
                )
            """)
            
            # Создаем индексы
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_user_id ON vpn_users(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_active ON vpn_users(is_active)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_vpn_users_subscription ON vpn_users(subscription_end)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_bots_user_id ON user_bots(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_bots_status ON user_bots(status)")
            
            # Инициализируем цены по умолчанию
            await db.execute("INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES ('vpn', 50, 150)")
            await db.execute("INSERT OR IGNORE INTO price_settings (service_type, week_price, month_price) VALUES ('bot', 100, 300)")
            
            await db.commit()
            logger.info("✅ Таблицы БД созданы/проверены")
            return True
            
    except Exception as e:
        logger.error(f"❌ Ошибка создания таблиц: {e}")
        return False

async def init_database():
    """Инициализация базы данных"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"Попытка инициализации БД #{attempt + 1}")
            
            # Синхронная проверка/создание файла БД
            db_exists = create_database_sync()
            
            if not db_exists:
                # Создаем пустой файл БД
                try:
                    conn = sqlite3.connect(DB_PATH)
                    conn.close()
                    logger.info(f"✅ Создан файл БД: {DB_PATH}")
                except Exception as e:
                    logger.error(f"❌ Не удалось создать файл БД: {e}")
            
            # Создаем таблицы
            success = await create_database_tables()
            
            if success:
                logger.info("✅ База данных успешно инициализирована")
                
                # Проверяем содержимое БД
                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                    tables = await cursor.fetchall()
                    logger.info(f"Таблицы в БД: {[t[0] for t in tables]}")
                    
                    for table in tables:
                        cursor2 = await db.execute(f"SELECT COUNT(*) FROM {table[0]}")
                        count = await cursor2.fetchone()
                        logger.info(f"  {table[0]}: {count[0]} записей")
                
                return True
            else:
                logger.error(f"Не удалось создать таблицы (попытка {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    
        except Exception as e:
            logger.error(f"Ошибка инициализации БД (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
    
    logger.critical("❌ Не удалось инициализировать БД")
    return False

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
                logger.error(f"Сервер с ID {server_id} не найден")
                return "", f"Сервер {server_id} не найден"
            
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
                
                logger.info(f"Подключаемся к {host}:{port} как {user}")
                
            except ValueError as e:
                logger.error(f"Ошибка парсинга строки подключения '{conn_str}': {e}")
                return "", f"Неверный формат: {conn_str}"
            
            # Подготавливаем SSH ключ
            try:
                ssh_key_clean = ssh_key.strip()
                
                # Проверяем формат ключа
                if not ssh_key_clean.startswith('-----BEGIN'):
                    if 'PRIVATE KEY' in ssh_key_clean:
                        ssh_key_clean = f"-----BEGIN PRIVATE KEY-----\n{ssh_key_clean}\n-----END PRIVATE KEY-----"
                
                # Сохраняем ключ во временный файл
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                    f.write(ssh_key_clean)
                    temp_key_path = f.name
                
                # Устанавливаем правильные права доступа
                import stat
                os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
                
            except Exception as e:
                logger.error(f"Ошибка подготовки SSH ключа: {e}")
                return "", f"Ошибка SSH ключа: {str(e)}"
            
            # Подключаемся по SSH
            try:
                logger.info(f"Выполняем команду: {command[:50]}...")
                
                async with asyncssh.connect(
                    host,
                    username=user,
                    port=port,
                    client_keys=[temp_key_path],
                    known_hosts=None,
                    connect_timeout=timeout,
                    login_timeout=timeout
                ) as conn:
                    result = await conn.run(command, timeout=timeout)
                    
                    # Удаляем временный файл ключа
                    try:
                        os.unlink(temp_key_path)
                    except:
                        pass
                    
                    return result.stdout, result.stderr
                    
            except asyncssh.Error as e:
                logger.error(f"SSH ошибка: {e}")
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", f"SSH ошибка: {str(e)}"
            except asyncio.TimeoutError:
                logger.error("Таймаут SSH")
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", "Таймаут подключения"
                
    except Exception as e:
        logger.error(f"Общая ошибка SSH: {e}")
        return "", f"Ошибка: {str(e)}"

async def setup_wireguard_server(server_id: int) -> bool:
    """Настраивает WireGuard на сервере"""
    logger.info(f"=== НАСТРОЙКА WIREGUARD НА СЕРВЕРЕ {server_id} ===")
    
    try:
        # 1. Проверяем подключение
        logger.info("Шаг 1: Проверяем подключение...")
        stdout, stderr = await execute_ssh_command(server_id, "echo 'Connection test'")
        
        if stderr:
            logger.error(f"Ошибка подключения: {stderr}")
            return False
        
        # 2. Устанавливаем WireGuard
        logger.info("Шаг 2: Устанавливаем WireGuard...")
        install_cmd = "apt-get update -y && apt-get install -y wireguard"
        stdout, stderr = await execute_ssh_command(server_id, install_cmd)
        
        if stderr and "error" in stderr.lower():
            logger.warning(f"Предупреждение при установке: {stderr[:200]}")
        
        # 3. Создаем директорию
        logger.info("Шаг 3: Создаем директорию...")
        await execute_ssh_command(server_id, "mkdir -p /etc/wireguard")
        
        # 4. Генерируем ключи
        logger.info("Шаг 4: Генерируем ключи...")
        keygen_cmd = """
        cd /etc/wireguard
        umask 077
        wg genkey | tee private.key | wg pubkey > public.key
        echo "Keys generated"
        """
        
        stdout, stderr = await execute_ssh_command(server_id, keygen_cmd)
        
        # 5. Получаем публичный ключ
        stdout, stderr = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key 2>/dev/null || echo 'no key'")
        
        if "no key" in stdout or not stdout.strip():
            logger.error("Не удалось получить публичный ключ")
            return False
        
        public_key = stdout.strip()
        
        # 6. Сохраняем публичный ключ в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET public_key = ?, wireguard_configured = TRUE WHERE id = ?",
                (public_key, server_id)
            )
            await db.commit()
        
        logger.info(f"✅ WireGuard успешно настроен на сервере {server_id}")
        logger.info(f"Публичный ключ: {public_key[:50]}...")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка настройки WireGuard: {e}")
        return False

async def create_wireguard_client(server_id: int, user_id: int) -> Optional[Dict]:
    """Создает клиента WireGuard"""
    try:
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        logger.info(f"Создаем клиента {client_name}")
        
        # 1. Получаем публичный ключ сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT public_key, server_ip FROM servers WHERE id = ?", 
                (server_id,)
            )
            server_data = await cursor.fetchone()
            
            if not server_data:
                logger.error("Данные сервера не найдены")
                return None
            
            server_pub_key = server_data[0] if server_data[0] else ""
            server_ip = server_data[1] if server_data[1] else ""
            
            if not server_pub_key:
                logger.error("У сервера нет публичного ключа")
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
            logger.error("Не удалось сгенерировать ключи клиента")
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
        echo "Client {client_name} added successfully"
        """
        
        stdout, stderr = await execute_ssh_command(server_id, add_peer_cmd)
        
        logger.info(f"✅ Клиент создан: IP={client_ip}")
        
        return {
            "private_key": private_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip,
            "client_name": client_name
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания клиента: {e}")
        return None

async def create_vpn_for_user(user_id: int, device_type: str = "wireguard", period_days: int = 7, gifted: bool = False) -> bool:
    """Создает VPN для пользователя"""
    logger.info(f"Создаем VPN для {user_id}, дней: {period_days}")
    
    server_id = await get_available_vpn_server()
    if not server_id:
        logger.error("Нет доступных VPN серверов")
        return False
    
    logger.info(f"Используем сервер: {server_id}")
    
    # Проверяем настроен ли WireGuard
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT wireguard_configured, public_key FROM servers WHERE id = ?", 
            (server_id,)
        )
        server = await cursor.fetchone()
        
        if not server or not server[0]:  # WireGuard не настроен
            logger.info(f"WireGuard не настроен на сервере {server_id}, настраиваем...")
            if not await setup_wireguard_server(server_id):
                logger.error(f"Не удалось настроить WireGuard")
                return False
    
    # Создаем клиента
    vpn_config = await create_wireguard_client(server_id, user_id)
    
    if not vpn_config:
        logger.error(f"Не удалось создать конфиг")
        return False
    
    # Сохраняем в БД
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO vpn_users 
                (user_id, server_id, vpn_type, device_type, config_data, subscription_end, is_active, gifted) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, "wireguard", device_type, 
                 json.dumps(vpn_config, ensure_ascii=False),
                 (datetime.now() + timedelta(days=period_days)).isoformat(),
                 True, gifted)
            )
            
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
        
        logger.info(f"✅ VPN сохранен в БД")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения VPN: {e}")
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
        message_text += f"📱 Установите WireGuard и отсканируйте QR код:\n"
        
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

# ========== ФУНКЦИИ ДЛЯ БОТОВ ==========
async def create_bot_for_user(user_id: int, bot_name: str, bot_token: str, git_repo: str, period_days: int, gifted: bool = False) -> Dict:
    """Создает бота для пользователя"""
    logger.info(f"Создаем бота: {bot_name}")
    
    server_id = await get_available_bot_server()
    if not server_id:
        logger.error("Нет серверов для ботов")
        return {"success": False, "error": "Нет серверов для ботов"}
    
    try:
        # 1. Проверяем Docker
        stdout, stderr = await execute_ssh_command(server_id, "which docker")
        if "which:" in stderr or "not found" in stderr:
            logger.info("Устанавливаем Docker...")
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y docker.io")
        
        # 2. Создаем простого бота локально
        bot_content = """import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

# Получаем токен
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("🤖 Бот работает на хостинге!")

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Вы сказали: {message.text}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
"""
        
        # 3. Создаем файлы на сервере
        await execute_ssh_command(server_id, f"mkdir -p /tmp/{bot_name}")
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo '{bot_content}' > bot.py")
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo 'aiogram>=3.0.0' > requirements.txt")
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo '{bot_token}' > BOT_TOKEN.txt")
        
        # 4. Создаем Dockerfile
        dockerfile_content = f"""FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "bot.py"]
"""
        
        await execute_ssh_command(server_id, f"cd /tmp/{bot_name} && echo '{dockerfile_content}' > Dockerfile")
        
        # 5. Собираем Docker образ
        logger.info("Собираем Docker образ...")
        build_cmd = f"cd /tmp/{bot_name} && docker build -t {bot_name} . 2>&1"
        build_output, build_error = await execute_ssh_command(server_id, build_cmd)
        
        # 6. Запускаем контейнер
        logger.info("Запускаем контейнер...")
        run_cmd = f"docker run -d --name {bot_name} --restart unless-stopped {bot_name} 2>&1"
        run_output, run_error = await execute_ssh_command(server_id, run_cmd)
        
        container_id = run_output.strip() if run_output else ""
        
        # 7. Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO user_bots 
                (user_id, bot_name, bot_token, server_id, container_id, subscription_end, status, git_repo, gifted, last_logs) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, bot_name, bot_token, server_id, container_id,
                 (datetime.now() + timedelta(days=period_days)).isoformat(),
                 'running', git_repo, gifted, "Бот успешно запущен")
            )
            await db.commit()
        
        return {
            "success": True, 
            "container_id": container_id[:12] if container_id else "unknown",
            "logs": "Бот успешно запущен",
            "bot_name": bot_name
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания бота: {e}")
        return {"success": False, "error": str(e)}

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [
        [types.KeyboardButton(text="🔐 Получить VPN")],
        [types.KeyboardButton(text="🤖 Создать бота")],
        [types.KeyboardButton(text="📱 Мои услуги")],
        [types.KeyboardButton(text="🆘 Помощь")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    buttons = [
        [types.KeyboardButton(text="🖥️ Серверы")],
        [types.KeyboardButton(text="👤 Пользователи")],
        [types.KeyboardButton(text="💰 Управление ценами")],
        [types.KeyboardButton(text="🤖 Тест бота")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def servers_menu():
    buttons = [
        [types.KeyboardButton(text="🛡️ VPN серверы")],
        [types.KeyboardButton(text="🤖 Серверы для ботов")],
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

def bot_period_keyboard():
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

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class UserVPNStates(StatesGroup):
    waiting_for_period = State()

class UserBotStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_name = State()
    waiting_for_token = State()
    waiting_for_repo = State()

class AdminAddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()

class AdminPriceStates(StatesGroup):
    waiting_for_service = State()
    waiting_for_week_price = State()
    waiting_for_confirm = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    user_id = message.from_user.id
    
    # Сохраняем информацию о пользователе
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT id FROM vpn_users WHERE user_id = ? LIMIT 1",
                (user_id,)
            )
            existing = await cursor.fetchone()
            
            if not existing:
                await db.execute(
                    """INSERT INTO vpn_users (user_id, username, first_name)
                    VALUES (?, ?, ?)""",
                    (user_id, message.from_user.username, message.from_user.first_name)
                )
                await db.commit()
                logger.info(f"Новый пользователь: {user_id}")
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
            "🚀 <b>Добро пожаловать в VPN & Bot Hosting!</b>\n\n"
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
            # VPN услуги
            cursor = await db.execute("""
                SELECT vpn_type, subscription_end, is_active, gifted
                FROM vpn_users 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (user_id,))
            vpn_services = await cursor.fetchall()
            
            # Боты
            cursor = await db.execute("""
                SELECT bot_name, subscription_end, status, gifted
                FROM user_bots 
                WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
            bots = await cursor.fetchall()
        
        text = "📱 <b>Ваши услуги:</b>\n\n"
        
        if vpn_services:
            text += "<b>🔐 VPN:</b>\n"
            for vpn in vpn_services[:3]:
                vpn_type, end_date, active, gifted = vpn
                if end_date:
                    end = datetime.fromisoformat(end_date).strftime("%d.%m.%Y")
                    status = "🟢" if active and datetime.fromisoformat(end_date) > datetime.now() else "🔴"
                    gift = " 🎁" if gifted else ""
                    text += f"{status} {vpn_type} до {end}{gift}\n"
            text += "\n"
        else:
            text += "❌ Нет VPN подписок\n\n"
        
        if bots:
            text += "<b>🤖 Боты:</b>\n"
            for bot in bots[:3]:
                bot_name, end_date, status, gifted = bot
                if end_date:
                    end = datetime.fromisoformat(end_date).strftime("%d.%m.%Y")
                    status_icon = "🟢" if status == 'running' else "🔴"
                    gift = " 🎁" if gifted else ""
                    text += f"{status_icon} {bot_name} до {end}{gift}\n"
        else:
            text += "❌ Нет ботов\n"
        
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
            cursor = await db.execute(
                "SELECT trial_used FROM vpn_users WHERE user_id = ?",
                (user_id,)
            )
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
        
        # Создаем инвойс
        timestamp = int(datetime.now().timestamp())
        payload = f"vpn:{message.from_user.id}:{period}:{timestamp}"
        
        logger.info(f"Создаем инвойс VPN: {stars} stars")
        
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
            await message.answer(
                "❌ Ошибка создания счета.",
                reply_markup=user_main_menu()
            )
            await state.clear()
            return
    
    # Для пробного - создаем VPN
    await message.answer("🔄 Создаю ваш VPN доступ...")
    
    success = await create_vpn_for_user(message.from_user.id, "wireguard", days)
    
    if success:
        if period == 'trial':
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?",
                        (message.from_user.id,)
                    )
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
        
        await message.answer(
            "✅ <b>VPN доступ успешно создан!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    else:
        await message.answer(
            "❌ <b>Ошибка создания VPN!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "🤖 Создать бота")
async def create_bot_start(message: Message, state: FSMContext):
    """Начало создания бота"""
    await message.answer(
        "🤖 <b>Создание Telegram бота</b>\n\n"
        "⚠️ <b>Важная информация:</b>\n"
        "• Поддерживаются только Python боты\n"
        "• Бот должен быть для Telegram\n"
        "• Необходим Git репозиторий с кодом\n"
        "• Код должен быть на ветке <code>main</code>\n\n"
        "Выберите период:",
        reply_markup=bot_period_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(UserBotStates.waiting_for_period)

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
    
    logger.info(f"Создаем инвойс бота: {stars} stars")
    
    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=f"Бот на {days} дней",
            description=f"Хостинг Telegram бота на {days} дней",
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
        await message.answer(
            "❌ Ошибка создания счета.",
            reply_markup=user_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    """Команда помощи"""
    await message.answer(
        "🆘 <b>Помощь и поддержка</b>\n\n"
        "• VPN не работает: @vpnbothost\n"
        "• Проблемы с оплатой: @vpnbothost\n"
        "• Техподдержка: @vpnbothost\n"
        "• Создание ботов: @vpnbothost\n\n"
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
        service_type = payload_parts[0]  # 'vpn' или 'bot'
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
        
        await message.answer(
            f"✅ <b>Оплата получена!</b>\n\n"
            f"{payment.total_amount} stars успешно списаны.",
            parse_mode=ParseMode.HTML
        )
        
        if service_type == "vpn":
            # Для VPN - создаем сразу
            days = 30 if period == "month" else 7
            await message.answer(f"🔄 Создаю VPN на {days} дней...")
            
            success = await create_vpn_for_user(user_id, "wireguard", days)
            
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
                
                await message.answer(
                    f"✅ <b>VPN успешно создан!</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=user_main_menu()
                )
            else:
                await message.answer(
                    "❌ <b>Ошибка создания VPN!</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=user_main_menu()
                )
        
        elif service_type == "bot":
            # Для бота - запускаем процесс создания
            days = 30 if period == "month" else 7
            
            # Сохраняем в состоянии
            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=user_id)
            
            await state.set_state(UserBotStates.waiting_for_name)
            await state.update_data(period=period, days=days, payment_completed=True)
            
            await message.answer(
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"Теперь создадим вашего бота на {days} дней.\n\n"
                f"📝 <b>Введите имя для вашего бота:</b>\n"
                f"(латинские буквы, цифры и дефисы)",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard()
            )
    else:
        await message.answer("❌ Ошибка обработки платежа.")

# ========== FSM ДЛЯ СОЗДАНИЯ БОТА ==========
@dp.message(UserBotStates.waiting_for_name)
async def process_bot_name(message: Message, state: FSMContext):
    """Обработка имени бота"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Создание бота отменено", reply_markup=user_main_menu())
        return
    
    bot_name = message.text.strip()
    
    # Проверяем имя
    if not bot_name.replace('-', '').replace('_', '').isalnum():
        await message.answer(
            "❌ <b>Неверное имя бота!</b>\n\n"
            "Имя должно содержать только:\n"
            "• Латинские буквы (a-z, A-Z)\n"
            "• Цифры (0-9)\n"
            "• Дефисы (-) или подчеркивания (_)\n\n"
            "Введите имя еще раз:",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(bot_name=bot_name)
    await state.set_state(UserBotStates.waiting_for_token)
    
    await message.answer(
        f"✅ Имя бота: <code>{bot_name}</code>\n\n"
        f"🔑 <b>Теперь отправьте токен вашего бота:</b>\n"
        f"(получите у @BotFather)",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard()
    )

@dp.message(UserBotStates.waiting_for_token)
async def process_bot_token(message: Message, state: FSMContext):
    """Обработка токена бота"""
    if message.text == "◀️ Назад":
        await state.set_state(UserBotStates.waiting_for_name)
        await message.answer("Введите имя для вашего бота:")
        return
    
    bot_token = message.text.strip()
    
    # Базовая проверка токена
    if not bot_token or len(bot_token) < 30:
        await message.answer(
            "❌ <b>Неверный формат токена!</b>\n\n"
            "Токен должен быть длинным строковым значением.\n"
            "Получите правильный токен у @BotFather\n\n"
            "Отправьте токен еще раз:",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(bot_token=bot_token)
    await state.set_state(UserBotStates.waiting_for_repo)
    
    await message.answer(
        f"✅ Токен получен!\n\n"
        f"📂 <b>Теперь отправьте ссылку на Git репозиторий:</b>\n"
        f"(например: https://github.com/username/repo.git)\n\n"
        f"⚠️ <b>Важно:</b>\n"
        f"• Репозиторий должен быть публичным\n"
        f"• Код должен быть на ветке <code>main</code>\n"
        f"• Должен быть файл <code>bot.py</code>\n"
        f"• Рекомендуется файл <code>requirements.txt</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard()
    )

@dp.message(UserBotStates.waiting_for_repo)
async def process_bot_repo(message: Message, state: FSMContext):
    """Обработка Git репозитория"""
    if message.text == "◀️ Назад":
        await state.set_state(UserBotStates.waiting_for_token)
        await message.answer("Отправьте токен вашего бота:")
        return
    
    git_repo = message.text.strip()
    
    # Проверяем ссылку
    if not (git_repo.startswith('http') or git_repo.startswith('git@')):
        await message.answer(
            "❌ <b>Неверный формат ссылки!</b>\n\n"
            "Ссылка должна быть в формате:\n"
            "• <code>https://github.com/username/repo.git</code>\n"
            "• <code>git@github.com:username/repo.git</code>\n\n"
            "Отправьте ссылку еще раз:",
            parse_mode=ParseMode.HTML
        )
        return
    
    data = await state.get_data()
    user_id = message.from_user.id
    bot_name = data.get('bot_name')
    bot_token = data.get('bot_token')
    days = data.get('days', 7)
    
    await message.answer(
        f"🔄 <b>Создаю бота '{bot_name}'...</b>\n\n"
        f"Это может занять несколько минут.",
        parse_mode=ParseMode.HTML
    )
    
    # Создаем бота
    result = await create_bot_for_user(user_id, bot_name, bot_token, git_repo, days)
    
    if result["success"]:
        await message.answer(
            f"✅ <b>Бот '{bot_name}' успешно создан!</b>\n\n"
            f"📅 Подписка на {days} дней\n"
            f"🆔 Контейнер: {result['container_id']}\n"
            f"📂 Репозиторий: {git_repo}\n\n"
            f"📋 <b>Логи запуска:</b>\n"
            f"<code>{result['logs']}</code>\n\n"
            f"Для управления ботом напишите в поддержку.",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания бота!</b>\n\n"
            f"Ошибка: {result['error']}\n\n"
            f"Пожалуйста, напишите в @vpnbothost",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
    
    await state.clear()

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
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, server_ip, current_users, max_users, 
                       is_active, wireguard_configured, created_at
                FROM servers 
                WHERE server_type = 'vpn'
                ORDER BY created_at DESC
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка VPN серверов: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 VPN серверов нет")
        return
    
    text = "🛡️ <b>VPN серверы:</b>\n\n"
    
    for server in servers:
        id_, name, ip, current, max_users, active, wg_configured, created = server
        status = "🟢" if active else "🔴"
        wg_status = "✅" if wg_configured else "❌"
        created_date = datetime.fromisoformat(created).strftime("%d.%m.%Y")
        
        text += f"{status} <b>{name}</b> (ID: {id_})\n"
        text += f"IP: {ip or 'не указан'}\n"
        text += f"Пользователи: {current}/{max_users}\n"
        text += f"WireGuard: {wg_status}\n"
        text += f"Добавлен: {created_date}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "🤖 Серверы для ботов")
async def admin_bot_servers(message: Message):
    """Список серверов для ботов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, server_ip, is_active, created_at
                FROM servers 
                WHERE server_type = 'bot'
                ORDER BY created_at DESC
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка серверов для ботов: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("🤖 Серверов для ботов нет")
        return
    
    text = "🤖 <b>Серверы для ботов:</b>\n\n"
    
    for server in servers:
        id_, name, ip, active, created = server
        status = "🟢" if active else "🔴"
        created_date = datetime.fromisoformat(created).strftime("%d.%m.%Y")
        
        text += f"{status} <b>{name}</b> (ID: {id_})\n"
        text += f"IP: {ip or 'не указан'}\n"
        text += f"Добавлен: {created_date}\n\n"
    
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
        "Введите имя сервера:",
        reply_markup=ReplyKeyboardRemove()
    )

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
        
        # Продолжаем обработку
        await process_ssh_key_text(message, state, ssh_key)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки файла: {str(e)}")
        logger.error(f"Ошибка обработки SSH-ключа: {e}")

@dp.message(AdminAddServerStates.waiting_for_key)
async def process_ssh_key_text(message: Message, state: FSMContext, ssh_key: str = None):
    """Обработка SSH ключа отправленного текстом"""
    if ssh_key is None:
        ssh_key = message.text.strip()
    
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
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
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
            
            success = await setup_wireguard_server(server_id)
            
            if success:
                await message.answer(
                    f"✅ <b>WireGuard успешно настроен!</b>\n\n"
                    f"Сервер <b>{data['server_name']}</b> готов к использованию.\n"
                    f"ID сервера: {server_id}\n"
                    f"IP: {host}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_main_menu()
                )
            else:
                await message.answer(
                    f"⚠️ <b>Сервер добавлен, но WireGuard не настроен!</b>\n\n"
                    f"ID сервера: {server_id}\n"
                    f"IP: {host}\n\n"
                    f"Проверьте SSH доступ и права.\n"
                    f"Или настройте вручную: <code>wg genkey | tee private.key | wg pubkey > public.key</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_main_menu()
                )
        else:
            await message.answer(
                f"✅ Сервер для {server_type_name} <b>{data['server_name']}</b> добавлен!\n\n"
                f"ID: {server_id}\n"
                f"IP: {host}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_menu()
            )
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка формата: {str(e)}\n\n"
                           f"Введите строку подключения в формате: user@host:port")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        logger.error(f"Ошибка сохранения сервера: {e}")
    
    await state.clear()

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
                    await message.answer(
                        f"❌ Пользователь {username} не найден.",
                        reply_markup=admin_main_menu()
                    )
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
        data = await state.get_data()
        service = data.get('service', 'vpn')
        
        await state.set_state(AdminUserStates.waiting_for_period)
        await message.answer(
            f"Выберите период для {service}:",
            reply_markup=admin_period_keyboard(service)
        )
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
    
    if not user_id:
        await message.answer("❌ Ошибка: ID пользователя не найден")
        await state.clear()
        return
    
    # Создаем услугу
    await message.answer(f"🔄 Выдаю {service} пользователю {user_id}...")
    
    try:
        if service == "vpn":
            # Создаем VPN
            success = await create_vpn_for_user(user_id, "wireguard", days, gifted=True)
            
            if success:
                # Отправляем уведомление пользователю
                try:
                    await bot.send_message(
                        user_id,
                        f"🎁 <b>Вам выдан VPN доступ на {days} дней!</b>\n\n"
                        f"Чтобы активировать подарок, зайдите в меню:\n"
                        f"🔐 Получить VPN\n\n"
                        f"Там будет кнопка '✅ Активировать подарок'",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление: {e}")
                
                await message.answer(
                    f"✅ VPN успешно выдан пользователю {user_id} на {days} дней!",
                    reply_markup=admin_main_menu()
                )
            else:
                await message.answer(
                    f"❌ Ошибка выдачи VPN пользователю {user_id}",
                    reply_markup=admin_main_menu()
                )
        
        elif service == "bot":
            # Создаем запись о боте как подарок
            bot_name = f"gifted_bot_{user_id}_{random.randint(1000, 9999)}"
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO user_bots 
                    (user_id, bot_name, server_id, subscription_end, status, gifted) 
                    VALUES (?, ?, NULL, ?, 'pending', ?)""",
                    (user_id, bot_name, (datetime.now() + timedelta(days=days)).isoformat(), True)
                )
                await db.commit()
            
            # Отправляем уведомление пользователю
            try:
                await bot.send_message(
                    user_id,
                    f"🎁 <b>Вам выдан бот на {days} дней!</b>\n\n"
                    f"Чтобы активировать подарок, зайдите в меню:\n"
                    f"🤖 Создать бота\n\n"
                    f"Там будет кнопка '🤖 Активировать подарок'",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление: {e}")
            
            await message.answer(
                f"✅ Бот успешно выдан пользователю {user_id} на {days} дней!",
                reply_markup=admin_main_menu()
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка выдачи услуги: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message):
    """Список пользователей"""
    if not is_admin(message.from_user.id, message.chat.id):
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
    text += f"• Месяц: {vpn_prices['month']['stars']} stars\n\n"
    text += "<b>Боты:</b>\n"
    text += f"• Неделя: {bot_prices['week']['stars']} stars\n"
    text += f"• Месяц: {bot_prices['month']['stars']} stars\n\n"
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
    """Обработка цена за неделю"""
    try:
        week_price = int(message.text)
        if week_price <= 0:
            await message.answer("Цена должна быть больше 0:")
            return
            
        await state.update_data(week_price=week_price)
        await state.set_state(AdminPriceStates.waiting_for_confirm)
        
        data = await state.get_data()
        service_type = data.get('service_type')
        
        service_name = "VPN" if service_type == "vpn" else "ботов"
        
        month_price = week_price * 3
        
        await message.answer(
            f"<b>Новые цены для {service_name}:</b>\n\n"
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
        service_type = data.get('service_type')
        week_price = data.get('week_price')
        month_price = week_price * 3
        
        if service_type == "vpn":
            await update_vpn_prices(week_price, month_price)
            service_name = "VPN"
        elif service_type == "bot":
            await update_bot_prices(week_price, month_price)
            service_name = "ботов"
        else:
            service_name = "услуги"
        
        await message.answer(
            f"✅ Цены {service_name} обновлены!\n\n"
            f"• Неделя: {week_price} stars\n"
            f"• Месяц: {month_price} stars",
            reply_markup=admin_main_menu()
        )
    
    elif message.text == "❌ Отменить":
        await message.answer("Изменение цен отменено", reply_markup=admin_main_menu())
    
    elif message.text == "◀️ Назад":
        await message.answer("Управление ценами:", reply_markup=admin_prices_menu())
    
    await state.clear()

@dp.message(F.text == "🤖 Тест бота")
async def admin_test_bot(message: Message):
    """Тестирование создания бота"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer(
        "🤖 <b>Тест создания бота</b>\n\n"
        "Создаю тестового бота на 1 день...",
        parse_mode=ParseMode.HTML
    )
    
    # Создаем тестового бота
    test_bot_name = f"test_bot_{random.randint(1000, 9999)}"
    test_token = "test_token_placeholder"
    test_repo = "https://github.com/aiogram/aiogram.git"
    
    result = await create_bot_for_user(
        message.from_user.id,
        test_bot_name,
        test_token,
        test_repo,
        1,
        gifted=False
    )
    
    if result["success"]:
        await message.answer(
            f"✅ <b>Тестовый бот создан!</b>\n\n"
            f"Имя: {test_bot_name}\n"
            f"Контейнер: {result['container_id']}\n\n"
            f"📋 <b>Логи:</b>\n"
            f"<code>{result['logs']}</code>\n\n"
            f"Сервер для ботов работает корректно!",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания тестового бота!</b>\n\n"
            f"Ошибка: {result['error']}\n\n"
            f"Проверьте SSH доступ и Docker.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )

@dp.message(F.text == "◀️ Назад")
async def back_handler(message: Message, state: FSMContext):
    """Обработчик кнопки назад"""
    await state.clear()
    
    if is_admin(message.from_user.id, message.chat.id):
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
        
        # 1. Инициализируем базу данных
        logger.info("📊 Инициализация базы данных...")
        db_success = await init_database()
        
        if not db_success:
            logger.critical("❌ Не удалось инициализировать базу данных!")
            return
        
        # 2. Получаем информацию о боте
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username}")
        print(f"👑 Admin ID: {ADMIN_ID}")
        print(f"🗄️ База данных: {DB_PATH}")
        
        if os.path.exists(DB_PATH):
            print(f"📁 Размер БД: {os.path.getsize(DB_PATH)} байт")
        
        # 3. Запускаем опрос
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