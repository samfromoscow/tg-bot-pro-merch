import logging
import os
import requests
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from typing import Dict

# ==============================
# 🔑 ТВОИ ТОКЕНЫ
# ==============================
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

# Папка для временных файлов
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Логирование
logging.basicConfig(level=logging.INFO)

# Сессии пользователей
user_sessions: Dict[int, dict] = {}

# Бот и диспетчер
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


# ==============================
# 📥 Прием команд
# ==============================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет 👋 Отправь мне фото, и я загружу его на Яндекс.Диск 📂")


@dp.message(lambda msg: msg.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.setdefault(user_id, {"photos": []})

    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path

    # Сохраняем фото локально
    local_filename = os.path.join(UPLOAD_DIR, f"{file_id}.jpg")
    await bot.download_file(file_path, local_filename)

    # Загружаем на Яндекс.Диск
    remote_filename = f"TelegramReports/{os.path.basename(local_filename)}"
    success = upload_to_yandex(local_filename, remote_filename)

    if success:
        await message.answer("✅ Фото загружено на Яндекс.Диск!")
    else:
        await message.answer("⚠️ Ошибка при загрузке на Яндекс.Диск.")

    session["photos"].append(local_filename)


# ==============================
# ☁️ Загрузка на Яндекс.Диск
# ==============================
def upload_to_yandex(local_path: str, remote_path: str) -> bool:
    """Загрузка файла на Яндекс.Диск"""
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    params = {"path": remote_path, "overwrite": "true"}

    try:
        # Запрашиваем ссылку для загрузки
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        href = response.json()["href"]

        # Отправляем файл
        with open(local_path, "rb") as f:
            upload_response = requests.put(href, files={"file": f})
        upload_response.raise_for_status()

        logging.info(f"✅ Файл {local_path} загружен как {remote_path}")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка загрузки {local_path}: {e}")
        return False


# ==============================
# ⏰ Планировщик
# ==============================
scheduler = AsyncIOScheduler()


def scheduled_task():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"⏰ Плановая задача запущена в {now}")


scheduler.add_job(scheduled_task, "interval", minutes=10)


# ==============================
# 🚀 Запуск бота
# ==============================
async def main():
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
