import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import ChatPermissions
from config import DB_PATH

class Moderator:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def mute_user(self, chat_id: int, user_id: int, minutes: int, reason: str = ""):
        """Выдать мут"""
        until = datetime.now() + timedelta(minutes=minutes)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""INSERT INTO moderations (chat_id, user_id, type, expires, reason)
                     VALUES (?, ?, 'mute', ?, ?)""",
                  (chat_id, user_id, until.isoformat(), reason))
        conn.commit()
        conn.close()

        await self.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )

    async def warn_user(self, chat_id: int, user_id: int, reason: str = ""):
        """Выдать предупреждение"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Считаем варны
        c.execute("""SELECT COUNT(*) FROM moderations 
                     WHERE chat_id=? AND user_id=? AND type='warn'""",
                  (chat_id, user_id))
        warn_count = c.fetchone()[0] + 1
        c.execute("""INSERT INTO moderations (chat_id, user_id, type, reason)
                     VALUES (?, ?, 'warn', ?)""",
                  (chat_id, user_id, reason))
        # Если 3 варна — бан
        if warn_count >= 3:
            c.execute("""INSERT INTO moderations (chat_id, user_id, type, reason)
                         VALUES (?, ?, 'ban', '3 предупреждения')""",
                      (chat_id, user_id))
            await self.bot.ban_chat_member(chat_id, user_id)
        conn.commit()
        conn.close()
        return warn_count

    async def check_flood(self, chat_id: int, user_id: int, message_type: str = 'text'):
        """Проверка на флуд (5 стикеров/гиф подряд)"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT text, timestamp FROM chat_history 
                     WHERE chat_id=? AND user_id=? 
                     ORDER BY id DESC LIMIT 5""",
                  (chat_id, user_id))
        rows = c.fetchall()
        conn.close()
        if len(rows) < 5:
            return False
        # Проверяем, что последние 5 сообщений — стикеры/гифы (в тексте храним тип)
        if all('sticker' in row[0] or 'gif' in row[0] for row in rows):
            await self.mute_user(chat_id, user_id, 5, "Флуд стикерами/гифками")
            return True
        return False

    async def check_spam(self, chat_id: int, user_id: int, text: str):
        """Проверка на спам одинаковыми сообщениями"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT text FROM chat_history 
                     WHERE chat_id=? AND user_id=? AND text=? 
                     ORDER BY id DESC LIMIT 3""",
                  (chat_id, user_id, text))
        rows = c.fetchall()
        conn.close()
        if len(rows) >= 3:
            await self.mute_user(chat_id, user_id, 10, "Спам одинаковыми сообщениями")
            return True
        return False

    async def auto_moderate(self, chat_id: int, user_id: int, text: str, message_type: str):
        """Автомодерация: вызов всех проверок"""
        if await self.check_flood(chat_id, user_id, message_type):
            return True
        if await self.check_spam(chat_id, user_id, text):
            return True
        return False