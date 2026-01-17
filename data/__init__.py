# Инициализация базы данных при импорте
from .models import init_database, add_user

init_database()

__all__ = ['init_database', 'add_user']