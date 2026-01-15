import sqlite3
import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.types import InputFile
from config import DB_PATH, CREATOR_ID, AD_LIMIT_PER_CHAT

router = Router()

def get_active_chats():
    """Получить список чатов, где бот активен (последние 7 дней)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT DISTINCT chat_id FROM chat_history 
                 WHERE timestamp > datetime('now', '-7 days')""")
    chats = [row[0] for row in c.fetchall()]
    conn.close()
    return chats

async def send_ad(bot: Bot, chat_id: int, image_path: str, text: str):
    """Отправить рекламное сообщение"""
    try:
        if image_path:
            photo = InputFile(image_path)
            await bot.send_photo(chat_id, photo, caption=text[:1024])
        else:
            await bot.send_message(chat_id, text[:4096])
        return True
    except Exception as e:
        print(f"Ошибка отправки рекламы в {chat_id}: {e}")
        return False

@router.message(Command("add_ad"))
async def add_ad_command(message: types.Message):
    """Добавить рекламную задачу (только создатель)"""
    if message.from_user.id != CREATOR_ID:
        return
    # Ожидаем: /add_ad [количество] [текст] (фото прикрепляется)
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /add_ad <количество> <текст> (можно с фото)")
        return
    try:
        total = int(parts[1])
        ad_text = parts[2]
    except ValueError:
        await message.answer("Укажите число количества")
        return

    image_path = None
    if message.photo:
        image_path = f"data/ads/{message.message_id}.jpg"
        await message.bot.download(message.photo[-1], destination=image_path)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO ad_tasks (creator_id, image, text, total) 
                 VALUES (?, ?, ?, ?)""",
              (CREATOR_ID, image_path, ad_text, total))
    task_id = c.lastrowid
    # Добавляем в очередь
    chats = get_active_chats()
    for chat_id in chats:
        c.execute("INSERT INTO ad_queue (task_id, chat_id) VALUES (?, ?)",
                  (task_id, chat_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Задача #{task_id} добавлена. Очередь: {len(chats)} чатов")

async def ad_scheduler(bot: Bot):
    """Планировщик отправки рекламы (запускать в фоне)"""
    while True:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Берём незавершённые задачи
        c.execute("""SELECT id, image, text, total, sent FROM ad_tasks 
                     WHERE sent < total""")
        tasks = c.fetchall()
        for task_id, image, text, total, sent in tasks:
            # Берём чаты, где ещё не отправляли
            c.execute("""SELECT chat_id FROM ad_queue 
                         WHERE task_id=? AND sent=FALSE 
                         ORDER BY RANDOM() LIMIT 1""")
            row = c.fetchone()
            if not row:
                continue
            chat_id = row[0]
            # Проверяем лимит
            c.execute("""SELECT COUNT(*) FROM ad_queue 
                         WHERE chat_id=? AND sent=TRUE 
                         AND sent_at > datetime('now', '-1 hour')""",
                      (chat_id,))
            sent_count = c.fetchone()[0]
            if sent_count >= AD_LIMIT_PER_CHAT:
                continue
            # Отправка
            success = await send_ad(bot, chat_id, image, text)
            if success:
                c.execute("""UPDATE ad_queue SET sent=TRUE, sent_at=datetime('now')
                             WHERE task_id=? AND chat_id=?""",
                          (task_id, chat_id))
                c.execute("UPDATE ad_tasks SET sent=sent+1 WHERE id=?", (task_id,))
                # Отчёт создателю
                if sent + 1 == total:
                    await bot.send_message(CREATOR_ID, f"✅ Задача #{task_id} выполнена полностью")
        conn.commit()
        conn.close()
        await asyncio.sleep(3600)  # Каждый час

@router.message(Command("ad_stats"))
async def ad_stats(message: types.Message):
    """Статистика по рекламе (только создатель)"""
    if message.from_user.id != CREATOR_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id, text, total, sent FROM ad_tasks 
                 ORDER BY id DESC LIMIT 10""")
    tasks = c.fetchall()
    report = "📊 Отчёт по рекламе:\n"
    for task_id, text, total, sent in tasks:
        report += f"#{task_id}: {sent}/{total} - {text[:30]}...\n"
    conn.close()
    await message.answer(report)