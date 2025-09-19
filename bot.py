# bot.py — единое статус-сообщение, только /otchet
import os
import re
import asyncio
import logging
import requests
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN   = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

STORES = [
    "ОБИ 013 Белая дача", "ОБИ 009 Варшавка", "ОБИ 017 Новгород", "ОБИ 006 Боровка",
    "ОБИ 037 Авиапарк", "ОБИ 039 Новая Рига", "ОБИ 033 Рязань", "ОБИ 023 Волгоград",
    "ОБИ 042 Брянск", "ОБИ 015 Парнас", "ОБИ 001 Теплый стан", "ОБИ 011 Федяково",
    "ОБИ 016 Лахта", "ОБИ 035 Митино", "ОБИ 108 Казань",
]
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

# user_id -> dict(store, files, status_msg, tmp_dir, lock)
user_sessions = {}

def ensure_folder_exists(path: str) -> bool:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    try:
        r = requests.put("https://cloud-api.yandex.net/v1/disk/resources",
                         headers=headers, params={"path": path}, timeout=30)
        return r.status_code in (201, 409)
    except Exception:
        logging.exception("ensure_folder_exists")
        return False

def upload_to_yandex(local_file: str, remote_path: str) -> bool:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    try:
        resp = requests.get("https://cloud-api.yandex.net/v1/disk/resources/upload",
                            headers=headers, params={"path": remote_path, "overwrite": "true"}, timeout=30)
        if resp.status_code != 200:
            logging.error("href failed %s %s", resp.status_code, resp.text); return False
        href = resp.json().get("href")
        if not href:
            logging.error("no href"); return False
        with open(local_file, "rb") as f:
            put = requests.put(href, files={"file": f}, timeout=60)
        return put.status_code in (201, 202)
    except Exception:
        logging.exception("upload_to_yandex")
        return False

def get_week_folder(dt=None) -> str:
    if dt is None:
        dt = datetime.now()
    start = dt - timedelta(days=dt.weekday())
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

def build_stores_keyboard() -> InlineKeyboardMarkup:
    def key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{s}")
               for s in sorted(STORES, key=key)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")]
    ])

@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин:", reply_markup=build_stores_keyboard())

@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def choose_store(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    store = cq.data.split(":", 1)[1]
    tmp_dir = os.path.join("tmp_reports", str(user_id))
    os.makedirs(tmp_dir, exist_ok=True)
    session = {
        "store": store,
        "files": [],
        "status_msg": None,
        "tmp_dir": tmp_dir,
        "lock": asyncio.Lock(),
    }
    # создаём СРАЗУ единое статус-сообщение (0 шт.)
    status_text = ("Теперь отправьте фото. После всех фото нажмите кнопку "
                   "«📤 Отправить отчёт».\n\nФото принято ✅  Всего: 0 шт.")
    sent = await cq.message.answer(status_text, reply_markup=build_send_keyboard())
    session["status_msg"] = (sent.chat.id, sent.message_id)
    user_sessions[user_id] = session

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(cq: CallbackQuery):
    await cq.answer()
    user_sessions.pop(cq.from_user.id, None)
    await cq.message.answer("Отменено. Введите /otchet заново при необходимости.")

@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Сначала вызови /otchet и выбери магазин.")
        return

    async with session["lock"]:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        os.makedirs(session["tmp_dir"], exist_ok=True)
        filename = os.path.join(
            session["tmp_dir"],
            f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{photo.file_id}.jpg",
        )
        await bot.download_file(file_info.file_path, destination=filename)
        session["files"].append(filename)

        total = len(session["files"])
        text = (f"Фото принято ✅  Всего: {total} шт.\n\n"
                f"Когда закончите — нажмите кнопку ниже, чтобы отправить отчёт.")
        chat_id, msg_id = session["status_msg"]
        try:
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id,
                                        reply_markup=build_send_keyboard())
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            # если вдруг нельзя отредактировать — удалим и создадим заново
            try: await bot.delete_message(chat_id, msg_id)
            except Exception: pass
            sent = await message.answer(text, reply_markup=build_send_keyboard())
            session["status_msg"] = (sent.chat.id, sent.message_id)

@dp.callback_query(lambda c: c.data == "confirm_upload")
async def upload_report(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session["files"]:
        await cq.message.answer("Нет фото. Отправьте фото или вызовите /otchet.")
        return

    chat_id, msg_id = session["status_msg"]
    try:
        await bot.edit_message_text("Идёт загрузка отчёта на Яндекс.Диск...",
                                    chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    store = session["store"]
    files = list(session["files"])
    week = get_week_folder()
    base = YANDEX_BASE
    week_path = f"{base}/{week}"
    store_path = f"{week_path}/{store}"

    def do_upload():
        ensure_folder_exists(base)
        ensure_folder_exists(week_path)
        ensure_folder_exists(store_path)
        ok = 0
        for f in files:
            if upload_to_yandex(f, f"{store_path}/{os.path.basename(f)}"):
                ok += 1
                try: os.remove(f)
                except Exception: pass
        # подчистить пустую временную папку
        try:
            if os.path.isdir(session["tmp_dir"]) and not os.listdir(session["tmp_dir"]):
                os.rmdir(session["tmp_dir"])
        except Exception:
            pass
        return ok, len(files)

    loop = asyncio.get_event_loop()
    uploaded, total = await loop.run_in_executor(None, do_upload)
    user_sessions.pop(user_id, None)

    final = f"Загрузка завершена.\n✅ Успешно загружено: {uploaded} из {total}.\nПапка: {store_path}"
    try:
        await bot.edit_message_text(final, chat_id=chat_id, message_id=msg_id)
    except Exception:
        await cq.message.answer(final)

if __name__ == "__main__":
    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)
