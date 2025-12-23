# main.py - ИСПРАВЛЕННАЯ ВЕРСИЯ
import os
import asyncio
import logging
import sys
import random
import qrcode
import io
import sqlite3
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ContentType
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
SUPPORT_USERNAME = "@vpnbothost"

DATA_DIR = "/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_database():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ssh_key TEXT NOT NULL,
                    connection_string TEXT NOT NULL,
                    max_users INTEGER DEFAULT 50,
                    current_users INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    server_ip TEXT,
                    public_key TEXT,
                    wireguard_configured BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS vpn_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    server_id INTEGER,
                    client_name TEXT,
                    client_public_key TEXT,
                    client_ip TEXT,
                    config_data TEXT,
                    subscription_end TIMESTAMP,
                    trial_used BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_stars INTEGER NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY, week_price INTEGER DEFAULT 50, month_price INTEGER DEFAULT 150)")
            await db.execute("INSERT OR IGNORE INTO prices (id, week_price, month_price) VALUES (1, 50, 150)")
            
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int, chat_id: int = None) -> bool:
    if chat_id:
        return user_id == ADMIN_ID or str(chat_id) == str(ADMIN_CHAT_ID)
    return user_id == ADMIN_ID

async def get_vpn_prices() -> Dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT week_price, month_price FROM prices WHERE id = 1")
            prices = await cursor.fetchone()
            if prices:
                return {"week": {"days": 7, "stars": prices[0]}, "month": {"days": 30, "stars": prices[1]}}
    except:
        pass
    return {"week": {"days": 7, "stars": 50}, "month": {"days": 30, "stars": 150}}

async def execute_ssh_command(server_id: int, command: str, timeout: int = 60, use_sudo: bool = False) -> Tuple[str, str, bool]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT connection_string, ssh_key FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            
            if not server:
                return "", "Сервер не найден", False
            
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
            
            import tempfile
            import stat
            
            ssh_key_clean = ssh_key.strip()
            if not ssh_key_clean.startswith('-----BEGIN'):
                ssh_key_clean = f"-----BEGIN PRIVATE KEY-----\n{ssh_key_clean}\n-----END PRIVATE KEY-----"
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(ssh_key_clean)
                temp_key_path = f.name
            
            os.chmod(temp_key_path, stat.S_IRUSR | stat.S_IWUSR)
            
            try:
                async with asyncssh.connect(
                    host,
                    username=user,
                    port=port,
                    client_keys=[temp_key_path],
                    known_hosts=None,
                    connect_timeout=timeout
                ) as conn:
                    if use_sudo:
                        command = f"sudo {command}"
                    
                    result = await conn.run(command, timeout=timeout)
                    
                    try:
                        os.unlink(temp_key_path)
                    except:
                        pass
                    
                    if result.exit_status == 0:
                        return result.stdout, result.stderr, True
                    else:
                        return result.stdout, result.stderr, False
                    
            except asyncssh.Error as e:
                error_msg = f"SSH ошибка: {str(e)}"
                try:
                    os.unlink(temp_key_path)
                except:
                    pass
                return "", error_msg, False
                
    except Exception as e:
        return "", f"Общая ошибка: {str(e)}", False

async def setup_wireguard_via_git(server_id: int, git_repo: str, message: Message):
    """Ручная установка WireGuard через Git"""
    await message.answer(f"🔄 Устанавливаю WireGuard через Git репозиторий: {git_repo}")
    
    commands = [
        "apt-get update -y",
        "apt-get install -y git build-essential libmnl-dev libelf-dev linux-headers-$(uname -r) pkg-config",
        f"cd /tmp && git clone {git_repo}",
        "cd /tmp/wireguard-linux-compat && make && make install",
        "cd /tmp && git clone https://git.zx2c4.com/wireguard-tools",
        "cd /tmp/wireguard-tools/src && make && make install",
        "modprobe wireguard && echo 'wireguard' >> /etc/modules-load.d/wireguard.conf",
        "systemctl enable wg-quick@wg0 2>/dev/null || true"
    ]
    
    for cmd in commands:
        await message.answer(f"Выполняю: {cmd[:50]}...")
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=120, use_sudo=True)
        
        if not success:
            await message.answer(f"❌ Ошибка: {stderr[:200]}")
            return False
    
    # Генерация ключей
    keygen_cmd = """
    cd /etc/wireguard
    umask 077
    wg genkey | tee private.key | wg pubkey > public.key
    echo "Ключи сгенерированы"
    """
    
    stdout, stderr, success = await execute_ssh_command(server_id, keygen_cmd, use_sudo=True)
    
    if success and "Ключи сгенерированы" in stdout:
        await message.answer("✅ WireGuard успешно установлен через Git!")
        return True
    else:
        await message.answer(f"❌ Ошибка генерации ключей: {stderr}")
        return False

async def remove_vpn_user_from_server(server_id: int, client_name: str, message: Message = None):
    """Удаляет пользователя VPN с сервера"""
    try:
        # Получаем информацию о правах
        stdout, stderr, success = await execute_ssh_command(server_id, "sudo -n true 2>&1 || echo 'No sudo'")
        has_sudo = success and 'No sudo' not in stdout + stderr
        
        if has_sudo:
            remove_cmd = f"""
            cd /etc/wireguard
            sudo wg set wg0 peer $(sudo cat {client_name}.public) remove
            sudo rm -f {client_name}.private {client_name}.public
            sudo wg-quick save wg0 2>/dev/null || true
            """
        else:
            remove_cmd = f"""
            cd ~/.wireguard
            wg set wg0 peer $(cat {client_name}.public) remove 2>/dev/null || true
            rm -f {client_name}.private {client_name}.public
            """
        
        stdout, stderr, success = await execute_ssh_command(server_id, remove_cmd, use_sudo=False)
        
        # Обновляем счетчик пользователей
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET current_users = current_users - 1 WHERE id = ? AND current_users > 0", (server_id,))
            await db.commit()
        
        if message:
            await message.answer(f"✅ Пользователь {client_name} удален с сервера")
        
        return True
    except Exception as e:
        if message:
            await message.answer(f"❌ Ошибка удаления пользователя: {str(e)}")
        return False

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
        [types.KeyboardButton(text="🔧 Ручная установка WG")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_users_menu():
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="📋 Список пользователей")],
        [types.KeyboardButton(text="🚫 Отключить VPN")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_keyboard():
    buttons = [[types.KeyboardButton(text="◀️ Назад")]]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def server_actions_keyboard(server_id: int):
    buttons = [
        [types.KeyboardButton(text=f"🔧 Установить WG (ID: {server_id})")],
        [types.KeyboardButton(text=f"🔍 Проверить SSH (ID: {server_id})")],
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

class AdminTestBotStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_token = State()

class AdminManualWGStates(StatesGroup):
    waiting_for_server = State()
    waiting_for_git_repo = State()

class AdminRemoveVPNStates(StatesGroup):
    waiting_for_user = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id):
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else:
        await message.answer("🚀 <b>Добро пожаловать в VPN Hosting!</b>", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "◀️ Назад")
async def back_button_handler(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id, message.chat.id):
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
    else:
        await message.answer("🚀 <b>Добро пожаловать в VPN Hosting!</b>", reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🖥️ Серверы")
async def admin_servers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    await state.clear()
    await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "📋 Список серверов")
async def admin_list_servers(message: Message):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured, current_users, max_users FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет", reply_markup=servers_menu())
        return
    
    text = "📋 <b>Список серверов:</b>\n\n"
    for server in servers:
        server_id, name, active, wg_configured, current_users, max_users = server
        status = "🟢" if active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        users = f"👥 {current_users}/{max_users}"
        text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id}) {users}\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=servers_menu())

@dp.message(F.text == "➕ Добавить сервер")
async def admin_add_server_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminAddServerStates.waiting_for_name)
    await message.answer("Введите имя сервера:", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    await state.update_data(server_name=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_key)
    await message.answer(
        "Отправьте приватный SSH ключ:\n\n"
        "📎 <b>Пришлите файл с ключом</b> (формат .key, .pem) ИЛИ\n"
        "📝 <b>Вставьте текст ключа</b> (начинается с -----BEGIN)",
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
    )

# Обработчик для текстового ключа
@dp.message(AdminAddServerStates.waiting_for_key)
async def process_ssh_key_text(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    # Проверяем, похож ли текст на SSH ключ
    text = message.text.strip()
    if '-----BEGIN' in text and '-----END' in text:
        await state.update_data(ssh_key=text)
        await state.set_state(AdminAddServerStates.waiting_for_connection)
        await message.answer("✅ Ключ принят!\n\nВведите строку подключения (например: opc@193.122.8.29):", reply_markup=back_keyboard())
    else:
        await message.answer("❌ Это не похоже на SSH ключ. Отправьте файл с ключом (.key) или текст ключа:")

# Обработчик для файлов с ключами
@dp.message(AdminAddServerStates.waiting_for_key, F.document)
async def process_ssh_key_file(message: Message, state: FSMContext):
    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте файл с SSH ключом")
        return
    
    # Проверяем расширение файла
    file_name = message.document.file_name or ""
    if not file_name.endswith(('.key', '.pem', '.txt')):
        await message.answer("❌ Файл должен быть с расширением .key, .pem или .txt")
        return
    
    await message.answer("📥 Загружаю файл...")
    
    try:
        # Получаем файл
        file = await bot.get_file(message.document.file_id)
        file_path = file.file_path
        
        # Скачиваем файл
        downloaded_file = await bot.download_file(file_path)
        file_content = downloaded_file.read()
        
        # Пробуем декодировать как UTF-8
        try:
            key_text = file_content.decode('utf-8')
        except UnicodeDecodeError:
            # Пробуем другие кодировки
            try:
                key_text = file_content.decode('latin-1')
            except:
                key_text = file_content.decode('utf-8', errors='ignore')
        
        # Проверяем, что это SSH ключ
        if '-----BEGIN' not in key_text:
            await message.answer("❌ Файл не содержит SSH ключ в PEM формате")
            return
        
        await state.update_data(ssh_key=key_text)
        await state.set_state(AdminAddServerStates.waiting_for_connection)
        await message.answer("✅ Файл с SSH ключом успешно загружен!\n\nВведите строку подключения (например: opc@193.122.8.29):", reply_markup=back_keyboard())
        
    except Exception as e:
        await message.answer(f"❌ Ошибка загрузки файла: {str(e)}")

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    
    # Проверяем, есть ли ключ
    if 'ssh_key' not in data or not data['ssh_key']:
        await message.answer("❌ SSH ключ не найден. Начните заново.", reply_markup=servers_menu())
        await state.clear()
        return
    
    try:
        conn_str = message.text.strip()
        
        # Проверяем формат
        if '@' not in conn_str:
            await message.answer("❌ Неверный формат. Используйте: user@host или user@host:port")
            return
        
        # Сохраняем в базу данных
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string) VALUES (?, ?, ?)",
                (data['server_name'], data['ssh_key'], conn_str)
            )
            server_id = cursor.lastrowid
            await db.commit()
        
        # Тестируем подключение
        await message.answer("🔍 Тестирую SSH подключение...")
        stdout, stderr, success = await execute_ssh_command(server_id, "echo 'SSH Test OK' && whoami", timeout=30)
        
        if success:
            await db.execute(
                "UPDATE servers SET is_active = TRUE WHERE id = ?",
                (server_id,)
            )
            await db.commit()
            
            await message.answer(
                f"✅ Сервер '{data['server_name']}' успешно добавлен!\n\n"
                f"SSH подключение: ✅ Работает\n"
                f"Пользователь: {stdout.strip().split()[-1] if stdout else 'N/A'}\n"
                f"Строка подключения: {conn_str}\n\n"
                f"ID сервера: {server_id}\n\n"
                f"Теперь можно установить WireGuard через меню серверов.",
                reply_markup=admin_main_menu()
            )
        else:
            # Помечаем как неактивный
            await db.execute("UPDATE servers SET is_active = FALSE WHERE id = ?", (server_id,))
            await db.commit()
            
            await message.answer(
                f"⚠️ Сервер добавлен, но SSH не работает:\n\n"
                f"Ошибка: {stderr}\n\n"
                f"ID сервера: {server_id}\n"
                f"Проверьте настройки подключения.",
                reply_markup=admin_main_menu()
            )
        
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка добавления сервера: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "🔧 Ручная установка WG")
async def admin_manual_wg_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Нет активных серверов")
        return
    
    text = "🔧 <b>Ручная установка WireGuard через Git</b>\n\n"
    text += "Доступные серверы:\n"
    for server_id, name in servers:
        text += f"ID: {server_id} - {name}\n"
    
    text += "\nВведите ID сервера для установки:"
    
    await state.set_state(AdminManualWGStates.waiting_for_server)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminManualWGStates.waiting_for_server)
async def process_manual_wg_server(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    try:
        server_id = int(message.text)
        
        # Проверяем существование сервера
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Сервер не найден")
                return
            
            server_name = server[0]
        
        await state.update_data(server_id=server_id, server_name=server_name)
        await state.set_state(AdminManualWGStates.waiting_for_git_repo)
        
        await message.answer(
            f"🔧 <b>Ручная установка на {server_name}</b>\n\n"
            f"Введите URL Git репозитория WireGuard (или оставьте пустым для стандартного):\n"
            f"Пример: https://git.zx2c4.com/wireguard-linux-compat",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await message.answer("Введите числовой ID сервера:")

@dp.message(AdminManualWGStates.waiting_for_git_repo)
async def process_git_repo(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    server_id = data['server_id']
    server_name = data['server_name']
    
    git_repo = message.text.strip()
    if not git_repo:
        git_repo = "https://git.zx2c4.com/wireguard-linux-compat"
    
    # Запускаем установку
    success = await setup_wireguard_via_git(server_id, git_repo, message)
    
    if success:
        # Обновляем статус в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET wireguard_configured = TRUE WHERE id = ?",
                (server_id,)
            )
            await db.commit()
        
        await message.answer(
            f"✅ WireGuard успешно установлен на {server_name}!\n\n"
            f"Сервер готов к созданию VPN подключений.",
            reply_markup=admin_main_menu()
        )
    else:
        await message.answer(
            f"❌ Не удалось установить WireGuard на {server_name}\n\n"
            f"Проверьте логи и права доступа.",
            reply_markup=admin_main_menu()
        )
    
    await state.clear()

@dp.message(F.text == "👤 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    await state.clear()
    await message.answer("👤 <b>Управление пользователями</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 Выдать VPN")
async def admin_gift_vpn_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.set_state(AdminUserStates.waiting_for_username)
    await message.answer("Введите username пользователя (с @ или без) или user_id:", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👤 <b>Управление пользователями</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)
        return
    
    username = message.text.replace('@', '').strip()
    await state.update_data(username=username)
    await state.set_state(AdminUserStates.waiting_for_period)
    
    prices = await get_vpn_prices()
    
    text = "Выберите период:\n"
    text += "1. 3 дня (пробный)\n"
    text += f"2. 7 дней ({prices['week']['stars']} Stars)\n"
    text += f"3. 30 дней ({prices['month']['stars']} Stars)\n\n"
    text += "Введите номер:"
    
    await message.answer(text, reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_period)
async def process_gift_period(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👤 <b>Управление пользователями</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    username = data['username']
    
    period_map = {"1": 3, "2": 7, "3": 30}
    if message.text not in period_map:
        await message.answer("Неверный номер. Введите 1, 2 или 3:")
        return
    
    days = period_map[message.text]
    
    try:
        # Определяем user_id (если username это число, считаем это user_id)
        user_id = 0
        if username.isdigit():
            user_id = int(username)
            username_to_save = f"id_{username}"
        else:
            username_to_save = username
        
        # Находим доступный сервер с WireGuard
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, name, current_users, max_users 
                FROM servers 
                WHERE wireguard_configured = TRUE 
                AND is_active = TRUE
                AND current_users < max_users
                LIMIT 1
            """)
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов с настроенным WireGuard")
                return
            
            server_id, server_name, current_users, max_users = server
            
            # Создаем клиента
            client_name = f"client_{user_id if user_id > 0 else username_to_save}_{random.randint(1000, 9999)}"
            subscription_end = (datetime.now() + timedelta(days=days)).isoformat()
            
            # Сохраняем пользователя
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, ?, TRUE)
            """, (user_id, username_to_save, server_id, client_name, subscription_end, days == 3))
            
            # Обновляем счетчик пользователей на сервере
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
        
        await state.clear()
        await message.answer(
            f"✅ VPN успешно выдан!\n\n"
            f"👤 Пользователь: @{username}\n"
            f"📅 Период: {days} дней\n"
            f"🖥️ Сервер: {server_name}\n"
            f"👥 Место: {current_users + 1}/{max_users}\n"
            f"🔑 Имя клиента: {client_name}\n\n"
            f"Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}",
            reply_markup=admin_main_menu()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.clear()
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.client_name, v.subscription_end, 
                       v.is_active, s.name as server_name
                FROM vpn_users v
                LEFT JOIN servers s ON v.server_id = s.id
                ORDER BY v.subscription_end DESC 
                LIMIT 50
            """)
            users = await cursor.fetchall()
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not users:
        await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu())
        return
    
    text = "📋 <b>Список пользователей VPN:</b>\n\n"
    for i, user in enumerate(users[:20], 1):
        user_id, tg_id, username, client_name, sub_end, active, server_name = user
        status = "🟢" if active else "🔴"
        username_display = f"@{username}" if username else f"ID:{tg_id}"
        
        if sub_end:
            sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m')
            days_left = (datetime.fromisoformat(sub_end) - datetime.now()).days
            text += f"{i}. {status} {username_display}"
            if client_name:
                text += f" [{client_name}]"
            text += f"\n   📅 до {sub_date} ({days_left}д) | 🖥️ {server_name or 'N/A'}\n"
        else:
            text += f"{i}. {status} {username_display}\n   📅 нет подписки\n"
    
    if len(users) > 20:
        text += f"\n... и еще {len(users) - 20} пользователей"
    
    text += "\n\nДля отключения VPN введите номер пользователя из списка:"
    
    await state.set_state(AdminRemoveVPNStates.waiting_for_user)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminRemoveVPNStates.waiting_for_user)
async def process_remove_vpn_user(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👤 <b>Управление пользователей</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)
        return
    
    try:
        user_num = int(message.text) - 1
        
        # Получаем список пользователей
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT v.id, v.user_id, v.username, v.client_name, v.server_id
                FROM vpn_users v
                WHERE v.is_active = TRUE
                ORDER BY v.subscription_end DESC 
                LIMIT 50
            """)
            users = await cursor.fetchall()
        
        if user_num < 0 or user_num >= len(users):
            await message.answer("❌ Неверный номер пользователя")
            return
        
        user_id, tg_id, username, client_name, server_id = users[user_num]
        
        # Удаляем с сервера
        if client_name and server_id:
            success = await remove_vpn_user_from_server(server_id, client_name, message)
        else:
            success = True
        
        # Деактивируем в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE vpn_users SET is_active = FALSE WHERE id = ?",
                (user_id,)
            )
            await db.commit()
        
        await state.clear()
        await message.answer(
            f"✅ VPN отключен для пользователя @{username}!\n\n"
            f"Пользователь деактивирован в системе.",
            reply_markup=admin_main_menu()
        )
        
    except ValueError:
        await message.answer("Введите номер пользователя из списка:")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())
        await state.clear()

@dp.message(F.text == "🚫 Отключить VPN")
async def admin_disable_vpn_start(message: Message, state: FSMContext):
    await admin_list_users(message, state)

@dp.message(F.text == "💰 Цены")
async def admin_prices(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    await state.clear()
    prices = await get_vpn_prices()
    
    text = "💰 <b>Текущие цены:</b>\n\n"
    text += f"💎 Неделя: {prices['week']['stars']} Stars\n"
    text += f"💎 Месяц: {prices['month']['stars']} Stars\n\n"
    text += "Введите новую цену за неделю (в Stars):"
    
    await state.set_state(AdminPriceStates.waiting_for_week_price)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminPriceStates.waiting_for_week_price)
async def process_week_price(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
        return
    
    try:
        week_price = int(message.text)
        if week_price < 10 or week_price > 1000:
            await message.answer("Цена должна быть от 10 до 1000 Stars. Введите снова:")
            return
        
        # Месяц = неделя * 3
        month_price = week_price * 3
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE prices SET week_price = ?, month_price = ? WHERE id = 1", (week_price, month_price))
            await db.commit()
        
        await state.clear()
        await message.answer(f"✅ Цены обновлены!\n\nНеделя: {week_price} Stars\nМесяц: {month_price} Stars", 
                           reply_markup=admin_main_menu())
    except ValueError:
        await message.answer("Введите число (например: 50):")

@dp.message(F.text == "🤖 Тест сервера")
async def admin_test_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM servers WHERE is_active = TRUE LIMIT 10")
            servers = await cursor.fetchall()
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Нет активных серверов для тестирования")
        return
    
    text = "🤖 <b>Тест сервера ботом</b>\n\n"
    text += "Доступные серверы:\n"
    for server_id, name in servers:
        text += f"ID: {server_id} - {name}\n"
    
    text += "\nВведите ID сервера для теста:"
    
    await state.set_state(AdminTestBotStates.waiting_for_server)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

@dp.message(AdminTestBotStates.waiting_for_server)
async def process_test_server_id(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
        return
    
    try:
        server_id = int(message.text)
        await state.update_data(server_id=server_id)
        await state.set_state(AdminTestBotStates.waiting_for_token)
        await message.answer("Введите токен бота для тестирования:", reply_markup=back_keyboard())
    except:
        await message.answer("Введите числовой ID сервера:")

@dp.message(AdminTestBotStates.waiting_for_token)
async def process_test_bot_token(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_main_menu(), parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    await state.clear()
    
    # Простая проверка SSH
    stdout, stderr, success = await execute_ssh_command(server_id, "echo 'Test' && whoami && uname -a", timeout=30)
    
    if success:
        await message.answer(f"✅ SSH подключение работает!\n\n{stdout}", reply_markup=admin_main_menu())
    else:
        await message.answer(f"❌ SSH ошибка: {stderr}", reply_markup=admin_main_menu())

# Обработчики действий с сервером (ID из текста)
@dp.message(F.text.contains("Установить WG (ID:"))
async def handle_install_wg(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    match = re.search(r'ID:\s*(\d+)', message.text)
    if not match:
        return
    
    server_id = int(match.group(1))
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server:
                await message.answer("❌ Сервер не найден")
                return
            server_name = server[0]
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    await message.answer(f"🔄 Устанавливаю WireGuard на {server_name}...")
    
    # Команды для установки WireGuard
    commands = [
        "apt-get update -y",
        "apt-get install -y wireguard wireguard-tools 2>&1 || apt-get install -y wireguard 2>&1",
        "systemctl enable wg-quick@wg0 2>/dev/null || true",
        "modprobe wireguard 2>/dev/null || true"
    ]
    
    all_success = True
    for cmd in commands:
        stdout, stderr, success = await execute_ssh_command(server_id, cmd, timeout=120, use_sudo=True)
        
        if not success:
            await message.answer(f"⚠️ Ошибка: {stderr[:200]}")
            all_success = False
    
    if all_success:
        # Помечаем как настроенный
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET wireguard_configured = TRUE WHERE id = ?",
                (server_id,)
            )
            await db.commit()
        
        await message.answer(f"✅ WireGuard установлен на {server_name}!", reply_markup=admin_main_menu())
    else:
        await message.answer(
            f"⚠️ Установка WireGuard на {server_name} завершена с ошибками.\n"
            f"Попробуйте ручную установку через Git.",
            reply_markup=admin_main_menu()
        )

@dp.message(F.text.contains("Проверить SSH (ID:"))
async def handle_check_ssh(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    match = re.search(r'ID:\s*(\d+)', message.text)
    if not match:
        return
    
    server_id = int(match.group(1))
    
    # Получаем информацию о сервере
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
            server = await cursor.fetchone()
            if not server:
                await message.answer("❌ Сервер не найден")
                return
            server_name = server[0]
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    await message.answer(f"🔍 Проверяю SSH подключение к {server_name}...")
    stdout, stderr, success = await execute_ssh_command(server_id, "echo 'SSH Test OK' && whoami && uname -a && date")
    
    if success:
        lines = stdout.strip().split('\n')
        response = f"✅ SSH работает!\n\n"
        if len(lines) > 0:
            response += f"{lines[0]}\n"
        if len(lines) > 1:
            response += f"Пользователь: {lines[1]}\n"
        if len(lines) > 2:
            response += f"Система: {lines[2]}\n"
        if len(lines) > 3:
            response += f"Дата: {lines[3]}\n"
        
        await message.answer(response, reply_markup=admin_main_menu())
    else:
        await message.answer(f"❌ SSH ошибка: {stderr}", reply_markup=admin_main_menu())

@dp.message(F.text == "◀️ Назад к списку")
async def back_to_server_list(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    await state.clear()
    prices = await get_vpn_prices()
    
    text = "🔐 <b>Получить VPN доступ</b>\n\n"
    text += "🎁 <b>3 дня бесплатно</b> - пробный период\n"
    text += f"💎 <b>7 дней</b> - {prices['week']['stars']} Stars\n"
    text += f"💎 <b>30 дней</b> - {prices['month']['stars']} Stars\n\n"
    text += "Выберите вариант:"
    
    buttons = [
        [types.KeyboardButton(text="🎁 3 дня (пробный)")],
        [types.KeyboardButton(text="💎 Неделя")],
        [types.KeyboardButton(text="💎 Месяц")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    keyboard = types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎁 3 дня (пробный)")
async def get_trial_vpn(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем, использовал ли уже пробный период
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            
            if user and user[0]:  # trial_used = TRUE
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
                return
            
            # Находим доступный сервер с WireGuard
            cursor = await db.execute("""
                SELECT id, name, current_users, max_users 
                FROM servers 
                WHERE wireguard_configured = TRUE 
                AND is_active = TRUE
                AND current_users < max_users
                LIMIT 1
            """)
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов. Обратитесь в поддержку.", reply_markup=user_main_menu())
                return
            
            server_id, server_name, current_users, max_users = server
            
            # Создаем клиента
            client_name = f"client_{user_id}_{random.randint(1000, 9999)}"
            subscription_end = (datetime.now() + timedelta(days=3)).isoformat()
            
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, client_name, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, ?, TRUE, TRUE)
            """, (user_id, username, server_id, client_name, subscription_end))
            
            # Обновляем счетчик пользователей
            await db.execute(
                "UPDATE servers SET current_users = current_users + 1 WHERE id = ?",
                (server_id,)
            )
            
            await db.commit()
        
        await message.answer(
            f"✅ <b>Пробный период активирован!</b>\n\n"
            f"👤 Ваш ID: {user_id}\n"
            f"🖥️ Сервер: {server_name}\n"
            f"👥 Место: {current_users + 1}/{max_users}\n"
            f"📅 Действует до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}\n\n"
            f"🔑 Имя клиента: {client_name}\n\n"
            f"Для получения конфигурации обратитесь в поддержку: {SUPPORT_USERNAME}",
            reply_markup=user_main_menu(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=user_main_menu())

@dp.message(F.text == "📱 Мои услуги")
async def my_services(message: Message):
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT subscription_end, is_active, client_name 
                FROM vpn_users 
                WHERE user_id = ? 
                ORDER BY subscription_end DESC 
                LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active, client_name = user
        
        if not is_active:
            await message.answer("❌ Ваша подписка не активна.", reply_markup=user_main_menu())
            return
        
        if sub_end:
            end_date = datetime.fromisoformat(sub_end)
            now = datetime.now()
            
            if end_date < now:
                status = "🔴 Истекла"
            else:
                days_left = (end_date - now).days
                status = f"🟢 Активна ({days_left} дней осталось)"
            
            text = f"📱 <b>Ваша подписка VPN</b>\n\n"
            text += f"Статус: {status}\n"
            text += f"Действует до: {end_date.strftime('%d.%m.%Y %H:%M')}\n"
            if client_name:
                text += f"🔑 Имя клиента: {client_name}\n"
            text += f"\nДля настройки VPN обратитесь в поддержку: {SUPPORT_USERNAME}"
        else:
            text = "📭 Нет информации о подписке"
        
        await message.answer(text, reply_markup=user_main_menu(), parse_mode=ParseMode.HTML)
    except:
        await message.answer("❌ Ошибка получения данных", reply_markup=user_main_menu())

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        f"🆘 <b>Помощь и поддержка</b>\n\n"
        f"По всем вопросам обращайтесь: {SUPPORT_USERNAME}\n\n"
        f"Мы всегда готовы помочь!",
        reply_markup=user_main_menu(),
        parse_mode=ParseMode.HTML
    )

# ========== ЗАПУСК ==========
async def main():
    print("🚀 ЗАПУСК VPN HOSTING БОТА")
    
    if not await init_database():
        logger.critical("❌ Не удалось инициализировать базу данных!")
        return
    
    me = await bot.get_me()
    print(f"✅ Бот запущен: @{me.username}")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"💬 Admin Chat ID: {ADMIN_CHAT_ID}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.critical(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)