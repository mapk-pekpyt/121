import sqlite3
from config import DB_PATH

def get_user_profile(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM user_profiles WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return eval(row[0]) if row else {}

def get_chat_memory(chat_id: int, limit: int = 10) -> list:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, text FROM chat_history 
        WHERE chat_id=? 
        ORDER BY id DESC LIMIT ?
    """, (chat_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "text": r[1]} for r in rows]