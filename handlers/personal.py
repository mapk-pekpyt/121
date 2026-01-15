from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.memory import save_user_profile

router = Router()

class Profile(StatesGroup):
    language = State()
    country = State()
    interests = State()
    style = State()
    banned_topics = State()

@router.message(Command("start"))
async def start_personal(message: types.Message, state: FSMContext):
    await message.answer("Давай настроим профиль. Основной язык общения?")
    await state.set_state(Profile.language)

@router.message(Profile.language)
async def set_language(message: types.Message, state: FSMContext):
    await state.update_data(language=message.text)
    await message.answer("Страна проживания?")
    await state.set_state(Profile.country)