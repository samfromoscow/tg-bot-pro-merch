# bot.py — без спама + admin-only /status + постоянные магазины (stores.json) + Moscow TZ
import os
import re
import json
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional, Set

try:
    from zoneinfo import ZoneInfo  # Py>=3.9
except Exception:
    from backports.zoneinfo import ZoneInfo  # Py3.8 fallback

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
ADMIN_ID = 445526501  # только этому пользователю доступны админ-команды и видно их в меню

# ====== TZ (Москва) ======
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# ====== ЛОГИ И БОТ ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# ====== Константы ======
SUMMARY_DELAY_SEC = 2.0  # пауза тишины, после которой показываем один статус

# ====== Данные (путь для постоянных магазинов) ======
BOT_DATA_DIR = os.path.expanduser("~/bot-data")
STORES_FILE  = os.path.join(BOT_DATA_DIR, "stores.json")

# ====== Список магазинов (seed для первого запуска) ======
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

# Текущие магазины (загружаются/сохраняются в stores.json)
DYNAMIC_STORES: List[str] = []

# База папки на Яндекс.Диске
YANDEX_BASE = "/Sam/Проект Crown/Фотоотчеты CROWN"

# Сессии пользователей
# user_id -> {"store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# Память об отправивших за неделю (fallback, если API листинга недоступен)
# submitted_by_week["DD.MM-DD.MM"] = set(store_names)
submitted_by_week: Dict[str, Set[str]] = {}

# ====== УТИЛИТЫ ======
def store_numeric_key(s: str) -> int:
    nums = re.findall(r"\d+", s)
    return int(nums[-1]) if nums else 0

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
    """Вернуть имена вложенных папок в каталоге на Я.Диске."""
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

def get_week_folder(now: Optional[datetime] = None) -> str:
    # Всегда московское время
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    else:
        if now.tzinfo is None:
            now = now.replace(tzinfo=MOSCOW_TZ)
        else:
            now = now.astimezone(MOSCOW_TZ)
    start = now - timedelta(days=now.weekday())  # понедельник
    end = start + timedelta(days=6)              # воскресенье
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ====== Постоянные магазины (stores.json) ======
def load_stores() -> None:
    """Загрузить магазины в DYNAMIC_STORES. Если файла нет — создать из SEED_STORES."""
    global DYNAMIC_STORES
    try:
        os.makedirs(BOT_DATA_DIR, exist_ok=True)
        if not os.path.isfile(STORES_FILE):
            DYNAMIC_STORES = sorted(set(SEED_STORES), key=store_numeric_key)
            save_stores()  # создаём файл
            logging.info("stores.json created with seed list (%d)", len(DYNAMIC_STORES))
            return
        with open(STORES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "stores" in data:
            items = data["stores"]
        elif isinstance(data, list):
            items = data
        else:
            items = SEED_STORES
        # нормализуем
        DYNAMIC_STORES = sorted({str(x).strip() for x in items if str(x).strip()}, key=store_numeric_key)
        logging.info("stores.json loaded (%d)", len(DYNAMIC_STORES))
    except Exception:
        logging.exception("load_stores error, fallback to seed")
        DYNAMIC_STORES = sorted(set(SEED_STORES), key=store_numeric_key)

def save_stores() -> None:
    """Сохранить текущие магазины из DYNAMIC_STORES в stores.json (с атомарной записью)."""
    tmp_path = STORES_FILE + ".tmp"
    data = {"stores": DYNAMIC_STORES}
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # простой бэкап предыдущей версии
        if os.path.exists(STORES_FILE):
            try:
                os.replace(STORES_FILE, STORES_FILE + ".bak")
            except Exception:
                pass
        os.replace(tmp_path, STORES_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

# ====== КЛАВИАТУРЫ ======
def build_stores_keyboard() -> InlineKeyboardMarkup:
    sorted_stores = sorted(DYNAMIC_STORES, key=store_numeric_key)
    buttons = [InlineKeyboardButton(text=s, callback_data=f"store:{s}") for s in sorted_stores]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

# ====== ХЭЛПЕРЫ ======
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

# ====== КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите магазин (нажми кнопку):", reply_markup=build_stores_keyboard())

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def process_store_choice(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    store = cq.data.split(":", 1)[1]

    # на случай, если список обновляли — проверим, что магазин существует
    if store not in DYNAMIC_STORES:
        await cq.message.answer("Этот магазин недоступен. Запустите /otchet ещё раз.")
        return

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

# ====== ФОТО: без спама, статус по таймеру тишины ======
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Пожалуйста, сначала вызови /otchet и выбери магазин.")
        return

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    ts = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    await schedule_summary_message(message, user_id)

# ====== ОТПРАВИТЬ ОТЧЁТ ======
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
    total = len(DYNAMIC_STORES)
    done = sorted([s for s in DYNAMIC_STORES if s in existing_dirs], key=store_numeric_key)
    missing = sorted([s for s in DYNAMIC_STORES if s not in existing_dirs], key=store_numeric_key)

    lines = [
        f"📆 Неделя: {week}",
        f"✅ Отчёты получены: {len(done)} / {total}",
    ]
    if missing:
        lines.append("\n❌ Не прислали:")
        lines += [f"• {s}" for s in missing]
    else:
        lines.append("\n🎉 Все магазины прислали отчёт!")
    await message.answer("\n".join(lines))

# ====== АДМИН: управление магазинами ======
@dp.message(Command("addstore"))
async def cmd_addstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    # всё после команды считаем названием
    raw = message.text or ""
    name = raw.partition(" ")[2].strip()
    if not name:
        await message.answer("Использование: /addstore <название магазина>")
        return
    if name in DYNAMIC_STORES:
        await message.answer("Такой магазин уже есть.")
        return
    DYNAMIC_STORES.append(name)
    DYNAMIC_STORES[:] = sorted(set(DYNAMIC_STORES), key=store_numeric_key)
    save_stores()
    await message.answer(f"Добавлено: «{name}». Всего магазинов: {len(DYNAMIC_STORES)}")

@dp.message(Command("delstore"))
async def cmd_delstore(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    raw = message.text or ""
    name = raw.partition(" ")[2].strip()
    if not name:
        await message.answer("Использование: /delstore <название магазина>")
        return
    if name not in DYNAMIC_STORES:
        await message.answer("Такого магазина нет.")
        return
    DYNAMIC_STORES.remove(name)
    save_stores()
    await message.answer(f"Удалено: «{name}». Осталось магазинов: {len(DYNAMIC_STORES)}")

@dp.message(Command("stores"))
async def cmd_stores(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    if not DYNAMIC_STORES:
        await message.answer("Список магазинов пуст.")
        return
    items = sorted(DYNAMIC_STORES, key=store_numeric_key)
    text = "📋 Текущие магазины:\n" + "\n".join(f"• {s}" for s in items)
    # если вдруг длинно — порежем по 4000
    MAX = 4000
    while text:
        chunk = text[:MAX]
        await message.answer(chunk)
        text = text[MAX:]

# ====== on_startup: загрузка stores.json + админ-меню ======
async def on_startup(bot: Bot):
    load_stores()
    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="otchet",   description="Начать отчёт"),
                BotCommand(command="status",   description="Статус отчётов"),
                BotCommand(command="addstore", description="Добавить магазин"),
                BotCommand(command="delstore", description="Удалить магазин"),
                BotCommand(command="stores",   description="Список магазинов"),
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
