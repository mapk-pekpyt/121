# vpn_bot_final_fixed.py - VPN БОТ (ИСПРАВЛЕННЫЙ)
import os, asyncio, logging, sys, random, sqlite3, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, LabeledPrice, PreCheckoutQuery, ContentType
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
    print("Запустите: BOT_TOKEN='ваш_токен' python vpn_bot_final_fixed.py")
    sys.exit(1)

SUPPORT_USERNAME = "@vpnhostik"
DATA_DIR = "/data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "381764678:TEST:85560")

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
    
    # Значения по умолчанию
    return {
        "week": {"days": 7, "stars": 50, "rub": 500.0, "eur": 5.0},
        "month": {"days": 30, "stars": 150, "rub": 1500.0, "eur": 15.0}
    }

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
    await message.answer(f"🚀 Начинаю установку {vpn_type}...")
    
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
    if not servers: await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
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
        await message.answer(f"❌ Ошибка получения данных: {e}"); return
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
₽ {prices['week']['rub']} RUB
€ {prices['week']['eur']} EUR

<b>Месяц (30 дней):</b>
💎 {prices['month']['stars']} Stars (авто: неделя ×3)
₽ {prices['month']['rub']} RUB
€ {prices['month']['eur']} EUR

Для изменения цен используйте команду: /setprice неделя_в_Stars"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_menu())

@dp.message(Command("setprice"))
async def set_price_command(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /setprice <цена_недели_в_stars>")
            return
        
        week_stars = int(args[1])
        if week_stars < 1 or week_stars > 10000:
            await message.answer("Цена должна быть от 1 до 10000 Stars"); return
        
        month_stars = week_stars * 3
        week_rub = week_stars * 10.0
        week_eur = week_stars * 0.1
        month_rub = month_stars * 10.0
        month_eur = month_stars * 0.1
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE prices SET 
                week_stars = ?, week_rub = ?, week_eur = ?,
                month_stars = ?, month_rub = ?, month_eur = ?
                WHERE id = 1
            """, (week_stars, week_rub, week_eur, month_stars, month_rub, month_eur))
            await db.commit()
        
        await message.answer(f"""✅ Цены обновлены!

<b>Неделя:</b>
💎 {week_stars} Stars
₽ {week_rub:.2f} RUB
€ {week_eur:.2f} EUR

<b>Месяц (неделя×3):</b>
💎 {month_stars} Stars
₽ {month_rub:.2f} RUB
€ {month_eur:.2f} EUR""", parse_mode=ParseMode.HTML)
        
    except ValueError:
        await message.answer("Введите число после команды")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(F.text == "🤖 Тест сервера")
async def admin_test_server(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    await message.answer("Для теста сервера используйте команду: /testserver <id_сервера>", reply_markup=admin_main_menu())

@dp.message(Command("testserver"))
async def test_server_command(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /testserver <id_сервера>")
            return
        
        server_id = int(args[1])
        await message.answer(f"🔍 Тестирую сервер ID {server_id}...")
        
        ssh_ok, ssh_msg, system_info = await check_ssh_connection(server_id)
        
        if not ssh_ok:
            await message.answer(f"❌ SSH тест не пройден: {ssh_msg}")
            return
        
        results = [f"✅ SSH подключение работает к серверу {server_id}",
                  f"👤 Пользователь: {system_info['user']}",
                  f"🌐 Хост: {system_info['host']}",
                  f"📦 ОС: {system_info['os_info'][:50]}...",
                  f"🔐 Sudo доступ: {'✅ Есть' if system_info['has_sudo'] else '❌ Нет'}"]
        
        await message.answer("\n".join(results))
        
    except ValueError:
        await message.answer("Введите числовой ID сервера")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message):
    prices = await get_vpn_prices()
    text = f"""🔐 <b>Получить VPN доступ</b>

📊 <b>Тарифы:</b>
🎁 <b>3 дня бесплатно</b> - пробный период
💎 <b>7 дней</b> - {prices['week']['stars']} Stars / ₽{prices['week']['rub']} / €{prices['week']['eur']}
💎 <b>30 дней</b> - {prices['month']['stars']} Stars / ₽{prices['month']['rub']} / €{prices['month']['eur']}

Выберите вариант:"""
    await message.answer(text, reply_markup=period_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        if user and user[0]:
            await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
            return
    
    await message.answer("🔐 Выберите тип VPN:", reply_markup=vpn_type_keyboard())

@dp.message(F.text.in_(["💎 Неделя", "💎 Месяц"]))
async def get_paid_vpn(message: Message):
    await message.answer("💳 Выберите способ оплаты:", reply_markup=payment_method_keyboard())

@dp.message(F.text == "💎 Stars (Telegram)")
async def pay_with_stars(message: Message):
    await message.answer("Для оплаты Stars просто отправьте нужное количество Stars в этот чат, затем выберите тип VPN.", reply_markup=vpn_type_keyboard())

@dp.message(F.text == "💳 Карта (RUB/€)")
async def pay_with_card(message: Message):
    await message.answer("Платежи картой временно недоступны. Используйте оплату Stars.", reply_markup=payment_method_keyboard())

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
            end_date = datetime.fromisoformat(sub_end)
            now = datetime.now()
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
        await message.answer(f"❌ Ошибка получения данных: {e}"); return
    
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

<b>Команды админа:</b>
• /setprice <stars> - установить цену за неделю
• /testserver <id> - протестировать сервер

<b>Контакты поддержки:</b>
{SUPPORT_USERNAME}"""
    
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