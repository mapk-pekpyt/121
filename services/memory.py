import sqlite3
import json
from datetime import datetime, timedelta
from config import DB_PATH

class Memory:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        """Создание таблиц, если их нет"""
        c = self.conn.cursor()
        # Контекстная память для чатов
        c.execute('''CREATE TABLE IF NOT EXISTS context_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, role, content)
        )''')
        # Персональные анкеты (расширенные)
        c.execute('''CREATE TABLE IF NOT EXISTS personal_profiles (
            user_id INTEGER PRIMARY KEY,
            data TEXT,  -- JSON с анкетой
            updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # Кэш прожарок
        c.execute('''CREATE TABLE IF NOT EXISTS roast_cache (
            target_id INTEGER,
            chat_id INTEGER,
            roast_text TEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(target_id, chat_id)
        )''')
        self.conn.commit()

    # === Работа с контекстом ===
    def add_context(self, chat_id: int, role: str, content: str):
        """Добавить сообщение в контекст"""
        c = self.conn.cursor()
        # Ограничиваем историю 20 последними сообщениями
        c.execute("SELECT COUNT(*) FROM context_memory WHERE chat_id=?", (chat_id,))
        count = c.fetchone()[0]
        if count >= 20:
            c.execute("""DELETE FROM context_memory WHERE id IN (
                SELECT id FROM context_memory WHERE chat_id=? ORDER BY id ASC LIMIT ?
            )""", (chat_id, count - 15))
        c.execute("""INSERT INTO context_memory (chat_id, role, content)
                     VALUES (?, ?, ?)""",
                  (chat_id, role, content))
        self.conn.commit()

    def get_context(self, chat_id: int, limit: int = 10) -> list:
        """Получить историю диалога для чата"""
        c = self.conn.cursor()
        c.execute("""SELECT role, content FROM context_memory 
                     WHERE chat_id=? ORDER BY id DESC LIMIT ?""",
                  (chat_id, limit))
        rows = c.fetchall()
        # Возвращаем в правильном порядке (старые -> новые)
        history = [{"role": role, "content": content} for role, content in reversed(rows)]
        return history

    def clear_context(self, chat_id: int):
        """Очистить контекст для чата"""
        c = self.conn.cursor()
        c.execute("DELETE FROM context_memory WHERE chat_id=?", (chat_id,))
        self.conn.commit()

    # === Профили пользователей ===
    def save_profile(self, user_id: int, data: dict):
        """Сохранить анкету пользователя"""
        c = self.conn.cursor()
        json_data = json.dumps(data, ensure_ascii=False)
        c.execute("""INSERT OR REPLACE INTO personal_profiles (user_id, data)
                     VALUES (?, ?)""", (user_id, json_data))
        self.conn.commit()

    def load_profile(self, user_id: int) -> dict:
        """Загрузить анкету пользователя"""
        c = self.conn.cursor()
        c.execute("SELECT data FROM personal_profiles WHERE user_id=?", (user_id,))
        row = c.fetchone()
        if row:
            return json.loads(row[0])
        return {}

    def get_profile_field(self, user_id: int, field: str):
        """Получить конкретное поле из профиля"""
        profile = self.load_profile(user_id)
        return profile.get(field)

    # === Прожарки ===
    def cache_roast(self, target_id: int, chat_id: int, roast_text: str):
        """Кэшировать прожарку"""
        c = self.conn.cursor()
        c.execute("""INSERT OR REPLACE INTO roast_cache (target_id, chat_id, roast_text)
                     VALUES (?, ?, ?)""", (target_id, chat_id, roast_text))
        self.conn.commit()

    def get_cached_roast(self, target_id: int, chat_id: int) -> str:
        """Получить закэшированную прожарку (если свежая)"""
        c = self.conn.cursor()
        c.execute("""SELECT roast_text FROM roast_cache 
                     WHERE target_id=? AND chat_id=? 
                     AND created > datetime('now', '-1 hour')""",
                  (target_id, chat_id))
        row = c.fetchone()
        return row[0] if row else None

    # === Активность (для прожарки чата) ===
    def get_chat_messages(self, chat_id: int, limit: int = 1000) -> list:
        """Получить последние сообщения чата"""
        c = self.conn.cursor()
        c.execute("""SELECT user_id, text, timestamp FROM chat_history 
                     WHERE chat_id=? ORDER BY id DESC LIMIT ?""",
                  (chat_id, limit))
        rows = c.fetchall()
        return [{"user_id": r[0], "text": r[1], "time": r[2]} for r in reversed(rows)]

    def get_user_messages(self, user_id: int, chat_id: int, limit: int = 50) -> list:
        """Получить сообщения конкретного пользователя"""
        c = self.conn.cursor()
        c.execute("""SELECT text FROM chat_history 
                     WHERE user_id=? AND chat_id=? 
                     ORDER BY id DESC LIMIT ?""",
                  (user_id, chat_id, limit))
        return [row[0] for row in c.fetchall()]

    def close(self):
        """Закрыть соединение"""
        self.conn.close()

# Глобальный экземпляр
memory = Memory()