# bot.py — отчёты с анти-спам статусом, Москва-тайм, admin /status + /addstore + /delstore (список)
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
)
from aiogram.filters import Command

# === ТОКЕНЫ (из твоей версии) ===
TELEGRAM_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"
YANDEX_TOKEN   = "y0__xCmksrUBxjjojogmLvAsxTMieHo_qAobIbgob8lZd-uDHpoew"

# === АДМИН ===
ADMIN_ID = 445526501  # только этому пользователю доступны /status /addstore /delstore и видно их в меню

# === ЛОГИ И БОТ ===
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# === КОНСТАНТЫ ===
SUMMARY_DELAY_SEC = 2.0  # пауза тишины, после которой показываем один статус
STORES_JSON = "stores.json"  # файл со списком магазинов
PAGE_SIZE = 9  # по сколько магазинов показывать на страницу при удалении

# === МОСКОВСКОЕ ВРЕМЯ ===
try:
    from zoneinfo import ZoneInfo  # py>=3.9; у нас есть backports в reqs
except Exception:
    from backports.zoneinfo import ZoneInfo  # py3.8 fallback
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# === Сессии пользователей ===
# user_id -> {"store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# На всякий случай память об отправивших за неделю (fallback)
# submitted_by_week["DD.MM-DD.MM"] = set(store_names)
submitted_by_week: Dict[str, Set[str]] = {}

# Небольшой "ожидатель" для админского ввода (добавление магазина)
admin_wait_add: Dict[int, bool] = {}  # user_id -> True если ждём текст названия нового магазина


# === STORES (load/save) ===
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

def load_stores() -> List[str]:
    if not os.path.exists(STORES_JSON):
        with open(STORES_JSON, "w", encoding="utf-8") as f:
            json.dump(SEED_STORES, f, ensure_ascii=False, indent=2)
        logging.info("stores.json created with seed list (%d)", len(SEED_STORES))
        return list(SEED_STORES)
    try:
        with open(STORES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("stores.json corrupted")
        return [str(x) for x in data]
    except Exception:
        logging.exception("Failed to read stores.json, fallback to seed")
        return list(SEED_STORES)

def save_stores(stores: List[str]) -> None:
    try:
        with open(STORES_JSON, "w", encoding="utf-8") as f:
            json.dump(stores, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Failed to save stores.json")


# === УТИЛИТЫ (Yandex.Disk) ===
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
    """Список имён вложенных папок в каталоге на Я.Диске."""
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

# База папки на Яндекс.Диске
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

def get_week_folder(now: Optional[datetime] = None) -> str:
    """Неделя по Москве: Пн–Вс в формате 'DD.MM-DD.MM'."""
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    # понедельник текущей недели
    start = (now - timedelta(days=now.weekday()))
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"


# === КЛАВИАТУРЫ ===
def _store_sort_key(s: str) -> int:
    nums = re.findall(r"\d+", s)
    return int(nums[-1]) if nums else 0

def build_stores_keyboard() -> InlineKeyboardMarkup:
    stores = sorted(load_stores(), key=_store_sort_key)
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{s}") for s in stores]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

def build_delstore_page(page: int = 0) -> InlineKeyboardMarkup:
    stores = sorted(load_stores(), key=_store_sort_key)
    total = len(stores)
    if total == 0:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Закрыть", callback_data="del_close")]
        ])

    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    chunk = stores[start:start + PAGE_SIZE]

    kb_rows: List[List[InlineKeyboardButton]] = []
    for name in chunk:
        kb_rows.append([
            InlineKeyboardButton(text=name, callback_data=f"delstore:{name}")
        ])

    nav_row: List[InlineKeyboardButton] = []
    if pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"delpage:{page-1}"))
        nav_row.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"delpage:{page+1}"))
    if nav_row:
        kb_rows.append(nav_row)

    kb_rows.append([InlineKeyboardButton(text="Отмена", callback_data="del_close")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

def build_del_confirm(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Удалить", callback_data=f"delconfirm:yes:{name}")],
        [InlineKeyboardButton(text="❌ Отмена",  callback_data=f"delconfirm:no:{name}")],
    ])


# === ХЭЛПЕРЫ (анти-спам статус) ===
async def schedule_summary_message(message: Message, user_id: int):
    """Планирует показ ОДНОГО статус-сообщения после паузы SUMMARY_DELAY_SEC."""
    session = user_sessions.get(user_id)
    if not session:
        return

    # отменяем предыдущий таймер, если есть
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


# === КОМАНДЫ ДЛЯ СОТРУДНИКОВ ===
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    # новая чистая сессия
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

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
        "status_msg": None,        # (chat_id, message_id)
        "summary_task": None,      # asyncio.Task
    }

    await cq.message.answer("Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт».")

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.pop(cq.from_user.id, None)
    if sess:
        clear_summary_task(sess)
    await cq.message.answer("Отменено. Начни заново: /otchet")

@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Пожалуйста, сначала вызови /otchet и выбери магазин.")
        return

    # сохраняем фото локально
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    ts = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    # планируем ОДИН статус после паузы
    await schedule_summary_message(message, user_id)

@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files"):
        await cq.message.answer("Нет фото для загрузки. Отправьте фото или вызовите /otchet.")
        return

    # убираем статус и таймер
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
        # удалить пустую временную папку (если опустела)
        try:
            tmpdir = session.get("tmp_dir")
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
        return uploaded, len(files)

    loop = asyncio.get_event_loop()  # совместимо с Python 3.8
    uploaded, total = await loop.run_in_executor(None, do_upload)

    # пометим, что у этого магазина есть отчёт на этой неделе
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


# === АДМИН: /status ===
@dp.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return

    week = get_week_folder()
    week_path = f"{YANDEX_BASE}/{week}"

    # магазины с отчётами из Я.Диска (папки внутри week_path)
    existing_dirs = set(list_folder_children(week_path))
    if not existing_dirs:
        # fallback к памяти если API недоступен/пусто
        existing_dirs = submitted_by_week.get(week, set())

    stores = load_stores()
    total = len(stores)
    done = sorted([s for s in stores if s in existing_dirs], key=_store_sort_key)
    missing = sorted([s for s in stores if s not in existing_dirs], key=_store_sort_key)

    text_lines = [
        f"📆 Неделя: {week}",
        f"✅ Отчёты получены: {len(done)} / {total}",
    ]
    if missing:
        text_lines.append("\n❌ Не прислали:")
        for s in missing:
            text_lines.append(f"• {s}")
    else:
        text_lines.append("\n🎉 Все магазины прислали отчёт!")

    await message.answer("\n".join(text_lines))


# === АДМИН: /addstore (ввод вручную) ===
@dp.message(Command("addstore"))
async def cmd_addstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    # если админ сразу написал название: /addstore ОБИ 034 Саратов
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        name = parts[1].strip()
        stores = load_stores()
        if name in stores:
            await message.answer("Такой магазин уже есть.")
            return
        stores.append(name)
        save_stores(stores)
        await message.answer(f"✅ Добавлен: {name}")
        return

    # иначе попросим прислать название отдельным сообщением
    admin_wait_add[message.from_user.id] = True
    await message.answer("Пришлите название магазина одной строкой.\nНапример: «ОБИ 034 Саратов»")

@dp.message(F.text)
async def on_admin_add_name(message: Message):
    # ловим текст только если ждём от админа /addstore
    if message.from_user.id != ADMIN_ID:
        return
    if not admin_wait_add.get(message.from_user.id):
        return
    name = message.text.strip()
    if not name:
        await message.answer("Название пустое. Пришлите корректное название.")
        return
    stores = load_stores()
    if name in stores:
        await message.answer("Такой магазин уже есть.")
    else:
        stores.append(name)
        save_stores(stores)
        await message.answer(f"✅ Добавлен: {name}")
    admin_wait_add.pop(message.from_user.id, None)


# === АДМИН: /delstore (список с выбором и подтверждением) ===
@dp.message(Command("delstore"))
async def cmd_delstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    kb = build_delstore_page(page=0)
    await message.answer("Выберите магазин для удаления:", reply_markup=kb)

@dp.callback_query(F.data.startswith("delpage:"))
async def on_del_page(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    try:
        page = int(cq.data.split(":", 1)[1])
    except Exception:
        page = 0
    await cq.message.edit_reply_markup(reply_markup=build_delstore_page(page))
    await cq.answer()

@dp.callback_query(F.data.startswith("delstore:"))
async def on_del_select(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    name = cq.data.split(":", 1)[1]
    await cq.message.edit_text(f"Удалить магазин?\n\n{name}", reply_markup=build_del_confirm(name))
    await cq.answer()

@dp.callback_query(F.data.startswith("delconfirm:"))
async def on_del_confirm(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    _, answer, name = cq.data.split(":", 2)
    if answer == "yes":
        stores = load_stores()
        if name in stores:
            stores = [s for s in stores if s != name]
            save_stores(stores)
            await cq.message.edit_text(f"✅ Удалён: {name}")
        else:
            await cq.message.edit_text("Не найден (возможно уже удалён).")
    else:
        await cq.message.edit_text("Отменено.")
    await cq.answer()

@dp.callback_query(lambda c: c.data == "del_close")
async def on_del_close(cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer()
        return
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()

@dp.callback_query(F.data == "noop")
async def on_noop(cq: CallbackQuery):
    await cq.answer(cache_time=1)


# === on_startup: админ-меню только для ADMIN_ID ===
async def on_startup(bot: Bot):
    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="otchet",   description="Начать отчёт"),
                BotCommand(command="status",   description="Статус отчётов"),
                BotCommand(command="addstore", description="Добавить магазин"),
                BotCommand(command="delstore", description="Удалить магазин"),
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except Exception as e:
        logging.warning("Can't set admin-only menu: %s", e)

dp.startup.register(on_startup)


# === ЗАПУСК ===
if __name__ == "__main__":
    print("✅ Бот запущен и слушает Telegram...")
    dp.run_polling(bot)
