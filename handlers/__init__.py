# Импортируем все роутеры для удобства
from .admin import router as admin_router
from .user import router as user_router
from .chat_monitor import router as chat_monitor_router
from .personal import router as personal_router
from .advertising import router as advertising_router
from . import admin, user, chat_monitor, personal, advertising

__all__ = [
    'admin_router',
    'user_router', 
    'chat_monitor_router',
    'personal_router',
    'advertising_router'
]