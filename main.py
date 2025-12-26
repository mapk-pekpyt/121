# vpn_bot_with_xray.py - VPN БОТ С ПОДДЕРЖКОЙ XRAY (VLESS+WS+TLS)
import os, asyncio, logging, sys, random, sqlite3, time, json, uuid, subprocess
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
    print("Запустите: BOT_TOKEN='ваш_токен' python vpn_bot_with_xray.py")
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

# ========== БАЗА ДАННЫХ (ОБНОВЛЕННАЯ) ==========
async def init_database():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Таблица серверов IKEv2/L2TP (старая - сохраняем)
            await db.execute("""CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                name TEXT NOT NULL UNIQUE, 
                ssh_key TEXT NOT NULL, 
                connection_string TEXT NOT NULL, 
                vpn_type TEXT DEFAULT 'ikev2', 
                max_users INTEGER DEFAULT 50, 
                current_users INTEGER DEFAULT 0, 
                is_active BOOLEAN DEFAULT TRUE, 
                server_ip TEXT, 
                ikev2_configured BOOLEAN DEFAULT FALSE, 
                l2tp_configured BOOLEAN DEFAULT FALSE,
                test_login TEXT,
                test_password TEXT,
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            
            # НОВАЯ: Таблица серверов Xray
            await db.execute("""CREATE TABLE IF NOT EXISTS xray_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                name TEXT NOT NULL UNIQUE, 
                ssh_key TEXT NOT NULL, 
                connection_string TEXT NOT NULL, 
                server_ip TEXT,
                uuid TEXT,  # UUID сервера
                private_key TEXT,  # Для Reality (опционально)
                public_key TEXT,   # Для Reality
                short_id TEXT,     # Для Reality
                ws_path TEXT DEFAULT '/ray',
                ws_host TEXT DEFAULT 'cloudflare.com',
                is_active BOOLEAN DEFAULT TRUE, 
                status TEXT DEFAULT 'pending',
                current_users INTEGER DEFAULT 0,
                max_users INTEGER DEFAULT 100,
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            
            # Таблица пользователей IKEv2/L2TP (старая)
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER NOT NULL, 
                username TEXT, 
                server_id INTEGER, 
                client_name TEXT, 
                vpn_login TEXT UNIQUE,
                vpn_password TEXT,
                vpn_type TEXT,
                device_type TEXT DEFAULT 'auto', 
                subscription_end TIMESTAMP, 
                trial_used BOOLEAN DEFAULT FALSE, 
                is_active BOOLEAN DEFAULT TRUE, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL)""")
            
            # НОВАЯ: Таблица пользователей Xray
            await db.execute("""CREATE TABLE IF NOT EXISTS xray_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER NOT NULL, 
                username TEXT, 
                server_id INTEGER, 
                uuid TEXT UNIQUE,  # UUID пользователя для VLESS
                subscription_end TIMESTAMP, 
                is_active BOOLEAN DEFAULT TRUE, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES xray_servers (id) ON DELETE SET NULL)""")
            
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
            
            await db.commit()
            logger.info("✅ База данных инициализирована (с поддержкой Xray)")
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ОБЩИЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (сохраняем) ==========
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
        
        if unlimited_stars is None: unlimited_stars = week_stars * 6
        if unlimited_rub is None: unlimited_rub = week_rub * 6
        if unlimited_eur is None: unlimited_eur = week_eur * 6
        
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

async def check_ssh_connection(server_id: int = None, conn_str: str = None, ssh_key: str = None, table: str = "servers"):
    """Проверка SSH подключения"""
    try:
        if server_id:
            async with aiosqlite.connect(DB_PATH) as db:
                if table == "servers":
                    cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
                else:  # xray_servers
                    cursor = await db.execute("SELECT connection_string, ssh_key FROM xray_servers WHERE id = ?", (server_id,))
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

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = True, table: str = "servers") -> Tuple[str, str, bool]:
    """Выполнение SSH команды"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if table == "servers":
                cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            else:
                cursor = await db.execute("SELECT connection_string, ssh_key FROM xray_servers WHERE id = ?", (server_id,))
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

# ========== XRAY ФУНКЦИИ (НОВЫЕ) ==========
async def setup_xray_vless_ws(server_id: int, message: Message):
    """Установка Xray с VLESS+WebSocket+TLS (рабочая конфигурация)"""
    await message.answer(f"🚀 Начинаю установку Xray (VLESS+WS+TLS)...")
    
    # 1. Проверка SSH
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id, table="xray_servers")
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}")
        return False
    
    if not system_info['has_sudo']:
        await message.answer("❌ Нет прав sudo. Установка невозможна.")
        return False
    
    try:
        # 2. Генерация UUID для сервера
        import uuid
        server_uuid = str(uuid.uuid4())
        
        # 3. Установка Xray
        install_commands = [
            "apt-get update -y",
            "apt-get install -y curl wget unzip openssl",
            "bash -c \"$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install"
        ]
        
        for cmd in install_commands:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, table="xray_servers")
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:200]}")
        
        # 4. Создание директории для сертификатов
        cert_commands = [
            "mkdir -p /usr/local/etc/xray/cert",
            f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /usr/local/etc/xray/cert/key.pem -out /usr/local/etc/xray/cert/cert.pem -subj \"/C=US/ST=California/L=San Francisco/O=MyVPN/CN=vpn.server.com\""
        ]
        
        for cmd in cert_commands:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=60, table="xray_servers")
            if not success:
                await message.answer(f"⚠️ Ошибка создания сертификата: {stderr[:200]}")
        
        # 5. Создание конфигурации Xray (рабочий конфиг)
        xray_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "port": 443,
                "protocol": "vless",
                "settings": {
                    "clients": [{
                        "id": server_uuid,
                        "flow": "xtls-rprx-vision"
                    }],
                    "decryption": "none"
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {
                        "certificates": [{
                            "certificateFile": "/usr/local/etc/xray/cert/cert.pem",
                            "keyFile": "/usr/local/etc/xray/cert/key.pem"
                        }]
                    },
                    "wsSettings": {
                        "path": "/ray",
                        "headers": {
                            "Host": "www.cloudflare.com"
                        }
                    }
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"]
                }
            }],
            "outbounds": [{"protocol": "freedom"}]
        }
        
        # Сохраняем конфиг на сервере
        config_json = json.dumps(xray_config, indent=2)
        config_cmd = f"cat > /usr/local/etc/xray/config.json << 'EOF'\n{config_json}\nEOF"
        
        stdout, stderr, success = await execute_ssh_command(server_id, config_cmd, table="xray_servers")
        if not success:
            await message.answer(f"❌ Ошибка создания конфига: {stderr}")
            return False
        
        # 6. Настройка прав и перезапуск
        setup_commands = [
            "chmod 600 /usr/local/etc/xray/cert/key.pem",
            "chmod 644 /usr/local/etc/xray/cert/cert.pem",
            "chown -R nobody:nogroup /usr/local/etc/xray/cert/",
            "systemctl restart xray",
            "systemctl enable xray",
            "sleep 2",
            "systemctl status xray --no-pager -l | head -10"
        ]
        
        for cmd in setup_commands:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, table="xray_servers")
        
        # 7. Проверка порта
        check_port = "ss -tlnp | grep ':443' || echo 'PORT_NOT_LISTENING'"
        stdout, stderr, success = await execute_ssh_command(server_id, check_port, use_sudo=False, table="xray_servers")
        
        if 'PORT_NOT_LISTENING' in stdout:
            await message.answer("❌ Xray не слушает порт 443")
            return False
        
        # 8. Обновление данных в БД
        server_ip = system_info.get('server_ip', '')
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE xray_servers 
                SET uuid = ?, server_ip = ?, status = 'installed', last_check = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (server_uuid, server_ip, server_id))
            await db.commit()
        
        # 9. Генерация ссылки для теста
        test_link = f"vless://{server_uuid}@{server_ip}:443?type=ws&security=tls&path=%2Fray&host=www.cloudflare.com&allowInsecure=true#Xray_Test"
        
        await message.answer(
            f"✅ <b>Xray успешно установлен и запущен!</b>\n\n"
            f"🌐 <b>IP сервера:</b> {server_ip}\n"
            f"🔐 <b>UUID сервера:</b> <code>{server_uuid}</code>\n"
            f"📡 <b>Протокол:</b> VLESS + WebSocket + TLS\n"
            f"🔧 <b>Путь WS:</b> <code>/ray</code>\n"
            f"🌍 <b>Host header:</b> <code>www.cloudflare.com</code>\n\n"
            f"<b>Тестовая ссылка:</b>\n<code>{test_link}</code>\n\n"
            f"<i>Используйте клиент V2Box на iOS или аналогичный для подключения.</i>",
            parse_mode=ParseMode.HTML
        )
        
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки Xray: {str(e)[:500]}")
        logger.error(f"Xray установка ошибка: {e}")
        return False

async def create_xray_user(server_id: int, user_id: int, username: str):
    """Создание пользователя Xray"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем данные сервера
            cursor = await db.execute("SELECT uuid, server_ip, current_users, max_users FROM xray_servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return None, "Сервер не найден"
            
            server_uuid, server_ip, current_users, max_users = server
            if current_users >= max_users:
                return None, "Сервер переполнен"
            
            # Генерируем UUID для пользователя
            import uuid
            user_uuid = str(uuid.uuid4())
            
            # Добавляем пользователя в конфиг Xray
            add_user_cmd = f"""
                cp /usr/local/etc/xray/config.json /usr/local/etc/xray/config.json.backup
                cat > /tmp/add_user.py << 'EOF'
import json
with open('/usr/local/etc/xray/config.json', 'r') as f:
    config = json.load(f)
config['inbounds'][0]['settings']['clients'].append({{
    "id": "{user_uuid}",
    "flow": "xtls-rprx-vision"
}})
with open('/usr/local/etc/xray/config.json', 'w') as f:
    json.dump(config, f, indent=2)
EOF
                python3 /tmp/add_user.py
                systemctl restart xray
            """
            
            stdout, stderr, success = await execute_ssh_command(server_id, add_user_cmd, table="xray_servers")
            if not success:
                return None, f"Ошибка добавления пользователя: {stderr}"
            
            # Обновляем счетчик пользователей
            await db.execute("UPDATE xray_servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            
            # Сохраняем пользователя в БД
            await db.execute("""
                INSERT INTO xray_users (user_id, username, server_id, uuid, is_active)
                VALUES (?, ?, ?, ?, TRUE)
            """, (user_id, username, server_id, user_uuid))
            
            await db.commit()
            
            # Формируем ссылку для пользователя
            user_link = f"vless://{user_uuid}@{server_ip}:443?type=ws&security=tls&path=%2Fray&host=www.cloudflare.com&allowInsecure=true#VPN_User"
            
            return {
                'uuid': user_uuid,
                'server_ip': server_ip,
                'link': user_link,
                'instructions': get_xray_instructions(server_ip, user_uuid)
            }, None
            
    except Exception as e:
        return None, f"Ошибка создания пользователя Xray: {str(e)}"

def get_xray_instructions(server_ip: str, user_uuid: str) -> str:
    """Инструкции для подключения к Xray"""
    return f"""🔧 <b>Инструкция для подключения к Xray (VLESS+WS+TLS):</b>

<b>Для iOS (V2Box):</b>
1. Установите V2Box из AppStore
2. Импортируйте ссылку:
<code>vless://{user_uuid}@{server_ip}:443?type=ws&security=tls&path=%2Fray&host=www.cloudflare.com&allowInsecure=true</code>
3. В настройках профиля включите:
   • <b>Allow Insecure</b> = ВКЛ
   • <b>TLS</b> = ВКЛ
   • <b>WebSocket</b> = ВКЛ
   • <b>Path</b> = <code>/ray</code>
   • <b>Host</b> = <code>www.cloudflare.com</code>
4. Подключитесь

<b>Для Android (v2rayNG):</b>
1. Скачайте v2rayNG
2. Нажмите ➕ → "Импорт из буфера обмена"
3. Вставьте ту же ссылку
4. Подключитесь

<b>Важные параметры:</b>
• Сервер: <code>{server_ip}</code>
• Порт: <code>443</code>
• UUID: <code>{user_uuid}</code>
• Тип: VLESS
• Transport: WebSocket
• TLS: Включен
• Path: <code>/ray</code>
• Host: <code>www.cloudflare.com</code>
• Allow Insecure: Да (галочка)

⚠️ <b>Ссылка содержит все настройки, просто импортируйте её!</b>"""

async def check_xray_status(server_id: int):
    """Проверка статуса Xray сервера"""
    try:
        # Проверяем, работает ли Xray
        check_commands = [
            "systemctl status xray --no-pager -l | head -5",
            "ss -tlnp | grep ':443' | grep xray || echo 'NOT_LISTENING'",
            "curl -sI https://localhost:443 --insecure --connect-timeout 3 || echo 'CURL_FAILED'"
        ]
        
        results = []
        for cmd in check_commands:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, table="xray_servers")
            results.append(stdout)
        
        # Проверяем наличие ошибок
        if 'NOT_LISTENING' in results[1]:
            return {"status": "error", "message": "Xray не слушает порт 443"}
        elif 'active (running)' not in results[0]:
            return {"status": "error", "message": "Служба Xray не запущена"}
        else:
            return {"status": "ok", "message": "Xray работает нормально"}
            
    except Exception as e:
        return {"status": "error", "message": f"Ошибка проверки: {str(e)}"}

# ========== КЛАВИАТУРЫ (ОБНОВЛЕННЫЕ) ==========
def user_main_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔐 Получить VPN")],
            [types.KeyboardButton(text="🚀 Получить Xray (новый)")],
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
            [types.KeyboardButton(text="🚀 Xray Серверы")],
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
            [types.KeyboardButton(text="🔧 Установить VPN")],
            [types.KeyboardButton(text="🔄 Перепроверить VPN")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def xray_servers_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📋 Список Xray серверов")],
            [types.KeyboardButton(text="➕ Добавить Xray сервер")],
            [types.KeyboardButton(text="⚡ Установить Xray")],
            [types.KeyboardButton(text="🔄 Проверить Xray")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

# ========== FSM СОСТОЯНИЯ (ДОБАВЛЯЕМ ДЛЯ XRAY) ==========
class AddXrayServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class InstallXrayStates(StatesGroup):
    waiting_for_server = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ (ОБНОВЛЕННЫЕ) ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer(f"🚀 Добро пожаловать в VPN Hosting!\n\n<b>Доступны:</b>\n🔐 IKEv2/L2TP (стабильный)\n🚀 Xray (маскировка трафика)\n\n💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Карта (RUB/€)\n\n🆘 Поддержка: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ XRAY ДЛЯ АДМИНА ==========
@dp.message(F.text == "🚀 Xray Серверы")
async def admin_xray_servers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    await message.answer("🚀 Управление Xray серверами", reply_markup=xray_servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список Xray серверов")
async def admin_list_xray_servers(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active, status, current_users, max_users, server_ip FROM xray_servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}"); return
    if not servers: 
        await message.answer("📭 Xray серверов нет", reply_markup=xray_servers_menu()); return
    
    text = "📋 <b>Список Xray серверов:</b>\n\n"
    for server in servers:
        server_id, name, active, status, current, max_users, server_ip = server
        status_icon = "🟢" if status == "installed" else "🟡" if status == "pending" else "🔴"
        active_icon = "✅" if active else "❌"
        load = f"{current}/{max_users}"
        ip_display = server_ip if server_ip else "N/A"
        text += f"{status_icon}{active_icon} <b>{name}</b>\nID: {server_id} | 👥 {load} | 🌐 {ip_display}\nСтатус: {status}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=xray_servers_menu())

@dp.message(F.text == "➕ Добавить Xray сервер")
async def admin_add_xray_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AddXrayServerStates.waiting_for_name)
    await message.answer("Введите имя Xray сервера:", reply_markup=back_keyboard())

@dp.message(AddXrayServerStates.waiting_for_name)
async def process_xray_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🚀 Управление Xray серверами", reply_markup=xray_servers_menu())
        return
    
    await state.update_data(server_name=message.text)
    await state.set_state(AddXrayServerStates.waiting_for_key)
    await message.answer("📎 Пришлите файл с SSH ключом (.key, .pem, .txt):", reply_markup=back_keyboard())

@dp.message(AddXrayServerStates.waiting_for_key, F.document)
async def process_xray_ssh_key_file(message: Message, state: FSMContext):
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
        await state.set_state(AddXrayServerStates.waiting_for_connection)
        await message.answer("✅ Файл загружен! Введите строку подключения (user@host:port):", reply_markup=back_keyboard())
        
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=back_keyboard())

@dp.message(AddXrayServerStates.waiting_for_connection)
async def process_xray_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(AddXrayServerStates.waiting_for_key)
        await message.answer("📎 Пришлите файл с SSH ключом:", reply_markup=back_keyboard())
        return
    
    data = await state.get_data()
    if 'ssh_key' not in data:
        await message.answer("❌ SSH ключ не найден", reply_markup=xray_servers_menu())
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
                "INSERT INTO xray_servers (name, ssh_key, connection_string, server_ip, status) VALUES (?, ?, ?, ?, 'pending')",
                (data['server_name'], data['ssh_key'], conn_str, server_ip)
            )
            server_id = cursor.lastrowid
            await db.commit()
        
        await message.answer(
            f"✅ Xray сервер '{data['server_name']}' добавлен!\n"
            f"ID: {server_id}\n"
            f"IP: {server_ip}\n\n"
            f"Теперь установите Xray через меню '⚡ Установить Xray'",
            reply_markup=admin_main_menu()
        )
        await state.clear()
        
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "⚡ Установить Xray")
async def admin_install_xray_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, status FROM xray_servers WHERE is_active = TRUE AND status != 'installed' LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=xray_servers_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет серверов для установки Xray", reply_markup=xray_servers_menu()); return
    
    text = "⚡ <b>Выберите сервер для установки Xray:</b>\n"
    for server_id, name, status in servers: 
        status_icon = "🟡" if status == "pending" else "🔴"
        text += f"ID: {server_id} - {name} {status_icon}\n"
    text += "\nВведите ID сервера:"
    
    await state.set_state(InstallXrayStates.waiting_for_server)
    await message.answer(text, reply_markup=back_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(InstallXrayStates.waiting_for_server)
async def process_install_xray_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🚀 Управление Xray серверами", reply_markup=xray_servers_menu())
        return
    
    try: 
        server_id = int(message.text)
    except: 
        await message.answer("Введите числовой ID:", reply_markup=back_keyboard())
        return
    
    success = await setup_xray_vless_ws(server_id, message)
    await state.clear()
    
    if success: 
        await message.answer(f"✅ Xray успешно установлен на сервер ID: {server_id}!", reply_markup=admin_main_menu())
    else: 
        await message.answer(f"⚠️ Xray установлен на сервер ID: {server_id} с проблемами", reply_markup=admin_main_menu())

@dp.message(F.text == "🔄 Проверить Xray")
async def admin_check_xray(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, server_ip, status FROM xray_servers WHERE is_active = TRUE AND status = 'installed' LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=xray_servers_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет установленных Xray серверов", reply_markup=xray_servers_menu()); return
    
    text = "🔄 <b>Проверка Xray серверов:</b>\n\n"
    
    for server_id, name, server_ip, status in servers:
        check_result = await check_xray_status(server_id)
        status_icon = "🟢" if check_result['status'] == "ok" else "🔴"
        text += f"{status_icon} <b>{name}</b> (ID: {server_id})\n"
        text += f"IP: {server_ip}\n"
        text += f"Статус: {check_result['message']}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=xray_servers_menu())

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ (XRAY) ==========
@dp.message(F.text == "🚀 Получить Xray (новый)")
async def get_xray_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Проверяем доступные серверы
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, current_users, max_users FROM xray_servers WHERE is_active = TRUE AND status = 'installed' AND current_users < max_users LIMIT 1")
            server = await cursor.fetchone()
    except Exception as e:
        await message.answer(f"❌ Ошибка проверки серверов: {e}", reply_markup=user_main_menu())
        return
    
    if not server:
        await message.answer("❌ Нет доступных Xray серверов. Попробуйте позже.", reply_markup=user_main_menu())
        return
    
    prices = await get_vpn_prices()
    text = f"""🚀 <b>Получить Xray VPN (маскировка трафика)</b>

<b>Преимущества:</b>
• Маскировка под обычный HTTPS трафик
• Обход блокировок через WebSocket
• Высокая скорость и стабильность
• Поддержка iOS (V2Box) и Android

📊 <b>Тарифы:</b>
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}
♾️ <b>Безлимит</b> - {prices['unlimited']['stars']} Stars / ₽{prices['unlimited']['rub']:.2f} / €{prices['unlimited']['eur']:.2f}

Выберите вариант:"""
    
    await state.set_state(UserPaymentStates.waiting_for_period)
    await state.update_data(vpn_type="xray")  # Отмечаем что это Xray
    await message.answer(text, reply_markup=period_keyboard(), parse_mode=ParseMode.HTML)

# ========== ПЕРИОДИЧЕСКИЕ ЗАДАЧИ ==========
async def periodic_tasks():
    """Периодические задачи бота"""
    while True:
        try:
            # Проверяем истекшие подписки (старые VPN)
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT v.id, v.user_id, v.server_id, v.vpn_login, v.vpn_type 
                    FROM vpn_users v 
                    WHERE v.is_active = TRUE 
                    AND v.subscription_end IS NOT NULL 
                    AND datetime(v.subscription_end) < datetime('now')
                """)
                expired_users = await cursor.fetchall()
                
                for user in expired_users:
                    user_id, tg_user_id, server_id, vpn_login, vpn_type = user
                    
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
                
                # Проверяем истекшие подписки Xray
                cursor = await db.execute("""
                    SELECT x.id, x.user_id, x.server_id, x.uuid 
                    FROM xray_users x 
                    WHERE x.is_active = TRUE 
                    AND x.subscription_end IS NOT NULL 
                    AND datetime(x.subscription_end) < datetime('now')
                """)
                expired_xray_users = await cursor.fetchall()
                
                for user in expired_xray_users:
                    user_id, tg_user_id, server_id, user_uuid = user
                    
                    # Отключаем пользователя в БД
                    await db.execute("UPDATE xray_users SET is_active = FALSE WHERE id = ?", (user_id,))
                    
                    # Отправляем уведомление
                    try:
                        await bot.send_message(
                            tg_user_id,
                            "⚠️ <b>Ваша подписка Xray VPN истекла!</b>\n\n"
                            "Для продолжения использования VPN приобретите новую подписку через кнопку '🚀 Получить Xray'.\n\n"
                            f"🆘 Поддержка: {SUPPORT_USERNAME}",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                await db.commit()
            
            await asyncio.sleep(3600)  # 1 час
            
        except Exception as e:
            logger.error(f"Ошибка в периодических задачах: {e}")
            await asyncio.sleep(300)

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 ЗАПУСК VPN БОТА С ПОДДЕРЖКОЙ XRAY")
    print("=" * 50)
    print(f"🔐 Поддержка: IKEv2, L2TP, Xray (VLESS+WS+TLS)")
    print(f"💳 Оплата: Stars, RUB, EUR")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Support: {SUPPORT_USERNAME}")
    
    # Инициализация БД
    if not await init_database():
        print("❌ Не удалось инициализировать базу данных!")
        return
    
    try:
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username} (ID: {me.id})")
        print(f"📝 Имя: {me.full_name}")
    except Exception as e:
        print(f"❌ Ошибка подключения к Telegram API: {e}")
        return
    
    # Запускаем периодические задачи
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