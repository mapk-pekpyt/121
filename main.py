# main.py - ПОЛНОСТЬЮ ИСПРАВЛЕННЫЯ ВЕРСИЯ
import os, asyncio, logging, sys, random, qrcode, io, sqlite3, re, subprocess, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, InlineKeyboardButton, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh, aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
ADMIN_CHAT_ID = -1003542769962
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_USERNAME = "@vpnbothost"
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("""CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, ssh_key TEXT NOT NULL, connection_string TEXT NOT NULL, vpn_type TEXT DEFAULT 'wireguard', max_users INTEGER DEFAULT 50, current_users INTEGER DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, server_ip TEXT, public_key TEXT, wireguard_configured BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, server_id INTEGER, client_name TEXT, client_public_key TEXT, client_ip TEXT, config_data TEXT, config_file_path TEXT, qr_code_path TEXT, subscription_end TIMESTAMP, trial_used BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount REAL NOT NULL, currency TEXT DEFAULT 'USD', payment_method TEXT, period_days INTEGER, status TEXT DEFAULT 'pending', telegram_payment_id TEXT, subscription_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY, week_price INTEGER DEFAULT 50, month_price INTEGER DEFAULT 150, week_usd REAL DEFAULT 5.0, month_usd REAL DEFAULT 15.0)")
            await db.execute("INSERT OR IGNORE INTO prices (id, week_price, month_price, week_usd, month_usd) VALUES (1, 50, 150, 5.0, 15.0)")
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    if chat_id: return user_id == ADMIN_ID or str(chat_id) == str(ADMIN_CHAT_ID)
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price, week_usd, month_usd FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices: return {"week": {"days": 7, "stars": prices[0], "usd": prices[2]}, "month": {"days": 30, "stars": prices[1], "usd": prices[3]}}
    except: pass
    return {"week": {"days": 7, "stars": 50, "usd": 5.0}, "month": {"days": 30, "stars": 150, "usd": 15.0}}

async def check_ssh_connection(server_id: int) -> Tuple[bool, str, Optional[Dict]]:
    """Проверка SSH подключения и получение информации о системе"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return False, "Сервер не найден", None
            conn_str, ssh_key = server
            try:
                if ':' in conn_str: user_host, port = conn_str.rsplit(':', 1); user, host = user_host.split('@'); port = int(port)
                else: user, host = conn_str.split('@'); port = 22
            except: return False, f"Неверный формат: {conn_str}", None
            
            import tempfile, stat
            ssh_key_clean = ssh_key.strip()
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean); temp_key_path = f.name
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=30) as conn:
                    # 1. Проверка базовых команд
                    result = await conn.run("whoami && pwd && echo 'SSH_CHECK_OK'", timeout=30)
                    if result.exit_status != 0 or 'SSH_CHECK_OK' not in result.stdout:
                        return False, f"Базовые команды не выполняются: {result.stderr}", None
                    
                    # 2. Проверка прав
                    sudo_check = await conn.run("sudo -n true 2>&1; echo $?", timeout=10)
                    has_sudo = sudo_check.stdout.strip() == '0'
                    is_root = 'root' in result.stdout
                    
                    # 3. Проверка ОС
                    os_info = await conn.run("cat /etc/os-release 2>/dev/null || uname -a", timeout=10)
                    os_data = os_info.stdout
                    
                    # 4. Проверка пакетных менеджеров
                    pkg_check = await conn.run("which apt-get yum dnf apk pacman 2>/dev/null | head -1", timeout=10)
                    pkg_manager = pkg_check.stdout.strip()
                    
                    # 5. Проверка ядра
                    kernel_check = await conn.run("uname -r", timeout=10)
                    kernel_version = kernel_check.stdout.strip()
                    
                    # 6. Проверка интерфейса
                    interface_check = await conn.run("ip route show default 2>/dev/null | awk '/default/ {print $5}' | head -1", timeout=10)
                    interface = interface_check.stdout.strip() or "eth0"
                    
                    # 7. Проверка Python
                    python_check = await conn.run("python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'NOT_FOUND'", timeout=10)
                    python_available = 'Python' in python_check.stdout
                    
                    system_info = {
                        'has_sudo': has_sudo,
                        'is_root': is_root,
                        'os_info': os_data,
                        'pkg_manager': pkg_manager,
                        'kernel': kernel_version,
                        'interface': interface,
                        'python': python_available,
                        'user': user,
                        'host': host
                    }
                    
                    try: os.unlink(temp_key_path)
                    except: pass
                    
                    return True, "SSH подключение работает", system_info
                    
            except asyncssh.Error as e:
                try: os.unlink(temp_key_path)
                except: pass
                return False, f"SSH ошибка: {str(e)}", None
    except Exception as e:
        return False, f"Общая ошибка: {str(e)}", None

async def execute_ssh_command_with_check(server_id: int, command: str, timeout: int = 60, use_sudo: bool = False, critical: bool = False) -> Tuple[str, str, bool]:
    """Выполнение команды с проверкой SSH подключения"""
    # Сначала проверяем SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        return "", f"SSH недоступен: {ssh_msg}", False
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return "", "Сервер не найден", False
            conn_str, ssh_key = server
            
            try:
                if ':' in conn_str: user_host, port = conn_str.rsplit(':', 1); user, host = user_host.split('@'); port = int(port)
                else: user, host = conn_str.split('@'); port = 22
            except: return "", f"Неверный формат: {conn_str}", False
            
            import tempfile, stat
            ssh_key_clean = ssh_key.strip()
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean); temp_key_path = f.name
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=timeout) as conn:
                    if use_sudo: command = f"sudo {command}"
                    result = await conn.run(command, timeout=timeout)
                    
                    try: os.unlink(temp_key_path)
                    except: pass
                    
                    if result.exit_status == 0:
                        return result.stdout, result.stderr, True
                    else:
                        error_msg = f"Команда завершилась с кодом {result.exit_status}: {result.stderr}"
                        if critical: return "", error_msg, False
                        else: return result.stdout, result.stderr, False
                    
            except asyncssh.Error as e:
                try: os.unlink(temp_key_path)
                except: pass
                return "", f"SSH ошибка: {str(e)}", False
    except Exception as e:
        return "", f"Ошибка выполнения: {str(e)}", False

async def setup_wireguard_with_checks(server_id: int, message: Message):
    """Установка WireGuard с полной проверкой"""
    await message.answer("🔍 Начинаю проверку окружения...")
    
    # 1. Проверка SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}\nУстановка отменена.")
        return False
    
    await message.answer(f"✅ SSH подключение работает\n👤 Пользователь: {system_info['user']}\n🖥️ Хост: {system_info['host']}")
    
    # 2. Проверка прав
    if not system_info['has_sudo'] and not system_info['is_root']:
        await message.answer("❌ Нет прав sudo/root. Установка невозможна.")
        return False
    
    if system_info['has_sudo']:
        await message.answer("✅ Права sudo доступны")
    elif system_info['is_root']:
        await message.answer("✅ Пользователь root")
    
    # 3. Проверка ОС
    await message.answer(f"📦 ОС: {system_info['os_info'][:100]}...")
    await message.answer(f"🐧 Ядро: {system_info['kernel']}")
    
    # 4. Проверка пакетного менеджера
    if not system_info['pkg_manager']:
        await message.answer("❌ Пакетный менеджер не найден")
        return False
    await message.answer(f"📦 Пакетный менеджер: {system_info['pkg_manager']}")
    
    # 5. Проверка доступа к репозиториям
    await message.answer("🔍 Проверяю доступ к репозиториям...")
    if 'apt-get' in system_info['pkg_manager']:
        check_cmd = "apt-get update 2>&1 | grep -E 'Get:|Hit:|Ign:' | head -3"
    elif 'yum' in system_info['pkg_manager'] or 'dnf' in system_info['pkg_manager']:
        check_cmd = "yum check-update 2>&1 | head -5 || dnf check-update 2>&1 | head -5"
    else:
        check_cmd = "echo 'Проверка репозиториев пропущена'"
    
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, check_cmd, use_sudo=system_info['has_sudo'])
    if success:
        await message.answer("✅ Репозитории доступны")
    else:
        await message.answer("⚠️ Проблемы с репозиториями, продолжаю...")
    
    # 6. Установка WireGuard
    await message.answer("🚀 Начинаю установку WireGuard...")
    
    if 'apt-get' in system_info['pkg_manager']:
        install_cmd = "apt-get install -y wireguard wireguard-tools qrencode"
    elif 'yum' in system_info['pkg_manager'] or 'dnf' in system_info['pkg_manager']:
        install_cmd = "yum install -y epel-release && yum install -y wireguard-tools qrencode || dnf install -y wireguard-tools qrencode"
    else:
        await message.answer("❌ Неподдерживаемый пакетный менеджер")
        return False
    
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, install_cmd, timeout=300, use_sudo=system_info['has_sudo'], critical=True)
    if not success:
        await message.answer(f"❌ Ошибка установки: {stderr[:200]}")
        return False
    await message.answer("✅ WireGuard установлен")
    
    # 7. Проверка установки
    await message.answer("🔍 Проверяю установку...")
    check_commands = [
        ("wg --version", "wg"),
        ("wg-quick --version", "wg-quick"),
        ("modprobe wireguard 2>&1 && echo 'Модуль загружен' || echo 'Модуль не загружен'", "wireguard модуль")
    ]
    
    for cmd, name in check_commands:
        stdout, stderr, success = await execute_ssh_command_with_check(server_id, cmd, use_sudo=system_info['has_sudo'])
        if success and name != "wireguard модуль":
            await message.answer(f"✅ {name}: установлен")
        elif name == "wireguard модуль" and "загружен" in stdout:
            await message.answer(f"✅ {name}: загружен")
    
    # 8. Создание конфигурации
    await message.answer("⚙️ Создаю конфигурацию WireGuard...")
    
    # Определяем активный интерфейс
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, f"ip route show default 2>/dev/null | awk '/default/ {{print $5}}' | head -1")
    interface = stdout.strip() or system_info['interface']
    await message.answer(f"🌐 Активный интерфейс: {interface}")
    
    # Включаем IP forwarding
    await message.answer("🔧 Включаю IP forwarding...")
    ip_forward_cmds = [
        "sysctl -w net.ipv4.ip_forward=1",
        "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
        "sysctl -p"
    ]
    for cmd in ip_forward_cmds:
        await execute_ssh_command_with_check(server_id, cmd, use_sudo=system_info['has_sudo'])
    
    # Создаем конфиг сервера
    config_cmds = [
        "mkdir -p /etc/wireguard && cd /etc/wireguard",
        "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
        f"""cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o {interface} -j MASQUERADE; sysctl -w net.ipv4.ip_forward=1
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o {interface} -j MASQUERADE
EOF""",
        "chmod 600 wg0.conf private.key public.key"
    ]
    
    for cmd in config_cmds:
        stdout, stderr, success = await execute_ssh_command_with_check(server_id, cmd, use_sudo=system_info['has_sudo'], critical=True)
        if not success:
            await message.answer(f"❌ Ошибка создания конфига: {stderr}")
            return False
    
    # Запускаем WireGuard
    await message.answer("🚀 Запускаю WireGuard...")
    startup_cmds = [
        "wg-quick up wg0",
        "systemctl enable wg-quick@wg0 2>/dev/null || true"
    ]
    for cmd in startup_cmds:
        await execute_ssh_command_with_check(server_id, cmd, use_sudo=system_info['has_sudo'])
    
    # Получаем публичный ключ и IP
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, "cat /etc/wireguard/public.key", use_sudo=system_info['has_sudo'])
    if not success or not stdout.strip():
        await message.answer("❌ Не удалось получить публичный ключ")
        return False
    
    public_key = stdout.strip()
    
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
    server_ip = stdout.strip() if success else ""
    
    # Сохраняем в БД
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE servers SET wireguard_configured = TRUE, public_key = ?, server_ip = ? WHERE id = ?", (public_key, server_ip, server_id))
        await db.commit()
    
    await message.answer(f"✅ WireGuard успешно настроен!\n🔑 Публичный ключ: {public_key[:50]}...\n🌐 IP: {server_ip}\n🔧 Интерфейс: {interface}")
    return True

async def create_wireguard_client(server_id: int, user_id: int, username: str):
    """Создание клиента WireGuard с конфигом"""
    try:
        # Получаем данные сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT public_key, server_ip, current_users, max_users FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return None, "Сервер не найден"
            
            public_key, server_ip, current_users, max_users = server
            if current_users >= max_users:
                return None, "Сервер переполнен"
            
            # Проверяем SSH
            ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
            if not ssh_ok:
                return None, f"SSH недоступен: {ssh_msg}"
            
            # Генерируем ключи клиента
            client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
            client_ip = f"10.0.0.{current_users + 2}"
            
            # Создаем ключи на сервере
            keygen_cmds = [
                f"cd /etc/wireguard && wg genkey | tee {client_name}.private | wg pubkey > {client_name}.public",
                f"cd /etc/wireguard && cat {client_name}.private",
                f"cd /etc/wireguard && cat {client_name}.public"
            ]
            
            private_key = None
            client_public_key = None
            
            for i, cmd in enumerate(keygen_cmds):
                stdout, stderr, success = await execute_ssh_command_with_check(server_id, cmd, use_sudo=system_info['has_sudo'])
                if not success:
                    return None, f"Ошибка генерации ключей: {stderr}"
                
                if i == 1: private_key = stdout.strip()
                if i == 2: client_public_key = stdout.strip()
            
            if not private_key or not client_public_key:
                return None, "Не удалось получить ключи"
            
            # Добавляем пира в конфиг
            add_peer_cmd = f"""
            cd /etc/wireguard
            echo '' >> wg0.conf
            echo '[Peer]' >> wg0.conf
            echo '# {username}' >> wg0.conf
            echo 'PublicKey = {client_public_key}' >> wg0.conf
            echo 'AllowedIPs = {client_ip}/32' >> wg0.conf
            wg set wg0 peer {client_public_key} allowed-ips {client_ip}/32
            """
            
            stdout, stderr, success = await execute_ssh_command_with_check(server_id, add_peer_cmd, use_sudo=system_info['has_sudo'])
            if not success:
                return None, f"Ошибка добавления пира: {stderr}"
            
            # Создаем конфиг клиента
            client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}/24
DNS = 8.8.8.8

[Peer]
PublicKey = {public_key}
Endpoint = {server_ip}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
            
            # Сохраняем конфиг локально
            config_filename = f"{client_name}.conf"
            config_path = os.path.join(DATA_DIR, config_filename)
            with open(config_path, 'w') as f:
                f.write(client_config)
            
            # Генерируем QR код
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(client_config)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            qr_filename = f"{client_name}_qr.png"
            qr_path = os.path.join(DATA_DIR, qr_filename)
            img.save(qr_path)
            
            # Обновляем счетчик пользователей
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
            
            return {
                'config': client_config,
                'config_path': config_path,
                'qr_path': qr_path,
                'client_name': client_name,
                'client_ip': client_ip,
                'client_public_key': client_public_key
            }, None
            
    except Exception as e:
        return None, f"Ошибка создания клиента: {str(e)}"

async def send_vpn_config_to_user(user_id: int, vpn_data: dict, message: Message):
    """Отправка конфига пользователю"""
    try:
        # Отправляем конфиг файлом
        config_file = FSInputFile(vpn_data['config_path'], filename=f"{vpn_data['client_name']}.conf")
        await bot.send_document(user_id, config_file, caption="📁 Ваш конфигурационный файл WireGuard")
        
        # Отправляем QR код
        qr_file = FSInputFile(vpn_data['qr_path'], filename=f"{vpn_data['client_name']}_qr.png")
        await bot.send_photo(user_id, qr_file, caption="📱 QR-код для быстрой настройки")
        
        # Отправляем инструкцию
        instructions = f"""🔧 <b>Инструкция по настройке WireGuard:</b>

1. <b>Установите WireGuard</b> на ваше устройство:
   • Android/iOS: App Store / Google Play
   • Windows/Mac/Linux: https://www.wireguard.com/install/

2. <b>Импортируйте конфиг</b>:
   • Откройте приложение WireGuard
   • Нажмите "+" или "Импорт"
   • Выберите файл <code>{vpn_data['client_name']}.conf</code>
   • Или отсканируйте QR-код

3. <b>Подключитесь</b>:
   • Активируйте переключатель в приложении
   • Значок 🔒 означает успешное подключение

📊 <b>Ваши данные:</b>
   • IP: <code>{vpn_data['client_ip']}</code>
   • Имя клиента: <code>{vpn_data['client_name']}</code>

🆘 <b>Если не работает:</b>
   • Перезапустите приложение
   • Проверьте интернет соединение
   • Обратитесь в поддержку: {SUPPORT_USERNAME}
"""
        await message.answer(instructions, parse_mode=ParseMode.HTML)
        
        # Сохраняем пути к файлам в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE vpn_users SET config_file_path = ?, qr_code_path = ? WHERE user_id = ? AND is_active = TRUE", 
                           (vpn_data['config_path'], vpn_data['qr_path'], user_id))
            await db.commit()
            
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки конфига: {str(e)}")

async def check_and_clean_expired_subscriptions():
    """Проверка и очистка истекших подписок"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.server_id, v.client_name, v.client_public_key 
                FROM vpn_users v 
                WHERE v.is_active = TRUE AND v.subscription_end < datetime('now')
            """)
            expired_users = await cursor.fetchall()
            
            for user_id, tg_id, server_id, client_name, client_public_key in expired_users:
                # Удаляем пира с сервера
                if server_id and client_public_key:
                    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
                    if ssh_ok:
                        remove_cmd = f"""
                        cd /etc/wireguard
                        wg set wg0 peer {client_public_key} remove 2>/dev/null || true
                        rm -f {client_name}.private {client_name}.public 2>/dev/null || true
                        """
                        await execute_ssh_command_with_check(server_id, remove_cmd, use_sudo=system_info['has_sudo'])
                        
                        # Уменьшаем счетчик пользователей
                        await db.execute("UPDATE servers SET current_users = current_users - 1 WHERE id = ? AND current_users > 0", (server_id,))
                
                # Деактивируем пользователя
                await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_id,))
                
                # Уведомляем пользователя
                try:
                    await bot.send_message(tg_id, "⚠️ Ваша VPN подписка истекла. Для продления обратитесь в поддержку.")
                except:
                    pass
                
                logger.info(f"Удален истекший VPN пользователь {tg_id}")
            
            await db.commit()
            
    except Exception as e:
        logger.error(f"Ошибка очистки подписок: {e}")

async def get_available_servers(server_type: str = 'wireguard') -> List[Dict]:
    """Получение списка доступных серверов"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, current_users, max_users, server_ip, vpn_type 
                FROM servers 
                WHERE is_active = TRUE AND wireguard_configured = TRUE 
                AND current_users < max_users
                AND vpn_type = ?
                ORDER BY current_users ASC
            """, (server_type,))
            servers = await cursor.fetchall()
            
            return [{
                'id': s[0],
                'name': s[1],
                'current_users': s[2],
                'max_users': s[3],
                'server_ip': s[4],
                'vpn_type': s[5],
                'load_percent': (s[2] / s[3] * 100) if s[3] > 0 else 0
            } for s in servers]
    except:
        return []

async def test_server_with_bot(server_id: int, bot_token: str, message: Message):
    """Тестирование сервера с проверкой окружения"""
    await message.answer("🔍 Проверяю окружение для тестового бота...")
    
    # 1. Проверка SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}")
        return False
    
    # 2. Проверка Python
    if not system_info['python']:
        await message.answer("❌ Python не найден на сервере")
        return False
    await message.answer("✅ Python доступен")
    
    # 3. Проверка возможности записи
    write_check_cmd = "touch /tmp/test_bot_write && rm /tmp/test_bot_write && echo 'WRITE_OK'"
    stdout, stderr, success = await execute_ssh_command_with_check(server_id, write_check_cmd)
    if not success or 'WRITE_OK' not in stdout:
        await message.answer("❌ Нет прав на запись в /tmp")
        return False
    await message.answer("✅ Права на запись OK")
    
    # 4. Создание тестового бота
    await message.answer("🤖 Создаю тестового бота...")
    
    bot_code = f'''#!/usr/bin/env python3
import asyncio, logging, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)
bot = Bot(token="{bot_token}", parse_mode=ParseMode.HTML)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("✅ Тестовый бот запущен на сервере!")

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    start = datetime.datetime.now()
    msg = await message.answer("🏓 Понг!")
    end = datetime.datetime.now()
    latency = (end - start).total_seconds() * 1000
    await msg.edit_text(f"🏓 Понг! Задержка: {{latency:.0f}}мс")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    
    create_cmds = [
        "cd /tmp && rm -f test_bot.py 2>/dev/null || true",
        f'''cd /tmp && cat > test_bot.py << 'EOF'
{bot_code}
EOF''',
        "chmod +x /tmp/test_bot.py",
        "cd /tmp && nohup python3 test_bot.py > bot.log 2>&1 &",
        "sleep 2 && ps aux | grep test_bot.py | grep -v grep"
    ]
    
    for cmd in create_cmds:
        stdout, stderr, success = await execute_ssh_command_with_check(server_id, cmd)
        if not success and 'ps aux' not in cmd:
            await message.answer(f"❌ Ошибка создания бота: {stderr}")
            return False
    
    await message.answer("✅ Тестовый бот запущен! Отправьте /start или /ping в бота для проверки.")
    return True

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [[types.KeyboardButton(text="🔐 Получить VPN")], [types.KeyboardButton(text="📱 Мои услуги")], [types.KeyboardButton(text="🌐 Серверы")], [types.KeyboardButton(text="🆘 Помощь")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_main_menu():
    buttons = [[types.KeyboardButton(text="🖥️ Серверы")], [types.KeyboardButton(text="👤 Пользователи")], [types.KeyboardButton(text="💰 Цены")], [types.KeyboardButton(text="🤖 Тест сервера")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def servers_menu():
    buttons = [[types.KeyboardButton(text="📋 Список серверов")], [types.KeyboardButton(text="➕ Добавить сервер")], [types.KeyboardButton(text="🔧 Установить WG")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_users_menu():
    buttons = [[types.KeyboardButton(text="🎁 Выдать VPN")], [types.KeyboardButton(text="📋 Список пользователей")], [types.KeyboardButton(text="🚫 Отключить VPN")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_type_keyboard():
    buttons = [[types.KeyboardButton(text="WireGuard")], [types.KeyboardButton(text="OpenVPN")], [types.KeyboardButton(text="IPSec/IKEv2")], [types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_selection_keyboard(servers):
    buttons = []
    for server in servers[:10]:
        load = server['load_percent']
        load_icon = "🟢" if load < 50 else "🟡" if load < 80 else "🔴"
        buttons.append([types.KeyboardButton(text=f"{load_icon} {server['name']} ({server['current_users']}/{server['max_users']})")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class AdminAddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_key = State()
    waiting_for_connection = State()
    waiting_for_max_users = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()
    waiting_for_server = State()

class AdminPriceStates(StatesGroup):
    waiting_for_week_price = State()

class AdminTestBotStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_token = State()

class AdminRemoveVPNStates(StatesGroup):
    waiting_for_user = State()

class UserGetVPNStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_server = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать в VPN Hosting!\n\n💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Криптовалюта (через поддержку)\n• Банковская карта (через поддержку)\n• PayPal (через поддержку)\n\n🆘 По вопросам оплаты: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    await message.answer("🖥️ Управление серверами", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список серверов")
async def admin_list_servers(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured, current_users, max_users, vpn_type FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
    text = "📋 Список серверов:\n\n"
    for server in servers:
        server_id, name, active, wg_configured, current_users, max_users, vpn_type = server
        status = "🟢" if active else "🔴"; wg_status = "🔐" if wg_configured else "❌"
        load = f"{current_users}/{max_users} ({int(current_users/max_users*100)}%)" if max_users > 0 else "0/0"
        text += f"{status}{wg_status} <b>{name}</b> ({vpn_type})\nID: {server_id} | 👥 {load}\n"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_type)
    await message.answer("Выберите тип VPN:", reply_markup=vpn_type_keyboard())

@dp.message(AdminAddServerStates.waiting_for_type)
async def process_server_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    if message.text not in ["WireGuard", "OpenVPN", "IPSec/IKEv2"]:
        await message.answer("Выберите тип из списка:"); return
    await state.update_data(vpn_type=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_max_users)
    await message.answer("Введите максимальное количество пользователей (рекомендуется 50-100):", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_max_users)
async def process_max_users(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    try:
        max_users = int(message.text)
        if max_users < 1 or max_users > 500:
            await message.answer("Введите число от 1 до 500:"); return
        await state.update_data(max_users=max_users)
        await state.set_state(AdminAddServerStates.waiting_for_key)
        await message.answer("📎 Пришлите файл с SSH ключом (.key, .pem):", reply_markup=back_keyboard())
    except ValueError:
        await message.answer("Введите число:")

@dp.message(AdminAddServerStates.waiting_for_key, F.document)
async def process_ssh_key_file(message: Message, state: FSMContext):
    if not message.document: await message.answer("❌ Отправьте файл с SSH ключом"); return
    file_name = message.document.file_name or ""
    if not file_name.endswith(('.key', '.pem', '.txt')): await message.answer("❌ Файл должен быть .key, .pem или .txt"); return
    await message.answer("📥 Загружаю файл...")
    try:
        file = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file.file_path)
        file_content = downloaded_file.read()
        try: key_text = file_content.decode('utf-8')
        except UnicodeDecodeError: key_text = file_content.decode('utf-8', errors='ignore')
        if '-----BEGIN' not in key_text: key_text = f"-----BEGIN PRIVATE KEY-----\n{key_text}\n-----END PRIVATE KEY-----"
        await state.update_data(ssh_key=key_text)
        await state.set_state(AdminAddServerStates.waiting_for_connection)
        await message.answer("✅ Файл загружен! Введите строку подключения (user@host:port):", reply_markup=back_keyboard())
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(AdminAddServerStates.waiting_for_key)
async def process_wrong_input_in_key_state(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    await message.answer("❌ Отправьте ФАЙЛ с SSH ключом")

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu()); return
    data = await state.get_data()
    if 'ssh_key' not in data: await message.answer("❌ SSH ключ не найден", reply_markup=servers_menu()); await state.clear(); return
    conn_str = message.text.strip()
    if '@' not in conn_str: await message.answer("❌ Формат: user@host или user@host:port"); return
    
    await message.answer("🔍 Проверяю SSH подключение...")
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(None, conn_str, data['ssh_key'])
    
    if not ssh_ok:
        await message.answer(f"❌ SSH недоступен: {ssh_msg}\nСервер не добавлен.", reply_markup=admin_main_menu())
        await state.clear()
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string, vpn_type, max_users) VALUES (?, ?, ?, ?, ?)",
                (data['server_name'], data['ssh_key'], conn_str, data.get('vpn_type', 'wireguard'), data.get('max_users', 50))
            )
            server_id = cursor.lastrowid; await db.commit()
        
        await message.answer(
            f"✅ Сервер '{data['server_name']}' добавлен!\n"
            f"ID: {server_id}\n"
            f"Тип: {data.get('vpn_type', 'wireguard')}\n"
            f"Пользователей: 0/{data.get('max_users', 50)}\n"
            f"SSH: ✅ Работает\n\n"
            f"Теперь установите VPN через меню серверов.",
            reply_markup=admin_main_menu()
        )
        await state.clear()
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu()); 
        await state.clear()

async def check_ssh_connection(server_id: int = None, conn_str: str = None, ssh_key: str = None):
    """Универсальная проверка SSH"""
    try:
        if server_id:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
                server = await cursor.fetchone()
                if not server: return False, "Сервер не найден", None
                conn_str, ssh_key = server
        
        try:
            if ':' in conn_str: user_host, port = conn_str.rsplit(':', 1); user, host = user_host.split('@'); port = int(port)
            else: user, host = conn_str.split('@'); port = 22
        except: return False, f"Неверный формат: {conn_str}", None
        
        import tempfile, stat
        ssh_key_clean = ssh_key.strip()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
            f.write(ssh_key_clean); temp_key_path = f.name
        os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
        
        try:
            async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=30) as conn:
                result = await conn.run("whoami && pwd && echo 'SSH_CHECK_OK'", timeout=30)
                if result.exit_status != 0 or 'SSH_CHECK_OK' not in result.stdout:
                    return False, f"Базовые команды не выполняются: {result.stderr}", None
                
                sudo_check = await conn.run("sudo -n true 2>&1; echo $?", timeout=10)
                has_sudo = sudo_check.stdout.strip() == '0'
                is_root = 'root' in result.stdout
                
                os_info = await conn.run("cat /etc/os-release 2>/dev/null || uname -a", timeout=10)
                pkg_check = await conn.run("which apt-get yum dnf apk pacman 2>/dev/null | head -1", timeout=10)
                kernel_check = await conn.run("uname -r", timeout=10)
                interface_check = await conn.run("ip route show default 2>/dev/null | awk '/default/ {print $5}' | head -1", timeout=10)
                python_check = await conn.run("python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'NOT_FOUND'", timeout=10)
                
                system_info = {
                    'has_sudo': has_sudo,
                    'is_root': is_root,
                    'os_info': os_info.stdout,
                    'pkg_manager': pkg_check.stdout.strip(),
                    'kernel': kernel_check.stdout.strip(),
                    'interface': interface_check.stdout.strip() or "eth0",
                    'python': 'Python' in python_check.stdout,
                    'user': user,
                    'host': host
                }
                
                try: os.unlink(temp_key_path)
                except: pass
                return True, "SSH подключение работает", system_info
                
        except asyncssh.Error as e:
            try: os.unlink(temp_key_path)
            except: pass
            return False, f"SSH ошибка: {str(e)}", None
    except Exception as e:
        return False, f"Общая ошибка: {str(e)}", None

@dp.message(F.text == "🔧 Установить WG")
async def admin_install_wg_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE AND vpn_type = 'wireguard' AND wireguard_configured = FALSE LIMIT 10")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Нет серверов для установки WireGuard"); return
    text = "🔧 Выберите сервер для установки WireGuard:\n"
    for server_id, name in servers: text += f"ID: {server_id} - {name}\n"
    text += "\nВведите ID сервера:"
    await state.set_state(AdminTestBotStates.waiting_for_server)
    await state.update_data(action="install_wg")
    await message.answer(text, reply_markup=back_keyboard())

@dp.message(AdminTestBotStates.waiting_for_server)
async def process_server_for_action(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👑 Админ-панель", reply_markup=admin_main_menu()); return
    try: server_id = int(message.text)
    except: await message.answer("Введите числовой ID:"); return
    data = await state.get_data()
    action = data.get('action')
    
    if action == "install_wg":
        success = await setup_wireguard_with_checks(server_id, message)
        if success: await message.answer("✅ WireGuard установлен! Теперь можно создавать VPN подключения.", reply_markup=admin_main_menu())
        else: await message.answer("❌ Установка не удалась.", reply_markup=admin_main_menu())
        await state.clear()
    
    elif action == "test_bot":
        await state.update_data(server_id=server_id)
        await state.set_state(AdminTestBotStates.waiting_for_token)
        await message.answer("Введите токен бота для тестирования:", reply_markup=back_keyboard())

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_gift_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer("Введите username или user_id:", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    username = message.text.replace('@', '').strip(); await state.update_data(username=username)
    await state.set_state(AdminUserStates.waiting_for_period)
    await message.answer("Выберите период:\n1. 3 дня (пробный)\n2. 7 дней\n3. 30 дней\n\nВведите номер:", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_period)
async def process_gift_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    data = await state.get_data(); username = data['username']
    period_map = {"1": 3, "2": 7, "3": 30}
    if message.text not in period_map: await message.answer("Неверный номер. Введите 1, 2 или 3:"); return
    days = period_map[message.text]
    
    # Получаем доступные серверы
    servers = await get_available_servers('wireguard')
    if not servers:
        await message.answer("❌ Нет доступных серверов"); await state.clear(); return
    
    await state.update_data(days=days, servers=servers)
    await state.set_state(AdminUserStates.waiting_for_server)
    
    text = "🖥️ Выберите сервер:\n"
    for server in servers:
        load_icon = "🟢" if server['load_percent'] < 50 else "🟡" if server['load_percent'] < 80 else "🔴"
        text += f"{load_icon} {server['name']}: {server['current_users']}/{server['max_users']} (ID: {server['id']})\n"
    text += "\nВведите ID сервера или нажмите 'Авто' для автоматического выбора:"
    
    buttons = [[types.KeyboardButton(text="Авто")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@dp.message(AdminUserStates.waiting_for_server)
async def process_gift_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    
    data = await state.get_data()
    username = data['username']
    days = data['days']
    servers = data['servers']
    
    server_id = None
    if message.text == "Авто":
        # Выбираем наименее загруженный сервер
        servers_sorted = sorted(servers, key=lambda x: x['load_percent'])
        if servers_sorted:
            server_id = servers_sorted[0]['id']
            server_name = servers_sorted[0]['name']
    else:
        try:
            server_id = int(message.text)
            # Проверяем, что сервер существует в списке доступных
            server = next((s for s in servers if s['id'] == server_id), None)
            if not server:
                await message.answer("❌ Неверный ID сервера"); return
            server_name = server['name']
        except ValueError:
            await message.answer("Введите ID сервера или 'Авто':"); return
    
    if not server_id:
        await message.answer("❌ Не удалось выбрать сервер"); await state.clear(); return
    
    try:
        user_id = 0
        if username.isdigit(): user_id = int(username); username_to_save = f"id_{username}"
        else: username_to_save = username
        
        # Создаем клиента WireGuard
        vpn_data, error = await create_wireguard_client(server_id, user_id, username_to_save)
        if error:
            await message.answer(f"❌ {error}", reply_markup=admin_main_menu())
            await state.clear()
            return
        
        subscription_end = (datetime.now() + timedelta(days=days)).isoformat()
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, client_public_key, client_ip, config_file_path, qr_code_path, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username_to_save, server_id, vpn_data['client_name'], vpn_data['client_public_key'], 
                  vpn_data['client_ip'], vpn_data['config_path'], vpn_data['qr_path'], subscription_end, days == 3))
            await db.commit()
        
        # Отправляем уведомление пользователю
        try:
            if user_id > 0:
                await bot.send_message(user_id, f"🎁 Вам выдан VPN доступ на {days} дней!\n\nСервер: {server_name}\nIP: {vpn_data['client_ip']}\n\nКонфигурация будет отправлена отдельным сообщением.")
                await send_vpn_config_to_user(user_id, vpn_data, message)
        except:
            pass
        
        await message.answer(
            f"✅ VPN выдан!\n👤 @{username}\n📅 {days} дней\n🖥️ {server_name}\n🔑 {vpn_data['client_name']}\n\nКонфиг отправлен пользователю.",
            reply_markup=admin_main_menu()
        )
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.client_name, v.subscription_end, v.is_active, s.name as server_name 
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                ORDER BY v.subscription_end DESC LIMIT 30
            """)
            users = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not users: await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu()); return
    text = "📋 Список пользователей:\n\n"
    for i, user in enumerate(users[:15], 1):
        user_id, tg_id, username, client_name, sub_end, active, server_name = user
        status = "🟢" if active else "🔴"; username_display = f"@{username}" if username else f"ID:{tg_id}"
        if sub_end: 
            sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m')
            days_left = max(0, (datetime.fromisoformat(sub_end) - datetime.now()).days)
            text += f"{i}. {status} {username_display} 📅{sub_date}({days_left}д) 🖥️{server_name or 'N/A'}\n"
        else: text += f"{i}. {status} {username_display} 📅нет подписки\n"
    if len(users) > 15: text += f"\n... и еще {len(users)-15} пользователей"
    text += "\n\nДля отключения введите номер:"
    await state.set_state(AdminRemoveVPNStates.waiting_for_user)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminRemoveVPNStates.waiting_for_user)
async def process_remove_vpn_user(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu()); return
    try:
        user_num = int(message.text) - 1
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.client_name, v.client_public_key, v.server_id 
                FROM vpn_users v 
                WHERE v.is_active = TRUE 
                ORDER BY v.subscription_end DESC LIMIT 30
            """)
            users = await cursor.fetchall()
        if user_num < 0 or user_num >= len(users): await message.answer("❌ Неверный номер"); return
        user_id, tg_id, username, client_name, client_public_key, server_id = users[user_num]
        
        # Удаляем с сервера
        if server_id and client_public_key:
            ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
            if ssh_ok:
                remove_cmd = f"""
                cd /etc/wireguard
                wg set wg0 peer {client_public_key} remove 2>/dev/null || true
                rm -f {client_name}.private {client_name}.public 2>/dev/null || true
                wg-quick strip wg0 > wg0.conf.new 2>/dev/null && mv wg0.conf.new wg0.conf 2>/dev/null || true
                """
                await execute_ssh_command_with_check(server_id, remove_cmd, use_sudo=system_info['has_sudo'])
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_id,))
            await db.execute("UPDATE servers SET current_users = current_users - 1 WHERE id = ? AND current_users > 0", (server_id,))
            await db.commit()
        
        await state.clear()
        await message.answer(f"✅ VPN отключен для @{username}!", reply_markup=admin_main_menu())
    except ValueError: await message.answer("Введите номер из списка:")
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu()); await state.clear()

@dp.message(F.text == "🚫 Отключить VPN")
async def admin_disable_vpn_start(message: Message, state: FSMContext):
    await admin_list_users(message, state)

@dp.message(F.text == "💰 Цены")
async def admin_prices(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear(); prices = await get_vpn_prices()
    text = f"💰 Текущие цены:\n💎 Неделя: {prices['week']['stars']} Stars (${prices['week']['usd']})\n💎 Месяц: {prices['month']['stars']} Stars (${prices['month']['usd']})\n\nВведите новую цену за неделю в Stars:"
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminPriceStates.waiting_for_week_price)
async def process_week_price(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👑 Админ-панель", reply_markup=admin_main_menu()); return
    try:
        week_price = int(message.text)
        if week_price < 10 or week_price > 1000: await message.answer("Цена от 10 до 1000 Stars:"); return
        month_price = week_price * 3
        week_usd = week_price * 0.10  # Примерный курс
        month_usd = month_price * 0.10
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE prices SET week_price = ?, month_price = ?, week_usd = ?, month_usd = ? WHERE id = 1", 
                           (week_price, month_price, week_usd, month_usd))
            await db.commit()
        
        await state.clear()
        await message.answer(f"✅ Цены обновлены!\nНеделя: {week_price} Stars (${week_usd:.2f})\nМесяц: {month_price} Stars (${month_usd:.2f})", reply_markup=admin_main_menu())
    except ValueError: await message.answer("Введите число (например: 50):")

@dp.message(F.text == "🤖 Тест сервера")
async def admin_test_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Нет активных серверов"); return
    text = "🤖 Выберите сервер для теста:\n"
    for server_id, name in servers: text += f"ID: {server_id} - {name}\n"
    text += "\nВведите ID сервера:"
    await state.set_state(AdminTestBotStates.waiting_for_server)
    await state.update_data(action="test_bot")
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminTestBotStates.waiting_for_token)
async def process_test_bot_token(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👑 Админ-панель", reply_markup=admin_main_menu()); return
    data = await state.get_data()
    server_id = data.get('server_id')
    bot_token = message.text.strip()
    if len(bot_token) < 30: await message.answer("Неверный формат токена"); return
    success = await test_server_with_bot(server_id, bot_token, message)
    if success: await message.answer("✅ Тестовый бот запущен! Проверьте его работу.", reply_markup=admin_main_menu())
    else: await message.answer("❌ Не удалось запустить тестового бота", reply_markup=admin_main_menu())
    await state.clear()

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear(); prices = await get_vpn_prices()
    text = f"""🔐 <b>Получить VPN доступ</b>

💳 <b>Способы оплаты:</b>
• Telegram Stars (автоматически)
• Криптовалюта (через поддержку)
• Банковская карта (через поддержку)
• PayPal (через поддержку)

📊 <b>Тарифы:</b>
🎁 <b>3 дня бесплатно</b> - пробный период
💎 <b>7 дней</b> - {prices['week']['stars']} Stars (${prices['week']['usd']})
💎 <b>30 дней</b> - {prices['month']['stars']} Stars (${prices['month']['usd']})

Выберите вариант:"""
    buttons = [[types.KeyboardButton(text="🎁 3 дня (пробный)")], [types.KeyboardButton(text="💎 Неделя")], [types.KeyboardButton(text="💎 Месяц")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message, state: FSMContext):
    await state.set_state(UserGetVPNStates.waiting_for_server)
    await state.update_data(period=3, is_trial=True)
    await show_server_selection(message, state)

@dp.message(F.text.in_(["💎 Неделя", "💎 Месяц"]))
async def get_paid_vpn(message: Message, state: FSMContext):
    period = 7 if message.text == "💎 Неделя" else 30
    await state.set_state(UserGetVPNStates.waiting_for_server)
    await state.update_data(period=period, is_trial=False)
    await show_server_selection(message, state)

async def show_server_selection(message: Message, state: FSMContext):
    """Показать выбор сервера"""
    servers = await get_available_servers('wireguard')
    if not servers:
        await message.answer("❌ Нет доступных серверов. Попробуйте позже.", reply_markup=user_main_menu())
        await state.clear()
        return
    
    text = "🖥️ <b>Выберите сервер:</b>\n\n"
    for server in servers:
        load = server['load_percent']
        load_icon = "🟢" if load < 50 else "🟡" if load < 80 else "🔴"
        text += f"{load_icon} <b>{server['name']}</b>\n"
        text += f"   👥 {server['current_users']}/{server['max_users']} пользователей\n"
        if server['server_ip']: text += f"   🌐 IP: {server['server_ip']}\n"
        text += f"   🆔 ID: {server['id']}\n\n"
    
    data = await state.get_data()
    if data.get('is_trial'):
        text += "Вы выбрали: 🎁 3 дня (пробный)"
    else:
        prices = await get_vpn_prices()
        price = prices['week']['stars'] if data['period'] == 7 else prices['month']['stars']
        text += f"Вы выбрали: 💎 {data['period']} дней ({price} Stars)"
    
    text += "\n\nВведите ID сервера или 'Авто' для автоматического выбора:"
    
    buttons = [[types.KeyboardButton(text="Авто")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(UserGetVPNStates.waiting_for_server)
async def process_user_server_selection(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await get_vpn_start(message, state)
        return
    
    servers = await get_available_servers('wireguard')
    if not servers:
        await message.answer("❌ Нет доступных серверов", reply_markup=user_main_menu())
        await state.clear()
        return
    
    server_id = None
    server_name = None
    
    if message.text == "Авто":
        # Выбираем наименее загруженный сервер
        servers_sorted = sorted(servers, key=lambda x: x['load_percent'])
        if servers_sorted:
            server_id = servers_sorted[0]['id']
            server_name = servers_sorted[0]['name']
    else:
        try:
            server_id = int(message.text)
            server = next((s for s in servers if s['id'] == server_id), None)
            if not server:
                await message.answer("❌ Неверный ID сервера"); return
            server_name = server['name']
        except ValueError:
            await message.answer("Введите ID сервера или 'Авто':"); return
    
    if not server_id:
        await message.answer("❌ Не удалось выбрать сервер", reply_markup=user_main_menu())
        await state.clear()
        return
    
    data = await state.get_data()
    period = data['period']
    is_trial = data.get('is_trial', False)
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"
    
    # Проверка пробного периода
    if is_trial:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if user and user[0]:
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
                await state.clear()
                return
    
    # Создаем клиента WireGuard
    vpn_data, error = await create_wireguard_client(server_id, user_id, username)
    if error:
        await message.answer(f"❌ {error}", reply_markup=user_main_menu())
        await state.clear()
        return
    
    subscription_end = (datetime.now() + timedelta(days=period)).isoformat()
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, client_public_key, client_ip, config_file_path, qr_code_path, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username, server_id, vpn_data['client_name'], vpn_data['client_public_key'], 
                  vpn_data['client_ip'], vpn_data['config_path'], vpn_data['qr_path'], subscription_end, is_trial))
            
            if not is_trial:
                # Записываем платеж
                prices = await get_vpn_prices()
                amount = prices['week']['stars'] if period == 7 else prices['month']['stars']
                await db.execute("""
                    INSERT INTO payments (user_id, amount, currency, payment_method, period_days, status)
                    VALUES (?, ?, 'Stars', 'Telegram Stars', ?, 'completed')
                """, (user_id, amount, period))
            
            await db.commit()
        
        # Отправляем конфиг пользователю
        await send_vpn_config_to_user(user_id, vpn_data, message)
        
        await message.answer(
            f"✅ VPN доступ активирован!\n\n"
            f"📅 Срок действия: {period} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"🔑 Имя клиента: {vpn_data['client_name']}\n"
            f"🌐 Ваш IP: {vpn_data['client_ip']}\n\n"
            f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())
    
    await state.clear()

@dp.message(F.text == "🌐 Серверы")
async def user_servers_list(message: Message):
    """Показ серверов пользователю"""
    servers = await get_available_servers('wireguard')
    if not servers:
        await message.answer("📭 Нет доступных серверов", reply_markup=user_main_menu())
        return
    
    text = "🌐 <b>Доступные серверы:</b>\n\n"
    for server in servers:
        load = server['load_percent']
        load_icon = "🟢" if load < 50 else "🟡" if load < 80 else "🔴"
        load_text = "мало" if load < 50 else "средне" if load < 80 else "много"
        text += f"{load_icon} <b>{server['name']}</b>\n"
        text += f"   👥 {server['current_users']}/{server['max_users']} ({load_text})\n"
        if server['server_ip']: text += f"   🌐 {server['server_ip']}\n"
        text += "\n"
    
    text += "🟢 - мало нагрузки\n🟡 - средняя нагрузка\n🔴 - высокая нагрузка"
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.subscription_end, v.is_active, v.client_name, v.client_ip, s.name as server_name, s.server_ip
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                WHERE v.user_id = ? AND v.is_active = TRUE 
                ORDER BY v.subscription_end DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active, client_name, client_ip, server_name, server_ip = user
        
        if not is_active:
            await message.answer("❌ Ваша подписка не активна.", reply_markup=user_main_menu())
            return
        
        if sub_end:
            end_date = datetime.fromisoformat(sub_end); now = datetime.now()
            if end_date < now: 
                status = "🔴 Истекла"
                days_left = 0
            else: 
                days_left = (end_date - now).days
                status = f"🟢 Активна ({days_left} дней осталось)"
            
            text = f"📱 <b>Ваша подписка VPN</b>\n\n"
            text += f"<b>Статус:</b> {status}\n"
            text += f"<b>Действует до:</b> {end_date.strftime('%d.%m.%Y %H:%M')}\n"
            if server_name: text += f"<b>Сервер:</b> {server_name}\n"
            if server_ip: text += f"<b>IP сервера:</b> {server_ip}\n"
            if client_name: text += f"<b>Имя клиента:</b> {client_name}\n"
            if client_ip: text += f"<b>Ваш IP в VPN:</b> {client_ip}\n"
            
            if days_left < 3 and days_left > 0:
                text += f"\n⚠️ <b>Внимание!</b> Подписка истекает через {days_left} дней.\n"
            
            text += f"\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
            
            # Добавляем кнопку для повторной отправки конфига
            buttons = [[types.KeyboardButton(text="📁 Получить конфиг снова")], [types.KeyboardButton(text="◀️ Назад")]]
            await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)
        else:
            await message.answer("📭 Нет информации о подписке", reply_markup=user_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "📁 Получить конфиг снова")
async def resend_config(message: Message):
    """Повторная отправка конфига"""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT config_file_path, qr_code_path, client_name, client_ip 
                FROM vpn_users 
                WHERE user_id = ? AND is_active = TRUE 
                ORDER BY subscription_end DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user or not user[0]:
            await message.answer("❌ Конфигурация не найдена", reply_markup=user_main_menu())
            return
        
        config_path, qr_path, client_name, client_ip = user
        
        vpn_data = {
            'config_path': config_path,
            'qr_path': qr_path,
            'client_name': client_name,
            'client_ip': client_ip
        }
        
        await send_vpn_config_to_user(user_id, vpn_data, message)
        await message.answer("✅ Конфигурация отправлена!", reply_markup=user_main_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    text = f"""🆘 <b>Помощь и поддержка</b>

💳 <b>Способы оплаты:</b>
• Telegram Stars (автоматически в боте)
• Криптовалюта (BTC, ETH, USDT)
• Банковская карта (Visa/Mastercard)
• PayPal

📞 <b>По всем вопросам:</b>
{SUPPORT_USERNAME}

🔧 <b>Частые проблемы:</b>
1. VPN не подключается
   • Перезапустите приложение WireGuard
   • Проверьте интернет соединение
   • Убедитесь, что подписка активна

2. Нет доступа к интернету через VPN
   • Проверьте настройки WireGuard
   • Попробуйте другой DNS (8.8.8.8)

3. Подписка истекла
   • Продлите через бота или поддержку

💡 <b>Советы:</b>
• Используйте последнюю версию WireGuard
• Храните конфиг в безопасном месте
• При проблемах - обратитесь в поддержку

Мы всегда готовы помочь! 😊"""
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def background_tasks():
    """Фоновые задачи для очистки подписок"""
    while True:
        try:
            await check_and_clean_expired_subscriptions()
            await asyncio.sleep(3600)  # Проверка каждый час
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)

# ========== ЗАПУСК ==========
async def main():
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    if not await init_database(): 
        logger.critical("❌ Не удалось инициализировать базу данных!"); return
    me = await bot.get_me()
    print(f"✅ Бот запущен: @{me.username}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Admin Chat ID: {ADMIN_CHAT_ID}")
    
    # Запускаем фоновые задачи
    asyncio.create_task(background_tasks())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except KeyboardInterrupt: 
        logger.info("👋 Бот остановлен")
    except Exception as e: 
        logger.critical(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)