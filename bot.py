# bot.py — отчёты без спама, неделя по Москве, admin: /status /addstore /delstore
import os
import re
import json
import asyncio
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple, Optional, Set

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
)
from aiogram.filters import Command

# ======= ТОКЕНЫ =======
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN   = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

# ====== АДМИН ======
ADMIN_ID = 445526501  # только этому пользователю доступны /status /addstore /delstore и видны в меню

# ====== ЛОГИ И БОТ ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# ====== Константы ======
SUMMARY_DELAY_SEC = 2.0  # пауза тишины для единственного статус-сообщения
MSK = timezone(timedelta(hours=3))  # Москва

# ====== Файлы/данные ======
STORES_FILE = "stores.json"

# Начальное «семя» магазинов (если файла ещё нет)
SEED_STORES: List[str] = [
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

# Сессии пользователей: состояния и временные файлы
# user_id -> {"store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task],
#             "mode": Optional[str]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# На всякий случай память об отправивших за неделю (fallback)
# submitted_by_week["DD.MM-DD.MM"] = set(store_names)
submitted_by_week: Dict[str, Set[str]] = {}

# ===================== ХРАНИЛИЩЕ МАГАЗИНОВ =====================
def load_stores() -> List[str]:
    if not os.path.exists(STORES_FILE):
        with open(STORES_FILE, "w", encoding="utf-8") as f:
            json.dump(SEED_STORES, f, ensure_ascii=False, indent=2)
        logging.info("stores.json created with seed list (%d)", len(SEED_STORES))
        return list(SEED_STORES)
    try:
        with open(STORES_FILE, "r", encoding="utf-8") as f:
            stores = json.load(f)
            if not isinstance(stores, list):
                raise ValueError("stores.json damaged")
            return stores
    except Exception as e:
        logging.exception("load_stores error, fallback to seed: %s", e)
        return list(SEED_STORES)

def save_stores(stores: List[str]) -> None:
    with open(STORES_FILE, "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, indent=2)

def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip())

def is_store_name_valid(name: str) -> bool:
    # Требуем формат: "ОБИ 123 Название"
    return bool(re.match(r"^ОБИ\s+\d{3}\s+.+", name.strip(), flags=re.IGNORECASE))

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
    """Вернуть имена вложенных папок на Я.Диске для week_path."""
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
        items = r.json().get("_embedded", {}).get("items", [])
        return [it.get("name") for it in items if it.get("type") == "dir"]
    except Exception:
        logging.exception("list_folder_children error")
        return []

def get_week_folder(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now(MSK)
    else:
        now = now.astimezone(MSK)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ====== КЛАВИАТУРЫ ======
def build_stores_keyboard(stores: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    if stores is None:
        stores = load_stores()

    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0

    sorted_stores = sorted(stores, key=store_key)
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{i}") for i, s in enumerate(sorted_stores)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")]
    ])

def build_cancel_kb(tag: str) -> InlineKeyboardMarkup:
    # tag нужен, чтобы отличать отмены разных режимов
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_cancel:{tag}")]
    ])

def build_del_list_kb(stores: List[str]) -> InlineKeyboardMarkup:
    # список магазинов для удаления
    buttons = [InlineKeyboardButton(text=s, callback_data=f"delpick:{i}") for i, s in enumerate(stores)]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel:del")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_del_confirm_kb(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Удалить", callback_data=f"delyes:{index}"),
            InlineKeyboardButton(text="↩️ Назад", callback_data="delback")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel:del")]
    ])

# ====== ХЭЛПЕРЫ СЕССИЙ ======
def set_mode(user_id: int, mode: Optional[str]):
    user_sessions.setdefault(user_id, {})
    user_sessions[user_id]["mode"] = mode

def get_mode(user_id: int) -> Optional[str]:
    return user_sessions.get(user_id, {}).get("mode")

async def schedule_summary_message(message: Message, user_id: int):
    """Планирует показ ОДНОГО статус-сообщения после паузы SUMMARY_DELAY_SEC."""
    session = user_sessions.setdefault(user_id, {})

    # отменяем предыдущий таймер
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
            total = len(sess.get("files", []))
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

# ====== ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    # если админ находился в режимах добавления/удаления — сбросить
    set_mode(message.from_user.id, None)
    # новая чистая сессия для отчёта
    user_sessions[message.from_user.id] = {
        "files": [],
        "tmp_dir": os.path.join("tmp_reports", str(message.from_user.id)),
        "status_msg": None,
        "summary_task": None,
    }
    os.makedirs(user_sessions[message.from_user.id]["tmp_dir"], exist_ok=True)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def process_store_choice(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    stores = load_stores()

    # в callback хранится индекс в отсортированном списке, поэтому пересоберём тот же порядок
    def store_key(s: str) -> int:
        nums = re.findall(r"\d+", s)
        return int(nums[-1]) if nums else 0
    sorted_stores = sorted(stores, key=store_key)

    idx = int(cq.data.split(":", 1)[1])
    if idx < 0 or idx >= len(sorted_stores):
        await cq.message.answer("Не удалось определить магазин, попробуйте ещё раз: /otchet")
        return

    store = sorted_stores[idx]

    user_sessions.setdefault(user_id, {})
    user_sessions[user_id]["store"] = store
    await cq.message.answer("Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт».")

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.pop(cq.from_user.id, None)
    if sess:
        clear_summary_task(sess)
    set_mode(cq.from_user.id, None)
    await cq.message.answer("Отменено. Начни заново: /otchet")

# ====== ФОТО: без спама, статус по таймеру тишины ======
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    # фото принимаем вне зависимостей от админ-режимов
    session = user_sessions.get(user_id)
    if not session or "store" not in session:
        await message.answer("Пожалуйста, сначала вызови /otchet и выбери магазин.")
        return

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    ts = datetime.now(MSK).strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session.setdefault("files", []).append(local_filename)

    await schedule_summary_message(message, user_id)

# ====== ОТПРАВИТЬ ОТЧЁТ ======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files") or "store" not in session:
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

    loop = asyncio.get_event_loop()
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

# ===================== АДМИН: /status =====================
@dp.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return

    week = get_week_folder()
    week_path = f"{YANDEX_BASE}/{week}"

    existing_dirs = set(list_folder_children(week_path))
    if not existing_dirs:
        existing_dirs = submitted_by_week.get(week, set())

    all_stores = load_stores()
    total = len(all_stores)
    done = sorted([s for s in all_stores if s in existing_dirs])
    missing = sorted([s for s in all_stores if s not in existing_dirs])

    lines = [f"📆 Неделя: {week}", f"✅ Отчёты получены: {len(done)} / {total}"]
    if missing:
        lines.append("\n❌ Не прислали:")
        lines += [f"• {s}" for s in missing]
    else:
        lines.append("\n🎉 Все магазины прислали отчёт!")

    await message.answer("\n".join(lines))

# ===================== АДМИН: /addstore =====================
@dp.message(Command("addstore"))
async def cmd_addstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return

    set_mode(ADMIN_ID, "adding")
    await message.answer(
        "Пришлите **название магазина одной строкой**.\n"
        "Формат: `ОБИ 034 Саратов` (строго с номером магазина).",
        reply_markup=build_cancel_kb("add"),
    )

@dp.callback_query(lambda c: c.data == "admin_cancel:add")
async def cancel_add(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    set_mode(ADMIN_ID, None)
    await cq.message.edit_text("Добавление магазина отменено.")

@dp.message(lambda m: get_mode(m.from_user.id) == "adding")
async def addstore_text(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = normalize_name(message.text or "")
    # запрет на команды, чтобы случайно не записать /delstore как магазин
    if text.startswith("/"):
        await message.answer("Это похоже на команду. Пришлите именно **название магазина**.\nНапример: `ОБИ 034 Саратов`")
        return
    if not is_store_name_valid(text):
        await message.answer("Неверный формат. Пример: `ОБИ 034 Саратов`")
        return

    stores = load_stores()
    lower_set = {s.lower() for s in stores}
    if text.lower() in lower_set:
        await message.answer("Такой магазин уже есть в списке.")
        set_mode(ADMIN_ID, None)
        return

    stores.append(text)
    save_stores(stores)
    set_mode(ADMIN_ID, None)
    await message.answer(f"✅ Магазин добавлен: {text}")

# ===================== АДМИН: /delstore =====================
@dp.message(Command("delstore"))
async def cmd_delstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return

    set_mode(ADMIN_ID, "deleting")
    stores = load_stores()
    if not stores:
        await message.answer("Список магазинов пуст.")
        set_mode(ADMIN_ID, None)
        return

    await message.answer(
        "Выберите магазин для удаления:",
        reply_markup=build_del_list_kb(stores)
    )

@dp.callback_query(lambda c: c.data.startswith("delpick:"))
async def on_del_pick(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    if get_mode(ADMIN_ID) != "deleting":
        await cq.answer("Режим удаления не активен.")
        return

    stores = load_stores()
    idx = int(cq.data.split(":")[1])
    if idx < 0 or idx >= len(stores):
        await cq.answer("Магазин не найден.")
        return

    await cq.message.edit_text(
        f"Удалить магазин?\n\n• {stores[idx]}",
        reply_markup=build_del_confirm_kb(idx)
    )

@dp.callback_query(lambda c: c.data == "delback")
async def on_del_back(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    if get_mode(ADMIN_ID) != "deleting":
        await cq.answer()
        return
    await cq.message.edit_text("Выберите магазин для удаления:", reply_markup=build_del_list_kb(load_stores()))

@dp.callback_query(lambda c: c.data.startswith("delyes:"))
async def on_del_yes(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    if get_mode(ADMIN_ID) != "deleting":
        await cq.answer()
        return

    stores = load_stores()
    idx = int(cq.data.split(":")[1])
    if idx < 0 or idx >= len(stores):
        await cq.answer("Магазин не найден.")
        return

    removed = stores.pop(idx)
    save_stores(stores)
    # после удаления остаёмся в режиме удаления (можно удалить ещё), либо выйти — на ваш вкус
    if stores:
        await cq.message.edit_text(
            f"🗑 Удалено: {removed}\n\nВыберите следующий магазин для удаления:",
            reply_markup=build_del_list_kb(stores)
        )
    else:
        set_mode(ADMIN_ID, None)
        await cq.message.edit_text(f"🗑 Удалено: {removed}\n\nСписок магазинов пуст.")

@dp.callback_query(lambda c: c.data == "admin_cancel:del")
async def cancel_del(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    set_mode(ADMIN_ID, None)
    await cq.message.edit_text("Удаление магазинов отменено.")

# ====== on_startup: меню для админа (не трогаем меню для сотрудников) ======
async def on_startup(bot: Bot):
    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="otchet", description="Начать отчёт"),
                BotCommand(command="status", description="Статус отчётов"),
                BotCommand(command="addstore", description="Добавить магазин"),
                BotCommand(command="delstore", description="Удалить магазин"),
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
