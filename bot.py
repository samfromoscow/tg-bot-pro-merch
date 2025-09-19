# bot.py — минимальный и стабильный
import os
import re
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple

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
STORES: List[str] = [
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

# Сессии пользователей
# user_id -> {"store": str, "files": List[str], "status_msg": Tuple[int,int] | None, "tmp_dir": str}
user_sessions: Dict[int, Dict[str, Any]] = {}

# ====== УТИЛИТЫ (Yandex) ======
def ensure_folder_exists(folder_path: str) -> bool:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    params = {"path": folder_path}
    try:
        r = requests.put(url, headers=headers, params=params, timeout=30)
        return r.status_code in (201, 409)
    except Exception:
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
            r = requests.put(upload_url, files={"file": f}, timeout=120)
        success = r.status_code in (201, 202)
        if not success:
            logging.error("Upload failed %s %s", r.status_code, r.text)
        return success
    except Exception:
        logging.exception("upload_to_yandex error")
        return False

def get_week_folder(now: datetime = None) -> str:
    if now is None:
        now = datetime.now()
    start = now - timedelta(days=now.weekday())
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

def build_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

# ====== КОМАНДЫ ======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    # новая сессия
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def process_store_choice(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    store = cq.data.split(":", 1)[1]

    tmp_dir = os.path.join("tmp_reports", str(user_id))
    os.makedirs(tmp_dir, exist_ok=True)

    user_sessions[user_id] = {
        "store": store,
        "files": [],
        "status_msg": None,  # (chat_id, message_id)
        "tmp_dir": tmp_dir,
    }

    # Инструкция
    await cq.message.answer(
        "Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт».",
    )

    # Стартовый статус (0 шт.)
    status_text = "Фото принято ✅  Всего: 0 шт.\n\nКогда закончите — нажмите кнопку ниже, чтобы отправить отчёт."
    sent = await cq.message.answer(status_text, reply_markup=build_send_keyboard())
    user_sessions[user_id]["status_msg"] = (sent.chat.id, sent.message_id)

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    user_sessions.pop(cq.from_user.id, None)
    await cq.message.answer("Отменено. Начни заново: /otchet")

# ====== ФОТО: ОДИН СТАТУС НИЖЕ ПОСЛЕДНЕГО ФОТО ======
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Пожалуйста, сначала вызови /otchet и выбери магазин.")
        return

    # сохраняем фото
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{timestamp}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)

    session["files"].append(local_filename)

    # пересобираем текст статуса
    total = len(session["files"])
    status_text = (
        f"Фото принято ✅  Всего: {total} шт.\n\n"
        f"Когда закончите — нажмите кнопку ниже, чтобы отправить отчёт."
    )

    # удаляем старый статус, создаём новый — так он всегда окажется ПОД последним фото
    if session.get("status_msg"):
        chat_id, msg_id = session["status_msg"]
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

    sent = await message.answer(status_text, reply_markup=build_send_keyboard())
    session["status_msg"] = (sent.chat.id, sent.message_id)

# ====== ОТПРАВИТЬ ОТЧЁТ ======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files"):
        await cq.message.answer("Нет фото для загрузки. Отправьте фото или вызовите /otchet.")
        return

    # показать «идёт загрузка» как новое сообщение, удалить статус
    if session.get("status_msg"):
        chat_id, msg_id = session["status_msg"]
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

    loading = await cq.message.answer("Идёт загрузка отчёта на Яндекс.Диск... Пожалуйста, подождите.")

    store = session["store"]
    files = list(session["files"])  # копия
    week_folder = get_week_folder()
    base = YANDEX_BASE
    week_path = f"{base}/{week_folder}"
    store_path = f"{week_path}/{store}"

    def do_upload():
        ensure_folder_exists(base)
        ensure_folder_exists(week_path)
        ensure_folder_exists(store_path)
        uploaded = 0
        for local_file in files:
            remote_path = f"{store_path}/{os.path.basename(local_file)}"
            if upload_to_yandex(local_file, remote_path):
                uploaded += 1
                try:
                    os.remove(local_file)
                except Exception:
                    pass
        # убрать пустую временную папку
        try:
            tmpdir = session.get("tmp_dir")
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
        return uploaded, len(files)

    loop = asyncio.get_event_loop()  # Py3.8-совместимый offload
    uploaded, total = await loop.run_in_executor(None, do_upload)

    # финал отдельным сообщением, «loading» удаляем
    try:
        await bot.delete_message(loading.chat.id, loading.message_id)
    except Exception:
        pass

    final_text = (
        f"Загрузка завершена.\n"
        f"✅ Успешно загружено: {uploaded} из {total}.\n"
        f"Папка: {store_path}"
    )
    await cq.message.answer(final_text)

    # очистить сессию
    user_sessions.pop(user_id, None)

# ====== ЗАПУСК ======
if __name__ == "__main__":
    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)
