import aiohttp
from config import GROQ_API_KEY, GROQ_API_URL, MODEL_NAME

async def ask_groq(messages: list, temperature: float = 0.85) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_API_URL, json=data, headers=headers) as resp:
            result = await resp.json()
            return result['choices'][0]['message']['content']