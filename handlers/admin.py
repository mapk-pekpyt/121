from aiogram import Router, types
from aiogram.filters import Command
from services.moderator import mute_user, warn_user
from core.security import check_admin_level

router = Router()

@router.message(Command("мут"))
async def cmd_mute(message: types.Message):
    if not await check_admin_level(message.from_user.id, 1):
        return
    target = message.reply_to_message.from_user
    await mute_user(chat_id=message.chat.id, user_id=target.id, duration=60)
    await message.reply(f"✅ {target.first_name} в муте на 5 минут.")