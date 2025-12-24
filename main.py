# vpn_bot_complete.py - VPN БОТ (ПОЛНАЯ ВЕРСИЯ)
import os, asyncio, logging, sys, random, sqlite3, time, json
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
    print("Запустите: BOT_TOKEN='ваш_токен' python vpn_bot_complete.py")
    sys.exit(1)

SUPPORT_USERNAME = "@vpnhostik"
SUPPORT_PAYMENT = "@юзподдержи"  # Для помощи с оплатой
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"  # Ваш токен

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
                vpn_type TEXT DEFAULT 'ikev2', 
                max_users INTEGER DEFAULT 50, 
                current_users INTEGER DEFAULT 0, 
                is_active BOOLEAN DEFAULT TRUE, 
                server_ip TEXT, 
                ikev2_configured BOOLEAN DEFAULT FALSE, 
                l2tp_configured BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            
            # Таблица пользователей
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER NOT NULL, 
                username TEXT, 
                server_id INTEGER, 
                client_name TEXT, 
                vpn_login TEXT,
                vpn_password TEXT,
                vpn_type TEXT,
                device_type TEXT DEFAULT 'auto', 
                subscription_end TIMESTAMP, 
                trial_used BOOLEAN DEFAULT FALSE, 
                is_active BOOLEAN DEFAULT TRUE, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
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
                month_eur REAL DEFAULT 15.0)""")
            
            # Начальные цены
            await db.execute("INSERT OR IGNORE INTO prices (id, week_stars, week_rub, week_eur, month_stars, month_rub, month_eur) VALUES (1, 50, 500.0, 5.0, 150, 1500.0, 15.0)")
            await db.commit()
            logger.info("✅ База данных инициализирована")
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
            cursor = await db.execute("SELECT week_stars, week_rub, week_eur, month_stars, month_rub, month_eur FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices: 
                return {
                    "week": {"days": 7, "stars": prices[0], "rub": prices[1], "eur": prices[2]},
                    "month": {"days": 30, "stars": prices[3], "rub": prices[4], "eur": prices[5]}
                }
    except Exception as e:
        logger.error(f"Ошибка получения цен: {e}")
    
    return {
        "week": {"days": 7, "stars": 50, "rub": 500.0, "eur": 5.0},
        "month": {"days": 30, "stars": 150, "rub": 1500.0, "eur": 15.0}
    }

async def update_prices(week_stars: int, week_rub: float, week_eur: float):
    """Обновление цен"""
    try:
        month_stars = week_stars * 3
        month_rub = week_rub * 3
        month_eur = week_eur * 3
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE prices SET 
                week_stars = ?, week_rub = ?, week_eur = ?,
                month_stars = ?, month_rub = ?, month_eur = ?
                WHERE id = 1
            """, (week_stars, week_rub, week_eur, month_stars, month_rub, month_eur))
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
                
                system_info = {
                    'has_sudo': has_sudo,
                    'os_info': os_info.stdout,
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

# ========== VPN УСТАНОВКИ ==========
async def setup_ikev2_l2tp_auto(server_id: int, vpn_type: str, message: Message):
    """Автоустановка IKEv2/L2TP"""
    await message.answer(f"🚀 Начинаю установку {vpn_type.upper()}...")
    
    ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
    if not ssh_ok:
        await message.answer(f"❌ {ssh_msg}")
        return False
    
    if not system_info['has_sudo']:
        await message.answer("❌ Нет прав sudo. Установка невозможна.")
        return False
    
    try:
        os_lower = system_info['os_info'].lower()
        
        if 'ubuntu' in os_lower or 'debian' in os_lower:
            cmds = [
                "apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y strongswan strongswan-pki libcharon-extra-plugins xl2tpd ppp",
                "ipsec stop 2>/dev/null || true"
            ]
        elif 'centos' in os_lower or 'redhat' in os_lower or 'oracle' in os_lower:
            cmds = [
                "yum install -y epel-release 2>/dev/null || true",
                "yum install -y strongswan strongswan-pki xl2tpd ppp 2>/dev/null || dnf install -y strongswan strongswan-pki xl2tpd ppp 2>/dev/null || true",
                "systemctl stop strongswan 2>/dev/null || true"
            ]
        else:
            await message.answer("❌ Неподдерживаемая ОС")
            return False
        
        for cmd in cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=180, use_sudo=True)
            if not success:
                await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
        
        # Конфигурация IKEv2
        ikev2_conf = """config setup
    charondebug="ike 1, knl 1, cfg 0"
    uniqueids=no

conn ikev2-vpn
    auto=add
    compress=no
    type=tunnel
    keyexchange=ikev2
    fragmentation=yes
    forceencaps=yes
    dpdaction=clear
    dpddelay=300s
    rekey=no
    left=%any
    leftid=@vpn-server
    leftcert=server-cert.pem
    leftsendcert=always
    leftsubnet=0.0.0.0/0
    right=%any
    rightid=%any
    rightauth=eap-mschapv2
    rightsourceip=10.10.10.0/24
    rightdns=8.8.8.8,8.8.4.4
    rightsendcert=never
    eap_identity=%identity"""
        
        # Конфигурация L2TP
        l2tp_conf = """[global]
    ipsec saref = yes
    listen-addr = 0.0.0.0
    [lns default]
    ip range = 10.10.20.2-10.10.20.254
    local ip = 10.10.20.1
    require chap = yes
    refuse pap = yes
    require authentication = yes
    name = l2tpd
    ppp debug = yes
    pppoptfile = /etc/ppp/options.xl2tpd
    length bit = yes"""
        
        ppp_options = """ipcp-accept-local
ipcp-accept-remote
ms-dns 8.8.8.8
ms-dns 8.8.4.4
noccp
auth
crtscts
idle 1800
mtu 1410
mru 1410
nodefaultroute
debug
lock
proxyarp
connect-delay 5000"""
        
        config_cmds = [
            "mkdir -p /etc/ipsec.d/private /etc/ipsec.d/certs",
            "chmod 700 /etc/ipsec.d/private",
            f"cat > /etc/ipsec.conf << 'EOF'\n{ikev2_conf}\nEOF",
            "echo ': PSK \"vpnsharedkey\"' > /etc/ipsec.secrets",
            f"cat > /etc/xl2tpd/xl2tpd.conf << 'EOF'\n{l2tp_conf}\nEOF",
            f"cat > /etc/ppp/options.xl2tpd << 'EOF'\n{ppp_options}\nEOF",
            "echo 'vpnuser * vpnpassword123 *' > /etc/ppp/chap-secrets",
            "sysctl -w net.ipv4.ip_forward=1",
            "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf",
            "sysctl -p"
        ]
        
        for cmd in config_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        startup_cmds = [
            "systemctl enable strongswan 2>/dev/null || systemctl enable ipsec 2>/dev/null || true",
            "systemctl enable xl2tpd 2>/dev/null || true",
            "systemctl start strongswan 2>/dev/null || systemctl start ipsec 2>/dev/null || true",
            "systemctl start xl2tpd 2>/dev/null || true"
        ]
        
        for cmd in startup_cmds:
            await execute_ssh_command(server_id, cmd, use_sudo=True)
        
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}' || echo '0.0.0.0'")
        server_ip = stdout.strip() if success else ""
        
        async with aiosqlite.connect(DB_PATH) as db:
            if vpn_type == "ikev2":
                await db.execute("UPDATE servers SET ikev2_configured = TRUE, server_ip = ? WHERE id = ?", (server_ip, server_id))
            elif vpn_type == "l2tp":
                await db.execute("UPDATE servers SET l2tp_configured = TRUE, server_ip = ? WHERE id = ?", (server_ip, server_id))
            await db.commit()
        
        await message.answer(f"✅ {vpn_type.upper()} успешно установлен!\n🌐 IP: {server_ip}\n🔑 Общий ключ (PSK): vpnsharedkey")
        return True
        
    except Exception as e:
        await message.answer(f"❌ Ошибка установки: {str(e)}")
        return False

async def create_vpn_client(server_id: int, user_id: int, username: str, vpn_type: str, device_type: str = "auto"):
    """Создание клиентской конфигурации"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT server_ip, current_users, max_users FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: return None, "Сервер не найден"
            
            server_ip, current_users, max_users = server
            if current_users >= max_users:
                return None, "Сервер переполнен"
            
            # Генерируем уникальные логин/пароль
            client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
            vpn_login = f"user{random.randint(10000, 99999)}"
            vpn_password = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=12))
            
            # Добавляем пользователя на сервер
            if vpn_type == "ikev2":
                add_user_cmd = f"echo '{vpn_login} : EAP \"{vpn_password}\"' >> /etc/ipsec.secrets"
                await execute_ssh_command(server_id, add_user_cmd, use_sudo=True)
                restart_cmd = "ipsec restart 2>/dev/null || systemctl restart strongswan 2>/dev/null || true"
                await execute_ssh_command(server_id, restart_cmd, use_sudo=True)
            elif vpn_type == "l2tp":
                add_user_cmd = f"echo '{vpn_login} * {vpn_password} *' >> /etc/ppp/chap-secrets"
                await execute_ssh_command(server_id, add_user_cmd, use_sudo=True)
                restart_cmd = "systemctl restart xl2tpd 2>/dev/null || true"
                await execute_ssh_command(server_id, restart_cmd, use_sudo=True)
            
            # Обновляем счетчик пользователей
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            
            # Сохраняем пользователя в БД
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, vpn_login, vpn_password, vpn_type, device_type, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username, server_id, client_name, vpn_login, vpn_password, vpn_type, device_type))
            
            await db.commit()
            
            return {
                'client_name': client_name,
                'server_ip': server_ip,
                'vpn_login': vpn_login,
                'vpn_password': vpn_password,
                'vpn_type': vpn_type,
                'device_type': device_type,
                'instructions': get_vpn_instructions(vpn_type, device_type, server_ip, vpn_login, vpn_password)
            }, None
            
    except Exception as e:
        return None, f"Ошибка создания клиента: {str(e)}"

def get_vpn_instructions(vpn_type: str, device_type: str, server_ip: str, login: str, password: str) -> str:
    """Получение инструкций по типу VPN"""
    
    if vpn_type == "ikev2":
        if device_type == "iphone":
            return f"""📱 <b>Инструкция для iPhone/iOS (IKEv2):</b>

1. <b>Настройки</b> → <b>Основные</b> → <b>VPN</b>
2. Нажмите <b>"Добавить конфигурацию VPN..."</b>
3. Заполните поля:
   • Тип: <b>IKEv2</b>
   • Описание: <b>VPN Сервер</b>
   • Сервер: <b>{server_ip}</b>
   • Удаленный ID: <b>{server_ip}</b>
   • Локальный ID: оставить пустым
4. <b>Аутентификация</b>:
   • Имя пользователя: <b>{login}</b>
   • Пароль: <b>{password}</b>
5. Нажмите <b>"Готово"</b>
6. Вернитесь и активируйте переключатель VPN"""
        
        elif device_type == "android":
            return f"""📱 <b>Инструкция для Android (IKEv2):</b>

1. <b>Настройки</b> → <b>Сеть и интернет</b> → <b>VPN</b>
2. Нажмите <b>"+"</b> или <b>"Добавить VPN"</b>
3. Заполните поля:
   • Имя: <b>VPN Сервер</b>
   • Тип: <b>IPSec Xauth PSK</b>
   • Адрес сервера: <b>{server_ip}</b>
   • IPSec identifier: <b>{server_ip}</b>
   • IPSec pre-shared key: <b>vpnsharedkey</b>
4. <b>Аутентификация</b>:
   • Имя пользователя: <b>{login}</b>
   • Пароль: <b>{password}</b>
5. Нажмите <b>"Сохранить"</b>
6. Нажмите на созданный профиль и <b>"Подключиться"</b>"""
        
        else:
            return f"""💻 <b>Универсальная инструкция (IKEv2):</b>

<b>Общие параметры:</b>
• Сервер: <b>{server_ip}</b>
• Тип VPN: <b>IPSec/IKEv2</b>
• Логин: <b>{login}</b>
• Пароль: <b>{password}</b>
• Общий ключ (PSK): <b>vpnsharedkey</b>"""
    
    elif vpn_type == "l2tp":
        if device_type == "iphone":
            return f"""📱 <b>Инструкция для iPhone/iOS (L2TP):</b>

1. <b>Настройки</b> → <b>Основные</b> → <b>VPN</b>
2. Нажмите <b>"Добавить конфигурацию VPN..."</b>
3. Заполните поля:
   • Тип: <b>L2TP</b>
   • Описание: <b>VPN Сервер</b>
   • Сервер: <b>{server_ip}</b>
   • Учетная запись: <b>{login}</b>
   • Общий ключ: <b>vpnsharedkey</b>
4. Нажмите <b>Готово</b>
5. Вернитесь, нажмите на созданную конфигурацию
6. Введите пароль: <b>{password}</b>
7. Активируйте переключатель VPN"""
        
        elif device_type == "android":
            return f"""📱 <b>Инструкция для Android (L2TP):</b>

1. <b>Настройки</b> → <b>Сеть и интернет</b> → <b>VPN</b>
2. Нажмите <b>"+"</b> или <b>"Добавить VPN"</b>
3. Заполните поля:
   • Имя: <b>VPN Сервер</b>
   • Тип: <b>L2TP/IPSec PSK</b>
   • Адрес сервера: <b>{server_ip}</b>
   • IPSec pre-shared key: <b>vpnsharedkey</b>
4. Нажмите <b>"Сохранить"</b>
5. Нажмите на созданный профиль
6. Введите:
   • Логин: <b>{login}</b>
   • Пароль: <b>{password}</b>
7. Нажмите <b>"Подключиться"</b>"""
        
        else:
            return f"""💻 <b>Универсальная инструкция (L2TP):</b>

<b>Общие параметры:</b>
• Сервер: <b>{server_ip}</b>
• Тип VPN: <b>L2TP/IPSec</b>
• Логин: <b>{login}</b>
• Пароль: <b>{password}</b>
• Общий ключ (PSK): <b>vpnsharedkey</b>"""
    
    return "Инструкция не найдена"

async def send_vpn_config_to_user(user_id: int, vpn_data: dict, message: Message):
    """Отправка конфига пользователю"""
    try:
        instructions = f"""🔧 <b>Ваши данные для подключения:</b>

🌐 <b>Сервер:</b> {vpn_data['server_ip']}
👤 <b>Логин:</b> {vpn_data['vpn_login']}
🔑 <b>Пароль:</b> {vpn_data['vpn_password']}
🔐 <b>Тип:</b> {vpn_data['vpn_type'].upper()}
📱 <b>Устройство:</b> {vpn_data['device_type']}

<b>Общий ключ (PSK) для L2TP/IPSec:</b> <code>vpnsharedkey</code>

{vpn_data['instructions']}

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
            [types.KeyboardButton(text="🤖 Тест сервера")]
        ],
        resize_keyboard=True
    )

def servers_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📋 Список серверов")],
            [types.KeyboardButton(text="➕ Добавить сервер")],
            [types.KeyboardButton(text="🔧 Установить VPN")],
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
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def vpn_type_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="IKEv2")],
            [types.KeyboardButton(text="L2TP")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def device_type_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📱 iPhone/iOS")],
            [types.KeyboardButton(text="🤖 Android")],
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
            [types.KeyboardButton(text="🔧 Установить VPN")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

def install_vpn_menu():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="IKEv2")],
            [types.KeyboardButton(text="L2TP")],
            [types.KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )

# ========== FSM СОСТОЯНИЯ ==========
class AddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_key = State()
    waiting_for_connection = State()
    waiting_for_max_users = State()

class InstallVPNStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_type = State()

class PriceStates(StatesGroup):
    waiting_for_prices = State()

class TestServerStates(StatesGroup):
    waiting_for_server = State()

class UserPaymentStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_payment = State()
    waiting_for_vpn_type = State()
    waiting_for_device = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): 
        await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: 
        await message.answer(f"🚀 Добро пожаловать в VPN Hosting!\n\n💳 <b>Способы оплаты:</b>\n• Telegram Stars\n• Карта (RUB/€)\n\n🆘 Поддержка: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

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
            cursor = await db.execute("SELECT id, name, is_active, ikev2_configured, l2tp_configured, current_users, max_users, server_ip FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}"); return
    if not servers: 
        await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
    
    text = "📋 Список серверов:\n\n"
    for server in servers:
        server_id, name, active, ikev2, l2tp, current, max_users, server_ip = server
        status = "🟢" if active else "🔴"
        ikev2_status = "🔐" if ikev2 else "❌"
        l2tp_status = "🅾️" if l2tp else "❌"
        load = f"{current}/{max_users}"
        ip_display = server_ip if server_ip else "N/A"
        text += f"{status}{ikev2_status}{l2tp_status} <b>{name}</b>\nID: {server_id} | 👥 {load} | 🌐 {ip_display}\n"
    
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
    await state.set_state(AddServerStates.waiting_for_type)
    await message.answer("Выберите тип VPN для этого сервера:", reply_markup=vpn_type_keyboard())

@dp.message(AddServerStates.waiting_for_type)
async def process_server_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🖥️ Управление серверами", reply_markup=servers_menu())
        return
    
    if message.text not in ["IKEv2", "L2TP"]:
        await message.answer("Выберите тип из списка:", reply_markup=vpn_type_keyboard())
        return
    
    await state.update_data(vpn_type=message.text.lower())
    await state.set_state(AddServerStates.waiting_for_max_users)
    await message.answer("Введите максимальное количество пользователей:", reply_markup=back_keyboard())

@dp.message(AddServerStates.waiting_for_max_users)
async def process_max_users(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(AddServerStates.waiting_for_type)
        await message.answer("Выберите тип VPN для этого сервера:", reply_markup=vpn_type_keyboard())
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
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string, vpn_type, max_users) VALUES (?, ?, ?, ?, ?)",
                (data['server_name'], data['ssh_key'], conn_str, data.get('vpn_type', 'ikev2'), data.get('max_users', 50))
            )
            server_id = cursor.lastrowid
            await db.commit()
        
        await message.answer(
            f"✅ Сервер '{data['server_name']}' добавлен!\n"
            f"ID: {server_id}\n"
            f"Тип VPN: {data.get('vpn_type', 'ikev2').upper()}\n"
            f"Лимит: {data.get('max_users', 50)} пользователей\n\n"
            f"Теперь установите VPN через меню '🔧 Установить VPN'",
            reply_markup=admin_main_menu()
        )
        await state.clear()
        
    except Exception as e: 
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "🔧 Установить VPN")
async def admin_install_vpn_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=servers_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет активных серверов", reply_markup=servers_menu()); return
    
    text = "🔧 Выберите сервер для установки VPN:\n"
    for server_id, name in servers: 
        text += f"ID: {server_id} - {name}\n"
    text += "\nВведите ID сервера:"
    
    await state.set_state(InstallVPNStates.waiting_for_server)
    await message.answer(text, reply_markup=back_keyboard())

@dp.message(InstallVPNStates.waiting_for_server)
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
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: 
                await message.answer("❌ Сервер не найден", reply_markup=back_keyboard())
                return
            server_name = server[0]
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=back_keyboard())
        return
    
    await state.update_data(server_id=server_id, server_name=server_name)
    await state.set_state(InstallVPNStates.waiting_for_type)
    await message.answer(f"Сервер: {server_name} (ID: {server_id})\n\nВыберите тип VPN для установки:", reply_markup=install_vpn_menu())

@dp.message(InstallVPNStates.waiting_for_type)
async def process_install_vpn_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(InstallVPNStates.waiting_for_server)
        await admin_install_vpn_menu(message, state)
        return
    
    if message.text not in ["IKEv2", "L2TP"]:
        await message.answer("Выберите тип VPN из списка:", reply_markup=install_vpn_menu())
        return
    
    data = await state.get_data()
    server_id = data['server_id']
    server_name = data['server_name']
    vpn_type = message.text.lower()
    
    success = await setup_ikev2_l2tp_auto(server_id, vpn_type, message)
    await state.clear()
    
    if success: 
        await message.answer(f"✅ {vpn_type.upper()} успешно установлен на сервер '{server_name}'!", reply_markup=admin_main_menu())
    else: 
        await message.answer(f"❌ Не удалось установить {vpn_type.upper()} на сервер '{server_name}'", reply_markup=admin_main_menu())

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
                SELECT v.id, v.user_id, v.username, v.vpn_login, v.vpn_type, v.subscription_end, v.is_active, v.device_type, s.name as server_name 
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                ORDER BY v.created_at DESC LIMIT 30
            """)
            users = await cursor.fetchall()
    except Exception as e: 
        await message.answer(f"❌ Ошибка получения данных: {e}", reply_markup=admin_users_menu()); return
    
    if not users: 
        await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu()); return
    
    text = "📋 Список пользователей:\n\n"
    for i, user in enumerate(users[:15], 1):
        user_id, tg_id, username, vpn_login, vpn_type, sub_end, active, device_type, server_name = user
        status = "🟢" if active else "🔴"
        username_display = f"@{username}" if username else f"ID:{tg_id}"
        device_icon = "📱" if device_type == "iphone" else "🤖" if device_type == "android" else "💻"
        vpn_icon = "🔐" if vpn_type == "ikev2" else "🅾️"
        
        if sub_end: 
            sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m')
            days_left = max(0, (datetime.fromisoformat(sub_end) - datetime.now()).days)
            text += f"{i}. {status}{device_icon}{vpn_icon} {username_display} 📅{sub_date}({days_left}д) 🖥️{server_name or 'N/A'}\n"
        else: 
            text += f"{i}. {status}{device_icon}{vpn_icon} {username_display} 📅бессрочно\n"
    
    if len(users) > 15: 
        text += f"\n... и еще {len(users)-15} пользователей"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_users_menu())

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
💎 {prices['month']['stars']} Stars (авто: неделя ×3)
₽ {prices['month']['rub']:.2f} RUB
€ {prices['month']['eur']:.2f} EUR

Для изменения цен используйте кнопку ниже:"""
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=prices_menu())

@dp.message(F.text == "✏️ Изменить цену")
async def admin_change_price_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    
    prices = await get_vpn_prices()
    text = f"""✏️ <b>Изменение цены</b>

<b>Текущая цена за неделю:</b>
💎 {prices['week']['stars']} Stars
₽ {prices['week']['rub']:.2f} RUB
€ {prices['week']['eur']:.2f} EUR

<b>Введите новую цену за неделю в формате:</b>
<code>Stars, RUB, EUR</code>

<b>Пример:</b> <code>50, 500.0, 5.0</code>

<b>Месячная цена будет рассчитана автоматически (×3)</b>"""
    
    await state.set_state(PriceStates.waiting_for_prices)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(PriceStates.waiting_for_prices)
async def process_new_prices(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await admin_prices(message)
        return
    
    try:
        # Разбираем введенные цены
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
        
        # Обновляем цены
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
€ {new_prices['month']['eur']:.2f} EUR"""
            
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
            cursor = await db.execute("SELECT id, name, server_ip FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: 
        await message.answer("❌ Ошибка получения данных", reply_markup=admin_main_menu()); return
    
    if not servers: 
        await message.answer("📭 Нет активных серверов", reply_markup=admin_main_menu()); return
    
    text = "🤖 <b>Выберите сервер для теста:</b>\n\n"
    for server_id, name, server_ip in servers:
        ip_display = server_ip if server_ip else "IP не установлен"
        text += f"<b>{name}</b>\nID: {server_id} | 🌐 {ip_display}\n\n"
    
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
                  f"🔐 <b>Sudo доступ:</b> {'✅ Есть' if system_info['has_sudo'] else '❌ Нет'}"]
        
        for cmd, desc in test_cmds:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=False)
            if success and stdout:
                results.append(f"📊 <b>{desc}:</b> {stdout.strip()}")
        
        # Проверка VPN
        vpn_checks = [
            ("which ipsec || which strongswan", "IKEv2/IPsec"),
            ("which xl2tpd", "L2TP"),
        ]
        
        results.append("\n🔧 <b>Установленные VPN:</b>")
        for cmd, name in vpn_checks:
            stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=False)
            if success and stdout:
                results.append(f"✅ {name}")
            else:
                results.append(f"❌ {name}")
        
        await message.answer("\n".join(results), parse_mode=ParseMode.HTML, reply_markup=admin_main_menu())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка тестирования: {str(e)}", reply_markup=admin_main_menu())
    
    await state.clear()

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear()
    prices = await get_vpn_prices()
    text = f"""🔐 <b>Получить VPN доступ</b>

📊 <b>Тарифы:</b>
🎁 <b>3 дня бесплатно</b> - пробный период
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']:.2f} / €{prices['week']['eur']:.2f}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']:.2f} / €{prices['month']['eur']:.2f}

Выберите вариант:"""
    
    await state.set_state(UserPaymentStates.waiting_for_period)
    await message.answer(text, reply_markup=period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(UserPaymentStates.waiting_for_period)
async def process_user_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.clear()
        await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu())
        return
    
    if message.text == "🎁 3 дня (пробный)":
        # Проверка пробного периода
        user_id = message.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if user and user[0]:
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
                await state.clear()
                return
        
        await state.update_data(period=3, is_trial=True, amount_stars=0, amount_rub=0, amount_eur=0)
        await state.set_state(UserPaymentStates.waiting_for_vpn_type)
        await message.answer("✅ Пробный период доступен!\n\n🔐 Выберите тип VPN:", reply_markup=vpn_type_keyboard())
        
    elif message.text in ["💎 Неделя", "💎 Месяц"]:
        period = 7 if message.text == "💎 Неделя" else 30
        prices = await get_vpn_prices()
        
        if period == 7:
            amount_stars = prices['week']['stars']
            amount_rub = prices['week']['rub']
            amount_eur = prices['week']['eur']
        else:
            amount_stars = prices['month']['stars']
            amount_rub = prices['month']['rub']
            amount_eur = prices['month']['eur']
        
        await state.update_data(
            period=period, 
            is_trial=False,
            amount_stars=amount_stars,
            amount_rub=amount_rub,
            amount_eur=amount_eur
        )
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
    amount_rub = data['amount_rub']
    
    if message.text == "💎 Stars (Telegram)":
        # Платеж через Stars (Telegram Payments)
        try:
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"VPN доступ на {period} дней",
                description=f"Доступ к VPN серверу на {period} дней. Оплата Stars.",
                payload=f"vpn_stars_{period}days_{message.from_user.id}",
                provider_token=PROVIDER_TOKEN,
                currency="XTR",  # Stars
                prices=[LabeledPrice(label=f"{period} дней VPN", amount=data['amount_stars'] * 100)],  # Stars в центах
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
        # Перенаправляем в поддержку
        await message.answer(
            f"💳 <b>Оплата картой (RUB/€)</b>\n\n"
            f"Для оплаты картой обратитесь в поддержку:\n{SUPPORT_PAYMENT}\n\n"
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
    
    # Записываем платеж
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO payments (user_id, amount_stars, amount_rub, amount_eur, period_days, status, telegram_payment_id)
                VALUES (?, ?, ?, ?, ?, 'completed', ?)
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
        f"🔐 Теперь выберите тип VPN:",
        parse_mode=ParseMode.HTML
    )
    
    await state.set_state(UserPaymentStates.waiting_for_vpn_type)
    await message.answer("🔐 Выберите тип VPN:", reply_markup=vpn_type_keyboard())

@dp.message(UserPaymentStates.waiting_for_vpn_type)
async def process_user_vpn_type(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await get_vpn_start(message, state)
        return
    
    if message.text not in ["IKEv2", "L2TP"]:
        await message.answer("Выберите тип VPN из списка:", reply_markup=vpn_type_keyboard())
        return
    
    vpn_type = message.text.lower()
    await state.update_data(vpn_type=vpn_type)
    await state.set_state(UserPaymentStates.waiting_for_device)
    await message.answer("📱 Выберите тип вашего устройства:", reply_markup=device_type_keyboard())

@dp.message(UserPaymentStates.waiting_for_device)
async def process_user_device(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": 
        await state.set_state(UserPaymentStates.waiting_for_vpn_type)
        await message.answer("🔐 Выберите тип VPN:", reply_markup=vpn_type_keyboard())
        return
    
    device_map = {
        "📱 iPhone/iOS": "iphone",
        "🤖 Android": "android",
        "💻 Другое": "auto"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите тип устройства из списка:", reply_markup=device_type_keyboard())
        return
    
    device_type = device_map[message.text]
    
    # Получаем все данные
    data = await state.get_data()
    period = data.get('period', 7)
    is_trial = data.get('is_trial', False)
    vpn_type = data.get('vpn_type', 'ikev2')
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"
    
    # Находим доступный сервер
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Ищем сервер с нужным типом VPN
            if vpn_type == "ikev2":
                condition = "ikev2_configured = TRUE"
            else:  # l2tp
                condition = "l2tp_configured = TRUE"
            
            cursor = await db.execute(f"""
                SELECT id, name, current_users, max_users 
                FROM servers 
                WHERE is_active = TRUE AND {condition} AND current_users < max_users
                ORDER BY current_users ASC LIMIT 1
            """)
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов. Попробуйте позже.", reply_markup=user_main_menu())
                await state.clear()
                return
            
            server_id, server_name, current_users, max_users = server
            
            # Создаем VPN клиента
            vpn_data, error = await create_vpn_client(server_id, user_id, username, vpn_type, device_type)
            
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
        await send_vpn_config_to_user(user_id, vpn_data, message)
        
        await message.answer(
            f"✅ VPN доступ активирован!\n\n"
            f"📅 Срок действия: {period} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"🔧 Тип: {vpn_type.upper()}\n"
            f"📱 Устройство: {device_type}\n"
            f"👤 Логин: {vpn_data['vpn_login']}\n"
            f"🔑 Пароль: {vpn_data['vpn_password']}\n\n"
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
                SELECT v.subscription_end, v.is_active, v.vpn_login, v.vpn_password, v.vpn_type, v.device_type, s.name as server_name, s.server_ip
                FROM vpn_users v 
                LEFT JOIN servers s ON v.server_id = s.id 
                WHERE v.user_id = ? AND v.is_active = TRUE 
                ORDER BY v.created_at DESC LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active, vpn_login, vpn_password, vpn_type, device_type, server_name, server_ip = user
        
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
            vpn_icon = "🔐" if vpn_type == "ikev2" else "🅾️"
            
            text = f"📱 <b>Ваша подписка VPN</b>\n\n"
            text += f"<b>Статус:</b> {status}\n"
            text += f"<b>Действует до:</b> {end_date.strftime('%d.%m.%Y %H:%M')}\n"
            if server_name: text += f"<b>Сервер:</b> {server_name}\n"
            if server_ip: text += f"<b>IP сервера:</b> {server_ip}\n"
            text += f"<b>Тип VPN:</b> {vpn_icon} {vpn_type.upper()}\n"
            text += f"<b>Устройство:</b> {device_icon} {device_type}\n"
            text += f"<b>Логин:</b> <code>{vpn_login}</code>\n"
            text += f"<b>Пароль:</b> <code>{vpn_password}</code>\n"
            
            if days_left < 3 and days_left > 0:
                text += f"\n⚠️ <b>Внимание!</b> Подписка истекает через {days_left} дней.\n"
            
            text += f"\n<b>Общий ключ (PSK):</b> <code>vpnsharedkey</code>"
            text += f"\n\n🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
            
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
                SELECT name, server_ip, ikev2_configured, l2tp_configured, current_users, max_users 
                FROM servers 
                WHERE is_active = TRUE 
                ORDER BY name
            """)
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}", reply_markup=user_main_menu()); return
    
    if not servers: 
        await message.answer("📭 Серверов нет в данный момент", reply_markup=user_main_menu()); return
    
    text = "🌐 <b>Доступные серверы:</b>\n\n"
    for server in servers:
        name, server_ip, ikev2, l2tp, current, max_users = server
        load_percent = (current / max_users * 100) if max_users > 0 else 0
        
        if load_percent < 50: load_icon = "🟢"
        elif load_percent < 80: load_icon = "🟡"
        else: load_icon = "🔴"
        
        vpn_types = []
        if ikev2: vpn_types.append("IKEv2")
        if l2tp: vpn_types.append("L2TP")
        
        text += f"{load_icon} <b>{name}</b>\n"
        text += f"   🌐 {server_ip or 'IP обновляется'}\n"
        text += f"   🔧 {', '.join(vpn_types) if vpn_types else 'Нет VPN'}\n"
        text += f"   👥 {current}/{max_users} ({load_percent:.0f}%)\n\n"
    
    text += f"🆘 <b>Поддержка:</b> {SUPPORT_USERNAME}"
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    text = f"""🆘 <b>Помощь и поддержка</b>

<b>Частые вопросы:</b>
1. <b>Как подключиться?</b> - После оплаты вы получите данные для подключения
2. <b>Какие типы VPN поддерживаются?</b> - IKEv2 и L2TP (встроены в iOS/Android)
3. <b>Как продлить подписку?</b> - Купите новую подписку через "🔐 Получить VPN"
4. <b>Не работает подключение?</b> - Перезагрузите устройство, проверьте настройки
5. <b>Проблемы с оплатой?</b> - Обратитесь в поддержку

<b>Полезные советы:</b>
• Сохраните данные подключения в надежном месте
• Для iOS используйте IKEv2 для лучшей стабильности
• При проблемах попробуйте другой тип VPN

<b>Контакты поддержки:</b>
{SUPPORT_USERNAME}

<b>Для оплаты картой:</b>
{SUPPORT_PAYMENT}"""
    
    await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    print("=" * 50)
    print(f"🔐 Только IKEv2/L2TP (без приложений)")
    print(f"💳 Поддержка Stars, RUB, EUR")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Support: {SUPPORT_USERNAME}")
    print(f"💳 Payment Support: {SUPPORT_PAYMENT}")
    
    # Инициализация БД
    if not await init_database():
        print("❌ Не удалось инициализировать базу данных!")
        return
    
    try:
        # Проверка соединения с ботом
        print("🔍 Проверяю соединение с Telegram API...")
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username} (ID: {me.id})")
        print(f"📝 Имя: {me.full_name}")
        print(f"💰 Provider Token: {PROVIDER_TOKEN[:20]}...")
    except Exception as e:
        print(f"❌ Ошибка подключения к Telegram API: {e}")
        print(f"Проверьте BOT_TOKEN: {'установлен' if BOT_TOKEN else 'НЕ УСТАНОВЛЕН'}")
        return
    
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