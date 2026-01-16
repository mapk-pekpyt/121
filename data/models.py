import sqlite3
from datetime import datetime
from pathlib import Path
from config import DB_PATH

def init_database():
    """Инициализация всех таблиц базы данных"""
    # Создаём директорию
    Path("data").mkdir(exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # === ПОЛЬЗОВАТЕЛИ ===
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # === ПРОФИЛИ ДЛЯ ЛС ===
    c.execute('''CREATE TABLE IF NOT EXISTS personal_profiles (
        user_id INTEGER PRIMARY KEY,
        language TEXT DEFAULT 'ru',
        country TEXT,
        interests TEXT,
        expertise TEXT,
        style TEXT DEFAULT 'neutral',
        banned_topics TEXT,
        timezone TEXT,
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # === АДМИНИСТРАТОРЫ ===
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER,
        chat_id INTEGER,
        level INTEGER CHECK(level IN (1,2,3)),
        appointed_by INTEGER,
        appointed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, chat_id),
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # === ИСТОРИЯ СООБЩЕНИЙ ===
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        text TEXT,
        message_type TEXT DEFAULT 'text',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # Ключевой индекс для скорости
    c.execute('''CREATE INDEX IF NOT EXISTS idx_chat_time 
                 ON chat_history(chat_id, timestamp DESC)''')
    
    # === АКТИВНОСТЬ ===
    c.execute('''CREATE TABLE IF NOT EXISTS activity (
        user_id INTEGER,
        chat_id INTEGER,
        date DATE DEFAULT CURRENT_DATE,
        messages INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, chat_id, date),
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # === МОДЕРАЦИЯ ===
    c.execute('''CREATE TABLE IF NOT EXISTS moderations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        type TEXT CHECK(type IN ('mute', 'warn', 'ban', 'ignore')),
        expires TIMESTAMP,
        reason TEXT,
        admin_id INTEGER,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # === КОНТЕКСТНАЯ ПАМЯТЬ ===
    c.execute('''CREATE TABLE IF NOT EXISTS context_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        role TEXT CHECK(role IN ('user', 'assistant', 'system')),
        content TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # === КЭШ ПРОЖАРОК ===
    c.execute('''CREATE TABLE IF NOT EXISTS roast_cache (
        target_id INTEGER,
        chat_id INTEGER,
        roast_text TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(target_id, chat_id)
    )''')
    
    # === РЕКЛАМНЫЕ ЗАДАЧИ ===
    c.execute('''CREATE TABLE IF NOT EXISTS ad_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        creator_id INTEGER,
        image TEXT,
        text TEXT,
        total INTEGER DEFAULT 1,
        sent INTEGER DEFAULT 0,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed', 'cancelled'))
    )''')
    
    # === ОЧЕРЕДЬ РЕКЛАМЫ ===
    c.execute('''CREATE TABLE IF NOT EXISTS ad_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        chat_id INTEGER,
        sent BOOLEAN DEFAULT FALSE,
        sent_at TIMESTAMP,
        error TEXT,
        FOREIGN KEY(task_id) REFERENCES ad_tasks(id) ON DELETE CASCADE
    )''')
    
    # === СИСТЕМНЫЕ НАСТРОЙКИ ===
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        chat_id INTEGER PRIMARY KEY,
        antimat BOOLEAN DEFAULT FALSE,
        antiflood BOOLEAN DEFAULT FALSE,
        last_roast_message INTEGER DEFAULT 0,
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()
    print(f"[{datetime.now()}] База данных инициализирована: {DB_PATH}")

def add_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Добавить пользователя в базу"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

# Автоматическая инициализация при импорте
if __name__ == "__main__":
    init_database()
else:
    # Инициализируем при загрузке модуля
    init_database()