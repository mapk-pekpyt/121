import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

def log_message(chat_id: int, user_id: int, text: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO chat_history (chat_id, user_id, text) VALUES (?, ?, ?)",
              (chat_id, user_id, text))
    # Активность
    c.execute('''INSERT OR REPLACE INTO activity (user_id, chat_id, date, messages)
                 VALUES (?, ?, DATE('now'), COALESCE((SELECT messages+1 FROM activity 
                 WHERE user_id=? AND chat_id=? AND date=DATE('now')), 1))''',
              (user_id, chat_id, user_id, chat_id))
    conn.commit()
    conn.close()

def get_chat_stats(chat_id: int, period: str = 'all'):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT user_id, SUM(messages) as total FROM activity WHERE chat_id=?"
    if period == 'day':
        query += " AND date = DATE('now')"
    elif period == 'week':
        query += " AND date >= DATE('now', '-7 days')"
    elif period == 'month':
        query += " AND date >= DATE('now', '-30 days')"
    query += " GROUP BY user_id ORDER BY total DESC LIMIT 10"
    c.execute(query, (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def detect_conflict(chat_id: int, recent_messages=20):
    """Анализ последних сообщений на предмет конфликта"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT text FROM chat_history 
                 WHERE chat_id=? ORDER BY id DESC LIMIT ?''',
              (chat_id, recent_messages))
    messages = [row[0] for row in c.fetchall()]
    conn.close()

    # Простая эвристика: повторяющиеся ответы, оскорбления, много восклицательных знаков
    conflict_keywords = ['дурак', 'идиот', 'заткнись', 'ты неправ', 'чушь']
    if len(messages) < 5:
        return False
    negative_count = sum(1 for msg in messages if any(kw in msg.lower() for kw in conflict_keywords))
    return negative_count >= 3  # Конфликт детектирован