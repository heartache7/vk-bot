from vkbottle.bot import Bot, Message

# ВСТАВЬ СЮДА СВОЙ ТОКЕН
bot = Bot(token="vk1.a.6f790amqcqoWVIoYKpyxZThiwL0tYxcC203wMm6YXLH1vXmKlPlIkDpEKkFbowjEmK-Y_nHlwjPxPSwn5GU_o4dkVaBDe9Xjeeo4iHoBSLYniLn9gQkbclJIhwd2UFgMbYb5twyJz5U-kG80dHUk5sI52R123G3pgTajWE69r3lOxMc1onWa0l-vAdedtHn-_uMxEfjrq9Ho6r-IDHK1hw")
@bot.on.message()
async def handler(message: Message):
    await message.answer("Бот работает 24/7 🚀")

bot.run_forever()
