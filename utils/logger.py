import logging
import sys
from pathlib import Path
from config import LOG_PATH

def setup_logger():
    """Настройка логгера с выводом в файл и консоль"""
    # Создаём директорию для логов
    Path("data").mkdir(exist_ok=True)
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Обработчик для файла
    file_handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # Обработчик для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчики
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# Глобальный логгер
logger = setup_logger()