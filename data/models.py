import sqlite3
from datetime import datetime
from config import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Пользователи и профили
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        language TEXT DEFAULT 'ru',
        country TEXT,
        interests TEXT,
        expertise TEXT,
        style TEXT DEFAULT 'sarcastic',
        banned_topics TEXT,
        timezone TEXT,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')

    # Администраторы
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER,
        chat_id INTEGER,
        level INTEGER CHECK(level IN (1,2,3)),
        PRIMARY KEY(user_id, chat_id)
    )''')

    # История сообщений (для памяти и прожарок)
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        text TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Статистика активности
    c.execute('''CREATE TABLE IF NOT EXISTS activity (
        user_id INTEGER,
        chat_id INTEGER,
        date DATE DEFAULT CURRENT_DATE,
        messages INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, chat_id, date)
    )''')

    # Варны/муты
    c.execute('''CREATE TABLE IF NOT EXISTS moderations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        type TEXT CHECK(type IN ('mute', 'warn', 'ban')),
        expires TIMESTAMP,
        reason TEXT
    )''')

    # Рекламные задачи
    c.execute('''CREATE TABLE IF NOT EXISTS ad_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        creator_id INTEGER,
        image TEXT,
        text TEXT,
        total INTEGER DEFAULT 1,
        sent INTEGER DEFAULT 0,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Очередь отправки рекламы
    c.execute('''CREATE TABLE IF NOT EXISTS ad_queue (
        task_id INTEGER,
        chat_id INTEGER,
        sent BOOLEAN DEFAULT FALSE,
        sent_at TIMESTAMP,
        FOREIGN KEY(task_id) REFERENCES ad_tasks(id)
    )''')

    conn.commit()
    conn.close()

# Вызывать при старте бота
if __name__ == "__main__":
    init_db()
    print("База данных инициализирована")