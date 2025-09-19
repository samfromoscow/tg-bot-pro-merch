# bot.py — полный готовый код
import os
import re
import asyncio
import logging
import requests
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.filters import Command

# ======= ТОКЕНЫ =======
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

# ====== ЛОГИ И БОТ ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ====== Список магазинов ======
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

# База папки на Яндекс.Диске
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

# Сессии пользователей: user_id -> { store, files: [paths], status_msg: (chat_id, message_id) }
user_sessions = {}

# ====== УТИЛИТЫ (Yandex) ======
def ensure_folder_exists(folder_path: str) -> bool:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    params = {"path": folder_path}
    try:
        r = requests.put(url, headers=headers, params=params, timeout=30)
        return r.status_code in (201, 409)
    except Exception as e:
        logging.exception("ensure_folder_exists error")
        return False

def upload_to_yandex(local_file: str, remote_path: str) -> bool:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    params = {"path": remote_path, "overwrite": "true"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            logging.error("Get upload href failed %s %s", resp.status_code, resp.text)
            return False
        upload_url = resp.json().get("href")
        if not upload_url:
            logging.error("No href in response")
            return False
        with open(local_file, "rb") as f:
            r = requests.put(upload_url, files={"file": f}, timeout=60)
        return r.status_code in (201, 202)
    except Exception:
        logging.exception("upload_to_yandex error")
        return False

def get_week_folder(dt=None) -> str:
    if dt is None:
        dt = datetime.now()
    start = dt - timedelta(days=dt.weekday())
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ====== КЛАВИАТУРЫ ======
def build_stores_keyboard() -> InlineKeyboardMarkup:
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0

    sorted_stores = sorted(STORES, key=store_key)
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{s}") for s in sorted_stores]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_single_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

# ====== КОМАНДЫ ======
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! 👋 Для отправки фотоотчёта используй команду /отчет")

@dp.message(Command("отчет"))
async def cmd_report(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

@dp.message(Command("отмена"))
async def cmd_cancel(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Сессия отменена. Если нужно — начните /отчет заново.")

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def process_store_choice(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    store = cq.data.split(":", 1)[1]
    user_sessions[user_id] = {
        "store": store,
        "files": [],
        "status_msg": None,
        "tmp_dir": os.path.join("tmp_reports", str(user_id)),
    }
    os.makedirs(user_sessions[user_id]["tmp_dir"], exist_ok=True)

    await cq.message.answer(
        f"Вы выбрали магазин:\n<b>{store}</b>\n\nТеперь отправьте фото. После всех фото нажмите кнопку «📤 Отправить отчёт».",
        reply_markup=None,
        parse_mode="HTML",
    )

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    user_sessions.pop(cq.from_user.id, None)
    await cq.message.answer("Отмена выбора. Сессия очищена.")

# ====== ОБРАБОТКА ФОТО ======
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Пожалуйста, сначала вызови /отчет и выбери магазин.")
        return

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    tmp_dir = session["tmp_dir"]
    os.makedirs(tmp_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(tmp_dir, f"{timestamp}_{photo.file_id}.jpg")

    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    total = len(session["files"])
    status_text = f"Фото принято ✅  Всего: {total} шт.\n\nКогда закончите — нажмите кнопку ниже, чтобы отправить отчёт."

    if not session.get("status_msg"):
        sent = await message.answer(status_text, reply_markup=build_single_send_keyboard())
        session["status_msg"] = (sent.chat.id, sent.message_id)
    else:
        chat_id, msg_id = session["status_msg"]
        try:
            await bot.edit_message_text(
                text=status_text,
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=build_single_send_keyboard()
            )
        except Exception:
            sent = await message.answer(status_text, reply_markup=build_single_send_keyboard())
            session["status_msg"] = (sent.chat.id, sent.message_id)

# ====== ОТПРАВКА НА ЯНДЕКС ======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files"):
        await cq.message.answer("Нет фото для загрузки. Отправьте фото или вызовите /отчет.")
        return

    chat_id, msg_id = session.get("status_msg", (cq.message.chat.id, cq.message.message_id))
    uploading_text = "Идёт загрузка отчёта на Яндекс.Диск... Пожалуйста, подождите."
    try:
        await bot.edit_message_text(text=uploading_text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        await cq.message.answer(uploading_text)

    store = session["store"]
    files = list(session["files"])
    week_folder = get_week_folder()
    base = YANDEX_BASE
    week_path = f"{base}/{week_folder}"
    store_path = f"{week_path}/{store}"

    def do_upload():
        results = {"uploaded": 0, "total": len(files)}
        ensure_folder_exists(base)
        ensure_folder_exists(week_path)
        ensure_folder_exists(store_path)
        for local_file in files:
            remote_path = f"{store_path}/{os.path.basename(local_file)}"
            ok = upload_to_yandex(local_file, remote_path)
            if ok:
                results["uploaded"] += 1
                try:
                    os.remove(local_file)
                except Exception:
                    pass
        try:
            tmpdir = session.get("tmp_dir")
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
        return results

    results = await asyncio.to_thread(do_upload)
    user_sessions.pop(user_id, None)

    final_text = (
        f"Загрузка завершена.\n✅ Успешно загружено: {results['uploaded']} из {results['total']}.\n"
        f"Папка: {store_path}"
    )
    try:
        await bot.edit_message_text(text=final_text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        await cq.message.answer(final_text)

# ====== ЗАПУСК ======
if __name__ == "__main__":
    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)
