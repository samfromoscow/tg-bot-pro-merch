# bot.py — проекты + GREENWORKS с клиентами, "Назад" везде, вертикальный список для GREENWORKS
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
ADMIN_ID = 445526501  # только этому пользователю доступны /status /addstore /delstore /delproject

# ====== ЛОГИ И БОТ ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher()

# ====== Константы ======
SUMMARY_DELAY_SEC = 2.0  # пауза тишины, после которой показываем один статус
PROJECTS_FILE = "projects.json"

# ====== Сид-данные (если ещё нет projects.json) ======
SEED_PROJECTS = {
    "CROWN": {
        "base": "/Sam/Проект Crown/Фотоотчеты CROWN",
        "stores": [
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
        ],
    },
    "PIT": {
        "base": "/Sam/Проект PIT/Фотоотчеты PIT",
        "stores": [
            "ОБИ 013 Белая дача",
            "ОБИ 042 Брянск",
            "ОБИ 006 Боровка",
            "ОБИ 037 Авиапарк",
            "ОБИ 039 Рига",
            "ОБИ 001 Теплый стан",
        ],
    },
    "GREENWORKS": {
        "clients": {
            "Михаил": {
                "base": "/Sam/Проект Seasons/Фотоотчеты Greenworks seasons/Михаил",
                "stores": [
                    "Бау Центр Дзержинка Калининград",
                    "Бау Центр Московский Калининград",
                    "Бау Центр Новороссийск",
                    "Бау Центр Пушкино",
                    "Дарвин Зеленоград",
                    "Дарвин Подольск",
                    "Дарвин Пушкино",
                    "Колорлон Новосибирск ",
                    "Колорлон, Бредск",
                    "Петрович Дмитровка",
                    "Петрович Санкт-Петербург",
                ],
            },
            "Александр": {
                "base": "/Sam/Проект Seasons/Фотоотчеты Greenworks seasons/Александр",
                "stores": [
                    "Вектор Пенза",
                    "Дачник Демская",
                    "Дачник Романтиков",
                    "Моя Родня Окружная",
                    "Моя Родня Рахманинова ",
                    "Моя Родня Терновского",
                    "Сарай (Ульяновск)",
                    "Строй-С Гвардейская",
                    "Строй-С Усть-Курдюмская",
                    "Юрат Чебоксары",
                ],
            },
        }
    },
}

# ====== Хранилища в памяти ======
# Сессии пользователей (отчёт)
# user_id -> {"project": str, "store": str, "files": List[str], "tmp_dir": str,
#             "status_msg": Optional[Tuple[int,int]], "summary_task": Optional[asyncio.Task]}
user_sessions: Dict[int, Dict[str, Any]] = {}

# Служебная память о сданных за неделю (fallback, если листинг недоступен)
# submitted_by_week[ (project, [client]), "DD.MM-DD.MM" ] = set(store_names)
submitted_by_week: Dict[Tuple[str, Optional[str], str], Set[str]] = {}

# Временные админ-процессы (добавить/удалить магазин/проект)
# admin_flows[user_id] = dict(...)
admin_flows: Dict[int, Dict[str, Any]] = {}

# ====== ПРОЕКТЫ ======
def load_projects() -> Dict[str, Any]:
    if os.path.isfile(PROJECTS_FILE):
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    # если файла нет — создаём из SEED_PROJECTS
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(SEED_PROJECTS, f, ensure_ascii=False, indent=2)
    logging.info("projects.json created with seed")
    return SEED_PROJECTS.copy()

def save_projects(data: Dict[str, Any]):
    tmp = PROJECTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROJECTS_FILE)

PROJECTS = load_projects()

def list_projects() -> List[str]:
    return list(PROJECTS.keys())

def project_has_clients(project: str) -> bool:
    return "clients" in PROJECTS.get(project, {})

def get_project_base(project: str) -> Optional[str]:
    cfg = PROJECTS.get(project)
    if not cfg:
        return None
    return cfg.get("base")

def get_clients(project: str) -> List[str]:
    if not project_has_clients(project):
        return []
    return list(PROJECTS[project]["clients"].keys())

def get_all_stores(project: str) -> List[str]:
    cfg = PROJECTS.get(project, {})
    if "clients" in cfg:
        acc: List[str] = []
        for cl in cfg["clients"].values():
            acc.extend(cl.get("stores", []))
        # без дубликатов, сохраняя порядок
        seen = set()
        uniq = []
        for s in acc:
            if s not in seen:
                uniq.append(s)
                seen.add(s)
        return uniq
    return cfg.get("stores", [])

def get_client_for_store_in_greenworks(store: str) -> Optional[str]:
    cfg = PROJECTS.get("GREENWORKS", {})
    if "clients" not in cfg:
        return None
    for client, ccfg in cfg["clients"].items():
        if store in ccfg.get("stores", []):
            return client
    return None

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

def week_folder_moscow(now: Optional[datetime] = None) -> str:
    """Текущая неделя по Москве (пн-вс)."""
    if now is None:
        now = datetime.now(timezone(timedelta(hours=3)))  # MSK, без переходов
    start = now - timedelta(days=now.weekday())
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ====== КЛАВИАТУРЫ ======
def rows_of(buttons: List[InlineKeyboardButton], cols: int) -> List[List[InlineKeyboardButton]]:
    if cols <= 1:
        return [[b] for b in buttons]
    return [buttons[i:i+cols] for i in range(0, len(buttons), cols)]

def build_projects_kb(for_admin: bool=False) -> InlineKeyboardMarkup:
    names = list_projects()
    btns = [[InlineKeyboardButton(text=proj, callback_data=f"proj:{proj}") ] for proj in projects]
    # сортировка и сборка
    buttons = [InlineKeyboardButton(text=p, callback_data=f"proj:{p}") for p in names]
    rows = rows_of(buttons, cols=2)
    if for_admin:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
    else:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_clients_kb(project: str, include_all_for_status: bool=False, admin_flow: bool=False) -> InlineKeyboardMarkup:
    clients = get_clients(project)
    buttons = []
    for c in clients:
        prefix = "adm" if admin_flow else "proj"
        buttons.append(InlineKeyboardButton(text=c, callback_data=f"{prefix}:client:{project}:{c}"))
    if include_all_for_status:
        buttons.insert(0, InlineKeyboardButton(text="Все клиенты", callback_data=f"status:client:{project}:*"))
    rows = rows_of(buttons, cols=2)
    back_cb = "adm:back:pickproj" if admin_flow else "back:projects"
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_stores_kb(project: str, stores: List[str], admin_mode: Optional[str]=None) -> InlineKeyboardMarkup:
    # GREENWORKS — вертикально (1 колонка), остальные — по 3 в строке
    cols = 1 if project == "GREENWORKS" else 3
    buttons = []
    for s in stores:
        if admin_mode == "del":
            # для удаления сформируем индекс в admin_flows динамически — здесь просто заглушка
            buttons.append(InlineKeyboardButton(text=s, callback_data=f"adm:del:choose:{s}"))
        else:
            buttons.append(InlineKeyboardButton(text=s, callback_data=f"store:{project}:{s}"))
    rows = rows_of(buttons, cols=cols)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:stores")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_send_keyboard() -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="confirm_upload")
    return InlineKeyboardMarkup(inline_keyboard=[[btn],
                                                [InlineKeyboardButton(text="🔙 Выбрать другой магазин", callback_data="back:stores")]])

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

def project_store_path(project: str, store: str, week: str) -> Optional[str]:
    cfg = PROJECTS.get(project)
    if not cfg:
        return None
    if "clients" in cfg:
        client = get_client_for_store_in_greenworks(store)
        if not client:
            return None
        base = cfg["clients"][client]["base"]
        return f"{base}/{week}/{store}"
    base = cfg["base"]
    return f"{base}/{week}/{store}"

# ====== КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ======
@dp.message(Command("otchet"))
async def cmd_report(message: Message):
    # новая чистая сессия
    user_sessions.pop(message.from_user.id, None)
    await message.answer("Выберите проект:", reply_markup=build_projects_kb())

@dp.callback_query(lambda c: c.data and c.data.startswith("proj:"))
async def choose_project(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    project = cq.data.split(":", 1)[1]
    if project not in PROJECTS:
        await cq.message.answer("Проект не найден. Попробуйте снова: /otchet")
        return

    # Создаём сессию (без выбранного магазина)
    tmp_dir = os.path.join("tmp_reports", str(user_id))
    os.makedirs(tmp_dir, exist_ok=True)
    user_sessions[user_id] = {
        "project": project,
        "store": None,
        "files": [],
        "tmp_dir": tmp_dir,
        "status_msg": None,
        "summary_task": None,
    }

    # Показать магазины
    stores = get_all_stores(project)
    if not stores:
        await cq.message.answer("В этом проекте пока нет магазинов.")
        return

    await cq.message.answer(
        f"Проект: {project}\nВыберите магазин:",
        reply_markup=build_stores_kb(project, stores)
    )

@dp.callback_query(lambda c: c.data == "back:projects")
async def back_to_projects(cq: CallbackQuery):
    await cq.answer()
    # Сбрасываем выбор проекта/магазина, но оставим tmp_dir и файлы как есть? Логично — очистить.
    sess = user_sessions.get(cq.from_user.id)
    if sess:
        clear_summary_task(sess)
        # удалим набранные фото, если они были (не загруженные)
        for p in sess.get("files", []):
            try:
                os.remove(p)
            except Exception:
                pass
        tmpdir = sess.get("tmp_dir")
        try:
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
        user_sessions.pop(cq.from_user.id, None)

    await cq.message.answer("Выберите проект:", reply_markup=build_projects_kb())

@dp.callback_query(lambda c: c.data == "back:stores")
async def back_to_stores(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.get(cq.from_user.id)
    if not sess:
        await cq.message.answer("Сессия не найдена. Начните заново: /otchet")
        return
    project = sess.get("project")
    stores = get_all_stores(project)
    await cq.message.answer(
        f"Проект: {project}\nВыберите магазин:",
        reply_markup=build_stores_kb(project, stores)
    )

# ====== ВЫБОР МАГАЗИНА ======
@dp.callback_query(lambda c: c.data and c.data.startswith("store:"))
async def choose_store(cq: CallbackQuery):
    await cq.answer()
    _, project, store = cq.data.split(":", 2)

    sess = user_sessions.get(cq.from_user.id)
    if not sess or sess.get("project") != project:
        await cq.message.answer("Сессия устарела. Начните заново: /otchet")
        return

    sess["store"] = store
    await cq.message.answer(
        "Теперь отправьте фото.\nПосле всех фото нажмите кнопку «📤 Отправить отчёт»."
    )

@dp.callback_query(lambda c: c.data == "cancel")
async def on_cancel(cq: CallbackQuery):
    await cq.answer()
    sess = user_sessions.pop(cq.from_user.id, None)
    if sess:
        clear_summary_task(sess)
        # подчистим локальные файлы
        for p in sess.get("files", []):
            try:
                os.remove(p)
            except Exception:
                pass
        tmpdir = sess.get("tmp_dir")
        try:
            if tmpdir and os.path.isdir(tmpdir) and not os.listdir(tmpdir):
                os.rmdir(tmpdir)
        except Exception:
            pass
    await cq.message.answer("Отменено. Начни заново: /otchet")

# ====== ФОТО: без спама, статус по таймеру тишины ======
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("store") or not session.get("project"):
        await message.answer("Пожалуйста, сначала вызови /otchet, выбери проект и магазин.")
        return

    # сохраняем фото
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_filename = os.path.join(session["tmp_dir"], f"{ts}_{photo.file_id}.jpg")
    await bot.download_file(file_info.file_path, destination=local_filename)
    session["files"].append(local_filename)

    # планируем ОДИН статус после паузы
    await schedule_summary_message(message, user_id)

# ====== ОТПРАВИТЬ ОТЧЁТ ======
@dp.callback_query(lambda c: c.data == "confirm_upload")
async def on_confirm_upload(cq: CallbackQuery):
    await cq.answer()
    user_id = cq.from_user.id
    session = user_sessions.get(user_id)
    if not session or not session.get("files") or not session.get("store") or not session.get("project"):
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

    project = session["project"]
    store = session["store"]
    files = list(session["files"])
    week = week_folder_moscow()
    store_path = project_store_path(project, store, week)

    if not store_path:
        try:
            await bot.delete_message(loading.chat.id, loading.message_id)
        except Exception:
            pass
        await cq.message.answer("Ошибка маршрутизации (проект/клиент). Обратитесь к администратору.")
        return

    def do_upload():
        # Создаём папки
        base_path = os.path.dirname(os.path.dirname(store_path))  # .../<base>/<week>
        week_path = os.path.dirname(store_path)                   # .../<base>/<week>/<store> -> parent is week
        ensure_folder_exists(base_path)  # на случай, если <base> не существует — этот вызов создает <base> (idempotent)
        ensure_folder_exists(os.path.dirname(store_path))  # <...>/<week>
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

    loop = asyncio.get_event_loop()  # совместимо с Python 3.8
    uploaded, total = await loop.run_in_executor(None, do_upload)

    # пометим, что у этого магазина есть отчёт на этой неделе
    if uploaded > 0:
        # ключ: (project, clientOrNone, week)
        client_key = None
        if project == "GREENWORKS":
            client_key = get_client_for_store_in_greenworks(store)
        key = (project, client_key, week)
        submitted_by_week.setdefault(key, set()).add(store)

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
    # Выбор проекта
    await message.answer("Статус: выберите проект:", reply_markup=build_projects_kb(for_admin=True))

@dp.callback_query(lambda c: c.data and c.data.startswith("status:client:"))
async def status_pick_client(cq: CallbackQuery):
    await cq.answer()
    _, _, project, who = cq.data.split(":", 3)
    week = week_folder_moscow()

    # Соберём список магазинов проекта/клиента
    target_clients: List[Optional[str]] = []
    if project_has_clients(project):
        if who == "*":
            target_clients = [c for c in get_clients(project)]
        else:
            target_clients = [who]
    else:
        target_clients = [None]

    done_set: Set[str] = set()
    for c in target_clients:
        if c is None:
            base = get_project_base(project)
            week_path = f"{base}/{week}"
            dirs = set(list_folder_children(week_path))
            if not dirs:
                key = (project, None, week)
                dirs = submitted_by_week.get(key, set())
            done_set |= dirs
        else:
            base = PROJECTS[project]["clients"][c]["base"]
            week_path = f"{base}/{week}"
            dirs = set(list_folder_children(week_path))
            if not dirs:
                key = (project, c, week)
                dirs = submitted_by_week.get(key, set())
            done_set |= dirs

    # Полный список для сравнения
    if project_has_clients(project):
        all_stores = get_all_stores(project)
    else:
        all_stores = PROJECTS[project].get("stores", [])

    done = sorted([s for s in all_stores if s in done_set])
    missing = sorted([s for s in all_stores if s not in done_set])

    lines = [f"📁 Проект: {project}", f"📆 Неделя: {week}", f"✅ Сдали: {len(done)} / {len(all_stores)}"]
    if done:
        lines.append("\nСдали:")
        for s in done:
            lines.append(f"• {s}")
    if missing:
        lines.append("\n❌ Не сдали:")
        for s in missing:
            lines.append(f"• {s}")
    if not done and not missing:
        lines.append("\nНет данных по этому проекту.")

    # кнопка назад к выбору проекта
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к проектам", callback_data="adm:back:pickproj")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="adm:cancel")],
    ])
    await cq.message.answer("\n".join(lines), reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("proj:client:"))
async def status_project_to_client(cq: CallbackQuery):
    # Это обработчик выбора клиента в админ-контексте — для статуса
    await cq.answer()
    _, _, project, client = cq.data.split(":", 3)
    await status_pick_client(CallbackQuery(
        id=cq.id, from_user=cq.from_user, chat_instance=cq.chat_instance,
        message=cq.message, data=f"status:client:{project}:{client}"
    ))

@dp.callback_query(lambda c: c.data == "adm:back:pickproj")
async def status_back_pickproj(cq: CallbackQuery):
    await cq.answer()
    await cq.message.answer("Статус: выберите проект:", reply_markup=build_projects_kb(for_admin=True))

@dp.callback_query(lambda c: c.data and c.data.startswith("proj:"))
async def status_or_otchet_router(cq: CallbackQuery):
    """
    Этот обработчик уже есть выше для обычного /otchet, поэтому чтобы не конфликтовать,
    мы не используем сюда статус. Статус запускается отдельной веткой.
    """
    pass

# ====== АДМИН МЕНЮ: add / del store, del project ======
@dp.message(Command("addstore"))
async def addstore_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    admin_flows[message.from_user.id] = {"mode": "add", "step": "pick_project"}
    await message.answer("Добавить магазин: выберите проект:", reply_markup=build_projects_kb(for_admin=True))

@dp.message(Command("delstore"))
async def delstore_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    admin_flows[message.from_user.id] = {"mode": "del", "step": "pick_project"}
    await message.answer("Удалить магазин: выберите проект:", reply_markup=build_projects_kb(for_admin=True))

@dp.message(Command("delproject"))
async def delproject_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    admin_flows[message.from_user.id] = {"mode": "delproject", "step": "pick_project"}
    # Кнопки проектов с подтверждением позже
    buttons = [InlineKeyboardButton(text=p, callback_data=f"adm:delproj:pick:{p}") for p in list_projects()]
    rows = rows_of(buttons, cols=2)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("Удалить проект: выберите проект:", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "adm:cancel")
async def admin_cancel(cq: CallbackQuery):
    await cq.answer()
    admin_flows.pop(cq.from_user.id, None)
    await cq.message.answer("Админ-операция отменена.")

# ---- addstore flow ----
@dp.callback_query(lambda c: c.data and c.data.startswith("proj:") and c.from_user.id == ADMIN_ID)
async def admin_pick_project(cq: CallbackQuery):
    # Эта ветка для админ-флоу (add/del), отличаем по наличию admin_flows
    flow = admin_flows.get(cq.from_user.id)
    if not flow:
        return  # не в админ-процессе
    await cq.answer()
    project = cq.data.split(":", 1)[1]
    flow["project"] = project

    if flow["mode"] == "add":
        if project_has_clients(project):
            flow["step"] = "pick_client_for_add"
            await cq.message.answer(
                f"Проект: {project}\nВыберите клиента:",
                reply_markup=build_clients_kb(project, admin_flow=True)
            )
        else:
            flow["step"] = "enter_store_name"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")],
            ])
            await cq.message.answer("Введите название магазина одним сообщением:", reply_markup=kb)

    elif flow["mode"] == "del":
        # Покажем список магазинов для удаления
        if project_has_clients(project):
            flow["step"] = "pick_client_for_del_or_all"
            # Для GREENWORKS можно выбрать конкретного клиента или «Все»
            buttons = [InlineKeyboardButton(text="Все", callback_data=f"adm:del:cl:{project}:*")]
            for c in get_clients(project):
                buttons.append(InlineKeyboardButton(text=c, callback_data=f"adm:del:cl:{project}:{c}"))
            rows = rows_of(buttons, cols=2)
            rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")])
            rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
            await cq.message.answer(f"Проект: {project}\nВыберите клиента (или «Все»):", reply_markup=kb)
        else:
            flow["step"] = "del_pick_store"
            stores = PROJECTS[project].get("stores", [])
            # сохраним индексную таблицу для callback
            flow["del_list"] = [(s, None) for s in stores]
            buttons = [InlineKeyboardButton(text=s, callback_data=f"adm:del:idx:{i}") for i, (s, _) in enumerate(flow["del_list"])]
            rows = rows_of(buttons, cols=1)
            rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")])
            rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
            await cq.message.answer(f"Проект: {project}\nВыберите магазин для удаления:", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("adm:back:"))
async def admin_back(cq: CallbackQuery):
    await cq.answer()
    flow = admin_flows.get(cq.from_user.id)
    if not flow:
        return
    back_to = cq.data.split(":", 2)[2]
    if back_to == "pickproj":
        flow["step"] = "pick_project"
        await cq.message.answer("Выберите проект:", reply_markup=build_projects_kb(for_admin=True))

@dp.callback_query(lambda c: c.data and c.data.startswith("proj:client:") and c.from_user.id == ADMIN_ID)
async def admin_pick_client_for_add(cq: CallbackQuery):
    flow = admin_flows.get(cq.from_user.id)
    if not flow or flow.get("mode") != "add" or flow.get("step") != "pick_client_for_add":
        return
    await cq.answer()
    _, _, project, client = cq.data.split(":", 3)
    flow["client"] = client
    flow["step"] = "enter_store_name"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")],
    ])
    await cq.message.answer("Введите название магазина одним сообщением:", reply_markup=kb)

@dp.message(F.text)
async def admin_enter_store_name_or_ignore(message: Message):
    # Обрабатываем ввод названия магазина в addstore
    flow = admin_flows.get(message.from_user.id)
    if not flow or flow.get("mode") != "add" or flow.get("step") != "enter_store_name":
        return  # это не админ-ввод
    name = message.text.strip()
    project = flow["project"]
    if project_has_clients(project):
        client = flow.get("client")
        if not client:
            await message.answer("Сначала выберите клиента.")
            return
        stores = PROJECTS[project]["clients"][client].setdefault("stores", [])
        if name in stores:
            await message.answer("Такой магазин уже есть у этого клиента.")
            return
        stores.append(name)
    else:
        stores = PROJECTS[project].setdefault("stores", [])
        if name in stores:
            await message.answer("Такой магазин уже есть в этом проекте.")
            return
        stores.append(name)

    save_projects(PROJECTS)
    admin_flows.pop(message.from_user.id, None)
    await message.answer(f"✅ Магазин «{name}» добавлен в проект {project}.")

# ---- delstore flow (продолжение) ----
@dp.callback_query(lambda c: c.data and c.data.startswith("adm:del:cl:") and c.from_user.id == ADMIN_ID)
async def admin_del_pick_client_or_all(cq: CallbackQuery):
    await cq.answer()
    _, _, _, project, which = cq.data.split(":", 4)
    flow = admin_flows.get(cq.from_user.id)
    if not flow or flow.get("mode") != "del":
        return
    flow["project"] = project
    flow["step"] = "del_pick_store"
    del_list: List[Tuple[str, Optional[str]]] = []
    if which == "*":
        for c in get_clients(project):
            for s in PROJECTS[project]["clients"][c].get("stores", []):
                del_list.append((s, c))
    else:
        for s in PROJECTS[project]["clients"][which].get("stores", []):
            del_list.append((s, which))
    flow["del_list"] = del_list
    buttons = [InlineKeyboardButton(text=f"{s} ({cl})" if cl else s, callback_data=f"adm:del:idx:{i}") for i, (s, cl) in enumerate(del_list)]
    rows = rows_of(buttons, cols=1)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await cq.message.answer("Выберите магазин для удаления:", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("adm:del:idx:") and c.from_user.id == ADMIN_ID)
async def admin_del_do(cq: CallbackQuery):
    await cq.answer()
    flow = admin_flows.get(cq.from_user.id)
    if not flow or flow.get("mode") != "del":
        return
    idx = int(cq.data.split(":", 3)[3])
    if "del_list" not in flow or idx < 0 or idx >= len(flow["del_list"]):
        await cq.message.answer("Список устарел. Начните заново: /delstore")
        return
    project = flow["project"]
    store, client = flow["del_list"][idx]

    removed = False
    if project_has_clients(project):
        if client:
            lst = PROJECTS[project]["clients"][client].get("stores", [])
            if store in lst:
                lst.remove(store)
                removed = True
        else:
            # защитный сценарий «все клиенты» — ищем и удаляем первое вхождение
            for c in get_clients(project):
                lst = PROJECTS[project]["clients"][c].get("stores", [])
                if store in lst:
                    lst.remove(store)
                    removed = True
                    break
    else:
        lst = PROJECTS[project].get("stores", [])
        if store in lst:
            lst.remove(store)
            removed = True

    if removed:
        save_projects(PROJECTS)
        await cq.message.answer(f"🗑 Удалено: «{store}» из проекта {project}.")
    else:
        await cq.message.answer("Не удалось удалить (не найден).")

    admin_flows.pop(cq.from_user.id, None)

# ---- delproject flow ----
@dp.callback_query(lambda c: c.data and c.data.startswith("adm:delproj:pick:") and c.from_user.id == ADMIN_ID)
async def delproj_pick(cq: CallbackQuery):
    await cq.answer()
    project = cq.data.split(":", 3)[3]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить удаление", callback_data=f"adm:delproj:confirm:{project}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:pickproj")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")],
    ])
    await cq.message.answer(f"Внимание! Будет удалён проект «{project}» из конфигурации (файлы на Я.Диске НЕ трогаем). Подтвердить?", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("adm:delproj:confirm:") and c.from_user.id == ADMIN_ID)
async def delproj_confirm(cq: CallbackQuery):
    await cq.answer()
    project = cq.data.split(":", 3)[3]
    if project in PROJECTS:
        PROJECTS.pop(project)
        save_projects(PROJECTS)
        await cq.message.answer(f"✅ Проект «{project}» удалён из конфигурации.")
    else:
        await cq.message.answer("Проект не найден.")
    admin_flows.pop(cq.from_user.id, None)

# ====== АДМИН-СТАТУС: выбор проекта -> (если GREENWORKS) выбор клиента/все ======
@dp.message(Command("status"))
async def status_start_again(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда недоступна.")
        return
    # перезаписываем ради удобства
    buttons = []
    for p in list_projects():
        if project_has_clients(p):
            buttons.append(InlineKeyboardButton(text=p, callback_data=f"status:pickproj:{p}"))
        else:
            buttons.append(InlineKeyboardButton(text=p, callback_data=f"status:client:{p}:*"))  # * как «без клиентов»
    rows = rows_of(buttons, cols=2)
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="adm:cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("Статус: выберите проект:", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("status:pickproj:"))
async def status_pickproj_greenworks(cq: CallbackQuery):
    await cq.answer()
    project = cq.data.split(":", 2)[2]
    if not project_has_clients(project):
        # для надёжности
        await status_pick_client(CallbackQuery(
            id=cq.id, from_user=cq.from_user, chat_instance=cq.chat_instance,
            message=cq.message, data=f"status:client:{project}:*"
        ))
        return
    kb = build_clients_kb(project, include_all_for_status=True, admin_flow=True)
    await cq.message.answer(f"Проект: {project}\nВыберите клиента или «Все клиенты»:", reply_markup=kb)

# ====== on_startup: меню ======
async def on_startup(bot: Bot):
    try:
        # глобально – только /otchet
        await bot.set_my_commands(
            commands=[BotCommand(command="otchet", description="Начать отчёт")]
        )
    except Exception as e:
        logging.warning("Can't set global menu: %s", e)
    try:
        # для админа – расширенное меню
        await bot.set_my_commands(
            commands=[
                BotCommand(command="otchet", description="Начать отчёт"),
                BotCommand(command="status", description="Статус отчётов"),
                BotCommand(command="addstore", description="Добавить магазин"),
                BotCommand(command="delstore", description="Удалить магазин"),
                BotCommand(command="delproject", description="Удалить проект"),
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
