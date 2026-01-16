import re
import html
from datetime import datetime, timedelta
from typing import Optional

def escape_markdown(text: str) -> str:
    """Экранирование символов MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def html_escape(text: str) -> str:
    """Экранирование HTML"""
    return html.escape(text)

def truncate_text(text: str, max_length: int = 300, suffix: str = "...") -> str:
    """Обрезать текст до максимальной длины"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix

def parse_time(time_str: str) -> Optional[int]:
    """Парсинг времени вида '5m', '2h', '1d' в минуты"""
    if not time_str:
        return None
    multipliers = {'m': 1, 'h': 60, 'd': 1440}
    match = re.match(r'^(\d+)([mhd])$', time_str.lower())
    if match:
        num, unit = match.groups()
        return int(num) * multipliers.get(unit, 1)
    return None

def format_time(minutes: int) -> str:
    """Форматирование минут в читаемый вид"""
    if minutes < 60:
        return f"{minutes} мин"
    elif minutes < 1440:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}ч {mins}мин" if mins else f"{hours}ч"
    else:
        days = minutes // 1440
        hours = (minutes % 1440) // 60
        return f"{days}д {hours}ч"

def is_admin_command(text: str) -> bool:
    """Проверка, является ли сообщение командой админа"""
    admin_commands = ['/мут', '/варн', '/бан', '/разбан', '/снять_варн', 
                     '/антимат', '/антифлуд', '/назначить', '/посадить_в_угол']
    return any(text.startswith(cmd) for cmd in admin_commands)

def get_mention(user_id: int, name: str = None) -> str:
    """Получить упоминание пользователя"""
    if name:
        return f'<a href="tg://user?id={user_id}">{html_escape(name)}</a>'
    return f'<a href="tg://user?id={user_id}">пользователь</a>'

def is_question(text: str) -> bool:
    """Определить, является ли текст вопросом"""
    question_words = ['кто', 'что', 'где', 'когда', 'почему', 'как', 'зачем', 'сколько']
    text_lower = text.lower().strip()
    return ('?' in text) or any(text_lower.startswith(word) for word in question_words)

def calculate_activity_level(messages_count: int) -> str:
    """Определить уровень активности по количеству сообщений"""
    if messages_count > 1000:
        return "🔥 БОГ ЧАТА"
    elif messages_count > 500:
        return "💪 АКТИВИСТ"
    elif messages_count > 100:
        return "📊 СРЕДНЯК"
    else:
        return "👶 НОВИЧОК"