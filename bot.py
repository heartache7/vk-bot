from vkbottle.bot import Bot, Message

# ВСТАВЬ СЮДА СВОЙ ТОКЕН
bot = Bot(token="vk1.a.ZTiuYTvGy_O_v5C851uFyvh5vnHrJcoOzvE6dOUdqxEsicPpfKDrS7c8-GthxyBhM9BhGD9es6WtBalvFI89AWm-ME75iWsEdg5JoHyyU20uuT4LrIQnxyS0vJiS2SPF4RPwsSxdtekCBn0wYRV0H2lT0kyQYwwmTXpta7UUfSzaDafRjXuw1Pdpih6EaSC0FEHTVRKYN_IniAii_eSO-w")
@bot.on.message()
async def handler(message: Message):
    await message.answer("Бот работает 24/7 🚀")

bot.run_forever()
