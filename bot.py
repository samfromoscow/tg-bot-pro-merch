import logging
import os
import requests
from datetime import datetime
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tzlocal import get_localzone


# 🔹 Токены и настройки
BOT_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_DISK_TOKEN = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

BASE_FOLDER = "/TelegramReports"

# 🔹 Список магазинов
STORES = [
    "ОБИ 013 Белая дача",
    "ОБИ 009 Варшавка",
    "ОБИ 017 Новгород",
    "ОБИ 006 Боровка",
    "ОБИ 037 Авиапарк",
    "ОБИ 039 Новая Рига",
    "ОБИ 033 Рязань",
    "ОБИ 023 Волгоград",
    "ОБИ 042 Брянск",
    "ОБИ 015 Парнас",
    "ОБИ 001 Теплый стан",
    "ОБИ 011 Федяково",
    "ОБИ 016 Лахта",
    "ОБИ 035 Митино",
    "ОБИ 108 Казань",
]

# 🔹 Сессии пользователей
user_sessions = {}  # {user_id: {"photos": [], "store": str}}


# --- Функции для Яндекс.Диска ---

def create_folder(path: str):
    """Создать папку на Яндекс.Диске (если нет)"""
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    headers = {"Authorization": f"OAuth {YANDEX_DISK_TOKEN}"}
    params = {"path": path}
    response = requests.put(url, headers=headers, params=params)
    if response.status_code not in (201, 409):  # 201 = создано, 409 = уже есть
        logging.error(f"Ошибка при создании папки {path}: {response.text}")


def upload_file(path: str, file_data: bytes):
    """Загрузить файл на Яндекс.Диск"""
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {YANDEX_DISK_TOKEN}"}
    params = {"path": path, "overwrite": "true"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        upload_url = response.json().get("href")
        res = requests.put(upload_url, files={"file": file_data})
        if res.status_code not in (201, 202):
            logging.error(f"Ошибка загрузки файла {path}: {res.text}")
    else:
        logging.error(f"Ошибка получения ссылки для загрузки {path}: {response.text}")


# --- Клавиатуры ---

def store_keyboard():
    kb = InlineKeyboardBuilder()
    for store in STORES:
        kb.button(text=store, callback_data=f"store:{store}")
    return kb.adjust(1).as_markup()


def confirm_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить отчет", callback_data="confirm:yes")
    kb.button(text="❌ Отменить", callback_data="confirm:no")
    return kb.adjust(1).as_markup()


# --- Основная логика ---

async def start_handler(message: Message):
    user_sessions[message.from_user.id] = {"photos": [], "store": None}
    await message.answer("Привет! 👋\nВыбери магазин для фотоотчета:", reply_markup=store_keyboard())


async def photo_handler(message: Message):
    session = user_sessions.get(message.from_user.id)
    if not session or not session.get("store"):
        await message.answer("Сначала выбери магазин командой /start")
        return

    file_id = message.photo[-1].file_id
    file = await message.bot.get_file(file_id)
    file_path = file.file_path
    file_data = await message.bot.download_file(file_path)

    session["photos"].append(file_data.read())
    await message.answer("Фото добавлено ✅")


async def store_handler(callback: CallbackQuery):
    store_name = callback.data.split(":", 1)[1]
    user_sessions[callback.from_user.id] = {"photos": [], "store": store_name}
    await callback.message.answer(f"Магазин выбран: {store_name}\nТеперь загружай фото 📸")
    await callback.answer()


async def confirm_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)

    if not session or not session.get("store") or not session.get("photos"):
        await callback.message.answer("Нет данных для отправки отчета ❌")
        return

    store_name = session["store"]
    today = datetime.now().strftime("%Y-%m-%d")
    folder_path = f"{BASE_FOLDER}/{today}/{store_name}"

    create_folder(folder_path)

    for i, photo_data in enumerate(session["photos"], 1):
        filename = f"{store_name}_{today}_{i}.jpg"
        path = f"{folder_path}/{filename}"
        upload_file(path, photo_data)

    await callback.message.answer("Отчет успешно отправлен на Яндекс.Диск ✅")
    user_sessions.pop(user_id, None)


async def confirm_request(message: Message):
    await message.answer("Хочешь отправить отчет или отменить?", reply_markup=confirm_keyboard())


# --- Запуск бота ---

def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, F.text == "/start")
    dp.message.register(photo_handler, F.photo)
    dp.message.register(confirm_request, F.text.lower() == "отправить")
    dp.callback_query.register(store_handler, F.data.startswith("store:"))
    dp.callback_query.register(confirm_handler, F.data.startswith("confirm:"))

    scheduler = AsyncIOScheduler(timezone=str(get_localzone()))
    scheduler.start()

    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)


if __name__ == "__main__":
    main()
