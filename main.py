# main.py - ИСПРАВЛЕННАЯ ВЕРСИЯ
import os
import asyncio
import logging
import sys
import random
import qrcode
import io
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
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
                    config_data TEXT,
                    subscription_end TIMESTAMP,
                    trial_used BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def admin_users_menu():
    buttons = [
        [types.KeyboardButton(text="🎁 Выдать VPN")],
        [types.KeyboardButton(text="📋 Список пользователей")],
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
            cursor = await db.execute("SELECT id, name, is_active, wireguard_configured FROM servers ORDER BY name")
            servers = await cursor.fetchall()
    except Exception as e:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет", reply_markup=servers_menu())
        return
    
    text = "📋 <b>Список серверов:</b>\n\n"
    for server in servers:
        server_id, name, active, wg_configured = server
        status = "🟢" if active else "🔴"
        wg_status = "🔐" if wg_configured else "❌"
        text += f"{status}{wg_status} <b>{name}</b> (ID: {server_id})\n"
    
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
    await message.answer("Отправьте приватный SSH ключ (в формате PEM):", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_key)
async def process_ssh_key(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    await state.update_data(ssh_key=message.text)
    await state.set_state(AdminAddServerStates.waiting_for_connection)
    await message.answer("Введите строку подключения (user@host:port):", reply_markup=back_keyboard())

@dp.message(AdminAddServerStates.waiting_for_connection)
async def process_connection_string(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("🖥️ <b>Управление серверами</b>", reply_markup=servers_menu(), parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO servers (name, ssh_key, connection_string) VALUES (?, ?, ?)",
                (data['server_name'], data['ssh_key'], message.text)
            )
            await db.commit()
        
        await state.clear()
        await message.answer(f"✅ Сервер '{data['server_name']}' добавлен!", reply_markup=admin_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}", reply_markup=admin_main_menu())

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
    await message.answer("Введите username пользователя (с @ или без):", reply_markup=back_keyboard())

@dp.message(AdminUserStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("👤 <b>Управление пользователями</b>", reply_markup=admin_users_menu(), parse_mode=ParseMode.HTML)
        return
    
    username = message.text.replace('@', '')
    await state.update_data(username=username)
    await state.set_state(AdminUserStates.waiting_for_period)
    await message.answer("Выберите период:\n1. 3 дня (пробный)\n2. 7 дней\n3. 30 дней\n\nВведите номер:", reply_markup=back_keyboard())

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
    await state.clear()
    await message.answer(f"✅ Пользователю @{username} выдано VPN на {days} дней!", reply_markup=admin_main_menu())

@dp.message(F.text == "📋 Список пользователей")
async def admin_list_users(message: Message):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id, username, subscription_end, is_active 
                FROM vpn_users 
                ORDER BY subscription_end DESC 
                LIMIT 50
            """)
            users = await cursor.fetchall()
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not users:
        await message.answer("📭 Пользователей нет", reply_markup=admin_users_menu())
        return
    
    text = "📋 <b>Последние пользователи:</b>\n\n"
    for user in users:
        user_id, username, sub_end, active = user
        status = "🟢" if active else "🔴"
        username_display = f"@{username}" if username else f"ID:{user_id}"
        
        if sub_end:
            sub_date = datetime.fromisoformat(sub_end).strftime('%d.%m.%Y')
            text += f"{status} {username_display} - до {sub_date}\n"
        else:
            text += f"{status} {username_display} - нет подписки\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_users_menu())

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
            cursor = await db.execute("SELECT id, name FROM servers LIMIT 10")
            servers = await cursor.fetchall()
    except:
        await message.answer("❌ Ошибка получения данных")
        return
    
    if not servers:
        await message.answer("📭 Серверов нет для тестирования")
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
    stdout, stderr, success = await execute_ssh_command(server_id, "echo 'Test' && whoami", timeout=30)
    
    if success:
        await message.answer(f"✅ SSH подключение работает!\n\nОтвет сервера:\n{stdout}", 
                           reply_markup=admin_main_menu())
    else:
        await message.answer(f"❌ SSH ошибка: {stderr}", reply_markup=admin_main_menu())

# Обработчики действий с сервером (ID из текста)
@dp.message(F.text.contains("ID:"))
async def handle_server_action(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.chat.id):
        return
    
    import re
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
    
    if "🔧 Установить WG" in message.text:
        await message.answer(f"🔄 Устанавливаю WireGuard на {server_name}...")
        stdout, stderr, success = await execute_ssh_command(
            server_id, 
            "apt-get update && apt-get install -y wireguard wireguard-tools 2>&1",
            timeout=180,
            use_sudo=True
        )
        
        if success:
            await message.answer(f"✅ WireGuard установлен на {server_name}!", reply_markup=admin_main_menu())
        else:
            await message.answer(f"❌ Ошибка установки: {stderr[:500]}", reply_markup=admin_main_menu())
    
    elif "🔍 Проверить SSH" in message.text:
        await message.answer(f"🔍 Проверяю SSH подключение к {server_name}...")
        stdout, stderr, success = await execute_ssh_command(server_id, "echo 'SSH Test OK' && uname -a")
        
        if success:
            await message.answer(f"✅ SSH работает!\n\n{stdout}", reply_markup=admin_main_menu())
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
    username = message.from_user.username
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем, использовал ли уже пробный период
            cursor = await db.execute("SELECT trial_used FROM vpn_users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            
            if user and user[0]:  # trial_used = TRUE
                await message.answer("❌ Вы уже использовали пробный период!", reply_markup=user_main_menu())
                return
            
            # Находим доступный сервер
            cursor = await db.execute("SELECT id FROM servers WHERE wireguard_configured = TRUE LIMIT 1")
            server = await cursor.fetchone()
            
            if not server:
                await message.answer("❌ Нет доступных серверов. Обратитесь в поддержку.", reply_markup=user_main_menu())
                return
            
            server_id = server[0]
            subscription_end = (datetime.now() + timedelta(days=3)).isoformat()
            
            await db.execute("""
                INSERT INTO vpn_users (user_id, username, server_id, subscription_end, trial_used, is_active)
                VALUES (?, ?, ?, ?, TRUE, TRUE)
            """, (user_id, username, server_id, subscription_end))
            
            await db.commit()
        
        await message.answer(
            f"✅ <b>Пробный период активирован!</b>\n\n"
            f"Доступ активен до: {datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Настройки VPN будут доступны в ближайшее время.",
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
                SELECT subscription_end, is_active 
                FROM vpn_users 
                WHERE user_id = ? 
                ORDER BY subscription_end DESC 
                LIMIT 1
            """, (user_id,))
            user = await cursor.fetchone()
        
        if not user:
            await message.answer("📭 У вас нет активных подписок.", reply_markup=user_main_menu())
            return
        
        sub_end, is_active = user
        
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
            
            text = f"📱 <b>Ваша подписка</b>\n\n"
            text += f"Статус: {status}\n"
            text += f"Действует до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            text += f"Для настройки VPN обратитесь в поддержку: {SUPPORT_USERNAME}"
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