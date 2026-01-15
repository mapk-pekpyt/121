import sqlite3
from config import DB_PATH, CREATOR_ID

def check_admin_level(user_id: int, required_level: int) -> bool:
    if user_id == CREATOR_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT level FROM admins WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row and row[0] >= required_level