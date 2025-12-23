# main.py - ИСПРАВЛЕННАЯ ВЕРСИЯ
import os, asyncio, logging, sys, random, qrcode, io, sqlite3, re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardButton, ContentType
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
            await db.execute("""CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, ssh_key TEXT NOT NULL, connection_string TEXT NOT NULL, max_users INTEGER DEFAULT 50, current_users INTEGER DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, server_ip TEXT, public_key TEXT, wireguard_configured BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS vpn_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, server_id INTEGER, client_name TEXT, client_public_key TEXT, client_ip TEXT, config_data TEXT, subscription_end TIMESTAMP, trial_used BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount_stars INTEGER NOT NULL, period TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY, week_price INTEGER DEFAULT 50, month_price INTEGER DEFAULT 150)")
            await db.execute("INSERT OR IGNORE INTO prices (id, week_price, month_price) VALUES (1, 50, 150)")
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
            cursor = await db.execute("SELECT week_price, month_price FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices: return {"week": {"days": 7, "stars": prices[0]}, "month": {"days": 30, "stars": prices[1]}}
    except: pass
    return {"week": {"days": 7, "stars": 50}, "month": {"days": 30, "stars": 150}}

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = False) -> Tuple[str, str, bool]:
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
                    if result.exit_status == 0: return result.stdout, result.stderr, True
                    else: return result.stdout, result.stderr, False
            except asyncssh.Error as e:
                error_msg = f"SSH ошибка: {str(e)}"
                try: os.unlink(temp_key_path)
                except: pass
                return "", error_msg, False
    except Exception as e:
        return "", f"Общая ошибка: {str(e)}", False

async def setup_wireguard_auto(server_id: int, message: Message):
    """АВТОМАТИЧЕСКАЯ установка WireGuard с определением пакетного менеджера"""
    await message.answer("🚀 Начинаю автоматическую установку WireGuard...")
    
    # Проверяем права sudo
    stdout, stderr, success = await execute_ssh_command(server_id, "sudo -n true 2>&1 || echo 'No sudo'")
    has_sudo = success and 'No sudo' not in stdout + stderr
    if not has_sudo: await message.answer("⚠️ Пользователь не имеет прав sudo. Установка может быть ограничена.")
    
    # Определяем ОС и пакетный менеджер
    await message.answer("🔍 Определяю операционную систему...")
    stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/os-release 2>/dev/null || echo 'Unknown'")
    
    install_commands = []
    if 'ubuntu' in stdout.lower() or 'debian' in stdout.lower():
        await message.answer("📦 Обнаружена Ubuntu/Debian, использую apt...")
        install_commands = [
            "apt-get update -y",
            "apt-get install -y wireguard wireguard-tools qrencode",
            "systemctl enable wg-quick@wg0 2>/dev/null || true",
            "modprobe wireguard 2>/dev/null || true"
        ]
    elif 'oracle' in stdout.lower() or 'centos' in stdout.lower() or 'redhat' in stdout.lower():
        await message.answer("📦 Обнаружен Oracle Linux/RHEL/CentOS, использую yum/dnf...")
        install_commands = [
            "yum check-update -y || dnf check-update -y || true",
            "yum install -y epel-release elrepo-release || dnf install -y epel-release elrepo-release || true",
            "yum install -y kmod-wireguard wireguard-tools qrencode || dnf install -y wireguard-tools qrencode || true",
            "modprobe wireguard 2>/dev/null || true"
        ]
    else:
        await message.answer("⚠️ Неизвестная ОС, пробую универсальную установку...")
        install_commands = [
            "which apt-get && (apt-get update -y && apt-get install -y wireguard wireguard-tools qrencode) || true",
            "which yum && (yum install -y wireguard-tools qrencode) || true",
            "which dnf && (dnf install -y wireguard-tools qrencode) || true",
            "modprobe wireguard 2>/dev/null || true"
        ]
    
    # Выполняем команды установки
    for cmd in install_commands:
        await message.answer(f"🔄 Выполняю: {cmd[:60]}...")
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=120, use_sudo=has_sudo)
        if not success: await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
    
    # Генерация ключей и конфигурации
    await message.answer("🔑 Генерирую ключи WireGuard...")
    
    if has_sudo:
        setup_cmds = [
            "mkdir -p /etc/wireguard && cd /etc/wireguard",
            "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
            """cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF""",
            "wg-quick up wg0 2>/dev/null || true",
            "systemctl enable wg-quick@wg0 2>/dev/null || true"
        ]
    else:
        setup_cmds = [
            "mkdir -p ~/.wireguard && cd ~/.wireguard",
            "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
            """cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
EOF""",
            "wg-quick up wg0 2>/dev/null || echo 'Запуск WireGuard может потребовать sudo'"
        ]
    
    for cmd in setup_cmds:
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=False)
    
    # Получаем публичный ключ
    if has_sudo:
        stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key", use_sudo=False)
    else:
        stdout, stderr, success = await execute_ssh_command(server_id, "cat ~/.wireguard/public.key")
    
    if success and stdout.strip():
        public_key = stdout.strip()
        # Получаем IP сервера
        stdout, stderr, success = await execute_ssh_command(server_id, "curl -s ifconfig.me || hostname -I | awk '{print $1}'")
        server_ip = stdout.strip() if success else ""
        
        # Сохраняем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET wireguard_configured = TRUE, public_key = ?, server_ip = ? WHERE id = ?", (public_key, server_ip, server_id))
            await db.commit()
        
        await message.answer(f"✅ WireGuard успешно установлен!\n🔑 Публичный ключ: {public_key[:50]}...\n🌐 IP: {server_ip}")
        return True
    else:
        await message.answer("❌ Не удалось получить публичный ключ после установки")
        return False

async def setup_wireguard_via_git(server_id: int, message: Message):
    """Ручная установка WireGuard через Git (универсальная)"""
    await message.answer("🔧 Начинаю ручную установку WireGuard через Git...")
    
    # Проверяем права
    stdout, stderr, success = await execute_ssh_command(server_id, "sudo -n true 2>&1 || echo 'No sudo'")
    has_sudo = success and 'No sudo' not in stdout + stderr
    
    # Определяем пакетный менеджер
    await message.answer("🔍 Проверяю доступные пакетные менеджеры...")
    stdout, stderr, success = await execute_ssh_command(server_id, "which apt-get yum dnf apk 2>/dev/null | head -1")
    pkg_manager = stdout.strip() if success else ""
    
    # Установка зависимостей
    await message.answer("📦 Устанавливаю зависимости...")
    if 'apt-get' in pkg_manager:
        deps_cmd = "apt-get update && apt-get install -y git build-essential libmnl-dev libelf-dev linux-headers-$(uname -r) pkg-config curl"
    elif 'yum' in pkg_manager or 'dnf' in pkg_manager:
        deps_cmd = "yum install -y git gcc make libmnl-devel libelf-devel kernel-devel pkgconfig curl || dnf install -y git gcc make libmnl-devel libelf-devel kernel-devel pkgconfig curl"
    else:
        deps_cmd = "echo 'Установите зависимости вручную: git, gcc, make, libmnl-dev, libelf-dev, linux-headers' && exit 1"
    
    stdout, stderr, success = await execute_ssh_command(server_id, deps_cmd, timeout=180, use_sudo=has_sudo)
    if not success:
        await message.answer(f"⚠️ Ошибка установки зависимостей: {stderr[:200]}")
    
    # Компиляция WireGuard
    await message.answer("🔨 Компилирую WireGuard из исходников...")
    compile_cmds = [
        "cd /tmp && rm -rf wireguard* 2>/dev/null || true",
        "cd /tmp && git clone https://git.zx2c4.com/wireguard-linux-compat",
        "cd /tmp/wireguard-linux-compat && make -j$(nproc) && make install",
        "cd /tmp && git clone https://git.zx2c4.com/wireguard-tools",
        "cd /tmp/wireguard-tools/src && make -j$(nproc) && make install",
        "modprobe wireguard 2>/dev/null || echo 'Модуль wireguard не загружен'"
    ]
    
    for cmd in compile_cmds:
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=300, use_sudo=has_sudo)
        if not success: await message.answer(f"⚠️ Предупреждение: {stderr[:100]}")
    
    # Создание конфигурации
    await message.answer("⚙️ Создаю конфигурацию WireGuard...")
    if has_sudo:
        config_cmds = [
            "mkdir -p /etc/wireguard && cd /etc/wireguard",
            "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
            """cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
PostUp = sysctl -w net.ipv4.ip_forward=1
PostDown = sysctl -w net.ipv4.ip_forward=0
EOF""",
            "wg-quick up wg0 2>/dev/null || true"
        ]
    else:
        config_cmds = [
            "mkdir -p ~/.wireguard && cd ~/.wireguard",
            "umask 077; wg genkey | tee private.key | wg pubkey > public.key",
            """cat > wg0.conf << 'EOF'
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat private.key)
EOF""",
            "echo 'Запустите WireGuard вручную: wg-quick up ~/.wireguard/wg0.conf'"
        ]
    
    for cmd in config_cmds:
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, use_sudo=False)
    
    # Проверка установки
    stdout, stderr, success = await execute_ssh_command(server_id, "wg --version 2>/dev/null || echo 'WireGuard не найден'")
    if 'WireGuard' in stdout:
        # Получаем публичный ключ
        if has_sudo:
            stdout, stderr, success = await execute_ssh_command(server_id, "cat /etc/wireguard/public.key", use_sudo=False)
        else:
            stdout, stderr, success = await execute_ssh_command(server_id, "cat ~/.wireguard/public.key")
        
        if success and stdout.strip():
            public_key = stdout.strip()
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET wireguard_configured = TRUE, public_key = ? WHERE id = ?", (public_key, server_id))
                await db.commit()
            await message.answer(f"✅ WireGuard успешно установлен через Git!\n🔑 Публичный ключ: {public_key[:50]}...")
            return True
    
    await message.answer("⚠️ WireGuard установлен, но требуется дополнительная настройка")
    return False

async def test_server_with_bot(server_id: int, bot_token: str, message: Message):
    """Тестирование сервера загрузкой тестового бота"""
    await message.answer("🤖 Загружаю тестового бота на сервер...")
    
    # Создаем простого Python бота
    bot_code = f'''#!/usr/bin/env python3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
import logging, datetime

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

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"Эхо: {{message.text}}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    
    # Записываем бота на сервер
    try:
        # Создаем файл бота
        await message.answer("📝 Создаю файл бота...")
        create_bot_cmd = f'''cd /tmp && cat > test_bot.py << 'EOF'
{bot_code}
EOF
chmod +x test_bot.py
echo "Файл бота создан"'''
        
        stdout, stderr, success = await execute_ssh_command(server_id, create_bot_cmd)
        if not success:
            await message.answer(f"❌ Ошибка создания файла: {stderr}")
            return False
        
        # Запускаем бота в фоне
        await message.answer("🚀 Запускаю тестового бота...")
        run_bot_cmd = "cd /tmp && nohup python3 test_bot.py > bot.log 2>&1 & sleep 2 && echo 'Бот запущен'"
        stdout, stderr, success = await execute_ssh_command(server_id, run_bot_cmd)
        
        if success:
            await message.answer("✅ Тестовый бот запущен на сервере!\n\nОтправьте /start или /ping в бот для проверки.")
            return True
        else:
            await message.answer(f"❌ Ошибка запуска бота: {stderr}")
            return False
            
    except Exception as e:
        await message.answer(f"❌ Ошибка тестирования: {str(e)}")
        return False

# ========== КЛАВИАТУРЫ ==========
def user_main_menu():
    buttons = [[types.KeyboardButton(text="🔐 Получить VPN")], [types.KeyboardButton(text="📱 Мои услуги")], [types.KeyboardButton(text="🆘 Помощь")]]
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

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_actions_keyboard(server_id: int):
    buttons = [[types.KeyboardButton(text=f"🔧 Установить WG (ID: {server_id})")], [types.KeyboardButton(text=f"🔍 Проверить SSH (ID: {server_id})")], [types.KeyboardButton(text=f"🤖 Тест ботом (ID: {server_id})")], [types.KeyboardButton(text="◀️ Назад к списку")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== FSM СОСТОЯНИЯ ==========
class AdminAddServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class AdminUserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_period = State()

class AdminPriceStates(StatesGroup):
    waiting_for_week_price = State()

class AdminTestBotStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_token = State()

class AdminRemoveVPNStates(StatesGroup):
    waiting_for_user = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: await message.answer("🚀 Добро пожаловать в VPN Hosting!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id): await message.answer("👑 Админ-панель", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else: await message.answer("🚀 Добро пожаловать!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

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
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured, current_users, max_users FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Серверов нет", reply_markup=servers_menu()); return
    text = "📋 Список серверов:\n\n"
    for server in servers:
        server_id, name, active, wg_configured, current_users, max_users = server
        status = "🟢" if active else "🔴"; wg_status = "🔐" if wg_configured else "❌"
        text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id}) 👥 {current_users}/{max_users}\n"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu(), parse_mode=ParseMode.HTML); return
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_key)
    await message.answer("📎 Пришлите файл с SSH ключом (.key, .pem):", reply_markup=back_keyboard())

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
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("INSERT INTO servers (name, ssh_key, connection_string) VALUES (?, ?, ?)", (data['server_name'], data['ssh_key'], conn_str))
            server_id = cursor.lastrowid; await db.commit()
        await message.answer(f"✅ Сервер '{data['server_name']}' добавлен! ID: {server_id}", reply_markup=admin_main_menu())
        await state.clear()
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu()); await state.clear()

@dp.message(F.text == "🔧 Установить WG")
async def admin_install_wg_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not servers: await message.answer("📭 Нет активных серверов"); return
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
        # Автоматическая установка WireGuard
        success = await setup_wireguard_auto(server_id, message)
        if success: await message.answer("✅ WireGuard установлен! Теперь можно создавать VPN подключения.", reply_markup=admin_main_menu())
        else: await message.answer("❌ Автоустановка не удалась. Попробуйте ручную установку.", reply_markup=admin_main_menu())
        await state.clear()
    
    elif action == "test_bot":
        await state.update_data(server_id=server_id)
        await state.set_state(AdminTestBotStates.waiting_for_token)
        await message.answer("Введите токен бота для тестирования:", reply_markup=back_keyboard())
    
    else:
        await state.clear()

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

@dp.message(F.text.contains("Установить WG (ID:"))
async def handle_install_wg_from_list(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    match = re.search(r'ID:\s*(\d+)', message.text)
    if not match: return
    server_id = int(match.group(1))
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: await message.answer("❌ Сервер не найден"); return
            server_name = server[0]
    except: await message.answer("❌ Ошибка получения данных"); return
    success = await setup_wireguard_auto(server_id, message)
    if success: await message.answer(f"✅ WireGuard установлен на {server_name}!", reply_markup=admin_main_menu())
    else: await message.answer(f"❌ Не удалось установить WireGuard на {server_name}", reply_markup=admin_main_menu())

@dp.message(F.text.contains("Проверить SSH (ID:"))
async def handle_check_ssh(message: Message):
    if not is_admin(message.from_user.id, message.chat.id): return
    match = re.search(r'ID:\s*(\d+)', message.text)
    if not match: return
    server_id = int(match.group(1))
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server: await message.answer("❌ Сервер не найден"); return
            server_name = server[0]
    except: await message.answer("❌ Ошибка получения данных"); return
    await message.answer(f"🔍 Проверяю SSH подключение к {server_name}...")
    stdout, stderr, success = await execute_ssh_command(server_id, "echo 'SSH Test OK' && whoami && uname -a && date")
    if success:
        lines = stdout.strip().split('\n')
        response = f"✅ SSH работает!\nПользователь: {lines[1] if len(lines)>1 else 'N/A'}\nСистема: {lines[2] if len(lines)>2 else 'N/A'}"
        await message.answer(response, reply_markup=admin_main_menu())
    else: await message.answer(f"❌ SSH ошибка: {stderr}", reply_markup=admin_main_menu())

@dp.message(F.text.contains("🤖 Тест ботом (ID:"))
async def handle_test_bot_from_list(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    match = re.search(r'ID:\s*(\d+)', message.text)
    if not match: return
    server_id = int(match.group(1))
    await state.set_state(AdminTestBotStates.waiting_for_token)
    await state.update_data(server_id=server_id)
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
    try:
        user_id = 0
        if username.isdigit(): user_id = int(username); username_to_save = f"id_{username}"
        else: username_to_save = username
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, current_users, max_users FROM servers WHERE wireguard_configured = TRUE AND is_active = TRUE AND current_users < max_users LIMIT 1")
            server = await cursor.fetchone()
            if not server: await message.answer("❌ Нет доступных серверов"); return
            server_id, server_name, current_users, max_users = server
            client_name = f"client_{user_id if user_id>0 else username_to_save}_{random.randint(1000,9999)}"
            subscription_end = (datetime.now() + timedelta(days=days)).isoformat()
            await db.execute("INSERT INTO vpn_users (user_id, username, server_id, client_name, subscription_end, trial_used, is_active) VALUES (?, ?, ?, ?, ?, ?, TRUE)", (user_id, username_to_save, server_id, client_name, subscription_end, days==3))
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        await state.clear()
        await message.answer(f"✅ VPN выдан!\n👤 @{username}\n📅 {days} дней\n🖥️ {server_name}\n👥 {current_users+1}/{max_users}\n🔑 {client_name}", reply_markup=admin_main_menu())
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id): return
    await state.clear()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT v.id, v.user_id, v.username, v.client_name, v.subscription_end, v.is_active, s.name as server_name FROM vpn_users v LEFT JOIN servers s ON v.server_id = s.id ORDER BY v.subscription_end DESC LIMIT 30")
            users = await cursor.fetchall()
    except: await message.answer("❌ Ошибка получения данных"); return
    if not users: await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu()); return
    text = "📋 Список пользователей:\n\n"
    for i, user in enumerate(users[:15], 1):
        user_id, tg_id, username, client_name, sub_end, active, server_name = user
        status = "🟢" if active else "🔴"; username_display = f"@{username}" if username else f"ID:{tg_id}"
        if sub_end: sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m'); days_left = (datetime.fromisoformat(sub_end) - datetime.now()).days
        text += f"{i}. {status} {username_display} 📅{sub_date}({days_left}д) 🖥️{server_name or 'N/A'}\n"
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
            cursor = await db.execute("SELECT v.id, v.user_id, v.username, v.client_name, v.server_id FROM vpn_users v WHERE v.is_active = TRUE ORDER BY v.subscription_end DESC LIMIT 30")
            users = await cursor.fetchall()
        if user_num < 0 or user_num >= len(users): await message.answer("❌ Неверный номер"); return
        user_id, tg_id, username, client_name, server_id = users[user_num]
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
    text = f"💰 Текущие цены:\n💎 Неделя: {prices['week']['stars']} Stars\n💎 Месяц: {prices['month']['stars']} Stars\n\nВведите новую цену за неделю:"
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminPriceStates.waiting_for_week_price)
async def process_week_price(message: Message, state: FSMContext):
    if message.text == "◀️ Назад": await state.clear(); await message.answer("👑 Админ-панель", reply_markup=admin_main_menu()); return
    try:
        week_price = int(message.text)
        if week_price < 10 or week_price > 1000: await message.answer("Цена от 10 до 1000 Stars:"); return
        month_price = week_price * 3
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE prices SET week_price = ?, month_price = ? WHERE id = 1", (week_price, month_price))
            await db.commit()
        await state.clear()
        await message.answer(f"✅ Цены обновлены!\nНеделя: {week_price} Stars\nМесяц: {month_price} Stars", reply_markup=admin_main_menu())
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

@dp.message(F.text == "◀️ Назад к списку")
async def back_to_server_list(message: Message, state: FSMContext):
    await state.clear(); await message.answer("🖥️ Управление серверами", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear(); prices = await get_vpn_prices()
    text = f"🔐 Получить VPN доступ:\n🎁 3 дня бесплатно\n💎 7 дней - {prices['week']['stars']} Stars\n💎 30 дней - {prices['month']['stars']} Stars\n\nВыберите вариант:"
    buttons = [[types.KeyboardButton(text="🎁 3 дня (пробный)")], [types.KeyboardButton(text="💎 Неделя")], [types.KeyboardButton(text="💎 Месяц")], [types.KeyboardButton(text="◀️ Назад")]]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message):
    user_id = message.from_user.id; username = message.from_user.username or f"id_{user_id}"
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if user and user[0]: await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu()); return
            cursor = await db.execute("SELECT id, name, current_users, max_users FROM servers WHERE wireguard_configured = TRUE AND is_active = TRUE AND current_users < max_users LIMIT 1")
            server = await cursor.fetchone()
            if not server: await message.answer("❌ Нет доступных серверов. Обратитесь в поддержку.", reply_markup=user_main_menu()); return
            server_id, server_name, current_users, max_users = server
            client_name = f"client_{user_id}_{random.randint(1000,9999)}"
            subscription_end = (datetime.now() + timedelta(days=3)).isoformat()
            await db.execute("INSERT INTO vpn_users (user_id, username, server_id, client_name, subscription_end, trial_used, is_active) VALUES (?, ?, ?, ?, ?, TRUE, TRUE)", (user_id, username, server_id, client_name, subscription_end))
            await db.execute("UPDATE servers SET current_users = current_users + 1 WHERE id = ?", (server_id,))
            await db.commit()
        await message.answer(f"✅ Пробный период активирован!\n👤 Ваш ID: {user_id}\n🖥️ Сервер: {server_name}\n👥 Место: {current_users+1}/{max_users}\n📅 Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}\n🔑 Имя клиента: {client_name}\n\nДля получения конфигурации обратитесь в поддержку: {SUPPORT_USERNAME}", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)
    except Exception as e: await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT subscription_end, is_active, client_name FROM vpn_users WHERE user_id = ? ORDER BY subscription_end DESC LIMIT 1", (user_id,))
            user = await cursor.fetchone()
        if not user: await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu()); return
        sub_end, is_active, client_name = user
        if not is_active: await message.answer("❌ Ваша подписка не активна.", reply_markup=user_main_menu()); return
        if sub_end:
            end_date = datetime.fromisoformat(sub_end); now = datetime.now()
            if end_date < now: status = "🔴 Истекла"
            else: days_left = (end_date - now).days; status = f"🟢 Активна ({days_left} дней)"
            text = f"📱 Ваша подписка VPN\n\nСтатус: {status}\nДействует до: {end_date.strftime('%d.%m.%Y %H:%M')}"
            if client_name: text += f"\n🔑 Имя клиента: {client_name}"
            text += f"\n\nДля настройки VPN обратитесь в поддержку: {SUPPORT_USERNAME}"
        else: text = "📭 Нет информации о подписке"
        await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)
    except: await message.answer("❌ Ошибка получения данных", reply_markup=user_main_menu())

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(f"🆘 Помощь и поддержка\n\nПо всем вопросам обращайтесь: {SUPPORT_USERNAME}\n\nМы всегда готовы помочь!", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

# ========== ЗАПУСК ==========
async def main():
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    if not await init_database(): logger.critical("❌ Не удалось инициализировать базу данных!"); return
    me = await bot.get_me()
    print(f"✅ Бот запущен: @{me.username}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Admin Chat ID: {ADMIN_CHAT_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logger.info("👋 Бот остановлен")
    except Exception as e: logger.critical(f"❌ Фатальная ошибка: {e}"); sys.exit(1)