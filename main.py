#!/usr/bin/env python3
import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.append(str(Path(__file__).parent))

from core.bot import bot, dp, setup_dispatcher
from utils.logger import logger
from data.models import init_database, add_user
from services.memory import memory
from handlers.advertising import ad_scheduler

async def main():
    """Основная функция запуска"""
    # Инициализация БД
    init_database()
    
    # Настройка диспетчера
    dispatcher = setup_dispatcher()
    
    # Запуск фоновых задач
    asyncio.create_task(ad_scheduler(bot))
    
    # Запуск бота
    logger.info("Запускаем поллинг...")
    try:
        await dispatcher.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        memory.close()
        logger.info("Бот остановлен")

if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запуск
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        sys.exit(1)