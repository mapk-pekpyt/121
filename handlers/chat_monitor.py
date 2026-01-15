from aiogram import Router
from services.ai_client import ask_groq
from services.memory import get_chat_memory

router = Router()

@router.message()
async def analyze_and_respond(message):
    if message.chat.type == "private":
        return

    # Если бота упомянули
    if message.text and "@" + (await message.bot.me()).username in message.text:
        context = get_chat_memory(message.chat.id, limit=10)
        prompt = [{"role": "user", "content": f"Контекст: {context}. Ответь коротко и жёстко на это: {message.text}"}]
        reply = await ask_groq(prompt)
        await message.reply(reply[:400])
        return

    # Автоответ на вопрос
    if "?" in message.text and len(message.text.split()) < 15:
        prompt = [{"role": "user", "content": f"Дай краткий, язвительный ответ на вопрос: {message.text}"}]
        answer = await ask_groq(prompt)
        await message.reply(answer[:300])