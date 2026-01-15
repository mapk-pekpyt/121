import asyncio
import logging
from aiogram import Bot, Dispatcher
from core.bot import dp, bot
from handlers import admin, user, chat_monitor, personal, advertising
from config import CREATOR_ID

async def main():
    dp.include_routers(
        admin.router,
        user.router,
        chat_monitor.router,
        personal.router,
        advertising.router
    )
    await bot.send_message(CREATOR_ID, "✅ Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())