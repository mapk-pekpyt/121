# main.py - VPN БОТ С XRAY REALITY (ПОЛНАЯ ВЕРСИЯ)
import os, asyncio, logging, sys, random, sqlite3, time, json, re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, FSInputFile, LabeledPrice, PreCheckoutQuery, 
    ContentType, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import asyncssh, aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
ADMIN_ID = 5791171535
ADMIN_CHAT_ID = -1003542769962
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не установлен!")
    print("Запустите: BOT_TOKEN='ваш_токен' python main.py")
    sys.exit(1)

SUPPORT_USERNAME = "@vpnhostik"
SUPPORT_PAYMENT = "@vpnhostik"
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

try:
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
except Exception as e:
    print(f"❌ Ошибка создания бота: {e}")
    sys.exit(1)

dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Таблица серверов
            await db.execute("""CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                name TEXT NOT NULL UNIQUE, 
                ssh_key TEXT NOT NULL, 
                connection_string TEXT NOT NULL, 
                max_users INTEGER DEFAULT 50, 
                current_users INTEGER DEFAULT 0, 
                is_active BOOLEAN DEFAULT TRUE, 
                server_ip TEXT, 
                xray_configured BOOLEAN DEFAULT FALSE,
                xray_public_key TEXT,
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            
            # Таблица пользователей
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER NOT NULL, 
                username TEXT, 
                server_id INTEGER, 
                client_name TEXT, 
                vpn_uuid TEXT UNIQUE,
                vpn_type TEXT DEFAULT 'xray',
                device_type TEXT DEFAULT 'auto', 
                subscription_end TIMESTAMP, 
                trial_used BOOLEAN DEFAULT FALSE, 
                is_active BOOLEAN DEFAULT TRUE, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL)""")
            
            # Таблица платежей
            await db.execute("""CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER NOT NULL, 
                amount_stars INTEGER DEFAULT 0,
                amount_rub REAL DEFAULT 0,
                amount_eur REAL DEFAULT 0,
                period_days INTEGER, 
                status TEXT DEFAULT 'pending', 
                telegram_payment_id TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            
            # Таблица цен
            await db.execute("""CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY, 
                week_stars INTEGER DEFAULT 50,
                week_rub REAL DEFAULT 500.0,
                week_eur REAL DEFAULT 5.0,
                month_stars INTEGER DEFAULT 150,
                month_rub REAL DEFAULT 1500.0,
                month_eur REAL DEFAULT 15.0,
                unlimited_stars INTEGER DEFAULT 300,
                unlimited_rub REAL DEFAULT 3000.0,
                unlimited_eur REAL DEFAULT 30.0)""")
            
            # Начальные цены
            await db.execute("""INSERT OR IGNORE INTO prices (id, week_stars, week_rub, week_eur, 
                month_stars, month_rub, month_eur, unlimited_stars, unlimited_rub, unlimited_eur) 
                VALUES (1, 50, 500.0, 5.0, 150, 1500.0, 15.0, 300, 3000.0, 30.0)""")
            
            # Проверяем и обновляем структуру если нужно
            cursor = await db.execute("PRAGMA table_info(servers)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            # Добавляем недостающие колонки
            if 'xray_configured' not in column_names:
                await db.execute("ALTER TABLE servers ADD COLUMN xray_configured BOOLEAN DEFAULT FALSE")
            if 'xray_public_key' not in column_names:
                await db.execute("ALTER TABLE servers ADD COLUMN xray_public_key TEXT")
            
            # Удаляем старые колонки IKEv2/L2TP если они есть
            if 'ikev2_configured' in column_names:
                # Создаем новую таблицу без старых колонок
                await db.execute("""
                    CREATE TABLE servers_new AS 
                    SELECT id, name, ssh_key, connection_string, max_users, current_users, 
                           is_active, server_ip, xray_configured, xray_public_key,
                           last_check, status, created_at 
                    FROM servers
                """)
                await db.execute("DROP TABLE servers")
                await db.execute("ALTER TABLE servers_new RENAME TO servers")
            
            # Обновляем таблицу пользователей
            cursor = await db.execute("PRAGMA table_info(vpn_users)")
            user_columns = await cursor.fetchall()
            user_column_names = [col[1] for col in user_columns]
            
            if 'vpn_uuid' not in user_column_names:
                # Переименовываем vpn_login в vpn_uuid
                if 'vpn_login' in user_column_names:
                    await db.execute("ALTER TABLE vpn_users RENAME COLUMN vpn_login TO vpn_uuid")
                else:
                    await db.execute("ALTER TABLE vpn_users ADD COLUMN vpn_uuid TEXT UNIQUE")
            
            if 'vpn_password' in user_column_names:
                await db.execute("ALTER TABLE vpn_users DROP COLUMN vpn_password")
            
            await db.commit()
            logger.info("✅ База данных инициализирована (XRay Reality)")
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
            cursor = await db.execute("""SELECT week_stars, week_rub, week_eur, 
                month_stars, month_rub, month_eur, unlimited_stars, unlimited_rub, unlimited_eur 
                FROM prices WHERE id = 1""")
            prices = await cursor.fetchone()
            if prices: 
                return {
                    "week": {"days": 7, "stars": prices[0], "rub": prices[1], "eur": prices[2]},
                    "month": {"days": 30, "stars": prices[3], "rub": prices[4], "eur": prices[5]},
                    "unlimited": {"days": 36500, "stars": prices[6], "rub": prices[7], "eur": prices[8]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен: {e}")
    
    return {
        "week": {"days": 7, "stars": 50, "rub": 500.0, "eur": 5.0},
        "month": {"days": 30, "stars": 150, "rub": 1500.0, "eur": 15.0},
        "unlimited": {"days": 36500, "stars": 300, "rub": 3000.0, "eur": 30.0}
    }

async def update_prices(week_stars: int, week_rub: float, week_eur: float, unlimited_stars: int = None, unlimited_rub: float = None, unlimited_eur: float = None):
    """Обновление цен"""
    try:
        month_stars = week_stars * 3
        month_rub = week_rub * 3
        month_eur = week_eur * 3
        
        if unlimited_stars is None:
            unlimited_stars = week_stars * 6
        if unlimited_rub is None:
            unlimited_rub = week_rub * 6
        if unlimited_eur is None:
            unlimited_eur = week_eur * 6
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE prices SET 
                week_stars = ?, week_rub = ?, week_eur = ?,
                month_stars = ?, month_rub = ?, month_eur = ?,
                unlimited_stars = ?, unlimited_rub = ?, unlimited_eur = ?
                WHERE id = 1
            """, (week_stars, week_rub, week_eur, month_stars, month_rub, month_eur, 
                  unlimited_stars, unlimited_rub, unlimited_eur))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления цен: {e}")
        return False

async def check_ssh_connection(server_id: int = None, conn_str: str = None, ssh_key: str = None):
    """Проверка SSH подключения"""
    try:
        if server_id:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
                server = await cursor.fetchone()
                if not server: return False, "Сервер не найден", None
                conn_str, ssh_key = server
        
        try:
            if ':' in conn_str: 
                user_host, port = conn_str.rsplit(':', 1)
                user, host = user_host.split('@')
                port = int(port)
            else: 
                user, host = conn_str.split('@')
                port = 22
        except: 
            return False, f"Неверный формат: {conn_str}", None
        
        import tempfile, stat
        ssh_key_clean = ssh_key.strip()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
            f.write(ssh_key_clean); temp_key_path = f.name
        os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
        
        try:
            async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=30) as conn:
                result = await conn.run("whoami && echo 'SSH_CHECK_OK'", timeout=30)
                if result.exit_status != 0 or 'SSH_CHECK_OK' not in result.stdout:
                    return False, f"Базовые команды не выполняются: {result.stderr}", None
                
                sudo_check = await conn.run("sudo -n true 2>&1; echo $?", timeout=10)
                has_sudo = sudo_check.stdout.strip() == '0'
                
                os_info = await conn.run("cat /etc/os-release 2>/dev/null || uname -a", timeout=10)
                
                # Получаем IP сервера
                ip_result = await conn.run("curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'", timeout=10)
                server_ip = ip_result.stdout.strip() if ip_result.stdout else ""
                
                system_info = {
                    'has_sudo': has_sudo,
                    'os_info': os_info.stdout,
                    'user': user,
                    'host': host,
                    'server_ip': server_ip
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

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = True) -> Tuple[str, str, bool]:
    """Выполнение SSH команды"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return "", "Сервер не найден", False
            conn_str, ssh_key = server
            
            try:
                if ':' in conn_str: 
                    user_host, port = conn_str.rsplit(':', 1)
                    user, host = user_host.split('@')
                    port = int(port)
                else: 
                    user, host = conn_str.split('@')
                    port = 22
            except: 
                return "", f"Неверный формат: {conn_str}", False
            
            import tempfile, stat
            ssh_key_clean = ssh_key.strip()
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean); temp_key_path = f.name
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(host, username=user, port=port, client_keys=[temp_key_path], known_hosts=None, connect_timeout=timeout) as conn:
                    if use_sudo and not command.strip().startswith('sudo '):
                        command = f"sudo {command}"
                    
                    result = await conn.run(command, timeout=timeout)
                    
                    try: os.unlink(temp_key_path)
                    except: pass
                    
                    if result.exit_status == 0:
                        return result.stdout, result.stderr, True
                    else:
                        return result.stdout, result.stderr, False
                    
            except asyncssh.Error as e:
                try: os.unlink(temp_key_path)
                except: pass
                return "", f"SSH ошибка: {str(e)}", False
    except Exception as e:
        return "", f"Ошибка выполнения: {str(e)}", False

# ========== XRAY REALITY УСТАНОВКА И УПРАВЛЕНИЕ ==========
async def setup_xray_vpn(server_id: int, message: Message):
    """Установка XRay с Reality на сервер"""
    await message.answer("🚀 Начинаю установку XRay Reality...")
    
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}")
        return False
    
    if not system_info['has_sudo']:
        await message.answer("❌ Нет прав sudo. Установка невозможна.")
        return False
    
    try:
        server_ip = system_info.get('server_ip', '')
        
        # Команды установки XRay
        install_cmds = [
            "apt-get update -y",
            "apt-get install -y curl wget",
            "bash -c \"$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install",
            "mkdir -p /usr/local/etc/xray",
            "touch /usr/local/etc/xray/users.json",
            'echo "{}" > /usr/local/etc/xray/users.json',
            "systemctl enable xray",
            "systemctl start xray"
        ]
        
        for cmd in install_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение при установке: {stderr[:200]}")
        
        # Генерация ключей Reality
        await message.answer("🔑 Генерирую ключи Reality...")
        keygen_cmd = "xray x25519"
        stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd, use_sudo=True)
        
        if not success or not stdout:
            await message.answer("❌ Ошибка генерации ключей XRay")
            return False
        
        # Парсим приватный и публичный ключи
        private_key_match = re.search(r'PrivateKey:\s*([A-Za-z0-9_-]+)', stdout)
        public_key_match = re.search(r'PublicKey:\s*([A-Za-z0-9_-]+)', stdout)
        
        if not private_key_match or not public_key_match:
            await message.answer("❌ Не удалось распознать сгенерированные ключи")
            return False
        
        private_key = private_key_match.group(1)
        public_key = public_key_match.group(1)
        
        # Создаем конфиг XRay
        config_template = {
            "log": {
                "loglevel": "warning",
                "access": "/var/log/xray/access.log",
                "error": "/var/log/xray/error.log"
            },
            "inbounds": [{
                "tag": "proxy",
                "port": 443,
                "protocol": "vless",
                "settings": {
                    "clients": [],
                    "decryption": "none"
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "dest": "google.com:443",
                        "serverNames": ["google.com"],
                        "privateKey": private_key,
                        "shortIds": ["aabbccdd"]
                    }
                },
                "sniffing": {
                    "enabled": true,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": true
                }
            }],
            "outbounds": [{
                "protocol": "freedom",
                "tag": "direct"
            }]
        }
        
        # Записываем конфиг на сервер
        config_json = json.dumps(config_template, indent=2)
        config_cmd = f"cat > /usr/local/etc/xray/config.json << 'EOF'\n{config_json}\nEOF"
        stdout, stderr, success = await execute_ssh_command(server_id, config_cmd, use_sudo=True)
        
        if not success:
            await message.answer(f"❌ Ошибка записи конфига: {stderr}")
            return False
        
        # Сохраняем публичный ключ в файл
        pubkey_cmd = f"echo '{public_key}' > /usr/local/etc/xray/public_key.txt"
        await execute_ssh_command(server_id, pubkey_cmd, use_sudo=True)
        
        # Перезапускаем XRay
        restart_cmd = "systemctl restart xray"
        stdout, stderr, success = await execute_ssh_command(server_id, restart_cmd, use_sudo=True)
        
        if not success:
            await message.answer(f"⚠️ XRay перезапущен с предупреждением: {stderr[:200]}")
        
        # Проверяем что XRay работает
        check_cmd = "systemctl is-active xray"
        stdout, stderr, success = await execute_ssh_command(server_id, check_cmd, use_sudo=True)
        
        if stdout.strip() != "active":
            await message.answer("⚠️ XRay установлен, но служба не активна")
            xray_ok = False
        else:
            xray_ok = True
        
        # Обновляем базу данных
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE servers SET 
                xray_configured = TRUE, 
                xray_public_key = ?,
                server_ip = ?,
                status = 'installed',
                last_check = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (public_key, server_ip, server_id))
            await db.commit()
        
        if xray_ok:
            await message.answer(
                f"✅ <b>XRay Reality успешно установлен!</b>\n\n"
                f"🌐 <b>IP сервера:</b> {server_ip}\n"
                f"🔐 <b>Тип VPN:</b> XRay (VLESS + Reality)\n"
                f"🔑 <b>Публичный ключ:</b> <code>{public_key}</code>\n"
                f"🚪 <b>Порт:</b> 443\n\n"
                f"<i>Теперь можно добавлять пользователей через админ-панель.</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer(
                f"⚠️ <b>XRay установлен с проблемами</b>\n\n"
                f"🌐 <b>IP сервера:</b> {server_ip}\n"
                f"🔐 <b>Тип VPN:</b> XRay (VLESS + Reality)\n"
                f"🔑 <b>Публичный ключ:</b> <code>{public_key}</code>\n"
                f"🚪 <b>Порт:</b> 443\n\n"
                f"<i>Проверьте статус службы: systemctl status xray</i>",
                parse_mode=ParseMode.HTML
            )
        
        return xray_ok
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки: {str(e)[:500]}")
        return False

async def test_xray_connection(server_id: int) -> Dict:
    """Проверка подключения XRay"""
    try:
        # Проверяем что XRay работает
        check_cmd = "systemctl is-active xray && echo 'XRAY_ACTIVE'"
        stdout, stderr, success = await execute_ssh_command(server_id, check_cmd, use_sudo=True)
        
        if "XRAY_ACTIVE" not in stdout:
            # Пытаемся перезапустить
            await execute_ssh_command(server_id, "systemctl restart xray", use_sudo=True)
            await asyncio.sleep(3)
            
            stdout, stderr, success = await execute_ssh_command(server_id, check_cmd, use_sudo=True)
            
            if "XRAY_ACTIVE" not in stdout:
                return {"success": False, "message": "Служба XRay не запущена"}
        
        # Проверяем порт
        port_check = "ss -tuln | grep ':443 ' || netstat -tuln | grep ':443 ' || echo 'PORT_NOT_OPEN'"
        stdout, stderr, success = await execute_ssh_command(server_id, port_check, use_sudo=False)
        
        if "PORT_NOT_OPEN" in stdout:
            return {"success": False, "message": "Порт 443 не открыт"}
        
        return {"success": True, "message": "XRay работает корректно"}
        
    except Exception as e:
        return {"success": False, "message": f"Ошибка проверки: {str(e)[:200]}"}

async def create_xray_user(server_id: int, user_id: int, username: str, device_type: str = "auto"):
    """Создание пользователя XRay"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем данные сервера
            cursor = await db.execute("""
                SELECT server_ip, current_users, max_users, xray_public_key 
                FROM servers WHERE id = ? AND xray_configured = TRUE
            """, (server_id,))
            server = await cursor.fetchone()
            
            if not server:
                return None, "Сервер не найден или XRay не установлен"
            
            server_ip, current_users, max_users, public_key = server
            
            if current_users >= max_users:
                return None, "Сервер переполнен"
            
            # Генерируем UUID
            uuid_cmd = "xray uuid"
            stdout, stderr, success = await execute_ssh_command(server_id, uuid_cmd, use_sudo=True)
            
            if not success or not stdout:
                return None, "Ошибка генерации UUID"
            
            vpn_uuid = stdout.strip()
            
            # Добавляем пользователя в конфиг XRay
            add_user_cmd = f"""
                python3 -c "
import json
with open('/usr/local/etc/xray/config.json', 'r') as f:
    config = json.load(f)
    
# Добавляем клиента в первый inbound (Reality)
client = {{'id': '{vpn_uuid}', 'flow': 'xtls-rprx-vision'}}
config['inbounds'][0]['settings']['clients'].append(client)

with open('/usr/local/etc/xray/config.json', 'w') as f:
    json.dump(config, f, indent=2)
"
            """
            
            stdout, stderr, success = await execute_ssh_command(server_id, add_user_cmd, use_sudo=True)
            
            if not success:
                return None, f"Ошибка добавления пользователя в конфиг: {stderr[:200]}"
            
            # Добавляем в users.json
            user_data_cmd = f"""
                python3 -c "
import json
try:
    with open('/usr/local/etc/xray/users.json', 'r') as f:
        users = json.load(f)
except:
    users = {{}}
    
users['{vpn_uuid}'] = '{username}'

with open('/usr/local/etc/xray/users.json', 'w') as f:
    json.dump(users, f, indent=2)
"
            """
            
            await execute_ssh_command(server_id, user_data_cmd, use_sudo=True)
            
            # Перезапускаем XRay
            await execute_ssh_command(server_id, "systemctl restart xray", use_sudo=True)
            
            # Обновляем счетчик пользователей
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            
            # Сохраняем пользователя в БД
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, vpn_uuid, vpn_type, device_type, is_active)
                VALUES (?, ?, ?, ?, ?, 'xray', ?, TRUE)
            """, (user_id, username, server_id, f"client_{user_id}_{random.randint(1000, 9999)}", vpn_uuid, device_type))
            
            await db.commit()
            
            # Формируем ссылку VLESS
            vless_link = f"vless://{vpn_uuid}@{server_ip}:443?security=reality&sni=google.com&alpn=h2&fp=chrome&pbk={public_key}&sid=aabbccdd&type=tcp&flow=xtls-rprx-vision&encryption=none#{username}"
            
            return {
                'server_ip': server_ip,
                'vpn_uuid': vpn_uuid,
                'public_key': public_key,
                'vless_link': vless_link,
                'device_type': device_type
            }, None
            
    except Exception as e:
        return None, f"Ошибка создания пользователя: {str(e)}"

async def delete_xray_user(server_id: int, vpn_uuid: str):
    """Удаление пользователя XRay"""
    try:
        # Удаляем из конфига
        remove_user_cmd = f"""
            python3 -c "
import json
with open('/usr/local/etc/xray/config.json', 'r') as f:
    config = json.load(f)

# Удаляем клиента
clients = config['inbounds'][0]['settings']['clients']
config['inbounds'][0]['settings']['clients'] = [c for c in clients if c['id'] != '{vpn_uuid}']

with open('/usr/local/etc/xray/config.json', 'w') as f:
    json.dump(config, f, indent=2)
"
        """
        
        stdout, stderr, success = await execute_ssh_command(server_id, remove_user_cmd, use_sudo=True)
        
        if not success:
            return False, f"Ошибка удаления из конфига: {stderr[:200]}"
        
        # Удаляем из users.json
        remove_data_cmd = f"""
            python3 -c "
import json
try:
    with open('/usr/local/etc/xray/users.json', 'r') as f:
        users = json.load(f)
except:
    users = {{}}
    
users.pop('{vpn_uuid}', None)

with open('/usr/local/etc/xray/users.json', 'w') as f:
    json.dump(users, f, indent=2)
"
        """
        
        await execute_ssh_command(server_id, remove_data_cmd, use_sudo=True)
        
        # Перезапускаем XRay
        await execute_ssh_command(server_id, "systemctl restart xray", use_sudo=True)
        
        # Уменьшаем счетчик
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET current_users = current_users - 1 WHERE id = ? AND current_users > 0", (server_id,))
            await db.commit()
        
        return True, "Пользователь успешно удален"
        
    except Exception as e:
        return False, f"Ошибка удаления: {str(e)}"

async def check_expired_subscriptions():
    """Проверка и удаление истекших подписок"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.server_id, v.vpn_uuid 
                FROM vpn_users v 
                WHERE v.is_active = TRUE 
                AND v.subscription_end IS NOT NULL 
                AND datetime(v.subscription_end) < datetime('now')
            """)
            expired_users = await cursor.fetchall()
            
            for user in expired_users:
                user_id, tg_user_id, server_id, vpn_uuid = user
                
                # Удаляем пользователя из VPN сервера
                if server_id and vpn_uuid:
                    await delete_xray_user(server_id, vpn_uuid)
                
                # Отключаем пользователя в БД
                await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_id,))
                
                # Отправляем уведомление
                try:
                    await bot.send_message(
                        tg_user_id,
                        "⚠️ <b>Ваша подписка VPN истекла!</b>\n\n"
                        "Для продолжения использования VPN приобретите новую подписку через кнопку '🔐 Получить VPN'.\n\n"
                        f"🆘 Поддержка: {SUPPORT_USERNAME}",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            
            await db.commit()
            return len(expired_users)
            
    except Exception as e:
        logger.error(f"Ошибка проверки подписок: {e}")
        return 0

async def extend_subscription(user_id: int, period_days: int, admin_action: bool = False):
    """Продление подписки пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, subscription_end, vpn_uuid, server_id
                FROM vpn_users 
                WHERE user_id = ? AND is_active = TRUE 
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
            
            if not user:
                return False, "У пользователя нет активной подписки"
            
            user_db_id, current_end, vpn_uuid, server_id = user
            
            # Вычисляем новую дату окончания
            if current_end:
                try:
                    current_end_dt = datetime.fromisoformat(current_end)
                    if current_end_dt > datetime.now():
                        new_end = current_end_dt + timedelta(days=period_days)
                    else:
                        new_end = datetime.now() + timedelta(days=period_days)
                except:
                    new_end = datetime.now() + timedelta(days=period_days)
            else:
                new_end = datetime.now() + timedelta(days=period_days)
            
            # Обновляем подписку
            await db.execute("""
                UPDATE vpn_users 
                SET subscription_end = ?, last_check = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_end.isoformat(), user_db_id))
            
            await db.commit()
            
            if admin_action:
                await db.execute("""
                    INSERT INTO payments (user_id, period_days, status, created_at)
                    VALUES (?, ?, 'admin_extended', CURRENT_TIMESTAMP)
                """, (user_id, period_days))
                await db.commit()
            
            return True, f"Подписка продлена на {period_days} дней. Новый срок: {new_end.strftime('%d.%m.%Y %H:%M')}"
            
    except Exception as e:
        return False, f"Ошибка продления: {str(e)}"

async def disable_user_vpn(user_id: int):
    """Отключение VPN для пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, vpn_uuid, server_id
                FROM vpn_users 
                WHERE user_id = ? AND is_active = TRUE 
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
            
            if not user:
                return False, "У пользователя нет активной подписки"
            
            user_db_id, vpn_uuid, server_id = user
            
            # Удаляем пользователя из VPN сервера
            if server_id and vpn_uuid:
                await delete_xray_user(server_id, vpn_uuid)
            
            # Отключаем пользователя в БД
            await db.execute("UPDATE vpn_users SET is_active = FALSE WHERE id = ?", (user_db_id,))
            await db.commit()
            
            return True, "VPN успешно отключен"
            
    except Exception as e:
        return False, f"Ошибка отключения: {str(e)}"

async def send_xray_config_to_user(user_id: int, vpn_data: dict, message: Message):
    """Отправка конфига XRay пользователю"""
    try:
        instructions = f"""🔧 <b>Ваши данные для подключения (XRay Reality):</b>

🌐 <b>Сервер:</b> {vpn_data['server_ip']}
🔑 <b>UUID:</b> <code>{vpn_data['vpn_uuid']}</code>
🔐 <b>Публичный ключ:</b> <code>{vpn_data['public_key']}</code>
🚪 <b>Порт:</b> 443
🔧 <b>Тип:</b> VLESS + Reality

<b>Готовая ссылка для приложений (Hiddify, Nekobox, v2rayNG):</b>
<code>{vpn_data['vless_link']}</code>

<b>Ручная настройка:</b>
1. Тип: VLESS
2. Адрес: {vpn_data['server_ip']}
3. Порт: 443
4. ID (UUID): {vpn_data['vpn_uuid']}
5. Зашифрование: none
6. Сеть: tcp
7. Тип безопасности: reality
8. SNI: google.com
9. Public Key: {vpn_data['public_key']}
10. Short ID: aabbccdd
11. Flow: xtls-rprx-vision

⚠️ <b>Сохраните эти данные!</b> Они не восстанавливаются.
🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"""
        
        await message.answer(instructions, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки конфига: {str(e)}")

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔐 Получить VPN")],
            [types.KeyboardButton(text="🔄 Продлить подписку")],
            [types.KeyboardButton(text="📱 Мои услуги")],
            [types.KeyboardButton(text="🌐 Серверы")],
            [types.KeyboardButton(text="🆘 Помощь")]
        ],
        resize_keyboard=True
    )

def admin_main_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🖥️ Серверы")],
            [types.KeyboardButton(text="👤 Пользователи")],
            [types.KeyboardButton(text="💰 Цены")],
            [types.KeyboardButton(text="🤖 Тест сервера")],
            [types.KeyboardButton(text="🔄 Продлить подписку")]
        ],
        resize_keyboard=True
    )

def servers_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📋 Список серверов")],
            [types.KeyboardButton(text="➕ Добавить сервер")],
            [types.KeyboardButton(text="🔧 Установить XRay")],
            [types.KeyboardButton(text="🔄 Проверить XRay")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def admin_users_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎁 Выдать VPN")],
            [types.KeyboardButton(text="📋 Список пользователей")],
            [types.KeyboardButton(text="🚫 Отключить VPN")],
            [types.KeyboardButton(text="🔄 Продлить подписку")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def device_type_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📱 iPhone/Hiddify")],
            [types.KeyboardButton(text="🤖 Android/NG")],
            [types.KeyboardButton(text="💻 Другое")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def payment_method_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="💎 Stars (Telegram)")],
            [types.KeyboardButton(text="💳 Карта (RUB/€)")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def period_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎁 3 дня (пробный)")],
            [types.KeyboardButton(text="💎 Неделя")],
            [types.KeyboardButton(text="💎 Месяц")],
            [types.KeyboardButton(text="♾️ Безлимит")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def extend_period_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="💎 Неделя")],
            [types.KeyboardButton(text="💎 Месяц")],
            [types.KeyboardButton(text="♾️ Безлимит")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def back_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="◀️ Назад")]],
        resize_keyboard=True
    )

def prices_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✏️ Изменить цену")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def test_server_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📋 Список для теста")],
            [types.KeyboardButton(text="🔧 Установить XRay")],
            [types.KeyboardButton(text="🔄 Проверить XRay")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def install_xray_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✅ Установить XRay Reality")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def recheck_xray_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔄 Проверить все серверы")],
            [types.KeyboardButton(text="📋 Выбрать сервер")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

# ========== FSM СОСТОЯНИЯ ==========
class AddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()
    waiting_for_max_users = State()

class InstallXRayStates(StatesGroup):
    waiting_for_server = State()

class PriceStates(StatesGroup):
    waiting_for_prices = State()

class TestServerStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_recheck_server = State()

class UserPaymentStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_payment = State()
    waiting_for_device = State()

class ExtendSubscriptionStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_period = State()

class DisableVPNStates(StatesGroup):
    waiting_for_user = State()

class IssueVPNStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_period = State()
    waiting_for_device = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer(
            f"🚀 Добро пожаловать в VPN Hosting!\n\n"
            f"🔐 <b>Теперь используем XRay Reality</b> - самый современный и быстрый протокол!\n\n"
            f"💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Карта (RUB/€)\n\n"
            f"🆘 Поддержка: {SUPPORT_USERNAME}",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ АДМИНА ==========
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
            cursor = await db.execute("SELECT id, name, is_active, xray_configured, current_users, max_users, server_ip, status FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}"); return
    if not servers: 
        await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
    
    text = "📋 <b>Список серверов:</b>\n\n"
    for server in servers:
        server_id, name, active, xray_configured, current, max_users, server_ip, status = server
        status_icon = "🟢" if status == "installed" else "🟡" if status == "pending" else "🔴"
        active_icon = "✅" if active else "❌"
        xray_status = "🔐" if xray_configured else "❌"
        load = f"{current}/{max_users}"
        ip_display = server_ip if server_ip else "N/A"
        text += f"{status_icon}{active_icon}{xray_status} <b>{name}</b>\nID: {server_id} | 👥 {load} | 🌐 {ip_display}\nСтатус: {status}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=back_keyboard())

@dp.message(AddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🖥️ Управление серверами", reply_markup=servers_menu())
        return
    
    await state.update_data(server_name=message.text)
    await state.set_state(AddServerStates.waiting_for_max_users)
    await message.answer("Введите максимальное количество пользователей:", reply_markup=back_keyboard())

@dp.message(AddServerStates.waiting_for_max_users)
async def process_max_users(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(AddServerStates.waiting_for_name)
        await message.answer("Введите имя сервера:", reply_markup=back_keyboard())
        return
    
    try:
        max_users = int(message.text)
        if max_users < 1 or max_users > 500:
            await message.answer("Введите число от 1 до 500:", reply_markup=back_keyboard())
            return
        
        await state.update_data(max_users=max_users)
        await state.set_state(AddServerStates.waiting_for_key)
        await message.answer("📎 Пришлите файл с SSH ключом (.key, .pem, .txt):", reply_markup=back_keyboard())
        
    except ValueError:
        await message.answer("Введите число:", reply_markup=back_keyboard())

@dp.message(AddServerStates.waiting_for_key, F.document)
async def process_ssh_key_file(message: Message, state: FSMContext):
    if not message.document: 
        await message.answer("❌ Отправьте файл с SSH ключом", reply_markup=back_keyboard())
        return
    
    file_name = message.document.file_name or ""
    if not file_name.endswith(('.key', '.pem', '.txt')):
        await message.answer("❌ Файл должен быть .key, .pem или .txt", reply_markup=back_keyboard())
        return
    
    await message.answer("📥 Загружаю файл...")
    
    try:
        file = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file.file_path)
        file_content = downloaded_file.read()
        
        try: 
            key_text = file_content.decode('utf-8')
        except UnicodeDecodeError: 
            key_text = file_content.decode('utf-8', errors='ignore')
        
        if '-----BEGIN' not in key_text:
            key_text = f"-----BEGIN PRIVATE KEY-----\n{key_text}\n-----END PRIVATE KEY-----"
        
        await state.update_data(ssh_key=key_text)
        await state.set_state(AddServerStates.waiting_for_connection)
        await message.answer("✅ Файл загружен! Введите строку подключения (user@host:port):", reply_markup=back_keyboard())
        
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=back_keyboard())

@dp.message(AddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(AddServerStates.waiting_for_key)
        await message.answer("📎 Пришлите файл с SSH ключом:", reply_markup=back_keyboard())
        return
    
    data = await state.get_data()
    if 'ssh_key' not in data:
        await message.answer("❌ SSH ключ не найден", reply_markup=servers_menu())
        await state.clear()
        return
    
    conn_str = message.text.strip()
    if '@' not in conn_str:
        await message.answer("❌ Формат: user@host или user@host:port", reply_markup=back_keyboard())
        return
    
    await message.answer("🔍 Проверяю SSH подключение...")
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(None, conn_str, data['ssh_key'])
    
    if not ssh_ok:
        await message.answer(f"❌ SSH недоступен: {ssh_msg}\nСервер не добавлен.", reply_markup=admin_main_menu())
        await state.clear()
        return
    
    try:
        server_ip = system_info.get('server_ip', '')
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string, max_users, server_ip, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                (data['server_name'], data['ssh_key'], conn_str, data.get('max_users', 50), server_ip)
            )
            server_id = cursor.lastrowid
            await db.commit()
        
        await message.answer(
            f"✅ Сервер '{data['server_name']}' добавлен!\n"
            f"ID: {server_id}\n"
            f"Лимит: {data.get('max_users', 50)} пользователей\n"
            f"IP: {server_ip}\n\n"
            f"Теперь установите XRay через меню '🔧 Установить XRay'",
            reply_markup=admin_main_menu()
        )
        await state.clear()
        
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "🔧 Установить XRay")
async def admin_install_xray_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, status, xray_configured FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=servers_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет активных серверов", reply_markup=servers_menu()); return
    
    text = "🔧 <b>Выберите сервер для установки XRay:</b>\n"
    for server_id, name, status, xray_configured in servers: 
        status_icon = "🟢" if status == "installed" else "🟡" if status == "pending" else "🔴"
        xray_icon = "✅" if xray_configured else "❌"
        text += f"ID: {server_id} - {name} {status_icon} XRay: {xray_icon}\n"
    text += "\nВведите ID сервера:"
    
    await state.set_state(InstallXRayStates.waiting_for_server)
    await message.answer(text, reply_markup=back_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(InstallXRayStates.waiting_for_server)
async def process_install_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🖥️ Управление серверами", reply_markup=servers_menu())
        return
    
    try: 
        server_id = int(message.text)
    except: 
        await message.answer("Введите числовой ID:", reply_markup=back_keyboard())
        return
    
    # Проверяем существование сервера
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name, xray_configured FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: 
                await message.answer("❌ Сервер не найден", reply_markup=back_keyboard())
                return
            server_name, xray_configured = server
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=back_keyboard())
        return
    
    if xray_configured:
        await message.answer(f"❌ XRay уже установлен на сервере '{server_name}'.", reply_markup=servers_menu())
        await state.clear()
        return
    
    success = await setup_xray_vpn(server_id, message)
    await state.clear()
    
    if success: 
        await message.answer(f"✅ XRay Reality успешно установлен на сервер '{server_name}'!", reply_markup=admin_main_menu())
    else: 
        await message.answer(f"⚠️ XRay установлен на сервер '{server_name}' с проблемами", reply_markup=admin_main_menu())

@dp.message(F.text == "🔄 Проверить XRay")
async def admin_recheck_xray(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    await message.answer("🔄 Выберите действие:", reply_markup=recheck_xray_menu())

@dp.message(F.text == "🔄 Проверить все серверы")
async def recheck_all_servers(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    await message.answer("🔍 Начинаю проверку всех серверов...")
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, xray_configured, server_ip FROM servers WHERE is_active = TRUE")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}", reply_markup=servers_menu())
        return
    
    if not servers:
        await message.answer("📭 Нет активных серверов", reply_markup=servers_menu())
        return
    
    results = []
    for server in servers:
        server_id, name, xray_configured, server_ip = server
        
        result_text = f"<b>{name}</b> (ID: {server_id})\n"
        
        if xray_configured:
            check_result = await test_xray_connection(server_id)
            status = "✅ Работает" if check_result['success'] else f"❌ Проблемы: {check_result['message'][:100]}"
            result_text += f"🔐 XRay Reality: {status}\n"
        else:
            result_text += f"❌ XRay не установлен\n"
        
        results.append(result_text)
        
        if len(results) % 3 == 0:
            await message.answer("\n".join(results), parse_mode=ParseMode.HTML)
            results = []
            await asyncio.sleep(1)
    
    if results:
        await message.answer("\n".join(results), parse_mode=ParseMode.HTML)
    
    await message.answer("✅ Проверка завершена!", reply_markup=servers_menu())

@dp.message(F.text == "📋 Выбрать сервер")
async def select_server_for_recheck(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, server_ip FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=recheck_xray_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет активных серверов", reply_markup=recheck_xray_menu()); return
    
    text = "🤖 <b>Выберите сервер для проверки:</b>\n\n"
    for server_id, name, server_ip in servers:
        ip_display = server_ip if server_ip else "IP не установлен"
        text += f"<b>{name}</b>\nID: {server_id} | 🌐 {ip_display}\n\n"
    
    text += "Введите ID сервера для проверки:"
    
    await state.set_state(TestServerStates.waiting_for_recheck_server)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(TestServerStates.waiting_for_recheck_server)
async def process_recheck_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🔄 Перепроверка VPN", reply_markup=recheck_xray_menu())
        return
    
    try: 
        server_id = int(message.text)
    except: 
        await message.answer("Введите числовой ID:", reply_markup=back_keyboard())
        return
    
    await message.answer(f"🔍 Тестирую сервер ID {server_id}...")
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name, xray_configured, server_ip FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server:
                await message.answer("❌ Сервер не найден", reply_markup=recheck_xray_menu())
                await state.clear()
                return
            
            name, xray_configured, server_ip = server
            
        result_text = f"<b>{name}</b> (ID: {server_id})\n"
        
        if xray_configured:
            check_result = await test_xray_connection(server_id)
            status = "✅ Работает" if check_result['success'] else f"❌ Проблемы: {check_result['message'][:100]}"
            result_text += f"🔐 XRay Reality: {status}\n"
        else:
            result_text += "❌ XRay не установлен\n"
        
        # Обновляем статус сервера
        async with aiosqlite.connect(DB_PATH) as db:
            status = "installed" if xray_configured else "pending"
            await db.execute("UPDATE servers SET status = ?, last_check = CURRENT_TIMESTAMP WHERE id = ?", (status, server_id))
            await db.commit()
        
        await message.answer(result_text, parse_mode=ParseMode.HTML, reply_markup=recheck_xray_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка тестирования: {str(e)}", reply_markup=recheck_xray_menu())
    
    await state.clear()

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.vpn_uuid, v.subscription_end, v.is_active, v.device_type, s.name as server_name 
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                ORDER BY v.created_at DESC LIMIT 30
            """)
            users = await cursor.fetchall()
    except Exception as e: 
        await message.answer(f"❌ Ошибка получения данных: {e}", reply_markup=admin_users_menu()); return
    
    if not users: 
        await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu()); return
    
    text = "📋 <b>Список пользователей:</b>\n\n"
    for i, user in enumerate(users[:15], 1):
        user_id, tg_id, username, vpn_uuid, sub_end, active, device_type, server_name = user
        status = "🟢" if active else "🔴"
        username_display = f"@{username}" if username else f"ID:{tg_id}"
        device_icon = "📱" if device_type == "iphone" else "🤖" if device_type == "android" else "💻"
        vpn_uuid_short = vpn_uuid[:8] + "..." if vpn_uuid else "N/A"
        
        if sub_end: 
            try:
                sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m')
                days_left = max(0, (datetime.fromisoformat(sub_end) - datetime.now()).days)
                text += f"{i}. {status}{device_icon} {username_display} 📅{sub_date}({days_left}д) 🖥️{server_name or 'N/A'}\nUUID: {vpn_uuid_short}\n"
            except:
                text += f"{i}. {status}{device_icon} {username_display} 📅бессрочно\nUUID: {vpn_uuid_short}\n"
        else: 
            text += f"{i}. {status}{device_icon} {username_display} 📅бессрочно\nUUID: {vpn_uuid_short}\n"
    
    if len(users) > 15: 
        text += f"\n... и еще {len(users)-15} пользователей"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_users_menu())

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_issue_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(IssueVPNStates.waiting_for_user)
    await message.answer("Введите ID пользователя или username (например: 123456789 или @username):", reply_markup=back_keyboard())

@dp.message(IssueVPNStates.waiting_for_user)
async def process_issue_vpn_user(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("👤 Управление пользователей", reply_markup=admin_users_menu())
        return
    
    user_identifier = message.text.strip()
    
    if user_identifier.startswith('@'):
        username = user_identifier[1:]
        await message.answer("Пожалуйста, введите числовой ID пользователя:")
        return
    else:
        try:
            user_id = int(user_identifier)
        except:
            await message.answer("Введите корректный числовой ID пользователя:", reply_markup=back_keyboard())
            return
    
    await state.update_data(user_id=user_id)
    await state.set_state(IssueVPNStates.waiting_for_period)
    
    prices = await get_vpn_prices()
    text = f"""🎁 <b>Выдача VPN пользователю ID: {user_id}</b>

📊 <b>Тарифы:</b>
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}
♾️ <b>Безлимит</b> - {prices['unlimited']['stars']} Stars / ₽{prices['unlimited']['rub']:.2f} / €{prices['unlimited']['eur']:.2f}

Выберите период:"""
    
    await message.answer(text, reply_markup=extend_period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(IssueVPNStates.waiting_for_period)
async def process_issue_vpn_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(IssueVPNStates.waiting_for_user)
        await message.answer("Введите ID пользователя или username:", reply_markup=back_keyboard())
        return
    
    period_map = {
        "💎 Неделя": 7,
        "💎 Месяц": 30,
        "♾️ Безлимит": 36500
    }
    
    if message.text not in period_map:
        await message.answer("Выберите период из списка:", reply_markup=extend_period_keyboard())
        return
    
    period_days = period_map[message.text]
    await state.update_data(period_days=period_days)
    await state.set_state(IssueVPNStates.waiting_for_device)
    await message.answer("📱 Выберите тип устройства пользователя:", reply_markup=device_type_keyboard())

@dp.message(IssueVPNStates.waiting_for_device)
async def process_issue_vpn_device(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(IssueVPNStates.waiting_for_period)
        await process_issue_vpn_period(message, state)
        return
    
    device_map = {
        "📱 iPhone/Hiddify": "iphone",
        "🤖 Android/NG": "android",
        "💻 Другое": "auto"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите тип устройства из списка:", reply_markup=device_type_keyboard())
        return
    
    device_type = device_map[message.text]
    
    data = await state.get_data()
    user_id = data['user_id']
    period_days = data['period_days']
    username = f"user_{user_id}"
    
    # Находим доступный сервер
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, current_users, max_users 
                FROM servers 
                WHERE is_active = TRUE AND xray_configured = TRUE AND current_users < max_users
                ORDER BY current_users ASC LIMIT 1
            """)
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов. Попробуйте позже.", reply_markup=admin_users_menu())
                await state.clear()
                return
            
            server_id, server_name, current_users, max_users = server
            
            # Создаем VPN клиента
            vpn_data, error = await create_xray_user(server_id, user_id, username, device_type)
            
            if error:
                await message.answer(f"❌ {error}", reply_markup=admin_users_menu())
                await state.clear()
                return
            
            # Устанавливаем срок подписки
            subscription_end = (datetime.now() + timedelta(days=period_days)).isoformat()
            
            # Обновляем пользователя с данными подписки
            await db.execute("""
                UPDATE vpn_users 
                SET subscription_end = ?, trial_used = TRUE, is_active = TRUE
                WHERE user_id = ? 
                ORDER BY id DESC LIMIT 1
            """, (subscription_end, user_id))
            
            await db.commit()
        
        # Отправляем сообщение пользователю
        try:
            await bot.send_message(
                user_id,
                f"🎁 <b>Вам выдан VPN доступ (XRay Reality)!</b>\n\n"
                f"Администратор предоставил вам доступ к VPN на {period_days} дней.\n\n"
                f"🌐 <b>Сервер:</b> {vpn_data['server_ip']}\n"
                f"🔑 <b>UUID:</b> <code>{vpn_data['vpn_uuid']}</code>\n"
                f"🔐 <b>Публичный ключ:</b> <code>{vpn_data['public_key']}</code>\n"
                f"🚪 <b>Порт:</b> 443\n\n"
                f"<b>Готовая ссылка:</b>\n<code>{vpn_data['vless_link']}</code>\n\n"
                f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}\n\n"
                f"🆘 Поддержка: {SUPPORT_USERNAME}",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
        
        await message.answer(
            f"✅ VPN успешно выдан пользователю ID: {user_id}\n\n"
            f"📅 Срок: {period_days} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"🔐 Тип: XRay Reality\n"
            f"📱 Устройство: {device_type}\n"
            f"🔑 UUID: {vpn_data['vpn_uuid']}\n"
            f"🔐 Публичный ключ: {vpn_data['public_key'][:20]}...\n\n"
            f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}",
            reply_markup=admin_users_menu(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_users_menu())
    
    await state.clear()

@dp.message(F.text == "🚫 Отключить VPN")
async def admin_disable_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(DisableVPNStates.waiting_for_user)
    await message.answer("Введите ID пользователя для отключения VPN:", reply_markup=back_keyboard())

@dp.message(DisableVPNStates.waiting_for_user)
async def process_disable_vpn_user(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("👤 Управление пользователями", reply_markup=admin_users_menu())
        return
    
    try:
        user_id = int(message.text)
    except:
        await message.answer("Введите корректный числовой ID пользователя:", reply_markup=back_keyboard())
        return
    
    success, message_text = await disable_user_vpn(user_id)
    
    if success:
        await message.answer(f"✅ VPN успешно отключен для пользователя ID: {user_id}", reply_markup=admin_users_menu())
    else:
        await message.answer(f"❌ {message_text}", reply_markup=admin_users_menu())
    
    await state.clear()

@dp.message(F.text == "💰 Цены")
async def admin_prices(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    prices = await get_vpn_prices()
    text = f"""💰 <b>Текущие цены:</b>

<b>Неделя (7 дней):</b>
💎 {prices['week']['stars']} Stars
₽ {prices['week']['rub']:.2f} RUB
€ {prices['week']['eur']:.2f} EUR

<b>Месяц (30 дней):</b>
💎 {prices['month']['stars']} Stars
₽ {prices['month']['rub']:.2f} RUB
€ {prices['month']['eur']:.2f} EUR

<b>Безлимит (100 лет):</b>
💎 {prices['unlimited']['stars']} Stars
₽ {prices['unlimited']['rub']:.2f} RUB
€ {prices['unlimited']['eur']:.2f} EUR

Для изменения цен используйте кнопку ниже:"""
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=prices_menu())

@dp.message(F.text == "✏️ Изменить цену")
async def admin_change_price_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    prices = await get_vpn_prices()
    text = f"""✏️ <b>Изменение цены</b>

<b>Текущие цены за неделю:</b>
💎 {prices['week']['stars']} Stars
₽ {prices['week']['rub']:.2f} RUB
€ {prices['week']['eur']:.2f} EUR

<b>Введите новые цены за неделю в формате:</b>
<code>Stars, RUB, EUR</code>

<b>Пример:</b> <code>50, 500.0, 5.0</code>

<b>Месячная цена будет рассчитана автоматически (×3)</b>
<b>Безлимит будет рассчитан автоматически (×6)</b>"""
    
    await state.set_state(PriceStates.waiting_for_prices)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(PriceStates.waiting_for_prices)
async def process_new_prices(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await admin_prices(message)
        return
    
    try:
        parts = [p.strip() for p in message.text.split(',')]
        if len(parts) != 3:
            raise ValueError("Нужно 3 значения через запятую")
        
        week_stars = int(parts[0])
        week_rub = float(parts[1])
        week_eur = float(parts[2])
        
        if week_stars < 1 or week_stars > 10000:
            await message.answer("Stars: введите число от 1 до 10000", reply_markup=back_keyboard())
            return
        
        if week_rub < 0 or week_eur < 0:
            await message.answer("RUB и EUR должны быть положительными числами", reply_markup=back_keyboard())
            return
        
        success = await update_prices(week_stars, week_rub, week_eur)
        
        if success:
            new_prices = await get_vpn_prices()
            text = f"""✅ <b>Цены обновлены!</b>

<b>Новая цена за неделю:</b>
💎 {new_prices['week']['stars']} Stars
₽ {new_prices['week']['rub']:.2f} RUB
€ {new_prices['week']['eur']:.2f} EUR

<b>Новая цена за месяц (неделя×3):</b>
💎 {new_prices['month']['stars']} Stars
₽ {new_prices['month']['rub']:.2f} RUB
€ {new_prices['month']['eur']:.2f} EUR

<b>Новая цена за безлимит (неделя×6):</b>
💎 {new_prices['unlimited']['stars']} Stars
₽ {new_prices['unlimited']['rub']:.2f} RUB
€ {new_prices['unlimited']['eur']:.2f} EUR"""
            
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_menu())
        else:
            await message.answer("❌ Ошибка обновления цен", reply_markup=admin_main_menu())
        
        await state.clear()
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка формата: {str(e)}\n\nИспользуйте формат: <code>Stars, RUB, EUR</code>\nПример: <code>50, 500.0, 5.0</code>", parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "🤖 Тест сервера")
async def admin_test_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, server_ip, status FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=admin_main_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет активных серверов", reply_markup=admin_main_menu()); return
    
    text = "🤖 <b>Выберите сервер для теста:</b>\n\n"
    for server_id, name, server_ip, status in servers:
        status_icon = "🟢" if status == "installed" else "🟡" if status == "pending" else "🔴"
        ip_display = server_ip if server_ip else "IP не установлен"
        text += f"<b>{name}</b> {status_icon}\nID: {server_id} | 🌐 {ip_display}\nСтатус: {status}\n\n"
    
    text += "Введите ID сервера для тестирования:"
    
    await state.set_state(TestServerStates.waiting_for_server)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(TestServerStates.waiting_for_server)
async def process_test_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu())
        return
    
    try: 
        server_id = int(message.text)
    except: 
        await message.answer("Введите числовой ID:", reply_markup=back_keyboard())
        return
    
    await message.answer(f"🔍 Тестирую сервер ID {server_id}...")
    
    try:
        ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
        
        if not ssh_ok:
            await message.answer(f"❌ SSH тест не пройден: {ssh_msg}", reply_markup=admin_main_menu())
            await state.clear()
            return
        
        # Тест базовых команд
        test_cmds = [
            ("uptime", "Время работы"),
            ("free -m | awk 'NR==2{printf \"RAM: %s/%sMB\", $3,$2}'", "Память"),
            ("df -h | awk '$NF==\"/\"{printf \"Диск: %s/%s\", $3,$2}'", "Диск"),
            ("top -bn1 | grep load | awk '{printf \"CPU: %.2f\", $(NF-2)}'", "Загрузка CPU"),
        ]
        
        results = [f"✅ <b>SSH подключение работает</b>",
                  f"👤 <b>Пользователь:</b> {system_info['user']}",
                  f"🌐 <b>Хост:</b> {system_info['host']}",
                  f"🔐 <b>Sudo доступ:</b> {'✅ Есть' if system_info['has_sudo'] else '❌ Нет'}",
                  f"🌐 <b>IP сервера:</b> {system_info.get('server_ip', 'Не определен')}"]
        
        for cmd, desc in test_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=False)
            if success and stdout:
                results.append(f"📊 <b>{desc}:</b> {stdout.strip()}")
        
        # Проверка XRay
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT xray_configured, xray_public_key FROM servers WHERE id = ?", (server_id,))
                server_info = await cursor.fetchone()
                
                if server_info:
                    xray_configured, xray_public_key = server_info
                    
                    results.append("\n🔧 <b>XRay Status:</b>")
                    results.append(f"🔐 XRay Reality: {'✅ Установлен' if xray_configured else '❌ Не установлен'}")
                    
                    if xray_configured:
                        check_result = await test_xray_connection(server_id)
                        status = "✅ Успешно" if check_result['success'] else f"❌ {check_result['message'][:100]}"
                        results.append(f"   Тест подключения: {status}")
                        
                        if xray_public_key:
                            results.append(f"   Публичный ключ: {xray_public_key[:20]}...")
                else:
                    results.append("\n⚠️ Не удалось получить информацию о XRay")
        except:
            results.append("\n⚠️ Не удалось проверить XRay")
        
        await message.answer("\n".join(results), parse_mode=ParseMode.HTML, reply_markup=admin_main_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка тестирования: {str(e)}", reply_markup=admin_main_menu())
    
    await state.clear()

@dp.message(F.text == "🔄 Продлить подписку")
async def admin_extend_subscription_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): 
        await user_extend_subscription_start(message, state)
        return
    
    await state.set_state(ExtendSubscriptionStates.waiting_for_user)
    await message.answer("Введите ID пользователя для продления подписки:", reply_markup=back_keyboard())

async def user_extend_subscription_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT subscription_end, is_active 
                FROM vpn_users 
                WHERE user_id = ? AND is_active = TRUE 
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
            
            if not user:
                await message.answer("❌ У вас нет активной подписки для продления.", reply_markup=user_main_menu())
                await state.clear()
                return
            
            subscription_end, is_active = user
            
            if not is_active:
                await message.answer("❌ Ваша подписка не активна.", reply_markup=user_main_menu())
                await state.clear()
                return
            
            if subscription_end:
                end_date = datetime.fromisoformat(subscription_end)
                days_left = max(0, (end_date - datetime.now()).days)
                
                text = f"📅 <b>Ваша текущая подписка</b>\n\n"
                text += f"Действует до: {end_date.strftime('%d.%m.%Y %H:%M')}\n"
                text += f"Осталось дней: {days_left}\n\n"
                text += f"<b>Выберите период для продления:</b>"
            else:
                text = "📅 <b>Ваша текущая подписка</b>\n\n"
                text += "Действует: бессрочно\n\n"
                text += "<b>Выберите период для продления:</b>"
            
            prices = await get_vpn_prices()
            text += f"\n\n💎 <b>Неделя (7 дней)</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}"
            text += f"\n💎 <b>Месяц (30 дней)</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}"
            text += f"\n♾️ <b>Безлимит</b> - {prices['unlimited']['stars']} Stars / ₽{prices['unlimited']['rub']:.2f} / €{prices['unlimited']['eur']:.2f}"
            
            await state.set_state(UserPaymentStates.waiting_for_period)
            await state.update_data(is_extension=True)
            await message.answer(text, reply_markup=extend_period_keyboard(), parse_mode=ParseMode.HTML)
            
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())
        await state.clear()

@dp.message(ExtendSubscriptionStates.waiting_for_user)
async def process_extend_user(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu())
        return
    
    try:
        user_id = int(message.text)
    except:
        await message.answer("Введите корректный числовой ID пользователя:", reply_markup=back_keyboard())
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT username FROM vpn_users WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
            user = await cursor.fetchone()
            
            if not user:
                await message.answer(f"❌ Пользователь ID {user_id} не найден.", reply_markup=back_keyboard())
                return
            
            username = user[0] or f"user_{user_id}"
    except:
        await message.answer("❌ Ошибка проверки пользователя", reply_markup=back_keyboard())
        return
    
    await state.update_data(user_id=user_id, username=username)
    await state.set_state(ExtendSubscriptionStates.waiting_for_period)
    
    prices = await get_vpn_prices()
    text = f"""🔄 <b>Продление подписки для пользователя:</b>

👤 ID: {user_id}
📛 Имя: {username}

📊 <b>Тарифы:</b>
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}
♾️ <b>Безлимит</b> - {prices['unlimited']['stars']} Stars / ₽{prices['unlimited']['rub']:.2f} / €{prices['unlimited']['eur']:.2f}

Выберите период продления:"""
    
    await message.answer(text, reply_markup=extend_period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(ExtendSubscriptionStates.waiting_for_period)
async def process_extend_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(ExtendSubscriptionStates.waiting_for_user)
        await message.answer("Введите ID пользователя для продления подписки:", reply_markup=back_keyboard())
        return
    
    period_map = {
        "💎 Неделя": 7,
        "💎 Месяц": 30,
        "♾️ Безлимит": 36500
    }
    
    if message.text not in period_map:
        await message.answer("Выберите период из списка:", reply_markup=extend_period_keyboard())
        return
    
    period_days = period_map[message.text]
    data = await state.get_data()
    user_id = data['user_id']
    username = data['username']
    
    success, result_text = await extend_subscription(user_id, period_days, admin_action=True)
    
    if success:
        try:
            await bot.send_message(
                user_id,
                f"🔄 <b>Ваша подписка VPN продлена!</b>\n\n"
                f"Администратор продлил вашу подписку на {period_days} дней.\n\n"
                f"{result_text}\n\n"
                f"🆘 Поддержка: {SUPPORT_USERNAME}",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
        
        await message.answer(
            f"✅ Подписка пользователя {username} (ID: {user_id}) успешно продлена!\n\n"
            f"{result_text}",
            reply_markup=admin_main_menu(),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            f"❌ Не удалось продлить подписку: {result_text}",
            reply_markup=admin_main_menu(),
            parse_mode=ParseMode.HTML
        )
    
    await state.clear()

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear()
    prices = await get_vpn_prices()
    text = f"""🔐 <b>Получить VPN доступ (XRay Reality)</b>

📊 <b>Тарифы:</b>
🎁 <b>3 дня бесплатно</b> - пробный период
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}
♾️ <b>Безлимит</b> - {prices['unlimited']['stars']} Stars / ₽{prices['unlimited']['rub']:.2f} / €{prices['unlimited']['eur']:.2f}

Выберите вариант:"""
    
    await state.set_state(UserPaymentStates.waiting_for_period)
    await message.answer(text, reply_markup=period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(UserPaymentStates.waiting_for_period)
async def process_user_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu())
        return
    
    data = await state.get_data()
    is_extension = data.get('is_extension', False)
    
    if message.text == "🎁 3 дня (пробный)":
        if is_extension:
            await message.answer("❌ Пробный период доступен только для новой подписки.", reply_markup=period_keyboard())
            return
            
        user_id = message.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if user and user[0]:
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
                await state.clear()
                return
        
        await state.update_data(period=3, is_trial=True, amount_stars=0, amount_rub=0, amount_eur=0)
        await state.set_state(UserPaymentStates.waiting_for_device)
        await message.answer("✅ Пробный период доступен!\n\n📱 Выберите тип вашего устройства:", reply_markup=device_type_keyboard())
        
    elif message.text in ["💎 Неделя", "💎 Месяц", "♾️ Безлимит"]:
        period_map = {
            "💎 Неделя": 7,
            "💎 Месяц": 30,
            "♾️ Безлимит": 36500
        }
        
        period = period_map[message.text]
        prices = await get_vpn_prices()
        
        if period == 7:
            price_key = "week"
        elif period == 30:
            price_key = "month"
        else:
            price_key = "unlimited"
        
        amount_stars = prices[price_key]['stars']
        amount_rub = prices[price_key]['rub']
        amount_eur = prices[price_key]['eur']
        
        await state.update_data(
            period=period, 
            is_trial=False,
            amount_stars=amount_stars,
            amount_rub=amount_rub,
            amount_eur=amount_eur
        )
        
        if is_extension:
            user_id = message.from_user.id
            success, result_text = await extend_subscription(user_id, period)
            
            if success:
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("""
                            INSERT INTO payments (user_id, amount_stars, amount_rub, amount_eur, period_days, status, created_at)
                            VALUES (?, ?, ?, ?, ?, 'pending_manual', CURRENT_TIMESTAMP)
                        """, (user_id, amount_stars, amount_rub, amount_eur, period))
                        await db.commit()
                except:
                    pass
                
                await message.answer(
                    f"✅ <b>Подписка продлена!</b>\n\n"
                    f"{result_text}\n\n"
                    f"💳 <b>Оплата:</b>\n"
                    f"💎 {amount_stars} Stars\n"
                    f"₽ {amount_rub:.2f} RUB\n"
                    f"€ {amount_eur:.2f} EUR\n\n"
                    f"Для оплаты обратитесь в поддержку: {SUPPORT_USERNAME}",
                    reply_markup=user_main_menu(),
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(f"❌ {result_text}", reply_markup=user_main_menu())
            
            await state.clear()
        else:
            await state.set_state(UserPaymentStates.waiting_for_payment)
            await message.answer("💳 Выберите способ оплаты:", reply_markup=payment_method_keyboard())
    
    else:
        await message.answer("Выберите вариант из списка:", reply_markup=period_keyboard())

@dp.message(UserPaymentStates.waiting_for_payment)
async def process_payment_method(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await get_vpn_start(message, state)
        return
    
    data = await state.get_data()
    period = data['period']
    amount_stars = data['amount_stars']
    
    if message.text == "💎 Stars (Telegram)":
        stars_amount = amount_stars
        
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN доступ на {period} дней",
                description=f"Доступ к XRay Reality серверу на {period} дней. Оплата Stars.",
                payload=f"vpn_stars_{period}days_{message.from_user.id}_{int(time.time())}",
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label=f"{period} дней VPN", amount=stars_amount)],
                start_parameter="vpn_subscription",
                need_email=False,
                need_phone_number=False,
                need_shipping_address=False,
                is_flexible=False,
                disable_notification=False,
                protect_content=False
            )
            await state.update_data(payment_method='stars')
            
        except Exception as e:
            await message.answer(f"❌ Ошибка создания счета: {str(e)}", reply_markup=user_main_menu())
            await state.clear()
    
    elif message.text == "💳 Карта (RUB/€)":
        amount_rub = data.get('amount_rub', 0)
        amount_eur = data.get('amount_eur', 0)
        
        await message.answer(
            f"💳 <b>Оплата картой (RUB/€)</b>\n\n"
            f"Для оплаты картой обратитесь в поддержку:\n{SUPPORT_PAYMENT}\n\n"
            f"<b>Сумма к оплате:</b>\n"
            f"₽ {amount_rub:.2f} RUB\n"
            f"€ {amount_eur:.2f} EUR\n\n"
            f"После оплаты вы получите доступ к VPN.",
            parse_mode=ParseMode.HTML,
            reply_markup=user_main_menu()
        )
        await state.clear()
    
    else:
        await message.answer("Выберите способ оплаты:", reply_markup=payment_method_keyboard())

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    
    if not data:
        await message.answer("❌ Ошибка: данные оплаты не найдены", reply_markup=user_main_menu())
        await state.clear()
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO payments (user_id, amount_stars, amount_rub, amount_eur, period_days, status, telegram_payment_id, created_at)
                VALUES (?, ?, ?, ?, ?, 'completed', ?, CURRENT_TIMESTAMP)
            """, (
                message.from_user.id,
                data.get('amount_stars', 0),
                data.get('amount_rub', 0),
                data.get('amount_eur', 0),
                data.get('period', 7),
                message.successful_payment.telegram_payment_charge_id
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка записи платежа: {e}")
    
    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Спасибо за оплату {data.get('period', 7)} дней VPN доступа.\n\n"
        f"📱 Теперь выберите тип вашего устройства:",
        parse_mode=ParseMode.HTML
    )
    
    await state.set_state(UserPaymentStates.waiting_for_device)
    await message.answer("📱 Выберите тип вашего устройства:", reply_markup=device_type_keyboard())

@dp.message(UserPaymentStates.waiting_for_device)
async def process_user_device(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await get_vpn_start(message, state)
        return
    
    device_map = {
        "📱 iPhone/Hiddify": "iphone",
        "🤖 Android/NG": "android",
        "💻 Другое": "auto"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите тип устройства из списка:", reply_markup=device_type_keyboard())
        return
    
    device_type = device_map[message.text]
    
    data = await state.get_data()
    period = data.get('period', 7)
    is_trial = data.get('is_trial', False)
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"
    
    # Находим доступный сервер
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, current_users, max_users 
                FROM servers 
                WHERE is_active = TRUE AND xray_configured = TRUE AND current_users < max_users
                ORDER BY current_users ASC LIMIT 1
            """)
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов. Попробуйте позже.", reply_markup=user_main_menu())
                await state.clear()
                return
            
            server_id, server_name, current_users, max_users = server
            
            # Создаем VPN клиента
            vpn_data, error = await create_xray_user(server_id, user_id, username, device_type)
            
            if error:
                await message.answer(f"❌ {error}", reply_markup=user_main_menu())
                await state.clear()
                return
            
            # Устанавливаем срок подписки
            subscription_end = (datetime.now() + timedelta(days=period)).isoformat()
            
            # Обновляем пользователя с данными подписки
            await db.execute("""
                UPDATE vpn_users 
                SET subscription_end = ?, trial_used = ?
                WHERE user_id = ? AND is_active = TRUE
                ORDER BY id DESC LIMIT 1
            """, (subscription_end, is_trial, user_id))
            
            await db.commit()
        
        # Отправляем данные пользователю
        await send_xray_config_to_user(user_id, vpn_data, message)
        
        await message.answer(
            f"✅ VPN доступ активирован!\n\n"
            f"📅 Срок действия: {period} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"🔧 Тип: XRay Reality\n"
            f"📱 Устройство: {device_type}\n"
            f"🔑 UUID: {vpn_data['vpn_uuid']}\n"
            f"🔐 Публичный ключ: {vpn_data['public_key'][:20]}...\n\n"
            f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}\n\n"
            f"🆘 Поддержка: {SUPPORT_USERNAME}",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())
    
    await state.clear()

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.subscription_end, v.is_active, v.vpn_uuid, v.device_type, s.name as server_name, s.server_ip, s.xray_public_key
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                WHERE v.user_id = ? AND v.is_active = TRUE 
                ORDER BY v.created_at DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active, vpn_uuid, device_type, server_name, server_ip, public_key = user
        
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
            
            device_icon = "📱" if device_type == "iphone" else "🤖" if device_type == "android" else "💻"
            
            text = f"📱 <b>Ваша подписка VPN (XRay Reality)</b>\n\n"
            text += f"<b>Статус:</b> {status}\n"
            text += f"<b>Действует до:</b> {end_date.strftime('%d.%m.%Y %H:%M')}\n"
            if server_name: text += f"<b>Сервер:</b> {server_name}\n"
            if server_ip: text += f"<b>IP сервера:</b> {server_ip}\n"
            text += f"<b>Тип VPN:</b> 🔐 XRay Reality\n"
            text += f"<b>Устройство:</b> {device_icon} {device_type}\n"
            text += f"<b>UUID:</b> <code>{vpn_uuid}</code>\n"
            if public_key: text += f"<b>Публичный ключ:</b> <code>{public_key}</code>\n"
            text += f"<b>Порт:</b> 443\n"
            text += f"<b>Псевдоним (SNI):</b> google.com\n"
            text += f"<b>Short ID:</b> aabbccdd\n"
            text += f"<b>Flow:</b> xtls-rprx-vision\n"
            
            # Генерируем ссылку
            if server_ip and vpn_uuid and public_key:
                vless_link = f"vless://{vpn_uuid}@{server_ip}:443?security=reality&sni=google.com&alpn=h2&fp=chrome&pbk={public_key}&sid=aabbccdd&type=tcp&flow=xtls-rprx-vision&encryption=none#{message.from_user.username or user_id}"
                text += f"\n<b>Готовая ссылка:</b>\n<code>{vless_link}</code>"
            
            if days_left < 3 and days_left > 0:
                text += f"\n\n⚠️ <b>Внимание!</b> Подписка истекает через {days_left} дней.\n"
            
            text += f"\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
            
            await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)
        else:
            await message.answer("📭 Нет информации о подписке", reply_markup=user_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "🌐 Серверы")
async def user_servers(message: Message):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT name, server_ip, xray_configured, current_users, max_users 
                FROM servers 
                WHERE is_active = TRUE 
                ORDER BY name
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}", reply_markup=user_main_menu()); return
    
    if not servers: 
        await message.answer("📭 Серверов нет в данный момент", reply_markup=user_main_menu()); return
    
    text = "🌐 <b>Доступные серверы (XRay Reality):</b>\n\n"
    for server in servers:
        name, server_ip, xray_configured, current, max_users = server
        load_percent = (current / max_users * 100) if max_users > 0 else 0
        
        if load_percent < 50: load_icon = "🟢"
        elif load_percent < 80: load_icon = "🟡"
        else: load_icon = "🔴"
        
        xray_status = "🔐 XRay" if xray_configured else "❌ Не готов"
        
        text += f"{load_icon} <b>{name}</b>\n"
        text += f"   🌐 {server_ip or 'IP обновляется'}\n"
        text += f"   🔧 {xray_status}\n"
        text += f"   👥 {current}/{max_users} ({load_percent:.0f}%)\n\n"
    
    text += f"🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    text = f"""🆘 <b>Помощь и поддержка (XRay Reality)</b>

<b>Что такое XRay Reality?</b>
• <b>Самый современный протокол</b> - обходит блокировки лучше WireGuard и Shadowsocks
• <b>Высокая скорость</b> - минимальные потери скорости
• <b>Простая настройка</b> - одна ссылка для всех приложений

<b>Как подключиться?</b>
1. Скачайте приложение:
   • <b>Android:</b> v2rayNG, Nekobox
   • <b>iOS:</b> Hiddify, Foxray (через TestFlight)
   • <b>Windows/Mac:</b> Nekoray, v2rayN

2. Скопируйте ссылку из бота
3. Вставьте в приложение
4. Включите VPN

<b>Частые вопросы:</b>
1. <b>Не работает подключение?</b> - Проверьте ссылку, перезапустите приложение
2. <b>Как продлить подписку?</b> - Используйте кнопку '🔄 Продлить подписку'
3. <b>Нет скорости?</b> - Попробуйте другой сервер из списка
4. <b>Проблемы с оплатой?</b> - Обратитесь в поддержку

<b>Контакты поддержки:</b>
{SUPPORT_USERNAME}

<b>Для оплаты картой:</b>
{SUPPORT_PAYMENT}

<b>Рекомендуемые приложения:</b>
• Android: <b>v2rayNG</b> (Play Market)
• iOS: <b>Hiddify</b> (TestFlight)
• Windows: <b>Nekoray</b> (GitHub)"""
    
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ПЕРИОДИЧЕСКИЕ ЗАДАЧИ ==========
async def periodic_tasks():
    """Периодические задачи бота"""
    while True:
        try:
            expired_count = await check_expired_subscriptions()
            if expired_count > 0:
                logger.info(f"Отключено {expired_count} истекших подписок")
            
            await asyncio.sleep(3600)
            
        except Exception as e:
            logger.error(f"Ошибка в периодических задачах: {e}")
            await asyncio.sleep(300)

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 ЗАПУСК VPN БОТА С XRAY REALITY")
    print("=" * 50)
    print(f"🔐 Протокол: XRay Reality (VLESS)")
    print(f"💳 Оплата: Stars, RUB, EUR")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Support: {SUPPORT_USERNAME}")
    
    if not await init_database():
        print("❌ Не удалось инициализировать базу данных!")
        return
    
    try:
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username} (ID: {me.id})")
        print(f"📝 Имя: {me.full_name}")
    except Exception as e:
        print(f"❌ Ошибка подключения к Telegram API: {e}")
        print(f"Проверьте BOT_TOKEN: {'установлен' if BOT_TOKEN else 'НЕ УСТАНОВЛЕН'}")
        return
    
    asyncio.create_task(periodic_tasks())
    
    print("=" * 50)
    print("✅ Бот готов к работе! Ожидаю сообщений...")
    print("=" * 50)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)