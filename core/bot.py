import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from config import CREATOR_ID
from utils.logger import logger

# Получаем токен из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не установлен в переменных окружения")
    raise ValueError("Установите BOT_TOKEN в переменных окружения")

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode="HTML",
        link_preview_is_disabled=True,
        protect_content=False
    )
)

# Хранилище состояний
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

async def on_startup():
    """Действия при запуске бота"""
    logger.info("Бот запускается...")
    try:
        await bot.send_message(
            CREATOR_ID,
            "🤖 Бот запущен!\n"
            f"ID: {(await bot.me()).id}\n"
            f"Username: @{(await bot.me()).username}"
        )
        logger.info("Уведомление создателю отправлено")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление создателю: {e}")

async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("Бот останавливается...")
    await bot.session.close()
    logger.info("Сессия закрыта")

def setup_dispatcher():
    """Настройка диспетчера с роутерами"""
    from handlers import admin, user, chat_monitor, personal, advertising
    
    # Включаем все роутеры
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(chat_monitor.router)
    dp.include_router(personal.router)
    dp.include_router(advertising.router)
    
    # Регистрируем обработчики событий
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    logger.info("Диспетчер настроен")
    return dp