import os

CREATOR_ID = 5791171535
GROQ_API_KEY = "тут вставлю"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "mixtral-8x7b-32768"

DB_PATH = "data/database.db"
LOG_PATH = "data/bot.log"

AD_LIMIT_PER_CHAT = 1  # рекламных поста в час на чат
ACTIVITY_THRESHOLD = 1000  # сообщений для прожарки