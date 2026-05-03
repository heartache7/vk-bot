from vkbottle.bot import Bot, Message

# ВСТАВЬ СЮДА СВОЙ ТОКЕН
bot = Bot(token="vk1.a.BjbUku4f6oA9OCTqULJsZtbJBtmW7-b4irA6pB9W51yMYdscwVQhN7_VYnZL1xTBz6gJERKpKCoyzeAvY5K66LPd3zM-uYOUZ8DXakhS_1CWSEbnRUWPctihcbjk5zSxLkEELlL1qg64W4WY6YwlNJG138FkWqL2yQgQbCL2Z-ELrJHiR-46mo9tdAIUOZZRTKZ54jJGKnM4oKGHhxvXEw")

@bot.on.message()
async def handler(message: Message):
    await message.answer("Бот работает 24/7 🚀")

bot.run_forever()
