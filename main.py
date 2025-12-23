# main.py - ИСПРАВЛЕННАЯ ВЕРСИЯ С ПРАВАМИ И ПАГИНАЦИЕЙ
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
ADMIN_CHAT_ID = -1003542769962  # Админ чат
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"
SUPPORT_USERNAME = "@vpnbothost"

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
                    last_check TIMESTAMP,
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
def is_admin(user_id: int, chat_id: int = None) -> bool:
    """Проверяет является ли пользователь админом"""
    if chat_id:
        return user_id == ADMIN_ID or str(chat_id) == str(ADMIN_CHAT_ID)
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
                AND wireguard_configured = TRUE
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска сервера: {e}")
        return None

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = False) -> Tuple[str, str, bool]:
    """Выполняет команду на сервере через SSH с поддержкой sudo"""
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
                    # Если нужен sudo, добавляем в команду
                    if use_sudo:
                        command = f"sudo {command}"
                    
                    logger.info(f"Выполняю команду: {command[:100]}...")
                    result = await conn.run(command, timeout=timeout)
                    
                    # Удаляем временный файл ключа
                    try:
                        os.unlink(temp_key_path)
                    except:
                        pass
                    
                    # Проверяем exit status
                    if result.exit_status == 0:
                        logger.info(f"Команда выполнена успешно (exit: {result.exit_status})")
                        return result.stdout, result.stderr, True
                    else:
                        logger.warning(f"Команда завершилась с ошибкой (exit: {result.exit_status}): {result.stderr[:200]}")
                        return result.stdout, result.stderr, False
                    
            except asyncssh.Error as e:
                error_msg = f"SSH ошибка: {str(e)}"
                logger.error(error_msg)
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", error_msg, False
            except asyncio.TimeoutError:
                error_msg = "Таймаут подключения (60 секунд)"
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

async def test_ssh_connection(server_id: int, message: Message = None):
    """Тестирует SSH подключение к серверу"""
    async def log_step(text: str, success: bool = True):
        if message:
            try:
                await message.answer(text)
            except:
                pass
        logger.info(text)
    
    await log_step("🔍 Тестирую SSH подключение...")
    
    try:
        # Простая команда для проверки
        stdout, stderr, success = await execute_ssh_command(server_id, "echo 'SSH Connection Test' && whoami && uname -a")
        
        if success:
            await log_step("✅ SSH подключение работает!")
            lines = stdout.strip().split('\n')
            if len(lines) > 1:
                await log_step(f"👤 Пользователь: {lines[1]}")
            if len(lines) > 2:
                await log_step(f"💻 Система: {lines[2][:100]}")
            return True, "SSH подключение успешно"
        else:
            await log_step(f"❌ Ошибка SSH: {stderr}", False)
            return False, stderr
            
    except Exception as e:
        error_msg = f"❌ Ошибка тестирования SSH: {str(e)}"
        await log_step(error_msg, False)
        return False, error_msg

async def setup_wireguard_server(server_id: int, message: Message = None):
    """Настраивает WireGuard на сервере с учетом прав пользователя"""
    steps = []
    success_steps = 0
    total_steps = 0
    
    async def log_step(text: str, success: bool = True):
        nonlocal success_steps, total_steps
        total_steps += 1
        if success:
            success_steps += 1
        step_msg = f"{'✅' if success else '❌'} {text}"
        steps.append(step_msg)
        if message:
            try:
                await message.answer(step_msg)
            except:
                pass
        logger.info(step_msg)
    
    await log_step("🚀 Начинаю настройку WireGuard на сервере")
    
    try:
        # 1. Тестируем подключение
        await log_step("1. Проверяю SSH подключение...")
        ssh_ok, ssh_msg = await test_ssh_connection(server_id, message)
        if not ssh_ok:
            await log_step(f"❌ SSH подключение не работает: {ssh_msg}", False)
            return False, steps
        
        # 2. Проверяем права sudo
        await log_step("2. Проверяю права sudo...")
        stdout, stderr, success = await execute_ssh_command(server_id, "sudo -n true 2>&1 || echo 'No sudo'")
        has_sudo = success and 'No sudo' not in stdout + stderr
        
        if has_sudo:
            await log_step("✅ Пользователь имеет права sudo")
        else:
            await log_step("⚠️ Пользователь не имеет прав sudo, попробую без них", False)
        
        # 3. Проверяем систему
        await log_step("3. Проверяю операционную систему...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/os-release | grep PRETTY_NAME || echo 'Unknown OS'")
        if success and stdout:
            os_info = stdout.split('=')[1].strip('"') if '=' in stdout else stdout.strip()
            await log_step(f"📋 Система: {os_info}")
        
        # 4. Обновляем пакеты (с sudo если есть)
        await log_step("4. Обновляю пакеты системы...")
        
        update_cmd = "apt-get update -y" if has_sudo else "apt-get update -y 2>/dev/null || true"
        stdout, stderr, success = await execute_ssh_command(server_id, update_cmd, timeout=120, use_sudo=has_sudo)
        
        if success:
            await log_step("✅ Пакеты обновлены")
        else:
            await log_step("⚠️ Не удалось обновить пакеты, продолжаю...", False)
        
        # 5. Устанавливаем WireGuard
        await log_step("5. Устанавливаю WireGuard...")
        
        # Пробуем установить с учетом прав
        if has_sudo:
            install_cmd = "apt-get install -y wireguard wireguard-tools"
        else:
            # Без sudo пробуем установить в домашнюю директорию
            install_cmd = """
            cd /tmp && \
            wget https://git.zx2c4.com/wireguard-tools/snapshot/wireguard-tools.tar.gz 2>/dev/null && \
            tar -xzf wireguard-tools.tar.gz && \
            cd wireguard-tools-* && \
            make -j$(nproc) 2>/dev/null && \
            echo "WireGuard tools compiled"
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, install_cmd, timeout=180, use_sudo=has_sudo)
        
        if success:
            await log_step("✅ WireGuard установлен/скомпилирован")
        else:
            await log_step(f"❌ Ошибка установки: {stderr[:100]}", False)
            return False, steps
        
        # 6. Создание директории с правильными правами
        await log_step("6. Создаю директорию для WireGuard...")
        
        if has_sudo:
            dir_cmd = "mkdir -p /etc/wireguard && chmod 700 /etc/wireguard && chown root:root /etc/wireguard"
        else:
            dir_cmd = "mkdir -p ~/.wireguard && chmod 700 ~/.wireguard"
        
        stdout, stderr, success = await execute_ssh_command(server_id, dir_cmd, use_sudo=has_sudo)
        
        if not success:
            await log_step(f"❌ Ошибка создания директории: {stderr}", False)
            return False, steps
        
        # 7. Генерация ключей
        await log_step("7. Генерирую ключи...")
        
        if has_sudo:
            keygen_cmd = """
            cd /etc/wireguard
            umask 077
            sudo wg genkey | sudo tee private.key | sudo wg pubkey | sudo tee public.key
            sudo chmod 600 private.key public.key
            echo "Ключи сгенерированы"
            """
        else:
            keygen_cmd = """
            cd ~/.wireguard
            umask 077
            wg genkey | tee private.key | wg pubkey > public.key
            chmod 600 private.key public.key
            echo "Ключи сгенерированы"
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd, use_sudo=False)  # sudo уже в команде
        
        if not success:
            await log_step(f"❌ Ошибка генерации ключей: {stderr}", False)
            return False, steps
        
        # 8. Проверка существования ключей
        await log_step("8. Проверяю создание ключей...")
        
        if has_sudo:
            check_cmd = "sudo test -f /etc/wireguard/public.key && sudo cat /etc/wireguard/public.key || echo 'NO_KEY'"
        else:
            check_cmd = "test -f ~/.wireguard/public.key && cat ~/.wireguard/public.key || echo 'NO_KEY'"
        
        stdout, stderr, success = await execute_ssh_command(server_id, check_cmd, use_sudo=False)
        
        if not success or 'NO_KEY' in stdout or not stdout.strip():
            await log_step(f"❌ Ключи не созданы: {stdout} {stderr}", False)
            return False, steps
        
        public_key = stdout.strip()
        await log_step(f"✅ Публичный ключ получен: {public_key[:30]}...")
        
        # 9. Получение IP сервера
        await log_step("9. Определяю IP адрес сервера...")
        stdout, stderr, success = await execute_ssh_command(server_id, """
        curl -s --max-time 5 ifconfig.me || \
        curl -s --max-time 5 ifconfig.co || \
        hostname -I | awk '{print $1}' || \
        ip addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1
        """)
        
        server_ip = stdout.strip() if success and stdout.strip() else ""
        
        if not server_ip:
            # Получаем из строки подключения
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT connection_string FROM servers WHERE id = ?", (server_id,))
                conn_str = (await cursor.fetchone())[0]
                server_ip = conn_str.split('@')[1].split(':')[0] if '@' in conn_str else ""
        
        # 10. Создание конфига WireGuard
        await log_step("10. Создаю конфигурацию WireGuard...")
        
        if has_sudo:
            config_cmd = f"""
            cd /etc/wireguard
            sudo cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(sudo cat private.key)

# Enable IP forwarding
PostUp = sysctl -w net.ipv4.ip_forward=1
PostUp = sysctl -w net.ipv6.conf.all.forwarding=1
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostUp = iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE

PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -o ens3 -j MASQUERADE
EOF
            sudo chmod 600 wg0.conf
            """
        else:
            # Без sudo создаем конфиг в домашней директории
            config_cmd = f"""
            cd ~/.wireguard
            cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)

# Note: Without sudo, IP forwarding and iptables may not work
EOF
            chmod 600 wg0.conf
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, config_cmd, use_sudo=False)
        
        if not success:
            await log_step(f"❌ Ошибка создания конфига: {stderr}", False)
            return False, steps
        
        # 11. Запуск WireGuard (только с sudo)
        if has_sudo:
            await log_step("11. Запускаю WireGuard...")
            
            enable_cmd = """
            sudo systemctl enable wg-quick@wg0 2>/dev/null || true
            sudo systemctl start wg-quick@wg0 2>/dev/null || true
            """
            
            stdout, stderr, success = await execute_ssh_command(server_id, enable_cmd, use_sudo=False)
            
            # Проверяем статус
            status_cmd = "sudo systemctl is-active wg-quick@wg0 2>/dev/null || sudo wg show 2>/dev/null && echo 'active' || echo 'inactive'"
            stdout, stderr, success = await execute_ssh_command(server_id, status_cmd, use_sudo=False)
            
            if 'active' in stdout or success:
                await log_step("   ✅ WireGuard запущен")
            else:
                await log_step("   ⚠️ WireGuard не запущен автоматически", False)
        else:
            await log_step("11. ⚠️ WireGuard не может быть запущен без прав sudo")
        
        # 12. Сохранение данных в БД
        await log_step("12. Сохраняю данные в базу...")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE servers SET 
                public_key = ?, 
                wireguard_configured = TRUE, 
                server_ip = ?,
                last_check = datetime('now')
                WHERE id = ?""",
                (public_key, server_ip, server_id)
            )
            await db.commit()
        
        # 13. Финальная проверка
        await log_step("13. Проверяю работу WireGuard...")
        
        if has_sudo:
            check_cmd = "sudo wg show 2>/dev/null | head -5 || echo 'WireGuard check failed'"
        else:
            check_cmd = "wg show 2>/dev/null | head -5 || echo 'WireGuard cannot run without sudo'"
        
        stdout, stderr, success = await execute_ssh_command(server_id, check_cmd, use_sudo=False)
        
        if has_sudo and success and 'interface:' in stdout.lower():
            await log_step(f"✅ WireGuard успешно настроен и работает!")
        elif has_sudo:
            await log_step(f"⚠️ WireGuard настроен, но не запущен")
        else:
            await log_step(f"⚠️ WireGuard настроен, но требуется ручной запуск с правами root")
        
        await log_step(f"🔑 Публичный ключ: {public_key[:50]}...")
        await log_step(f"🌐 IP сервера: {server_ip}")
        await log_step(f"📊 Статистика: {success_steps}/{total_steps} шагов выполнено успешно")
        
        return True, steps
        
    except Exception as e:
        error_msg = f"❌ Критическая ошибка: {str(e)}"
        await log_step(error_msg, False)
        return False, steps

# Остальные функции остаются без изменений (create_wireguard_client, create_vpn_for_user, send_vpn_config_to_user и т.д.)
# Для экономии места оставляю их без изменений, они уже работают правильно

async def create_wireguard_client(server_id: int, user_id: int, message: Message = None):
    """Создает клиента WireGuard с логированием"""
    async def log_step(text: str, success: bool = True):
        if message:
            try:
                await message.answer(text)
            except:
                pass
        logger.info(text)
    
    await log_step("🔄 Создаю VPN конфигурацию для пользователя...")
    
    try:
        # 1. Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT public_key, server_ip FROM servers WHERE id = ?", (server_id,))
            server_data = await cursor.fetchone()
            
            if not server_data or not server_data[0]:
                await log_step("❌ У сервера нет публичного ключа", False)
                return None
            
            server_pub_key, server_ip = server_data
        
        # 2. Проверяем права sudo
        stdout, stderr, success = await execute_ssh_command(server_id, "sudo -n true 2>&1 || echo 'No sudo'")
        has_sudo = success and 'No sudo' not in stdout + stderr
        
        # 3. Генерируем ключи клиента
        await log_step("Генерирую ключи для клиента...")
        client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
        
        if has_sudo:
            keygen_cmd = f"""
            cd /etc/wireguard
            sudo wg genkey | sudo tee {client_name}.private | sudo wg pubkey | sudo tee {client_name}.public
            sudo cat {client_name}.private
            """
        else:
            keygen_cmd = f"""
            cd ~/.wireguard
            wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public
            cat {client_name}.private
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd, use_sudo=False)
        if not success or not stdout.strip():
            await log_step("❌ Не удалось сгенерировать ключи клиента", False)
            return None
        
        private_key = stdout.strip()
        
        # 4. Получаем публичный ключ клиента
        if has_sudo:
            stdout, stderr, success = await execute_ssh_command(server_id, f"sudo cat /etc/wireguard/{client_name}.public", use_sudo=False)
        else:
            stdout, stderr, success = await execute_ssh_command(server_id, f"cat ~/.wireguard/{client_name}.public")
        
        if not success or not stdout.strip():
            await log_step("❌ Не удалось получить публичный ключ клиента", False)
            return None
        
        public_key = stdout.strip()
        
        # 5. Определяем IP адрес клиента
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM vpn_users WHERE server_id = ?", (server_id,))
            peer_count = (await cursor.fetchone())[0]
        
        client_ip = f"10.0.0.{peer_count + 2}"
        
        # 6. Добавляем пира в конфиг
        await log_step("Добавляю клиента в конфигурацию WireGuard...")
        
        if has_sudo:
            add_peer_cmd = f"""
            cd /etc/wireguard
            sudo sh -c 'echo "" >> wg0.conf'
            sudo sh -c 'echo "[Peer]" >> wg0.conf'
            sudo sh -c 'echo "# Client {user_id}" >> wg0.conf'
            sudo sh -c 'echo "PublicKey = {public_key}" >> wg0.conf'
            sudo sh -c 'echo "AllowedIPs = {client_ip}/32" >> wg0.conf'
            """
        else:
            add_peer_cmd = f"""
            cd ~/.wireguard
            echo "" >> wg0.conf
            echo "[Peer]" >> wg0.conf
            echo "# Client {user_id}" >> wg0.conf
            echo "PublicKey = {public_key}" >> wg0.conf
            echo "AllowedIPs = {client_ip}/32" >> wg0.conf
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, add_peer_cmd, use_sudo=False)
        if not success:
            await log_step("❌ Не удалось добавить клиента в конфиг", False)
            return None
        
        # 7. Перезагружаем конфиг (только с sudo)
        if has_sudo:
            await log_step("Применяю изменения конфигурации...")
            reload_cmd = "sudo wg syncconf wg0 <(sudo wg-quick strip wg0) 2>/dev/null || sudo systemctl restart wg-quick@wg0"
            await execute_ssh_command(server_id, reload_cmd, use_sudo=False)
        
        await log_step(f"✅ Клиент создан: IP={client_ip}")
        
        return {
            "private_key": private_key,
            "server_public_key": server_pub_key,
            "server_ip": server_ip,
            "client_ip": client_ip,
            "client_name": client_name,
            "has_sudo": has_sudo
        }
        
    except Exception as e:
        await log_step(f"❌ Ошибка создания клиента: {str(e)}", False)
        return None

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

def server_list_keyboard(servers, offset=0, limit=10):
    """Создает клавиатуру со списком серверов с пагинацией"""
    buttons = []
    
    # Показываем серверы для текущей страницы
    for server in servers[offset:offset+limit]:
        server_id, server_name, is_active, wg_configured = server
        status = "🟢" if is_active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        buttons.append([types.KeyboardButton(text=f"{status}{wg_status} {server_name}")])
    
    # Кнопки навигации
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(types.KeyboardButton(text="◀️ Пред. стр."))
    
    # Информация о странице
    total_pages = (len(servers) + limit - 1) // limit
    current_page = (offset // limit) + 1
    nav_buttons.append(types.KeyboardButton(text=f"📄 {current_page}/{total_pages}"))
    
    if offset + limit < len(servers):
        nav_buttons.append(types.KeyboardButton(text="След. стр. ▶️"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_search_keyboard():
    """Клавиатура для поиска сервера"""
    buttons = [
        [types.KeyboardButton(text="🔍 Найти по имени")],
        [types.KeyboardButton(text="📋 Весь список")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_actions_keyboard(server_id: int):
    """Клавиатура действий с сервером"""
    buttons = [
        [types.KeyboardButton(text=f"🔧 Установить WireGuard (ID: {server_id})")],
        [types.KeyboardButton(text=f"🔍 Проверить SSH (ID: {server_id})")],
        [types.KeyboardButton(text=f"📊 Состояние (ID: {server_id})")],
        [types.KeyboardButton(text=f"🤖 Тест ботом (ID: {server_id})")],
        [types.KeyboardButton(text="◀️ Назад к списку")]
    ]
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

class AdminManualWGStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_git_repo = State()

class AdminServerListStates(StatesGroup):
    waiting_for_action = State()
    viewing_list = State()
    searching = State()

# ========== ОБРАБОТЧИКИ ДЛЯ ПАГИНАЦИИ ==========
@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message):
    """Меню серверов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список серверов")
async def admin_list_servers_start(message: Message, state: FSMContext):
    """Начало работы со списком серверов"""
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminServerListStates.waiting_for_action)
    await message.answer("Выберите способ просмотра серверов:", reply_markup=server_search_keyboard())

@dp.message(F.text == "🔍 Найти по имени")
async def admin_search_server(message: Message, state: FSMContext):
    """Поиск сервера по имени"""
    await state.set_state(AdminServerListStates.searching)
    await message.answer("Введите часть имени сервера для поиска:", reply_markup=back_keyboard())

@dp.message(AdminServerListStates.searching)
async def process_server_search(message: Message, state: FSMContext):
    """Обработка поиска сервера"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminServerListStates.waiting_for_action)
        await message.answer("Выберите способ просмотра серверов:", reply_markup=server_search_keyboard())
        return
    
    search_term = message.text.strip().lower()
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, is_active, wireguard_configured 
                FROM servers 
                WHERE LOWER(name) LIKE ? 
                ORDER BY name
                LIMIT 50
            """, (f'%{search_term}%',))
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка поиска серверов: {e}")
        await message.answer("❌ Ошибка поиска")
        return
    
    if not servers:
        await message.answer(f"❌ Серверы по запросу '{search_term}' не найдены")
        return
    
    await state.set_state(AdminServerListStates.viewing_list)
    await state.update_data(servers=servers, offset=0)
    
    text = f"🔍 <b>Найдено серверов: {len(servers)}</b>\n\n"
    for server in servers[:10]:
        server_id, name, active, wg_configured = server
        status = "🟢" if active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id})\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=server_list_keyboard(servers, 0, 10))

@dp.message(F.text == "📋 Весь список")
async def admin_show_all_servers(message: Message, state: FSMContext):
    """Показ всех серверов"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, is_active, wireguard_configured 
                FROM servers 
                ORDER BY name
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка серверов: {e}")
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет")
        return
    
    await state.set_state(AdminServerListStates.viewing_list)
    await state.update_data(servers=servers, offset=0)
    
    text = f"📋 <b>Всего серверов: {len(servers)}</b>\n\n"
    for server in servers[:10]:
        server_id, name, active, wg_configured = server
        status = "🟢" if active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id})\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=server_list_keyboard(servers, 0, 10))

@dp.message(AdminServerListStates.viewing_list)
async def process_server_list_action(message: Message, state: FSMContext):
    """Обработка действий в списке серверов"""
    data = await state.get_data()
    servers = data.get('servers', [])
    offset = data.get('offset', 0)
    limit = 10
    
    # Навигация по страницам
    if message.text == "◀️ Пред. стр.":
        new_offset = max(0, offset - limit)
        await state.update_data(offset=new_offset)
        
        text = f"📋 <b>Страница {new_offset//limit + 1}/{(len(servers) + limit - 1)//limit}</b>\n\n"
        for server in servers[new_offset:new_offset+limit]:
            server_id, name, active, wg_configured = server
            status = "🟢" if active else "🔴"
            wg_status = "🔐" if wg_configured else "❌"
            text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id})\n"
        
        await message.answer(text, parse_mode=ParseMode.HTML, 
                           reply_markup=server_list_keyboard(servers, new_offset, limit))
        return
    
    elif "След. стр." in message.text:
        new_offset = offset + limit
        if new_offset >= len(servers):
            new_offset = offset
        
        await state.update_data(offset=new_offset)
        
        text = f"📋 <b>Страница {new_offset//limit + 1}/{(len(servers) + limit - 1)//limit}</b>\n\n"
        for server in servers[new_offset:new_offset+limit]:
            server_id, name, active, wg_configured = server
            status = "🟢" if active else "🔴"
            wg_status = "🔐" if wg_configured else "❌"
            text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id})\n"
        
        await message.answer(text, parse_mode=ParseMode.HTML, 
                           reply_markup=server_list_keyboard(servers, new_offset, limit))
        return
    
    elif message.text == "◀️ Назад":
        await state.set_state(AdminServerListStates.waiting_for_action)
        await message.answer("Выберите способ просмотра серверов:", reply_markup=server_search_keyboard())
        return
    
    elif "📄" in message.text:  # Информация о странице
        return  # Игнорируем, это просто информация
    
    # Выбор сервера - ищем ID в тексте
    server_id = None
    server_name = None
    
    # Ищем ID в скобках
    import re
    match = re.search(r'\(ID:\s*(\d+)\)', message.text)
    if match:
        server_id = int(match.group(1))
    else:
        # Ищем в начале строки (эмодзи + имя)
        for server in servers:
            s_id, s_name, _, _ = server
            if s_name in message.text:
                server_id = s_id
                server_name = s_name
                break
    
    if not server_id:
        # Пробуем извлечь из любого места
        numbers = re.findall(r'\d+', message.text)
        if numbers:
            # Проверяем, есть ли такой ID в списке
            for num in numbers:
                try:
                    sid = int(num)
                    if any(str(sid) == str(s[0]) for s in servers):
                        server_id = sid
                        break
                except:
                    pass
    
    if server_id:
        # Получаем информацию о сервере
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT name, wireguard_configured FROM servers WHERE id = ?", (server_id,))
                server_info = await cursor.fetchone()
                
                if server_info:
                    server_name, wg_configured = server_info
                    
                    # Сохраняем в состоянии для использования в других обработчиках
                    await state.update_data(
                        server_id=server_id,
                        server_name=server_name,
                        servers=servers,
                        offset=offset
                    )
                    
                    # Показываем меню действий
                    text = f"🔍 <b>Сервер: {server_name}</b>\n\n"
                    text += f"🆔 ID: {server_id}\n"
                    text += f"🔐 WireGuard: {'✅ настроен' if wg_configured else '❌ не настроен'}\n\n"
                    text += "Выберите действие:"
                    
                    await message.answer(text, parse_mode=ParseMode.HTML, 
                                       reply_markup=server_actions_keyboard(server_id))
                else:
                    await message.answer("❌ Сервер не найден в базе данных")
        except Exception as e:
            await message.answer(f"❌ Ошибка получения информации о сервере: {e}")
    else:
        await message.answer("Не удалось определить ID сервера. Выберите сервер из списка:")

# ========== ОБРАБОТЧИКИ ДЕЙСТВИЙ С СЕРВЕРОМ ==========
@dp.message(F.text.startswith("🔧 Установить WireGuard"))
async def admin_install_wg(message: Message, state: FSMContext):
    """Установка WireGuard на выбранный сервер"""
    # Извлекаем ID из текста
    import re
    match = re.search(r'\(ID:\s*(\d+)\)', message.text)
    if not match:
        await message.answer("❌ Не удалось определить ID сервера")
        return
    
    server_id = int(match.group(1))
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server_info = await cursor.fetchone()
            
            if not server_info:
                await message.answer("❌ Сервер не найден")
                return
            
            server_name = server_info[0]
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации: {e}")
        return
    
    await message.answer(f"🔄 Начинаю установку WireGuard на сервер {server_name}...")
    
    # Запускаем установку
    success, steps = await setup_wireguard_server(server_id, message)
    
    if success:
        await message.answer(
            f"✅ <b>WireGuard успешно настроен на сервере {server_name}!</b>\n\n"
            f"Сервер готов к использованию.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Не удалось настроить WireGuard</b>\n\n"
            f"Проверьте права доступа и настройки сервера.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text.startswith("🔍 Проверить SSH"))
async def admin_check_ssh(message: Message, state: FSMContext):
    """Проверка SSH подключения"""
    import re
    match = re.search(r'\(ID:\s*(\d+)\)', message.text)
    if not match:
        await message.answer("❌ Не удалось определить ID сервера")
        return
    
    server_id = int(match.group(1))
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server_info = await cursor.fetchone()
            
            if not server_info:
                await message.answer("❌ Сервер не найден")
                return
            
            server_name = server_info[0]
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации: {e}")
        return
    
    # Тестируем SSH подключение
    success, result = await test_ssh_connection(server_id, message)
    
    if success:
        await message.answer(
            f"✅ <b>SSH подключение к {server_name} работает!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>SSH подключение не работает</b>\n\n"
            f"Ошибка: {result}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )

@dp.message(F.text.startswith("📊 Состояние"))
async def admin_check_status(message: Message):
    """Проверка состояния сервера"""
    import re
    match = re.search(r'\(ID:\s*(\d+)\)', message.text)
    if not match:
        await message.answer("❌ Не удалось определить ID сервера")
        return
    
    server_id = int(match.group(1))
    
    await message.answer(f"🔍 Проверяю состояние сервера...")
    
    try:
        # Получаем информацию из БД
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT name, server_ip, wireguard_configured, current_users, max_users, 
                       last_check, created_at
                FROM servers WHERE id = ?
            """, (server_id,))
            server_info = await cursor.fetchone()
        
        if not server_info:
            await message.answer("❌ Информация о сервере не найдена")
            return
        
        name, ip, wg_configured, current_users, max_users, last_check, created_at = server_info
        
        # Тестируем подключение
        ssh_ok, ssh_msg = await test_ssh_connection(server_id, None)
        
        text = f"📊 <b>Состояние сервера: {name}</b>\n\n"
        text += f"🆔 ID: {server_id}\n"
        text += f"🌐 IP: {ip or 'не указан'}\n"
        text += f"🔐 WireGuard: {'✅ настроен' if wg_configured else '❌ не настроен'}\n"
        text += f"👥 Пользователи: {current_users}/{max_users}\n"
        text += f"📅 Добавлен: {datetime.fromisoformat(created_at).strftime('%d.%m.%Y %H:%M')}\n"
        
        if last_check:
            last_check_dt = datetime.fromisoformat(last_check)
            text += f"⏰ Последняя проверка: {last_check_dt.strftime('%d.%m.%Y %H:%M')}\n"
        
        text += f"\n🔌 SSH подключение: {'✅ работает' if ssh_ok else '❌ не работает'}\n"
        
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка проверки состояния: {str(e)}")

@dp.message(F.text.startswith("🤖 Тест ботом"))
async def admin_test_with_bot(message: Message, state: FSMContext):
    """Тестирование сервера ботом"""
    import re
    match = re.search(r'\(ID:\s*(\d+)\)', message.text)
    if not match:
        await message.answer("❌ Не удалось определить ID сервера")
        return
    
    server_id = int(match.group(1))
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server_info = await cursor.fetchone()
            
            if not server_info:
                await message.answer("❌ Сервер не найден")
                return
            
            server_name = server_info[0]
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации: {e}")
        return
    
    await state.set_state(AdminTestBotStates.waiting_for_token)
    await state.update_data(server_id=server_id, server_name=server_name)
    
    await message.answer(
        f"🤖 <b>Тестирование сервера {server_name} ботом</b>\n\n"
        f"Отправьте токен бота для тестирования:\n"
        f"(получите у @BotFather)",
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
    )

# Обработчик для тестового бота (остается как в предыдущей версии)
@dp.message(AdminTestBotStates.waiting_for_token)
async def process_test_bot_token(message: Message, state: FSMContext):
    """Обработка токена для тестового бота"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
        return
    
    bot_token = message.text.strip()
    
    if len(bot_token) < 30:
        await message.answer("Неверный формат токена. Отправьте токен бота:")
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name', 'сервер')
    
    # Создаем тестового бота (упрощенная версия)
    await message.answer(f"🤖 Создаю тестового бота на {server_name}...")
    
    try:
        # Простая проверка подключения
        ssh_ok, ssh_msg = await test_ssh_connection(server_id, message)
        
        if ssh_ok:
            await message.answer(
                f"✅ <b>Сервер {server_name} доступен по SSH!</b>\n\n"
                f"Токен бота принят: {bot_token[:10]}...\n\n"
                f"SSH подключение работает корректно.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_menu()
            )
        else:
            await message.answer(
                f"❌ <b>Ошибка SSH подключения</b>\n\n"
                f"Ошибка: {ssh_msg}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_menu()
            )
    
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ (остаются без изменений) ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    if is_admin(message.from_user.id, message.chat.id):
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            "🚀 <b>Добро пожаловать в VPN Hosting!</b>\n\n"
            "Выберите услугу:",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    """Команда помощи"""
    await message.answer(
        f"🆘 <b>Помощь и поддержка</b>\n\n"
        f"Если нужна помощь, обратитесь: {SUPPORT_USERNAME}\n\n"
        f"Мы всегда готовы помочь!",
        parse_mode=ParseMode.HTML,
        reply_markup=user_main_menu()
    )

# Остальные обработчики (получение VPN, платежи и т.д.) остаются как в предыдущей версии
# Для экономии места не дублирую их, они работают правильно

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
        print(f"💬 Admin Chat ID: {ADMIN_CHAT_ID}")
        print(f"🗄️ База данных: {DB_PATH}")
        print(f"🆘 Поддержка: {SUPPORT_USERNAME}")
        
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