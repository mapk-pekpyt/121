# main.py - VPN МЕНЕДЖЕР С ОПЛАТОЙ TELEGRAM STARS
import os
import asyncio
import logging
import json
import random
import string
import qrcode
import io
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
PROVIDER_TOKEN = "5775769170:LIVE:TG_ADz_HW287D54Wfd3pqBi_BQA"  # Твой провайдер токен
DB_PATH = "/data/database.db" if os.path.exists("/data") else "database.db"

# Цены в Stars (1 Star = ~0.01€)
PRICES = {
    "trial": {"days": 3, "price": 0, "stars": 0},
    "week": {"days": 7, "price": 5, "stars": 50},    # 50 stars = 5€
    "month": {"days": 30, "price": 12, "stars": 120} # 120 stars = 12€
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ ==========
async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Серверы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ssh_key TEXT NOT NULL,
                connection_string TEXT NOT NULL,
                vpn_type TEXT DEFAULT 'wireguard',
                country TEXT,
                city TEXT,
                max_users INTEGER DEFAULT 30,
                current_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                server_ip TEXT,
                public_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Пользователи VPN
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vpn_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                server_id INTEGER,
                vpn_type TEXT,
                device_type TEXT,
                client_name TEXT,
                private_key TEXT,
                public_key TEXT,
                address TEXT,
                subscription_end TIMESTAMP,
                trial_used BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                notified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE SET NULL
            )
        """)
        
        # Платежи (Stars)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_stars INTEGER,
                period TEXT,
                status TEXT DEFAULT 'pending',
                telegram_payment_id TEXT,
                invoice_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Запросы на пробный период
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'pending',
                approved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Настройки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Инициализируем настройки
        default_settings = [
            ("prices", json.dumps(PRICES)),
            ("welcome_message", "Добро пожаловать в VPN бот! 🔐"),
            ("admin_contact", "@ваш_юзернейм")
        ]
        
        for key, value in default_settings:
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        
        await db.commit()

# ========== FSM СОСТОЯНИЯ ==========
class AddServerStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_name = State()
    waiting_for_country = State()
    waiting_for_city = State()
    waiting_for_key = State()
    waiting_for_connection = State()

class UserBuyStates(StatesGroup):
    waiting_for_period = State()
    waiting_for_device = State()

class AdminAddUserStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_device = State()
    waiting_for_period = State()
    waiting_for_server = State()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def get_setting(key: str) -> str:
    """Получает настройку из БД"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else ""

def get_prices_from_message(text: str) -> tuple:
    """Получает период и цену из текста кнопки"""
    if "Неделя" in text:
        return "week", 50  # 50 stars
    elif "Месяц" in text:
        return "month", 120  # 120 stars
    return "week", 50

def period_keyboard(show_trial: bool = True):
    """Клавиатура выбора периода"""
    buttons = []
    
    if show_trial:
        buttons.append([types.KeyboardButton(text="🎁 3 дня (пробный)")])
    
    buttons.append([types.KeyboardButton(text="💎 Неделя - 50 stars")])
    buttons.append([types.KeyboardButton(text="💎 Месяц - 120 stars")])
    buttons.append([types.KeyboardButton(text="◀️ Назад")])
    
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def vpn_device_keyboard():
    buttons = [
        [types.KeyboardButton(text="📱 Android")],
        [types.KeyboardButton(text="🍎 iOS")],
        [types.KeyboardButton(text="💻 WireGuard (все устройства)")],
        [types.KeyboardButton(text="◀️ Назад")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def main_menu(is_admin: bool = True):
    buttons = []
    if is_admin:
        buttons = [
            [types.KeyboardButton(text="📋 Мои серверы")],
            [types.KeyboardButton(text="➕ Добавить сервер")],
            [types.KeyboardButton(text="👥 Пользователи VPN")],
            [types.KeyboardButton(text="💰 Платежи")],
            [types.KeyboardButton(text="⚙️ Настройки")]
        ]
    else:
        buttons = [
            [types.KeyboardButton(text="🔐 Получить VPN")],
            [types.KeyboardButton(text="📱 Мои подключения")],
            [types.KeyboardButton(text="💎 Купить подписку")],
            [types.KeyboardButton(text="🆘 Помощь")]
        ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    
    # Сохраняем пользователя
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO vpn_users (user_id, username, first_name)
            VALUES (?, ?, ?)""",
            (user_id, message.from_user.username, message.from_user.first_name)
        )
        await db.commit()
    
    if is_admin:
        await message.answer(
            "👑 Админ-панель VPN менеджера",
            reply_markup=main_menu(is_admin=True)
        )
    else:
        welcome = await get_setting("welcome_message")
        await message.answer(
            f"{welcome}\n\nВыберите действие:",
            reply_markup=main_menu(is_admin=False)
        )

@dp.message(F.text == "🔐 Получить VPN")
async def get_vpn_start(message: Message, state: FSMContext):
    """Начало получения VPN"""
    user_id = message.from_user.id
    
    # Проверяем использовал ли пробный
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT trial_used FROM vpn_users WHERE user_id = ?",
            (user_id,)
        )
        user_data = await cursor.fetchone()
    
    has_used_trial = user_data and user_data[0]
    
    if has_used_trial:
        # Прямо к покупке
        await message.answer(
            "Вы уже использовали пробный период.\nВыберите период подписки:",
            reply_markup=period_keyboard(show_trial=False)
        )
        await state.set_state(UserBuyStates.waiting_for_period)
    else:
        # Предлагаем выбор
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎁 Получить пробный период", callback_data="request_trial")],
            [types.InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_now")]
        ])
        await message.answer("Выберите вариант:", reply_markup=keyboard)

@dp.callback_query(F.data == "request_trial")
async def request_trial(callback: types.CallbackQuery):
    """Запрос пробного периода"""
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем не запрашивал ли уже
        cursor = await db.execute(
            "SELECT id FROM trial_requests WHERE user_id = ?",
            (user_id,)
        )
        existing = await cursor.fetchone()
        
        if existing:
            await callback.answer("Вы уже отправили запрос!")
            return
        
        # Создаем запрос
        await db.execute(
            """INSERT INTO trial_requests (user_id, username, first_name)
            VALUES (?, ?, ?)""",
            (user_id, callback.from_user.username, callback.from_user.first_name)
        )
        await db.commit()
    
    admin_contact = await get_setting("admin_contact")
    
    await callback.message.edit_text(
        f"✅ Запрос отправлен!\n\nАдминистратор {admin_contact} свяжется с вами."
    )
    
    # Уведомляем админа
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"approve_trial:{user_id}"
            ),
            types.InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_trial:{user_id}"
            )
        ]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"🆕 Запрос на пробный период!\n\n"
        f"Пользователь: @{callback.from_user.username or 'нет'}\n"
        f"ID: {user_id}",
        reply_markup=keyboard
    )
    
    await callback.answer()

@dp.callback_query(F.data == "buy_now")
async def buy_now(callback: types.CallbackQuery, state: FSMContext):
    """Начало покупки"""
    await callback.message.edit_text("Выберите период подписки:")
    await callback.message.answer(
        "Выберите период:",
        reply_markup=period_keyboard(show_trial=False)
    )
    await state.set_state(UserBuyStates.waiting_for_period)
    await callback.answer()

@dp.message(UserBuyStates.waiting_for_period)
async def process_period(message: Message, state: FSMContext):
    """Обработка выбора периода"""
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_menu(False))
        return
    
    if "🎁" in message.text:
        # Пробный период
        await request_trial_direct(message, state)
        return
    
    # Определяем период и цену
    period, stars = get_prices_from_message(message.text)
    
    await state.update_data(period=period, stars=stars)
    await state.set_state(UserBuyStates.waiting_for_device)
    
    period_days = PRICES.get(period, PRICES["week"])["days"]
    
    await message.answer(
        f"Вы выбрали подписку на {period_days} дней за {stars} stars.\n\n"
        "Теперь выберите устройство:",
        reply_markup=vpn_device_keyboard()
    )

async def request_trial_direct(message: Message, state: FSMContext):
    """Прямой запрос пробного периода"""
    user_id = message.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM trial_requests WHERE user_id = ?",
            (user_id,)
        )
        existing = await cursor.fetchone()
        
        if existing:
            await message.answer("Вы уже отправили запрос на пробный период!")
            await state.clear()
            return
        
        await db.execute(
            """INSERT INTO trial_requests (user_id, username, first_name)
            VALUES (?, ?, ?)""",
            (user_id, message.from_user.username, message.from_user.first_name)
        )
        await db.commit()
    
    admin_contact = await get_setting("admin_contact")
    await message.answer(
        f"✅ Запрос на пробный период отправлен!\n\n"
        f"Администратор {admin_contact} свяжется с вами."
    )
    
    # Уведомляем админа
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"approve_trial:{user_id}"
            )
        ]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"🆕 Запрос на пробный период!\n\n"
        f"Пользователь: @{message.from_user.username or 'нет'}\n"
        f"ID: {user_id}",
        reply_markup=keyboard
    )
    
    await state.clear()

@dp.message(UserBuyStates.waiting_for_device)
async def process_device(message: Message, state: FSMContext):
    """Обработка выбора устройства и создание инвойса"""
    if message.text == "◀️ Назад":
        await state.set_state(UserBuyStates.waiting_for_period)
        await message.answer("Выберите период:", reply_markup=period_keyboard(show_trial=False))
        return
    
    device_map = {
        "📱 Android": "android",
        "🍎 iOS": "ios",
        "💻 WireGuard (все устройства)": "wireguard"
    }
    
    if message.text not in device_map:
        await message.answer("Выберите устройство из списка:")
        return
    
    device_type = device_map[message.text]
    data = await state.get_data()
    
    # Создаем инвойс для оплаты Stars
    period = data.get('period', 'week')
    stars = data.get('stars', 50)
    period_days = PRICES.get(period, PRICES["week"])["days"]
    
    # Создаем payload для идентификации
    payload = f"{message.from_user.id}:{period}:{device_type}:{int(datetime.now().timestamp())}"
    
    try:
        # Создаем инвойс
        prices = [LabeledPrice(label=f"VPN на {period_days} дней", amount=stars * 100)]  # В центах
        
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=f"VPN подписка на {period_days} дней",
            description=f"Доступ к VPN серверам на {period_days} дней",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars
            prices=prices,
            start_parameter="vpn_subscription",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        
        # Сохраняем платеж в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO payments (user_id, amount_stars, period, invoice_payload)
                VALUES (?, ?, ?, ?)""",
                (message.from_user.id, stars, period, payload)
            )
            await db.commit()
        
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        await message.answer(
            "❌ Ошибка создания счета. Попробуйте позже или свяжитесь с администратором."
        )

# ========== ОБРАБОТКА ПЛАТЕЖЕЙ STARS ==========
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    """Обработка pre-checkout запроса"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Обработка успешного платежа Stars"""
    payment = message.successful_payment
    user_id = message.from_user.id
    
    logger.info(f"Успешный платеж: {payment.total_amount} stars от {user_id}")
    
    # Парсим payload
    try:
        payload_parts = payment.invoice_payload.split(':')
        if len(payload_parts) >= 4:
            original_user_id = int(payload_parts[0])
            period = payload_parts[1]
            device_type = payload_parts[2]
        else:
            original_user_id = user_id
            period = "week"
            device_type = "wireguard"
    except:
        original_user_id = user_id
        period = "week"
        device_type = "wireguard"
    
    # Обновляем статус платежа
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE payments 
            SET status = 'completed', 
                telegram_payment_id = ?
            WHERE invoice_payload = ? AND status = 'pending'""",
            (payment.telegram_payment_charge_id, payment.invoice_payload)
        )
        await db.commit()
    
    # Получаем информацию о периоде
    period_days = PRICES.get(period, PRICES["week"])["days"]
    
    await message.answer(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"Спасибо за покупку! {payment.total_amount // 100} stars успешно списаны.\n"
        f"Ваша подписка активирована на {period_days} дней.\n\n"
        f"Администратор скоро добавит вас на VPN сервер и отправит конфигурацию.",
        parse_mode=ParseMode.HTML
    )
    
    # Уведомляем админа
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="🚀 Активировать VPN",
                callback_data=f"activate_paid:{user_id}:{period}:{device_type}"
            )
        ]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"💎 <b>Успешный платеж Stars!</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or 'нет'}\n"
        f"🆔 ID: {user_id}\n"
        f"💰 Сумма: {payment.total_amount // 100} stars\n"
        f"📅 Период: {period} ({period_days} дней)\n"
        f"📱 Устройство: {device_type}\n\n"
        f"Нажмите кнопку чтобы активировать VPN:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

# ========== АДМИН ФУНКЦИИ ==========
@dp.callback_query(F.data.startswith("approve_trial:"))
async def approve_trial(callback: types.CallbackQuery):
    """Одобрение пробного периода админом"""
    user_id = int(callback.data.split(":")[1])
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Обновляем запрос
        await db.execute(
            """UPDATE trial_requests 
            SET status = 'approved', 
                approved_at = datetime('now')
            WHERE user_id = ?""",
            (user_id,)
        )
        # Помечаем пробный как использованный
        await db.execute(
            "UPDATE vpn_users SET trial_used = 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
    
    await callback.message.edit_text(f"✅ Пробный период для {user_id} одобрен!")
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_id,
            "🎉 Ваш запрос на пробный период одобрен!\n"
            "Администратор скоро добавит вас на VPN сервер."
        )
    except:
        pass
    
    await callback.answer()

@dp.callback_query(F.data.startswith("activate_paid:"))
async def activate_paid_user(callback: types.CallbackQuery):
    """Активация VPN для оплатившего пользователя"""
    parts = callback.data.split(":")
    user_id = int(parts[1])
    period = parts[2]
    device_type = parts[3] if len(parts) > 3 else "wireguard"
    
    period_days = PRICES.get(period, PRICES["week"])["days"]
    
    await callback.message.edit_text(
        f"🔄 Активирую VPN для пользователя {user_id}...\n"
        f"Период: {period_days} дней\n"
        f"Устройство: {device_type}"
    )
    
    # Здесь будет логика добавления на сервер и отправки конфига
    # Пока просто уведомляем
    try:
        await bot.send_message(
            user_id,
            f"✅ Ваш VPN доступ активирован!\n\n"
            f"Подписка: {period_days} дней\n"
            f"Администратор скоро отправит вам конфигурацию."
        )
    except:
        pass
    
    await callback.answer()

@dp.message(F.text == "📋 Мои серверы")
async def list_servers(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Главное меню:", reply_markup=main_menu(False))
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM servers")
        servers = await cursor.fetchall()
    
    if not servers:
        await message.answer("Серверы не добавлены")
        return
    
    text = "📋 Ваши серверы:\n\n"
    for server in servers:
        text += f"🖥️ {server[1]}\n"
        text += f"   Тип: {server[4]}\n"
        text += f"   Пользователи: {server[8]}/{server[7]}\n\n"
    
    await message.answer(text)

@dp.message(F.text == "💰 Платежи")
async def show_payments(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT COUNT(*), SUM(amount_stars) 
            FROM payments WHERE status = 'completed'
        """)
        stats = await cursor.fetchone()
        
        cursor = await db.execute("""
            SELECT user_id, amount_stars, period, created_at 
            FROM payments WHERE status = 'completed'
            ORDER BY created_at DESC LIMIT 10
        """)
        recent = await cursor.fetchall()
    
    text = f"💰 Статистика платежей:\n\n"
    text += f"Всего платежей: {stats[0] or 0}\n"
    text += f"Всего stars: {stats[1] or 0}\n\n"
    text += "Последние платежи:\n"
    
    for payment in recent:
        date = datetime.fromisoformat(payment[3]).strftime("%d.%m %H:%M")
        text += f"• {payment[0]}: {payment[1]} stars ({payment[2]}) - {date}\n"
    
    await message.answer(text)

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_subscriptions():
    """Проверка истекающих подписок"""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Находим истекшие подписки
                cursor = await db.execute("""
                    SELECT user_id, username, subscription_end
                    FROM vpn_users 
                    WHERE is_active = 1 
                    AND subscription_end < datetime('now')
                """)
                expired = await cursor.fetchall()
                
                for user in expired:
                    user_id = user[0]
                    await db.execute(
                        "UPDATE vpn_users SET is_active = 0 WHERE user_id = ?",
                        (user_id,)
                    )
                    
                    try:
                        await bot.send_message(
                            user_id,
                            "⏰ Ваша VPN подписка истекла!\nДля продления нажмите /start"
                        )
                    except:
                        pass
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Ошибка проверки подписок: {e}")
        
        await asyncio.sleep(3600)  # Каждый час

# ========== ЗАПУСК ==========
async def main():
    await init_db()
    
    # Запускаем проверку подписок
    asyncio.create_task(check_subscriptions())
    
    me = await bot.get_me()
    logger.info(f"✅ Бот запущен: @{me.username}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"💎 Provider token: {PROVIDER_TOKEN}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())