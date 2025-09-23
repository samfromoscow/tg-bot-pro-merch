# bot.py
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Рекомендуется хранить токен в окружении
if not BOT_TOKEN:
    # fallback — можешь подставить свой токен, но лучше один раз выставить переменную окружения
    BOT_TOKEN = "PUT_YOUR_TOKEN_HERE"

ADMIN_IDS = {445526501}  # Sam

DATA_DIR = Path(__file__).parent
PROJECTS_FILE = DATA_DIR / "projects.json"
SUBMITS_FILE = DATA_DIR / "submits.json"

CROWN = "CROWN"
PIT = "PIT"
GREENWORKS = "GREENWORKS"

# ================== ЛОГИ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger("bot")

# ================== ХЕЛПЕРЫ ДАТ ==================
def week_label_msk() -> str:
    """Строка недели вида 'dd.mm-dd.mm' по МСК."""
    now = datetime.now(timezone(timedelta(hours=3)))
    start = now - timedelta(days=now.weekday())  # понедельник
    end = start + timedelta(days=6)              # воскресенье
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

def week_key() -> str:
    now = datetime.now(timezone(timedelta(hours=3)))
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}:{week_label_msk()}"

# ================== РАБОТА С ДИСКОМ ==================
def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def ensure_files():
    # projects.json — создаём только при отсутствии
    if not PROJECTS_FILE.exists():
        seed = {
            PIT: {
                "title": "PIT",
                "base": "/Sam/Проект PIT/Фотоотчеты PIT",
                "clients": [],
                "stores": [
                    {"name": "ОБИ 013 Белая дача", "client": None},
                    {"name": "ОБИ 042 Брянск", "client": None},
                    {"name": "ОБИ 006 Боровка", "client": None},
                    {"name": "ОБИ 037 Авиапарк", "client": None},
                    {"name": "ОБИ 039 Новая Рига", "client": None},  # <— исправлено
                    {"name": "ОБИ 001 Теплый стан", "client": None},
                ],
            },
            GREENWORKS: {
                "title": "GREENWORKS",
                "base": "/Sam/Проект Seasons/Фотоотчеты Greenworks seasons",
                "clients": ["Михаил", "Александр"],
                "stores": [
                    # Михаил
                    {"name": "Бау Центр Дзержинка Калининград", "client": "Михаил"},
                    {"name": "Бау Центр Московский Калининград", "client": "Михаил"},
                    {"name": "Бау Центр Новороссийск", "client": "Михаил"},
                    {"name": "Бау Центр Пушкино", "client": "Михаил"},
                    {"name": "Дарвин Зеленоград", "client": "Михаил"},
                    {"name": "Дарвин Подольск", "client": "Михаил"},
                    {"name": "Дарвин Пушкино", "client": "Михаил"},
                    {"name": "Колорлон Новосибирск", "client": "Михаил"},
                    {"name": "Колорлон, Бредск", "client": "Михаил"},
                    {"name": "Петрович Дмитровка", "client": "Михаил"},
                    {"name": "Петрович Санкт-Петербург", "client": "Михаил"},
                    # Александр
                    {"name": "Вектор Пенза", "client": "Александр"},
                    {"name": "Дачник Демская", "client": "Александр"},
                    {"name": "Дачник Романтиков", "client": "Александр"},
                    {"name": "Моя Родня Окружная", "client": "Александр"},
                    {"name": "Моя Родня Рахманинова", "client": "Александр"},
                    {"name": "Моя Родня Терновского", "client": "Александр"},
                    {"name": "Сарай (Ульяновск)", "client": "Александр"},
                    {"name": "Строй-С Гвардейская", "client": "Александр"},
                    {"name": "Строй-С Усть-Курдюмская", "client": "Александр"},
                    {"name": "Юрат Чебоксары", "client": "Александр"},
                ],
            },
            CROWN: {
                "title": "CROWN",
                "base": "/Sam/Проект CROWN/Фотоотчеты CROWN",
                "clients": [],
                "stores": [],  # не трогаем — добавляешь сам в бою
            },
        }
        save_json(PROJECTS_FILE, seed)
        log.info("projects.json created with seed")
    # submits.json
    if not SUBMITS_FILE.exists():
        save_json(SUBMITS_FILE, {})
        log.info("submits.json created")

def load_projects() -> Dict[str, Any]:
    return load_json(PROJECTS_FILE, {})

def save_projects(data: Dict[str, Any]) -> None:
    save_json(PROJECTS_FILE, data)

def list_projects() -> List[str]:
    data = load_projects()
    # Порядок: CROWN, PIT, GREENWORKS если есть
    order = [CROWN, PIT, GREENWORKS]
    names = [p for p in order if p in data] + [k for k in data.keys() if k not in order]
    return names

def get_clients(slug: str) -> List[str]:
    data = load_projects()
    item = data.get(slug, {})
    return item.get("clients", []) or []

def get_stores(slug: str, client: Optional[str] = None) -> List[Dict[str, Any]]:
    data = load_projects()
    stores = data.get(slug, {}).get("stores", [])
    if client:
        stores = [s for s in stores if s.get("client") == client]
    return stores

def add_store(slug: str, name: str, client: Optional[str]) -> None:
    data = load_projects()
    proj = data.setdefault(slug, {"title": slug, "base": "", "clients": [], "stores": []})
    # если клиент для GREENWORKS, но не добавлен в список — добавим
    if client and client not in proj.get("clients", []):
        proj.setdefault("clients", []).append(client)
    proj.setdefault("stores", []).append({"name": name, "client": client})
    save_projects(data)

def del_store(slug: str, index: int) -> Optional[str]:
    data = load_projects()
    stores = data.get(slug, {}).get("stores", [])
    if 0 <= index < len(stores):
        name = stores[index]["name"]
        del stores[index]
        save_projects(data)
        return name
    return None

def del_project(slug: str) -> bool:
    data = load_projects()
    if slug in data:
        del data[slug]
        save_projects(data)
        return True
    return False

def mark_submitted(slug: str, store_name: str):
    db = load_json(SUBMITS_FILE, {})
    key = week_key()
    proj = db.setdefault(key, {}).setdefault(slug, {})
    proj[store_name] = True
    save_json(SUBMITS_FILE, db)

def get_submitted(slug: str) -> Dict[str, bool]:
    db = load_json(SUBMITS_FILE, {})
    key = week_key()
    return db.get(key, {}).get(slug, {})

# ================== КЛАВИАТУРЫ ==================
def rows_of(buttons: List[InlineKeyboardButton], cols: int) -> List[List[InlineKeyboardButton]]:
    if cols <= 1:
        return [[b] for b in buttons]
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]

def kb_projects(ns: str, admin: bool = False) -> InlineKeyboardMarkup:
    """ns задаёт пространство имён для коллбэков:
       report / admadd / admdel / admstatus / admdelproj
    """
    names = list_projects()
    buttons = [InlineKeyboardButton(text=p, callback_data=f"{ns}:proj:{p}") for p in names]
    rows = rows_of(buttons, cols=2)
    # нижний ряд
    if admin:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ns}:cancel")])
    else:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="report:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_clients(ns: str, project: str, include_all_for_status: bool = False) -> InlineKeyboardMarkup:
    clients = get_clients(project)
    buttons: List[InlineKeyboardButton] = []
    for c in clients:
        buttons.append(InlineKeyboardButton(text=c, callback_data=f"{ns}:client:{project}:{c}"))
    if include_all_for_status:
        buttons.insert(0, InlineKeyboardButton(text="Все клиенты", callback_data=f"{ns}:client:{project}:*"))
    rows = rows_of(buttons, cols=2)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{ns}:back:projects")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ns}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_stores(ns: str, project: str, client: Optional[str], for_greenworks_vertical: bool, show_delete: bool = False) -> InlineKeyboardMarkup:
    stores = get_stores(project, client=None if show_delete else client)  # для удаления — показываем все
    # индексируем для безопасного короткого callback
    buttons: List[InlineKeyboardButton] = []
    for idx, s in enumerate(stores):
        title = s["name"]
        if show_delete:
            cb = f"{ns}:store:{project}:{idx}"
        else:
            cb = f"{ns}:store:{project}:{(client or '-') }:{idx}"
        buttons.append(InlineKeyboardButton(text=title, callback_data=cb))
    cols = 1 if (project == GREENWORKS and for_greenworks_vertical) else 2
    rows = rows_of(buttons, cols=cols)
    # back
    if show_delete:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{ns}:back:projects")])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ns}:cancel")])
    else:
        # назад либо к клиентам (если есть), либо к проектам
        if get_clients(project):
            rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{ns}:back:clients:{project}")])
        else:
            rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{ns}:back:projects")])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ns}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_done_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Готово", callback_data="report:done"),
    ], [
        InlineKeyboardButton(text="❌ Отмена", callback_data="report:cancel"),
    ]])

def kb_confirm_delete_project(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить проект", callback_data=f"admdelproj:confirm:{slug}:yes"),
    ], [
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admdelproj:back:projects"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admdelproj:cancel"),
    ]])

# ================== СОСТОЯНИЯ ==================
class ReportFSM(StatesGroup):
    choose_project = State()
    choose_client = State()
    choose_store = State()
    wait_photos = State()

class AddStoreFSM(StatesGroup):
    choose_project = State()
    choose_client = State()
    wait_name = State()

class DelStoreFSM(StatesGroup):
    choose_project = State()
    choose_store = State()

class DelProjectFSM(StatesGroup):
    choose_project = State()
    confirm = State()

class StatusFSM(StatesGroup):
    choose_project = State()
    choose_client = State()

# ================== ИНИЦИАЛИЗАЦИЯ ==================
router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ================== КОМАНДЫ ==================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "Привет! 👋\n\n"
        "Основные команды:\n"
        "• /otchet — отправить фотоотчёт\n"
        "• /status — статус «сдали / не сдали» (админ)\n"
        "• /addstore — добавить магазин (админ)\n"
        "• /delstore — удалить магазин (админ)\n"
        "• /delproject — удалить проект целиком (админ)"
    )
    await message.answer(text)

# ----------- ОТЧЁТ -----------
@router.message(Command("otchet"))
async def cmd_otchet(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ReportFSM.choose_project)
    await message.answer("Выберите проект:", reply_markup=kb_projects("report", admin=False))

@router.callback_query(F.data.startswith("report:proj:"), ReportFSM.choose_project)
async def report_pick_project(cb: CallbackQuery, state: FSMContext):
    _, _, slug = cb.data.split(":", 2)
    await cb.answer()
    clients = get_clients(slug)
    await state.update_data(project=slug)
    if clients:
        await state.set_state(ReportFSM.choose_client)
        await cb.message.edit_text("Выберите клиента:", reply_markup=kb_clients("report", slug))
    else:
        await state.set_state(ReportFSM.choose_store)
        await cb.message.edit_text("Выберите магазин:", reply_markup=kb_stores("report", slug, client=None, for_greenworks_vertical=True))

@router.callback_query(F.data.startswith("report:client:"), ReportFSM.choose_client)
async def report_pick_client(cb: CallbackQuery, state: FSMContext):
    _, _, slug, client = cb.data.split(":", 3)
    await cb.answer()
    if client == "*":
        client = None
    await state.update_data(client=client)
    await state.set_state(ReportFSM.choose_store)
    await cb.message.edit_text(
        "Выберите магазин:",
        reply_markup=kb_stores("report", slug, client=client, for_greenworks_vertical=True)
    )

@router.callback_query(F.data.startswith("report:store:"), ReportFSM.choose_store)
async def report_pick_store(cb: CallbackQuery, state: FSMContext):
    # report:store:<project>:<client or ->:<idx>
    parts = cb.data.split(":")
    _, _, slug, client_or_dash, idx_s = parts
    client = None if client_or_dash == "-" else client_or_dash
    idx = int(idx_s)
    stores = get_stores(slug, client=client)
    if not (0 <= idx < len(stores)):
        await cb.answer("Не найдено", show_alert=True)
        return
    store_name = stores[idx]["name"]
    await state.update_data(project=slug, client=client, store_index=idx, store_name=store_name)

    await cb.answer()
    # удаляем клавиатуру выбора магазина
    try:
        await cb.message.delete()
    except Exception:
        pass

    await state.set_state(ReportFSM.wait_photos)
    await cb.message.bot.send_message(
        cb.from_user.id,
        f"📸 Отправляй фото для:\n<b>{slug}</b>\n{('Клиент: ' + client + '\n') if client else ''}"
        f"Магазин: <b>{store_name}</b>\n\n"
        "Когда закончишь — нажми «Готово».",
        reply_markup=kb_done_cancel()
    )

@router.message(F.photo, ReportFSM.wait_photos)
async def report_receive_photo(message: Message, state: FSMContext):
    # помечаем как сданный при первом фото
    data = await state.get_data()
    slug = data.get("project")
    store_name = data.get("store_name")
    if slug and store_name:
        mark_submitted(slug, store_name)
    # можно просто подтвердить
    await message.answer("✅ Фото получено. Ещё можно присылать. Нажми «Готово» когда закончишь.")

@router.callback_query(F.data == "report:done", ReportFSM.wait_photos)
async def report_done(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Готово!")
    await state.clear()
    await cb.message.edit_text("Спасибо! Отчёт сохранён на эту неделю ✅")

@router.callback_query(F.data == "report:cancel")
async def report_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Отменено")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("report:back:"))
async def report_back(cb: CallbackQuery, state: FSMContext):
    # report:back:projects | report:back:clients:<project>
    parts = cb.data.split(":")
    await cb.answer()
    if parts[2] == "projects":
        await state.set_state(ReportFSM.choose_project)
        await cb.message.edit_text("Выберите проект:", reply_markup=kb_projects("report", admin=False))
    elif parts[2] == "clients":
        slug = parts[3]
        await state.set_state(ReportFSM.choose_client)
        await cb.message.edit_text("Выберите клиента:", reply_markup=kb_clients("report", slug))
    else:
        await cb.answer()

# ----------- СТАТУС (АДМИН) -----------
@router.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ только для администратора.")
        return
    await state.clear()
    await state.set_state(StatusFSM.choose_project)
    await message.answer("Статус: выберите проект:", reply_markup=kb_projects("admstatus", admin=True))

@router.callback_query(F.data.startswith("admstatus:proj:"), StatusFSM.choose_project)
async def status_pick_proj(cb: CallbackQuery, state: FSMContext):
    _, _, slug = cb.data.split(":", 2)
    await cb.answer()
    await state.update_data(project=slug)
    clients = get_clients(slug)
    if clients:
        await state.set_state(StatusFSM.choose_client)
        await cb.message.edit_text("Статус: выберите клиента:", reply_markup=kb_clients("admstatus", slug, include_all_for_status=True))
    else:
        # сразу показываем статус
        await show_status(cb, slug, None)
        await state.clear()

@router.callback_query(F.data.startswith("admstatus:client:"), StatusFSM.choose_client)
async def status_pick_client(cb: CallbackQuery, state: FSMContext):
    _, _, slug, client = cb.data.split(":", 3)
    await cb.answer()
    if client == "*":
        client = None
    await show_status(cb, slug, client)
    await state.clear()

async def show_status(cb: CallbackQuery, slug: str, client: Optional[str]):
    all_stores = [s["name"] for s in get_stores(slug, client=client)]
    submitted_map = get_submitted(slug)
    submitted = [s for s in all_stores if submitted_map.get(s)]
    not_submitted = [s for s in all_stores if s not in submitted]
    wl = week_label_msk()
    txt = (
        f"📊 Статус за неделю {wl}\n<b>{slug}</b>{(' • ' + client) if client else ''}\n\n"
        f"✅ Сдали ({len(submitted)}):\n" + ("\n".join(submitted) if submitted else "—") + "\n\n"
        f"❌ Не сдали ({len(not_submitted)}):\n" + ("\n".join(not_submitted) if not_submitted else "—")
    )
    await cb.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admstatus:back:projects"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="admstatus:cancel"),
    ]]))

@router.callback_query(F.data == "admstatus:cancel")
async def status_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Закрыто")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "admstatus:back:projects")
async def status_back_to_projects(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(StatusFSM.choose_project)
    await cb.message.edit_text("Статус: выберите проект:", reply_markup=kb_projects("admstatus", admin=True))

# ----------- ДОБАВИТЬ МАГАЗИН (АДМИН) -----------
@router.message(Command("addstore"))
async def cmd_addstore(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ только для администратора.")
        return
    await state.clear()
    await state.set_state(AddStoreFSM.choose_project)
    await message.answer("Добавить магазин: выберите проект:", reply_markup=kb_projects("admadd", admin=True))

@router.callback_query(F.data.startswith("admadd:proj:"), AddStoreFSM.choose_project)
async def addstore_pick_project(cb: CallbackQuery, state: FSMContext):
    _, _, slug = cb.data.split(":", 2)
    await cb.answer()
    await state.update_data(project=slug)
    clients = get_clients(slug)
    if clients:
        await state.set_state(AddStoreFSM.choose_client)
        await cb.message.edit_text("К какому клиенту добавить магазин?", reply_markup=kb_clients("admadd", slug))
    else:
        await state.set_state(AddStoreFSM.wait_name)
        await cb.message.edit_text("Пришлите название магазина (или нажмите «Отмена»).",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                       InlineKeyboardButton(text="⬅️ Назад", callback_data="admadd:back:projects"),
                                       InlineKeyboardButton(text="❌ Отмена", callback_data="admadd:cancel"),
                                   ]]))

@router.callback_query(F.data.startswith("admadd:client:"), AddStoreFSM.choose_client)
async def addstore_pick_client(cb: CallbackQuery, state: FSMContext):
    _, _, slug, client = cb.data.split(":", 3)
    await cb.answer()
    await state.update_data(project=slug, client=client)
    await state.set_state(AddStoreFSM.wait_name)
    await cb.message.edit_text(
        f"Клиент: {client}\nПришлите название магазина.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admadd:back:projects"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admadd:cancel"),
        ]])
    )

@router.message(AddStoreFSM.wait_name)
async def addstore_receive_name(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустое имя. Пришли название или нажми «Отмена».")
        return
    data = await state.get_data()
    slug = data.get("project")
    client = data.get("client")  # None для PIT/CROWN; выбранный для GREENWORKS
    add_store(slug, text, client)
    await state.clear()
    await message.answer(f"✅ Магазин «{text}» добавлен в проект {slug}.")

@router.callback_query(F.data == "admadd:cancel")
async def addstore_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Отменено")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "admadd:back:projects")
async def addstore_back_projects(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(AddStoreFSM.choose_project)
    await cb.message.edit_text("Добавить магазин: выберите проект:", reply_markup=kb_projects("admadd", admin=True))

# ----------- УДАЛИТЬ МАГАЗИН (АДМИН) -----------
@router.message(Command("delstore"))
async def cmd_delstore(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ только для администратора.")
        return
    await state.clear()
    await state.set_state(DelStoreFSM.choose_project)
    await message.answer("Удалить магазин: выберите проект:", reply_markup=kb_projects("admdel", admin=True))

@router.callback_query(F.data.startswith("admdel:proj:"), DelStoreFSM.choose_project)
async def delstore_pick_project(cb: CallbackQuery, state: FSMContext):
    _, _, slug = cb.data.split(":", 2)
    await cb.answer()
    await state.update_data(project=slug)
    await state.set_state(DelStoreFSM.choose_store)
    await cb.message.edit_text(
        "Выберите магазин для удаления:",
        reply_markup=kb_stores("admdel", slug, client=None, for_greenworks_vertical=True, show_delete=True)
    )

@router.callback_query(F.data.startswith("admdel:store:"), DelStoreFSM.choose_store)
async def delstore_do_delete(cb: CallbackQuery, state: FSMContext):
    # admdel:store:<project>:<idx>
    _, _, slug, idx_s = cb.data.split(":")
    idx = int(idx_s)
    name = del_store(slug, idx)
    await cb.answer()
    if name:
        # обновим список
        await cb.message.edit_text(
            f"🗑 Удалено: {name}\n\nВыберите следующий магазин или «Назад».",
            reply_markup=kb_stores("admdel", slug, client=None, for_greenworks_vertical=True, show_delete=True)
        )
    else:
        await cb.message.edit_text(
            "Не удалось удалить (не найдено).",
            reply_markup=kb_stores("admdel", slug, client=None, for_greenworks_vertical=True, show_delete=True)
        )

@router.callback_query(F.data == "admdel:cancel")
async def delstore_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Отменено")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "admdel:back:projects")
async def delstore_back_projects(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(DelStoreFSM.choose_project)
    await cb.message.edit_text("Удалить магазин: выберите проект:", reply_markup=kb_projects("admdel", admin=True))

# ----------- УДАЛИТЬ ПРОЕКТ (АДМИН) -----------
@router.message(Command("delproject"))
async def cmd_delproject(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ только для администратора.")
        return
    await state.clear()
    await state.set_state(DelProjectFSM.choose_project)
    await message.answer("Удалить проект: выберите проект:", reply_markup=kb_projects("admdelproj", admin=True))

@router.callback_query(F.data.startswith("admdelproj:proj:"), DelProjectFSM.choose_project)
async def delproject_pick(cb: CallbackQuery, state: FSMContext):
    _, _, slug = cb.data.split(":", 2)
    await cb.answer()
    await state.update_data(project=slug)
    await state.set_state(DelProjectFSM.confirm)
    await cb.message.edit_text(
        f"Подтвердите удаление проекта <b>{slug}</b> и всех его магазинов:",
        reply_markup=kb_confirm_delete_project(slug)
    )

@router.callback_query(F.data.startswith("admdelproj:confirm:"), DelProjectFSM.confirm)
async def delproject_confirm(cb: CallbackQuery, state: FSMContext):
    _, _, slug, answer = cb.data.split(":")
    await cb.answer()
    if answer == "yes":
        ok = del_project(slug)
        await state.clear()
        if ok:
            await cb.message.edit_text(f"✅ Проект {slug} удалён целиком.")
        else:
            await cb.message.edit_text("Не удалось удалить проект (не найден).")
    else:
        await cb.message.edit_text("Операция отменена.")
        await state.clear()

@router.callback_query(F.data == "admdelproj:cancel")
async def delproject_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Отменено")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "admdelproj:back:projects")
async def delproject_back_projects(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(DelProjectFSM.choose_project)
    await cb.message.edit_text("Удалить проект: выберите проект:", reply_markup=kb_projects("admdelproj", admin=True))

# ================== ЗАПУСК ==================
async def main():
    ensure_files()
    bot = Bot(BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(router)
    log.info("✅ Бот запущен и слушает Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("⏹ Остановлено")

