# bot.py — без спама + /status только у админа и видно только ему в меню
import os
import re
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand,
    BotCommandScopeDefault, BotCommandScopeChat,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
)

# ======= КОНФИГ =======
ADMIN_ID = 445526501  # ← твой Telegram ID (видит/может /status)
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN   = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

SUMMARY_DELAY_SEC = 2.0  # задержка тишины перед показом ОДНОГО статуса
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

# ======= ЛОГИ И БОТ =======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# ======= Справочник магазинов =======
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

# ======= Сессии =======
# user_id -> {"store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# ======= Утилиты: неделя, Я.Диск =======
def get_week_folder(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now()
    start = now - timedelta(days=now.weekday())
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

def y_headers() -> Dict[str, str]:
    return {"Authorization": f"OAuth {YANDEX_TOKEN}"}

def ensure_folder_exists(path: str) -> bool:
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    try:
        r = requests.put(url, headers=y_headers(), params={"path": path}, timeout=30)
        return r.status_code in (201, 409)
    except Exception:
        logging.exception("ensure_folder_exists error")
        return False

def upload_to_yandex(local_file: str, remote_path: str) -> bool:
    get_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    try:
        resp = requests.get(get_url, headers=y_headers(),
                            params={"path": remote_path, "overwrite": "true"},
                            timeout=30)
        if resp.status_code != 200:
            logging.error("Get href failed: %s %s", resp.status_code, resp.text)
            return False
        href = resp.json().get("href")
        if not href:
            return False
        with open(local_file, "rb") as f:
            put = requests.put(href, files={"file": f}, timeout=120)
        return put.status_code in (201, 202)
    except Exception:
        logging.exception("upload_to_yandex error")
        return False

def yandex_list_count(path: str) -> int:
    """Кол-во объектов в папке (0 если нет/пусто)."""
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    try:
        r = requests.get(url, headers=y_headers(), params={"path": path, "limit": 2000}, timeout=30)
        if r.status_code != 200:
            return 0
        items = r.json().get("_embedded", {}).get("items", [])
        return len(items)
    except Exception:
        logging.exception("yandex_list_count error")
        return 0

# ======= Клавиатуры =======
def build_stores_keyboard() -> InlineKeyboardMarkup:
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{s}")
               for s in sorted(STORES, key=store_key)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")]]
    )

# ======= Помощники анти-спама =======
async def schedule_summary_message(message: Message, user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return

    # отменяем прошлый таймер
    task: Optional[asyncio.Task] = session.get("summary_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except Exception:
            pass

    async def delayed():
        try:
            await asyncio.sleep(SUMMARY_DELAY_SEC)
            sess = user_sessions.get(user_id)
            if not sess:
                return
            total = len(sess["files"])
            text = (
                f"Фото принято ✅  Всего: {total} шт.\n\n"
                f"Когда закончите — нажмите кнопку ниже, чтобы отправить отчёт."
            )
            kb = build_send_keyboard()
            if sess.get("status_msg"):
                chat_id, msg_id = sess["status_msg"]
                try:
                    await bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
                except Exception:
                    sent = await message.answer(text, reply_markup=kb)
                    sess["status_msg"] = (sent.chat.id, sent.message_id)
            else:
                sent = await message.answer(text, reply_markup=kb)
                sess["status_msg"] = (sent.chat.id, sent.message_id)
        except asyncio.CancelledError:
            return

    session["summary_task"] = asyncio.create_task(delayed())

def clear_summary_task(session: Dict[str, Any]):
    task: Optional[asyncio.Task] = session.get("summary_task")
    if task and not task.done():
        task.cancel()

# ======= Команды =======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

# Только в твоём меню и только для тебя доступна:
@dp.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        # Для остальных команда недоступна (и не видна в меню)
        await message.answer("Команда недоступна.")
        return

    week = get_week_folder()
    week_path = f"{YANDEX_BASE}/{week}"

    uploaded_stores: List[str] = []
    for s in STORES:
        store_path = f"{week_path}/{s}"
        if yandex_list_count(store_path) > 0:
            uploaded_stores.append(s)

    not_uploaded = [s for s in STORES if s not in uploaded_stores]
    count_done = len(uploaded_stores)
    total = len(STORES)

    lines = [f"Статус отчётов за неделю {week}",
             f"Сдали: {count_done} из {total}", ""]
    if not_uploaded:
        lines.append("Ещё не сдали:")
        for s in not_uploaded:
            lines.append(f"• {s}")
    else:
        lines.append("✅ Все магазины сдали отчёт!")
    await message.answer("\n".join(lines))

# ======= Выбор магазина =======
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
        "tmp_dir": tmp_dir,
        "status_msg": None,
        "summary_task": None,
    }

    await cq.message.answer("Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт».")

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.pop(cq.from_user.id, None)
    if sess:
        clear_summary_task(sess)
    await cq.message.answer("Отменено. Начни заново: /otchet")

# ======= Приём фото (анти-спам статуса) =======
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
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    # показываем ОДИН статус по таймеру тишины
    await schedule_summary_message(message, user_id)

# ======= Отправка отчёта =======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files"):
        await cq.message.answer("Нет фото для загрузки. Отправьте фото или вызовите /otchet.")
        return

    # убрать статус и таймер
    clear_summary_task(session)
    if session.get("status_msg"):
        chat_id, msg_id = session["status_msg"]
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        session["status_msg"] = None

    loading = await cq.message.answer("Идёт загрузка отчёта на Яндекс.Диск... Пожалуйста, подождите.")

    store = session["store"]
    files = list(session["files"])
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
        # удалить пустую временную папку
        try:
            tmpdir = session.get("tmp_dir")
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
        return uploaded, len(files)

    loop = asyncio.get_event_loop()  # Py3.8 совместимо
    uploaded, total = await loop.run_in_executor(None, do_upload)

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

    user_sessions.pop(user_id, None)

# ======= Настройка команд в меню =======
async def setup_commands():
    # 1) Для всех по умолчанию — только /otchet
    await bot.set_my_commands(
        commands=[BotCommand(command="otchet", description="отправить отчёт")],
        scope=BotCommandScopeDefault()
    )
    # Подстрахуемся: очистим другие глобальные области
    await bot.set_my_commands([], scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands([], scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands([], scope=BotCommandScopeAllChatAdministrators())

    # 2) Для твоего личного чата — /otchet и /status (видишь только ты)
    await bot.set_my_commands(
        commands=[
            BotCommand(command="otchet", description="отправить отчёт"),
            BotCommand(command="status", description="проверка статуса отчётов"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )

# ======= Запуск =======
async def main():
    await setup_commands()
    print("✅ Бот запущен и слушает Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
