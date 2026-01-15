from aiogram import Router, types
from aiogram.filters import Command
from services.ai_client import ask_groq
from services.memory import get_chat_memory, get_user_profile

router = Router()

@router.message(Command("ты_кто"))
async def cmd_who(message: types.Message):
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    profile = get_user_profile(target.id)
    context = f"Инфа о юзере: {profile}. Его последние сообщения: {get_chat_memory(target.id, limit=5)}"
    prompt = [{"role": "user", "content": f"Жёстко подыми юзера на основе этой инфы, 2 предложения: {context}"}]
    answer = await ask_groq(prompt, temperature=0.9)
    await message.reply(answer)