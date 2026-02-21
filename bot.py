import asyncio
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

# =====================
# ENV VARIABLES
# =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))

# =====================
# BOT INIT (AIROGRAM 3.7 FIX)
# =====================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# =====================
# START COMMAND
# =====================
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer("✅ Nazorat bot ishlayapti!")

# =====================
# CHAT ID CHECK
# =====================
@dp.message(Command("chatid"))
async def chat_id(message: Message):
    await message.answer(f"Chat ID: <code>{message.chat.id}</code>")

# =====================
# TEST SEND TO GROUP
# =====================
@dp.message(Command("test"))
async def test_send(message: Message):
    if GROUP_ID != 0:
        await bot.send_message(GROUP_ID, "✅ Test хабар гуруҳга юборилди")
        await message.answer("Гуруҳга юборилди ✅")
    else:
        await message.answer("GROUP_ID ўрнатилмаган ❌")

# =====================
# MAIN
# =====================
async def main():
    print("🚀 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
