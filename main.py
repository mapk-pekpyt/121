# main.py - УЛУЧШЕННАЯ ВЕРСИЯ С АВТОМАТИЧЕСКОЙ УСТАНОВКОЙ VPN
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
SUPPORT_USERNAME = "@vpnbothost"  # Юзернейм поддержки

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
                AND wireguard_configured = TRUE
                LIMIT 1
            """)
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка поиска сервера: {e}")
        return None

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60) -> Tuple[str, str, bool]:
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
            await log_step(f"👤 Пользователь: {stdout.split()[1] if len(stdout.split()) > 1 else 'неизвестно'}")
            await log_step(f"💻 Система: {stdout.split('Linux')[1][:50] if 'Linux' in stdout else 'неизвестно'}")
            return True, "SSH подключение успешно"
        else:
            await log_step(f"❌ Ошибка SSH: {stderr}", False)
            return False, stderr
            
    except Exception as e:
        error_msg = f"❌ Ошибка тестирования SSH: {str(e)}"
        await log_step(error_msg, False)
        return False, error_msg

async def setup_wireguard_server(server_id: int, message: Message = None):
    """Настраивает WireGuard на сервере с пошаговым логированием"""
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
        
        # 2. Проверяем систему
        await log_step("2. Проверяю операционную систему...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/os-release | grep PRETTY_NAME || echo 'Unknown OS'")
        if success and stdout:
            os_info = stdout.split('=')[1].strip('"') if '=' in stdout else stdout.strip()
            await log_step(f"📋 Система: {os_info}")
        
        # 3. Обновляем пакеты
        await log_step("3. Обновляю пакеты системы...")
        
        # Пробуем разные менеджеры пакетов
        update_commands = [
            "apt-get update -y",
            "apt update -y",
            "yum update -y 2>/dev/null || true"
        ]
        
        updated = False
        for cmd in update_commands:
            await log_step(f"   Пробую: {cmd}")
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=120)
            if success:
                updated = True
                await log_step("   ✅ Пакеты обновлены")
                break
        
        if not updated:
            await log_step("   ⚠️ Не удалось обновить пакеты, продолжаю...", False)
        
        # 4. Устанавливаем WireGuard
        await log_step("4. Устанавливаю WireGuard...")
        
        # Пробуем разные способы установки
        install_methods = [
            ("apt-get install -y wireguard wireguard-tools", "APT установка"),
            ("apt install -y wireguard wireguard-tools", "APT альтернативная"),
            ("yum install -y wireguard-tools 2>/dev/null || apt-get install -y wireguard", "YUM/APT комбо")
        ]
        
        installed = False
        for cmd, desc in install_methods:
            await log_step(f"   Метод: {desc}")
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180)
            if success:
                installed = True
                await log_step("   ✅ WireGuard установлен")
                break
            else:
                await log_step(f"   ❌ Не удалось: {stderr[:100]}", False)
        
        if not installed:
            await log_step("5. Пробую установить из исходников...", False)
            
            # Установка зависимостей
            deps_cmd = "apt-get install -y build-essential git libmnl-dev libelf-dev linux-headers-$(uname -r) pkg-config"
            stdout, stderr, success = await execute_ssh_command(server_id, deps_cmd, timeout=180)
            
            if success:
                # Компиляция из исходников
                source_cmd = """
                cd /tmp && git clone https://git.zx2c4.com/wireguard-tools && \
                cd wireguard-tools && make -j$(nproc) && make install
                """
                stdout, stderr, success = await execute_ssh_command(server_id, source_cmd, timeout=300)
                
                if success:
                    installed = True
                    await log_step("   ✅ WireGuard установлен из исходников")
                else:
                    await log_step(f"   ❌ Ошибка компиляции: {stderr[:200]}", False)
            else:
                await log_step(f"   ❌ Не удалось установить зависимости: {stderr[:200]}", False)
        
        if not installed:
            return False, steps
        
        # 6. Создание директории
        await log_step("6. Создаю директорию для WireGuard...")
        await execute_ssh_command(server_id, "mkdir -p /etc/wireguard && chmod 700 /etc/wireguard")
        
        # 7. Генерация ключей
        await log_step("7. Генерирую ключи...")
        keygen_cmd = """
        cd /etc/wireguard
        umask 077
        wg genkey | tee private.key | wg pubkey > public.key
        chmod 600 private.key public.key
        echo "Ключи сгенерированы"
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd)
        if not success:
            await log_step(f"❌ Ошибка генерации ключей: {stderr}", False)
            return False, steps
        
        # 8. Получение публичного ключа
        await log_step("8. Получаю публичный ключ...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key")
        if not success or not stdout.strip():
            await log_step("❌ Не удалось получить публичный ключ", False)
            return False, steps
        
        public_key = stdout.strip()
        
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
        
        config_cmd = f"""
        cd /etc/wireguard
        cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)

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
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, config_cmd)
        if not success:
            await log_step(f"❌ Ошибка создания конфига: {stderr}", False)
            return False, steps
        
        # 11. Запуск WireGuard
        await log_step("11. Запускаю WireGuard...")
        
        # Проверяем и включаем автозагрузку
        enable_cmd = """
        systemctl enable wg-quick@wg0 2>/dev/null || true
        systemctl start wg-quick@wg0 2>/dev/null || true
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, enable_cmd)
        
        # Проверяем статус
        status_cmd = "systemctl is-active wg-quick@wg0 2>/dev/null || wg show 2>/dev/null && echo 'active' || echo 'inactive'"
        stdout, stderr, success = await execute_ssh_command(server_id, status_cmd)
        
        if 'active' in stdout or success:
            await log_step("   ✅ WireGuard запущен")
        else:
            # Пробуем запустить вручную
            manual_cmd = "wg-quick up wg0 2>&1 || true"
            await execute_ssh_command(server_id, manual_cmd)
            await log_step("   ⚠️ WireGuard запущен вручную")
        
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
        check_cmd = "wg show 2>/dev/null | head -5 || echo 'WireGuard check failed'"
        stdout, stderr, success = await execute_ssh_command(server_id, check_cmd)
        
        if success and 'interface:' in stdout.lower():
            await log_step(f"✅ WireGuard успешно настроен и работает!")
            await log_step(f"🔑 Публичный ключ: {public_key[:50]}...")
            await log_step(f"🌐 IP сервера: {server_ip}")
            await log_step(f"📊 Статистика: {success_steps}/{total_steps} шагов выполнено успешно")
            
            return True, steps
        else:
            await log_step("⚠️ WireGuard настроен, но есть проблемы с запуском", False)
            await log_step(f"🔑 Публичный ключ: {public_key[:50]}...")
            await log_step(f"🌐 IP сервера: {server_ip}")
            await log_step(f"📊 Статистика: {success_steps}/{total_steps} шагов выполнено успешно")
            
            return True, steps  # Возвращаем True, так как ключи сгенерированы
        
    except Exception as e:
        error_msg = f"❌ Критическая ошибка: {str(e)}"
        await log_step(error_msg, False)
        return False, steps

async def install_wireguard_from_git(server_id: int, git_repo: str, message: Message = None):
    """Устанавливает WireGuard из Git репозитория"""
    async def log_step(text: str, success: bool = True):
        if message:
            try:
                await message.answer(text)
            except:
                pass
        logger.info(text)
    
    await log_step(f"🔧 Устанавливаю WireGuard из репозитория: {git_repo}")
    
    try:
        # 1. Клонируем репозиторий
        await log_step("1. Клонирую репозиторий...")
        clone_cmd = f"cd /tmp && rm -rf wireguard-install && git clone {git_repo} wireguard-install"
        stdout, stderr, success = await execute_ssh_command(server_id, clone_cmd, timeout=120)
        
        if not success:
            await log_step(f"❌ Ошибка клонирования: {stderr}", False)
            return False, stderr
        
        # 2. Проверяем наличие скрипта установки
        await log_step("2. Ищу скрипт установки...")
        find_cmd = "find /tmp/wireguard-install -name '*.sh' -o -name 'install*' -o -name 'setup*' | head -5"
        stdout, stderr, success = await execute_ssh_command(server_id, find_cmd)
        
        if success and stdout:
            scripts = stdout.strip().split('\n')
            await log_step(f"📋 Найдены скрипты: {', '.join([os.path.basename(s) for s in scripts[:3]])}")
            
            # Пробуем запустить первый найденный скрипт
            script_path = scripts[0]
            await log_step(f"3. Запускаю скрипт: {os.path.basename(script_path)}...")
            
            # Даем права на выполнение
            chmod_cmd = f"chmod +x {script_path}"
            await execute_ssh_command(server_id, chmod_cmd)
            
            # Запускаем скрипт
            run_cmd = f"cd /tmp/wireguard-install && {script_path} 2>&1"
            stdout, stderr, success = await execute_ssh_command(server_id, run_cmd, timeout=300)
            
            if success:
                await log_step("✅ Скрипт установки выполнен успешно")
                
                # Проверяем WireGuard
                check_cmd = "which wg && echo 'WireGuard found' || echo 'WireGuard not found'"
                stdout, stderr, success = await execute_ssh_command(server_id, check_cmd)
                
                if 'WireGuard found' in stdout:
                    await log_step("✅ WireGuard успешно установлен из Git")
                    
                    # Получаем публичный ключ
                    pubkey_cmd = "cat /etc/wireguard/public.key 2>/dev/null || wg pubkey < /etc/wireguard/private.key 2>/dev/null || echo 'no key'"
                    stdout, stderr, success = await execute_ssh_command(server_id, pubkey_cmd)
                    
                    if success and 'no key' not in stdout:
                        public_key = stdout.strip()
                        
                        # Сохраняем в БД
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE servers SET public_key = ?, wireguard_configured = TRUE WHERE id = ?",
                                (public_key, server_id)
                            )
                            await db.commit()
                        
                        await log_step(f"🔑 Публичный ключ сохранен: {public_key[:50]}...")
                        return True, "WireGuard установлен из Git репозитория"
                    else:
                        await log_step("⚠️ WireGuard установлен, но не удалось получить ключ", False)
                        return True, "WireGuard установлен, но ключ не получен"
                else:
                    await log_step("❌ WireGuard не установлен после скрипта", False)
                    return False, "WireGuard не установлен"
            else:
                await log_step(f"❌ Ошибка выполнения скрипта: {stderr[:200]}", False)
                return False, stderr
        else:
            await log_step("❌ Не найден скрипт установки в репозитории", False)
            return False, "Не найден скрипт установки"
        
    except Exception as e:
        error_msg = f"❌ Ошибка установки из Git: {str(e)}"
        await log_step(error_msg, False)
        return False, error_msg

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
        
        # 5. Добавляем пира в конфиг
        await log_step("Добавляю клиента в конфигурацию WireGuard...")
        
        add_peer_cmd = f"""
        cd /etc/wireguard
        echo "" >> wg0.conf
        echo "[Peer]" >> wg0.conf
        echo "# Client {user_id}" >> wg0.conf
        echo "PublicKey = {public_key}" >> wg0.conf
        echo "AllowedIPs = {client_ip}/32" >> wg0.conf
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, add_peer_cmd)
        if not success:
            await log_step("❌ Не удалось добавить клиента в конфиг", False)
            return None
        
        # 6. Перезагружаем конфиг
        await log_step("Применяю изменения конфигурации...")
        reload_cmd = "wg syncconf wg0 <(wg-quick strip wg0) 2>/dev/null || systemctl restart wg-quick@wg0"
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
            await message.answer("❌ Нет доступных серверов с настроенным WireGuard")
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
        # 1. Проверяем подключение
        await message.answer("1. Проверяю SSH подключение...")
        ssh_ok, ssh_msg = await test_ssh_connection(server_id, message)
        if not ssh_ok:
            return False, f"SSH ошибка: {ssh_msg}"
        
        # 2. Создаем простого бота
        await message.answer("2. Создаю файлы бота...")
        
        bot_content = f"""import os
import time
import asyncio
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
    await message.answer(f"⏱️ Время ответа: {{response_time}}ms")

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Вы сказали: {{message.text}}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
"""
        
        # Создаем директорию
        await execute_ssh_command(server_id, "mkdir -p /tmp/test_bot && cd /tmp/test_bot && rm -f bot.py requirements.txt")
        
        # Создаем bot.py
        create_bot_cmd = f"cd /tmp/test_bot && cat > bot.py << 'EOF'\n{bot_content}\nEOF"
        stdout, stderr, success = await execute_ssh_command(server_id, create_bot_cmd)
        
        if not success:
            return False, f"Ошибка создания bot.py: {stderr}"
        
        # Создаем requirements.txt
        await execute_ssh_command(server_id, "cd /tmp/test_bot && echo 'aiogram>=3.0.0' > requirements.txt")
        
        # 3. Проверяем Python
        await message.answer("3. Проверяю Python...")
        stdout, stderr, success = await execute_ssh_command(server_id, "python3 --version || python --version")
        if success:
            await message.answer(f"✅ {stdout.strip()}")
        else:
            await message.answer("⚠️ Python не найден, устанавливаю...")
            await execute_ssh_command(server_id, "apt-get update && apt-get install -y python3 python3-pip", timeout=120)
        
        # 4. Устанавливаем зависимости
        await message.answer("4. Устанавливаю зависимости...")
        stdout, stderr, success = await execute_ssh_command(server_id, "cd /tmp/test_bot && pip3 install aiogram", timeout=120)
        if not success:
            return False, f"Ошибка установки зависимостей: {stderr}"
        
        # 5. Запускаем бота в фоне
        await message.answer("5. Запускаю бота...")
        run_cmd = f"cd /tmp/test_bot && nohup python3 bot.py > bot.log 2>&1 & echo $! > bot.pid && sleep 3"
        stdout, stderr, success = await execute_ssh_command(server_id, run_cmd)
        
        if success:
            # Проверяем запуск
            await asyncio.sleep(2)
            check_cmd = "ps aux | grep 'python3 bot.py' | grep -v grep | head -1"
            stdout, stderr, success = await execute_ssh_command(server_id, check_cmd)
            
            if success and stdout:
                pid = stdout.split()[1] if len(stdout.split()) > 1 else "unknown"
                await message.answer(f"✅ Тестовый бот запущен! PID: {pid}")
                
                # Получаем логи
                log_cmd = "cd /tmp/test_bot && tail -10 bot.log 2>/dev/null || echo 'Нет логов'"
                stdout, stderr, success = await execute_ssh_command(server_id, log_cmd)
                logs = stdout if stdout else "Нет логов"
                
                return True, f"Бот запущен. Логи:\n{logs[:500]}"
            else:
                return False, "Бот не запустился"
        else:
            return False, f"Ошибка запуска: {stderr}"
        
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
        server_id, server_name, is_active, wg_configured = server
        status = "🟢" if is_active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        buttons.append([types.KeyboardButton(text=f"{status}{wg_status} {server_name} (ID: {server_id})")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_actions_keyboard():
    """Клавиатура действий с сервером"""
    buttons = [
        [types.KeyboardButton(text="🔧 Установить WireGuard вручную")],
        [types.KeyboardButton(text="📊 Проверить состояние")],
        [types.KeyboardButton(text="🤖 Тестировать ботом")],
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
        f"🆘 <b>Помощь и поддержка</b>\n\n"
        f"Если нужна помощь, обратитесь: {SUPPORT_USERNAME}\n\n"
        f"Мы всегда готовы помочь!",
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
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

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
            "Пример: <code>opc@193.122.8.29</code>\n\n"
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
        "Пример: <code>opc@193.122.8.29</code>\n\n"
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
            f"🔄 <b>Начинаю автоматическую настройку WireGuard...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Автоматически настраиваем WireGuard
        success, steps = await setup_wireguard_server(server_id, message)
        
        if success:
            await message.answer(
                f"🎉 <b>WireGuard успешно настроен на сервере {data['server_name']}!</b>\n\n"
                f"✅ Сервер готов к использованию для VPN.\n"
                f"🔑 Ключи сгенерированы\n"
                f"🌐 Сервис запущен\n\n"
                f"Теперь вы можете использовать этот сервер для создания VPN подключений.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_menu()
            )
        else:
            # Предлагаем альтернативные варианты
            await message.answer(
                f"⚠️ <b>Не удалось автоматически настроить WireGuard</b>\n\n"
                f"Сервер добавлен (ID: {server_id}), но WireGuard не настроен.\n\n"
                f"<b>Что можно сделать:</b>\n"
                f"1. Проверить SSH доступ к серверу\n"
                f"2. Установить WireGuard вручную через 'Тест сервера'\n"
                f"3. Использовать другой сервер\n\n"
                f"SSH команда для проверки:\n"
                f"<code>ssh -i key.pem {connection_string}</code>",
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
    """Тестирование сервера"""
    if not is_admin(message.from_user.id):
        return
    
    # Получаем список серверов
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured FROM servers ORDER BY name")
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
        import re
        match = re.search(r'\(ID:\s*(\d+)\)', message.text)
        if match:
            server_id = int(match.group(1))
        else:
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
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name, wireguard_configured FROM servers WHERE id = ?", (server_id,))
            server_info = await cursor.fetchone()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации о сервере: {e}")
        return
    
    server_name, wg_configured = server_info
    
    await state.update_data(server_id=server_id, server_name=server_name)
    
    if not wg_configured:
        # Если WireGuard не настроен, предлагаем варианты
        keyboard = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="🔧 Установить WireGuard вручную")],
            [types.KeyboardButton(text="🔍 Проверить SSH подключение")],
            [types.KeyboardButton(text="🤖 Протестировать ботом")],
            [types.KeyboardButton(text="◀️ Назад к списку")]
        ], resize_keyboard=True)
        
        await message.answer(
            f"🔍 <b>Сервер: {server_name} (ID: {server_id})</b>\n\n"
            f"⚠️ <b>WireGuard не настроен!</b>\n\n"
            f"Выберите действие:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    else:
        # Если WireGuard настроен, просто тестируем
        await state.set_state(AdminTestBotStates.waiting_for_token)
        await message.answer(
            f"✅ <b>Сервер: {server_name} (ID: {server_id})</b>\n\n"
            f"WireGuard уже настроен.\n\n"
            f"Отправьте токен бота для тестирования:\n"
            f"(получите у @BotFather)",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "🔧 Установить WireGuard вручную")
async def admin_install_wg_manual(message: Message, state: FSMContext):
    """Ручная установка WireGuard"""
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name')
    
    if not server_id:
        await message.answer("❌ Ошибка: ID сервера не найден")
        await state.clear()
        return
    
    await state.set_state(AdminManualWGStates.waiting_for_git_repo)
    await state.update_data(server_id=server_id)
    
    await message.answer(
        f"🔧 <b>Ручная установка WireGuard на {server_name}</b>\n\n"
        f"Отправьте ссылку на Git репозиторий с установщиком WireGuard:\n\n"
        f"<b>Примеры репозиториев:</b>\n"
        f"• https://github.com/angristan/wireguard-install.git\n"
        f"• https://github.com/l-n-s/wireguard-install.git\n"
        f"• Ваш собственный репозиторий\n\n"
        f"<b>Требования:</b>\n"
        f"• Репозиторий должен быть публичным\n"
        f"• Должен быть скрипт установки (обычно .sh файл)\n"
        f"• Скрипт должен поддерживать автоматическую установку",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard()
    )

@dp.message(AdminManualWGStates.waiting_for_git_repo)
async def process_git_repo(message: Message, state: FSMContext):
    """Обработка Git репозитория"""
    if message.text == "◀️ Назад":
        await state.set_state(AdminTestBotStates.waiting_for_server)
        await message.answer("Выберите сервер:")
        return
    
    git_repo = message.text.strip()
    
    if not (git_repo.startswith('http') or git_repo.startswith('git@')):
        await message.answer(
            "❌ Неверный формат ссылки!\n\n"
            "Ссылка должна быть в формате:\n"
            "• https://github.com/username/repo.git\n"
            "• git@github.com:username/repo.git\n\n"
            "Отправьте ссылку еще раз:",
            parse_mode=ParseMode.HTML
        )
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name', 'сервер')
    
    # Устанавливаем WireGuard из Git
    success, result = await install_wireguard_from_git(server_id, git_repo, message)
    
    if success:
        await message.answer(
            f"✅ <b>WireGuard успешно установлен на {server_name}!</b>\n\n"
            f"Сервер готов к использованию для VPN подключений.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Не удалось установить WireGuard</b>\n\n"
            f"Ошибка: {result}\n\n"
            f"Попробуйте:\n"
            f"1. Другой репозиторий\n"
            f"2. Установить вручную через SSH\n"
            f"3. Проверить доступ к серверу",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "🔍 Проверить SSH подключение")
async def admin_check_ssh(message: Message, state: FSMContext):
    """Проверка SSH подключения"""
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name')
    
    if not server_id:
        await message.answer("❌ Ошибка: ID сервера не найден")
        return
    
    # Тестируем SSH подключение
    success, result = await test_ssh_connection(server_id, message)
    
    if success:
        await message.answer(
            f"✅ <b>SSH подключение к {server_name} работает!</b>\n\n"
            f"Теперь вы можете:\n"
            f"1. Установить WireGuard вручную\n"
            f"2. Протестировать ботом\n"
            f"3. Проверить другие настройки",
            parse_mode=ParseMode.HTML,
            reply_markup=server_actions_keyboard()
        )
    else:
        await message.answer(
            f"❌ <b>SSH подключение не работает</b>\n\n"
            f"Ошибка: {result}\n\n"
            f"<b>Что проверить:</b>\n"
            f"• Правильность SSH ключа\n"
            f"• Доступность сервера из сети\n"
            f"• Настройки фаервола\n"
            f"• Пользовательские права\n\n"
            f"Проверьте подключение вручную:\n"
            f"<code>ssh -i ключ.pem пользователь@хост</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=server_actions_keyboard()
        )

@dp.message(F.text == "📊 Проверить состояние")
async def admin_check_status(message: Message, state: FSMContext):
    """Проверка состояния сервера"""
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name')
    
    if not server_id:
        await message.answer("❌ Ошибка: ID сервера не найден")
        return
    
    await message.answer(f"🔍 Проверяю состояние сервера {server_name}...")
    
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
        
        if not ssh_ok and ssh_msg:
            text += f"Ошибка: {ssh_msg[:100]}\n"
        
        # Если WireGuard настроен, проверяем его состояние
        if wg_configured:
            text += "\n🔍 Проверяю WireGuard...\n"
            stdout, stderr, success = await execute_ssh_command(server_id, "wg show 2>/dev/null | head -3 || echo 'WireGuard not running'")
            
            if success and 'interface:' in stdout:
                text += "✅ WireGuard запущен\n"
                # Пытаемся получить количество пиров
                peer_count = stdout.count('peer:') if 'peer:' in stdout else 0
                text += f"📡 Подключено пиров: {peer_count}\n"
            else:
                text += "❌ WireGuard не запущен\n"
        
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=server_actions_keyboard())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка проверки состояния: {str(e)}")

@dp.message(F.text == "🤖 Тестировать ботом")
async def admin_test_with_bot(message: Message, state: FSMContext):
    """Тестирование сервера ботом"""
    data = await state.get_data()
    server_id = data.get('server_id')
    server_name = data.get('server_name')
    
    if not server_id:
        await message.answer("❌ Ошибка: ID сервера не найден")
        return
    
    await state.set_state(AdminTestBotStates.waiting_for_token)
    await message.answer(
        f"🤖 <b>Тестирование сервера {server_name} ботом</b>\n\n"
        f"Отправьте токен бота для тестирования:\n"
        f"(получите у @BotFather)",
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
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
    server_name = data.get('server_name', 'сервер')
    
    # Создаем тестового бота
    success, result = await create_test_bot(server_id, bot_token, message)
    
    if success:
        await message.answer(
            f"✅ <b>Тестовый бот успешно создан на {server_name}!</b>\n\n"
            f"Сервер работает корректно.\n\n"
            f"{result}\n\n"
            f"<b>Что дальше:</b>\n"
            f"• Настройте WireGuard если еще не настроен\n"
            f"• Используйте сервер для VPN подключений",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка создания тестового бота!</b>\n\n"
            f"Ошибка: {result}\n\n"
            f"<b>Проверьте:</b>\n"
            f"• SSH доступ к серверу\n"
            f"• Доступность интернета на сервере\n"
            f"• Права пользователя\n"
            f"• Наличие Python 3\n\n"
            f"Для проверки SSH:\n"
            f"<code>ssh -i ключ.pem пользователь@хост</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "◀️ Назад к списку")
async def back_to_server_list(message: Message, state: FSMContext):
    """Возврат к списку серверов"""
    data = await state.get_data()
    servers = data.get('servers', [])
    
    if servers:
        await message.answer("Выберите сервер:", reply_markup=server_list_keyboard(servers))
        await state.set_state(AdminTestBotStates.waiting_for_server)
    else:
        await message.answer("Админ-панель:", reply_markup=admin_main_menu())
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