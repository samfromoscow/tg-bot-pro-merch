# bot.py — отчёты, неделя по МСК, без спама; админ: /status /addstore /delstore
import os
import re
import json
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional, Set

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from aiogram.filters import Command

# Py3.8: backports.zoneinfo
try:
    from zoneinfo import ZoneInfo  # Py>=3.9
except Exception:
    from backports.zoneinfo import ZoneInfo  # Py3.8

# ======= ТОКЕНЫ =======
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN   = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

# ====== АДМИН ======
ADMIN_ID = 445526501  # только этому пользователю доступны админ-команды

# ====== ЛОГИ И БОТ ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# ====== Константы ======
SUMMARY_DELAY_SEC = 2.0  # пауза тишины перед показом единственного статуса
MSK_TZ = ZoneInfo("Europe/Moscow")

# ====== Хранилище магазинов ======
STORES_FILE = "stores.json"
DEFAULT_STORES: List[str] = [
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

def load_stores() -> List[str]:
    if os.path.exists(STORES_FILE):
        try:
            with open(STORES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return list(dict.fromkeys([str(x).strip() for x in data if str(x).strip()]))
        except Exception:
            logging.exception("Failed to read stores.json, fallback to default.")
    # создать файл с дефолтным списком
    with open(STORES_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_STORES, f, ensure_ascii=False, indent=2)
    logging.info("stores.json created with seed list (%d)", len(DEFAULT_STORES))
    return DEFAULT_STORES[:]

def save_stores(stores: List[str]) -> None:
    # уникализируем и сортируем по номеру (если есть)
    def store_key(s: str) -> Tuple[int, str]:
        nums = re.findall(r"\d+", s)
        num = int(nums[-1]) if nums else 10**9
        return (num, s.lower())
    clean = [s.strip() for s in stores if s and s.strip()]
    unique = list(dict.fromkeys(clean))
    unique_sorted = sorted(unique, key=store_key)
    with open(STORES_FILE, "w", encoding="utf-8") as f:
        json.dump(unique_sorted, f, ensure_ascii=False, indent=2)

STORES: List[str] = load_stores()

# База папки на Яндекс.Диске
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

# Сессии пользователей
# user_id -> {"store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# На всякий случай память об отправивших за неделю (если API листинга недоступен)
# submitted_by_week["DD.MM-DD.MM"] = set(store_names)
submitted_by_week: Dict[str, Set[str]] = {}

# Простое состояние админа для добавления магазина
admin_state: Dict[int, str] = {}  # {ADMIN_ID: "await_add_name"}

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
        return r.status_code in (201, 202)
    except Exception:
        logging.exception("upload_to_yandex error")
        return False

def list_folder_children(folder_path: str) -> List[str]:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    params = {
        "path": folder_path,
        "limit": 1000,
        "fields": "_embedded.items.name,_embedded.items.type"
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            logging.warning("list_folder_children %s -> %s %s", folder_path, r.status_code, r.text)
            return []
        data = r.json()
        items = data.get("_embedded", {}).get("items", [])
        return [it.get("name") for it in items if it.get("type") == "dir"]
    except Exception:
        logging.exception("list_folder_children error")
        return []

def now_msk() -> datetime:
    return datetime.now(MSK_TZ)

def get_week_folder(now: Optional[datetime] = None) -> str:
    if now is None:
        now = now_msk()
    start = (now - timedelta(days=now.weekday())).date()
    end = (start + timedelta(days=6))
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ====== КЛАВИАТУРЫ ======
def build_stores_keyboard() -> InlineKeyboardMarkup:
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    sorted_stores = sorted(STORES, key=store_key)
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{i}") for i, s in enumerate(sorted_stores)]
    # сохраним сопоставление индексов к текущему отсортированному списку
    # (для простоты — пересчитаем из STORES в обработчике)
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

def build_delete_list_keyboard() -> InlineKeyboardMarkup:
    # Кнопки для удаления магазинов (по индексам текущего STORES, отсортированных как в выборе)
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    sorted_idx = sorted(range(len(STORES)), key=lambda k: store_key(STORES[k]))
    buttons = [
        InlineKeyboardButton(text=STORES[i], callback_data=f"askdel:{i}")
        for i in sorted_idx
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="del_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_confirm_delete_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delok:{idx}")],
        [InlineKeyboardButton(text="↩️ Назад к списку", callback_data="del_back")]
    ])

# ====== ХЭЛПЕРЫ (статус) ======
async def schedule_summary_message(message: Message, user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return
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

# ====== КОМАНДЫ ДЛЯ ВСЕХ ======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    # новая чистая сессия
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def process_store_choice(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    # индекс магазина из отсортированного списка
    try:
        idx_in_sorted = int(cq.data.split(":", 1)[1])
    except Exception:
        await cq.message.answer("Не удалось определить магазин. Попробуйте /otchet ещё раз.")
        return

    # пересоберём такой же отсортированный список
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    sorted_stores = sorted(STORES, key=store_key)
    if idx_in_sorted < 0 or idx_in_sorted >= len(sorted_stores):
        await cq.message.answer("Магазин не найден. Попробуйте /otchet снова.")
        return
    store = sorted_stores[idx_in_sorted]

    tmp_dir = os.path.join("tmp_reports", str(user_id))
    os.makedirs(tmp_dir, exist_ok=True)

    user_sessions[user_id] = {
        "store": store,
        "files": [],
        "tmp_dir": tmp_dir,
        "status_msg": None,        # (chat_id, message_id)
        "summary_task": None,      # asyncio.Task
    }

    await cq.message.answer(
        "Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт»."
    )

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.pop(cq.from_user.id, None)
    if sess:
        clear_summary_task(sess)
    await cq.message.answer("Отменено. Начни заново: /otchet")

# ====== ПРИЁМ ФОТО (без спама) ======
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
    ts = now_msk().strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    # планируем единый статус
    await schedule_summary_message(message, user_id)

# ====== ЗАГРУЗКА НА ДИСК ======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files"):
        await cq.message.answer("Нет фото для загрузки. Отправьте фото или вызовите /otchet.")
        return

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

    if uploaded > 0:
        submitted_by_week.setdefault(week_folder, set()).add(store)

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

# ====== АДМИН /status ======
@dp.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return

    week = get_week_folder()
    week_path = f"{YANDEX_BASE}/{week}"
    existing_dirs = set(list_folder_children(week_path)) or submitted_by_week.get(week, set())

    total = len(STORES)
    done = sorted([s for s in STORES if s in existing_dirs])
    missing = sorted([s for s in STORES if s not in existing_dirs])

    text_lines = [
        f"📆 Неделя: {week} (МСК)",
        f"✅ Отчёты получены: {len(done)} / {total}",
    ]
    if missing:
        text_lines.append("\n❌ Не прислали:")
        for s in missing:
            text_lines.append(f"• {s}")
    else:
        text_lines.append("\n🎉 Все магазины прислали отчёт!")

    await message.answer("\n".join(text_lines))

# ====== АДМИН /addstore (интерактивно) ======
@dp.message(Command("addstore"))
async def cmd_addstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    admin_state[ADMIN_ID] = "await_add_name"
    await message.answer(
        "Отправьте точное название нового магазина одним сообщением.\n"
        "Например: «ОБИ 034 Саратов».\n"
        "Чтобы отменить — отправьте /cancel."
    )

@dp.message(Command("cancel"))
async def cmd_cancel_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    if admin_state.get(ADMIN_ID) == "await_add_name":
        admin_state.pop(ADMIN_ID, None)
        await message.answer("Добавление магазина отменено.")
    else:
        await message.answer("Нечего отменять.")

@dp.message(F.text)
async def handle_admin_add_name(message: Message):
    # перехватываем только когда админ в режиме добавления
    if message.from_user.id != ADMIN_ID:
        return
    if admin_state.get(ADMIN_ID) != "await_add_name":
        return

    name = message.text.strip()
    if not name:
        await message.answer("Пустое название. Введите ещё раз или /cancel.")
        return
    if name in STORES:
        await message.answer("Такой магазин уже есть в списке.")
        admin_state.pop(ADMIN_ID, None)
        return

    STORES.append(name)
    save_stores(STORES)
    admin_state.pop(ADMIN_ID, None)
    await message.answer(f"✅ Магазин добавлен: {name}")

# ====== АДМИН /delstore (списком) ======
@dp.message(Command("delstore"))
async def cmd_delstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not STORES:
        await message.answer("Список магазинов пуст.")
        return
    await message.answer("Выберите магазин для удаления:", reply_markup=build_delete_list_keyboard())

@dp.callback_query(lambda c: c.data and c.data.startswith("askdel:"))
async def cb_ask_delete(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("Недоступно.")
        return
    try:
        idx = int(cq.data.split(":", 1)[1])
    except Exception:
        await cq.answer("Ошибка индекса.")
        return
    if idx < 0 or idx >= len(STORES):
        await cq.answer("Магазин не найден.")
        return
    name = STORES[idx]
    await cq.message.edit_text(
        f"Удалить магазин?\n\n🗑 {name}",
        reply_markup=build_confirm_delete_keyboard(idx)
    )

@dp.callback_query(lambda c: c.data and c.data.startswith("delok:"))
async def cb_delete_ok(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("Недоступно.")
        return
    try:
        idx = int(cq.data.split(":", 1)[1])
    except Exception:
        await cq.answer("Ошибка индекса.")
        return
    if idx < 0 or idx >= len(STORES):
        await cq.answer("Магазин не найден.")
        return
    name = STORES[idx]
    # удаляем
    del STORES[idx]
    save_stores(STORES)
    await cq.message.edit_text(f"✅ Удалено: {name}")

@dp.callback_query(lambda c: c.data == "del_back")
async def cb_delete_back(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("Недоступно.")
        return
    await cq.message.edit_text("Выберите магазин для удаления:", reply_markup=build_delete_list_keyboard())

@dp.callback_query(lambda c: c.data == "del_cancel")
async def cb_delete_cancel(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("Недоступно.")
        return
    await cq.message.edit_text("Закрыто.")

# ====== on_startup: меню ======
async def on_startup(bot: Bot):
    # меню по умолчанию (для всех): только /otchet
    try:
        await bot.set_my_commands(
            commands=[BotCommand(command="otchet", description="Начать отчёт")],
            scope=BotCommandScopeDefault(),
        )
    except Exception as e:
        logging.warning("Can't set default menu: %s", e)
    # меню админа
    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="otchet",    description="Начать отчёт"),
                BotCommand(command="status",    description="Статус отчётов"),
                BotCommand(command="addstore",  description="Добавить магазин"),
                BotCommand(command="delstore",  description="Удалить магазин"),
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except Exception as e:
        logging.warning("Can't set admin-only menu: %s", e)

dp.startup.register(on_startup)

# ====== ЗАПУСК ======
if __name__ == "__main__":
    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)
