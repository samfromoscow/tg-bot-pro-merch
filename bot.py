# bot.py
# -*- coding: utf-8 -*-
"""
Полный бот: CROWN + PIT + GREENWORKS (мульти-клиент).
Aiogram v3.x, FSM, «Назад/Отмена», очистка меню, статус за неделю, админ-операции.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# ================= ЛОГИ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger("bot")

# ================= ТОКЕН =================
# Токен из переписки — оставлен по просьбе пользователя.
BOT_TOKEN = "8306801846:AAEvDQFoiepNmDaxPi5UVDqiNWmz6tUO_KQ"

# ================= ФАЙЛЫ =================
BASE_DIR = os.path.dirname(__file__)
PROJECTS_FILE = os.path.join(BASE_DIR, "projects.json")
SUBMIT_FILE = os.path.join(BASE_DIR, "submissions.json")

# ================= КОНСТ =================
MSK_TZ = timezone(timedelta(hours=3))
ADMINS: List[int] = [445526501]  # Sam

# ================= ДАТЫ ==================
def current_week_label() -> str:
    """Метка текущей недели ПН-ВС (МСК): DD.MM-DD.MM"""
    now = datetime.now(MSK_TZ)
    start = now - timedelta(days=now.weekday())
    end = start + timedelta(days=6)
    return f"{start.day:02}.{start.month:02}-{end.day:02}.{end.month:02}"

# ================= FSM СОСТОЯНИЯ =========
class Report(StatesGroup):
    waiting_project = State()
    waiting_client  = State()
    waiting_store   = State()
    waiting_photos  = State()

class AdminAdd(StatesGroup):
    waiting_project = State()
    waiting_client  = State()
    waiting_name    = State()

class AdminDelStore(StatesGroup):
    waiting_project = State()
    waiting_store   = State()

class AdminDelProject(StatesGroup):
    confirm = State()

class StatusFlow(StatesGroup):
    waiting_project = State()
    waiting_client  = State()

# ================= УТИЛИТЫ ДАННЫХ =========
def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def ensure_seed():
    """Создать projects.json если отсутствует."""
    projects = load_json(PROJECTS_FILE, default=None)
    if projects is not None:
        return

    pit_base = os.getenv("PIT_BASE", "/Sam/Проект PIT/Фотоотчеты PIT")
    green_m_base = os.getenv("GREENWORKS_BASE_M", "/Sam/Проект Seasons/Фотоотчеты Greenworks seasons/Михаил")
    green_a_base = os.getenv("GREENWORKS_BASE_A", "/Sam/Проект Seasons/Фотоотчеты Greenworks seasons/Александр")
    crown_base = os.getenv("CROWN_BASE", "/Sam/Проект Crown/Фотоотчеты Crown")

    pit_stores = [
        "ОБИ 013 Белая дача",
        "ОБИ 042 Брянск",
        "ОБИ 006 Боровка",
        "ОБИ 037 Авиапарк",
        "ОБИ 039 Новая Рига",  # фикс на "Новая Рига"
        "ОБИ 001 Теплый стан",
    ]

    gw_m = [
        "Бау Центр Дзержинка Калининград",
        "Бау Центр Московский Калининград",
        "Бау Центр Новороссийск",
        "Бау Центр Пушкино",
        "Дарвин Зеленоград",
        "Дарвин Подольск",
        "Дарвин Пушкино",
        "Колорлон Новосибирск",
        "Колорлон, Бредск",
        "Петрович Дмитровка",
        "Петрович Санкт-Петербург",
    ]

    gw_a = [
        "Вектор Пенза",
        "Дачник Демская",
        "Дачник Романтиков",
        "Моя Родня Окружная",
        "Моя Родня Рахманинова",
        "Моя Родня Терновского",
        "Сарай (Ульяновск)",
        "Строй-С Гвардейская",
        "Строй-С Усть-Курдюмская",
        "Юрат Чебоксары",
    ]

    projects_seed = {
        "CROWN": {
            "type": "simple",
            "base": crown_base,
            "stores": []
        },
        "PIT": {
            "type": "simple",
            "base": pit_base,
            "stores": pit_stores
        },
        "GREENWORKS": {
            "type": "multi",
            "clients": ["Михаил", "Александр"],
            "bases": {"Михаил": green_m_base, "Александр": green_a_base},
            "stores": {"Михаил": gw_m, "Александр": gw_a}
        }
    }
    save_json(PROJECTS_FILE, projects_seed)
    log.info("projects.json created with seed")

def load_projects() -> dict:
    return load_json(PROJECTS_FILE, default={})

def save_projects(p: dict) -> None:
    save_json(PROJECTS_FILE, p)

def load_submissions() -> dict:
    return load_json(SUBMIT_FILE, default={})

def save_submissions(s: dict) -> None:
    save_json(SUBMIT_FILE, s)

def list_projects() -> List[str]:
    p = load_projects()
    return sorted(p.keys())

def is_multi(project: str) -> bool:
    p = load_projects()
    data = p.get(project, {})
    return data.get("type") == "multi"

def get_clients(project: str) -> List[str]:
    p = load_projects()
    data = p.get(project, {})
    if data.get("type") == "multi":
        return data.get("clients", [])
    return ["*"]

def get_stores(project: str, client: Optional[str] = None) -> List[str]:
    p = load_projects()
    data = p.get(project, {})
    if data.get("type") == "multi":
        if not client:
            return []
        return data.get("stores", {}).get(client, [])
    return data.get("stores", [])

def get_base_path(project: str, client: Optional[str] = None) -> str:
    p = load_projects()
    data = p.get(project, {})
    if data.get("type") == "multi":
        if not client:
            return ""
        return data.get("bases", {}).get(client, "")
    return data.get("base", "")

def add_store(project: str, name: str, client: Optional[str] = None) -> bool:
    if name.strip().upper() in {"DELETE", "/DELETE", "DEL", "/DEL"}:
        return False
    p = load_projects()
    if project not in p:
        return False
    data = p[project]
    if data.get("type") == "multi":
        if not client:
            return False
        stores = data.setdefault("stores", {}).setdefault(client, [])
    else:
        stores = data.setdefault("stores", [])
    if name not in stores:
        stores.append(name)
        save_projects(p)
    return True

def del_store(project: str, store: str) -> bool:
    p = load_projects()
    if project not in p:
        return False
    data = p[project]
    changed = False
    if data.get("type") == "multi":
        for c in data.get("clients", []):
            lst = data.get("stores", {}).get(c, [])
            if store in lst:
                lst.remove(store)
                changed = True
    else:
        lst = data.get("stores", [])
        if store in lst:
            lst.remove(store)
            changed = True
    if changed:
        save_projects(p)
    return changed

def del_project(project: str) -> bool:
    p = load_projects()
    if project not in p:
        return False
    p.pop(project, None)
    save_projects(p)
    s = load_submissions()
    week = current_week_label()
    if week in s and project in s[week]:
        s[week].pop(project, None)
        save_submissions(s)
    return True

def mark_submitted(project: str, store: str, client: Optional[str]) -> None:
    s = load_submissions()
    week = current_week_label()
    s.setdefault(week, {}).setdefault(project, {})
    key = store if not client else f"{client}::{store}"
    s[week][project][key] = True
    save_submissions(s)

def get_week_status(project: str, client: Optional[str]) -> Tuple[List[str], List[str]]:
    s = load_submissions()
    p = load_projects()
    week = current_week_label()

    submitted: set = set()
    if week in s and project in s[week]:
        submitted = set(k for k, v in s[week][project].items() if v)

    all_stores: List[Tuple[Optional[str], str]] = []
    data = p.get(project, {})
    if data.get("type") == "multi":
        for c in data.get("clients", []):
            if (client is None) or (client == c):
                for st in data.get("stores", {}).get(c, []):
                    all_stores.append((c, st))
    else:
        for st in data.get("stores", []):
            all_stores.append((None, st))

    done, not_done = [], []
    for c, st in all_stores:
        key = st if not c else f"{c}::{st}"
        label = st if not c else f"{st} ({c})"
        if key in submitted:
            done.append(label)
        else:
            not_done.append(label)
    return sorted(done), sorted(not_done)

# ================= КЛАВИАТУРЫ =============
def rows_of(buttons: List[InlineKeyboardButton], cols: int) -> List[List[InlineKeyboardButton]]:
    if cols <= 1:
        return [[b] for b in buttons]
    return [buttons[i:i+cols] for i in range(0, len(buttons), cols)]

def kb_projects(for_admin: bool = False) -> InlineKeyboardMarkup:
    names = list_projects()
    buttons = [InlineKeyboardButton(text=p, callback_data=f"proj:{p}") for p in names]
    rows = rows_of(buttons, cols=2)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel" if for_admin else "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_clients(project: str, include_all_for_status: bool = False, admin_flow: bool=False) -> InlineKeyboardMarkup:
    clients = get_clients(project)
    prefix = "adm" if admin_flow else "proj"
    buttons: List[InlineKeyboardButton] = [
        InlineKeyboardButton(text=c, callback_data=f"{prefix}:client:{project}:{c}") for c in clients
    ]
    if include_all_for_status and not admin_flow:
        buttons.insert(0, InlineKeyboardButton(text="Все клиенты", callback_data=f"status:client:{project}:*"))
    rows = rows_of(buttons, cols=2)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:projects")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel" if admin_flow else "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_stores(project: str, client: Optional[str], admin_flow: bool=False, vertical_greenworks: bool=True) -> InlineKeyboardMarkup:
    stores = get_stores(project, client)
    prefix = "adm" if admin_flow else "proj"
    buttons = [InlineKeyboardButton(text=s, callback_data=f"{prefix}:store:{project}:{client or '*'}:{s}") for s in stores]
    rows = rows_of(buttons, cols=1 if (project == "GREENWORKS" and vertical_greenworks) else 2)
    if is_multi(project):
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:clients:{project}")])
    else:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:projects")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel" if admin_flow else "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_delstore_chooser(project: str) -> InlineKeyboardMarkup:
    p = load_projects()
    data = p.get(project, {})
    btns: List[InlineKeyboardButton] = []
    if data.get("type") == "multi":
        for c in data.get("clients", []):
            for s in data.get("stores", {}).get(c, []):
                btns.append(InlineKeyboardButton(text=f"{s} ({c})", callback_data=f"adm:delstore:{project}:{c}:{s}"))
        rows = rows_of(btns, cols=1)
    else:
        for s in data.get("stores", []):
            btns.append(InlineKeyboardButton(text=s, callback_data=f"adm:delstore:{project}:*:{s}"))
        rows = rows_of(btns, cols=2)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back:projects")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ================= БОТ/ДИСПЕТЧЕР ==========
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ================= ХЭЛПЕРЫ UI =============
async def safe_delete_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def replace_with_menu(message: Message, text: str, kb: InlineKeyboardMarkup, state: FSMContext):
    """Удаляет предыдущее меню и отправляет новое. Запоминает id."""
    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(message.chat.id, last_menu_id)
    m = await message.answer(text, reply_markup=kb)
    await state.update_data(last_menu_msg_id=m.message_id)

# ================= КОМАНДЫ ПОЛЬЗОВАТЕЛЕЙ ===
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "Привет! 👋\n\n"
        "Команды:\n"
        "• /otchet — отправить фотоотчёт\n"
        "• /status — статус сдачи за неделю\n"
        "\nАдмин-команды:\n"
        "• /addstore — добавить магазин\n"
        "• /delstore — удалить магазин\n"
        "• /delproject — удалить проект\n"
    )
    await message.answer(text)

@dp.message(Command("otchet"))
async def cmd_report(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(flow="report")
    kb = kb_projects(for_admin=False)
    await replace_with_menu(message, "Выберите проект:", kb, state)
    await state.set_state(Report.waiting_project)

@dp.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(flow="status")
    kb = kb_projects(for_admin=True)
    await replace_with_menu(message, "Статус: выберите проект:", kb, state)
    await state.set_state(StatusFlow.waiting_project)

# ================= АДМИН ====================
def ensure_admin(user_id: int) -> bool:
    return user_id in ADMINS

@dp.message(Command("addstore"))
async def addstore_start(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await state.clear()
    await state.update_data(flow="add")
    kb = kb_projects(for_admin=True)
    await replace_with_menu(message, "Добавить магазин: выберите проект:", kb, state)
    await state.set_state(AdminAdd.waiting_project)

@dp.message(Command("delstore"))
async def delstore_start(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await state.clear()
    await state.update_data(flow="delstore")
    kb = kb_projects(for_admin=True)
    await replace_with_menu(message, "Удалить магазин: выберите проект:", kb, state)
    await state.set_state(AdminDelStore.waiting_project)

@dp.message(Command("delproject"))
async def delproject_start(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await state.clear()
    await state.update_data(flow="delproject")
    names = list_projects()
    if not names:
        await message.answer("Нет проектов для удаления.")
        return
    builder = InlineKeyboardBuilder()
    for p in names:
        builder.button(text=f"Удалить {p}", callback_data=f"adm:delproject:{p}")
    builder.button(text="❌ Отмена", callback_data="adm:cancel")
    builder.adjust(1)
    m = await message.answer("Выберите проект для удаления (безвозвратно):", reply_markup=builder.as_markup())
    await state.update_data(last_menu_msg_id=m.message_id)
    await state.set_state(AdminDelProject.confirm)

# ================= ОБЩИЕ КОЛБЭКИ ===========
@dp.callback_query(F.data == "cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer("Отменено", show_alert=False)
    try:
        await c.message.edit_text("Операция отменена.")
    except Exception:
        await c.message.answer("Операция отменена.")

@dp.callback_query(F.data == "adm:cancel")
async def cb_adm_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer("Отменено", show_alert=False)
    try:
        await c.message.edit_text("Админ-операция отменена.")
    except Exception:
        await c.message.answer("Админ-операция отменена.")

@dp.callback_query(F.data.startswith("back:"))
async def cb_back(c: CallbackQuery, state: FSMContext):
    """Назад из пользовательских экранов."""
    await c.answer()
    parts = c.data.split(":")
    where = parts[1]

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    flow = data.get("flow", "report")
    if where == "projects":
        kb = kb_projects(for_admin=(flow in {"add","delstore","status"}))
        m = await c.message.answer("Выберите проект:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        if flow == "report":
            await state.set_state(Report.waiting_project)
        elif flow == "add":
            await state.set_state(AdminAdd.waiting_project)
        elif flow == "delstore":
            await state.set_state(AdminDelStore.waiting_project)
        elif flow == "status":
            await state.set_state(StatusFlow.waiting_project)
        else:
            await state.clear()
        return

    if where == "clients":
        project = parts[2] if len(parts) > 2 else data.get("project")
        kb = kb_clients(project, include_all_for_status=False, admin_flow=False)
        m = await c.message.answer(f"Проект: <b>{project}</b>\nВыберите клиента:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        await state.set_state(Report.waiting_client)
        return

@dp.callback_query(F.data.startswith("adm:back:"))
async def cb_adm_back(c: CallbackQuery, state: FSMContext):
    """Назад из админ-экранов к списку проектов."""
    await c.answer()
    data = await state.get_data()
    kb = kb_projects(for_admin=True)
    m = await c.message.answer("Выберите проект:", reply_markup=kb)
    await state.update_data(last_menu_msg_id=m.message_id)
    flow = data.get("flow")
    if flow == "add":
        await state.set_state(AdminAdd.waiting_project)
    elif flow == "delstore":
        await state.set_state(AdminDelStore.waiting_project)
    else:
        await state.clear()

# ================= /otchet =================
@dp.callback_query(Report.waiting_project, F.data.startswith("proj:"))
async def cb_report_pick_project(c: CallbackQuery, state: FSMContext):
    project = c.data.split(":", 1)[1]
    await c.answer()
    await state.update_data(project=project, client=None)

    if is_multi(project):
        kb = kb_clients(project, include_all_for_status=False, admin_flow=False)
        m = await c.message.answer(f"Проект: <b>{project}</b>\nВыберите клиента:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        await state.set_state(Report.waiting_client)
    else:
        kb = kb_stores(project, client=None, admin_flow=False, vertical_greenworks=True)
        m = await c.message.answer(f"Проект: <b>{project}</b>\nВыберите магазин:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        await state.set_state(Report.waiting_store)

@dp.callback_query(Report.waiting_client, F.data.startswith("proj:client:"))
async def cb_report_pick_client(c: CallbackQuery, state: FSMContext):
    _, _, project, client = c.data.split(":", 3)
    await c.answer()
    await state.update_data(project=project, client=client)
    kb = kb_stores(project, client, admin_flow=False, vertical_greenworks=True)
    m = await c.message.answer(
        f"Проект: <b>{project}</b>\nКлиент: <b>{client}</b>\nВыберите магазин:",
        reply_markup=kb
    )
    await state.update_data(last_menu_msg_id=m.message_id)
    await state.set_state(Report.waiting_store)

@dp.callback_query(Report.waiting_store, F.data.startswith("proj:store:"))
async def cb_report_pick_store(c: CallbackQuery, state: FSMContext):
    _, _, project, client, store = c.data.split(":", 4)
    if client == "*":
        client = None
    await c.answer()

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    lines = [f"📸 Отправляй фото для:\n<b>{store}</b>"]
    if client:
        lines.append(f"Клиент: {client}")
    m = await c.message.answer("\n".join(lines))
    await state.update_data(project=project, client=client, store=store, anchor_msg_id=m.message_id)
    await state.set_state(Report.waiting_photos)

@dp.message(Report.waiting_photos, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    project = data.get("project")
    client  = data.get("client")
    store   = data.get("store")

    # Здесь может быть загрузка в Я.Диск. Пока — просто отметка сдачи:
    mark_submitted(project, store, client)
    await message.answer("✅ Принял фото. Спасибо!")

# ================= /status =================
@dp.callback_query(StatusFlow.waiting_project, F.data.startswith("proj:"))
async def cb_status_pick_project(c: CallbackQuery, state: FSMContext):
    project = c.data.split(":", 1)[1]
    await c.answer()
    await state.update_data(project=project)

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    if is_multi(project):
        kb = kb_clients(project, include_all_for_status=True, admin_flow=False)
        m = await c.message.answer("Статус: выберите клиента или «Все клиенты»:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        await state.set_state(StatusFlow.waiting_client)
    else:
        done, not_done = get_week_status(project, None)
        lines = [
            f"📊 Статус за неделю {current_week_label()}",
            f"Проект: <b>{project}</b>",
            "",
            f"✅ Сдали ({len(done)}):",
        ]
        lines += ([f"• {x}" for x in done] if done else ["—"])
        lines += ["", f"❌ Не сдали ({len(not_done)}):"]
        lines += ([f"• {x}" for x in not_done] if not_done else ["—"])
        await c.message.edit_text("\n".join(lines))

@dp.callback_query(StatusFlow.waiting_client, F.data.startswith("status:client:"))
async def cb_status_pick_client(c: CallbackQuery, state: FSMContext):
    _, _, project, client = c.data.split(":", 3)
    await c.answer()
    client_opt = None if client == "*" else client
    done, not_done = get_week_status(project, client_opt)
    header = f"📊 Статус за неделю {current_week_label()}\nПроект: <b>{project}</b>"
    if client_opt:
        header += f"\nКлиент: {client_opt}"
    lines = [header, "", f"✅ Сдали ({len(done)}):"]
    lines += ([f"• {x}" for x in done] if done else ["—"])
    lines += ["", f"❌ Не сдали ({len(not_done)}):"]
    lines += ([f"• {x}" for x in not_done] if not_done else ["—"])
    await c.message.edit_text("\n".join(lines))

# ================= /addstore ===============
@dp.callback_query(AdminAdd.waiting_project, F.data.startswith("proj:"))
async def cb_add_pick_project(c: CallbackQuery, state: FSMContext):
    project = c.data.split(":", 1)[1]
    await c.answer()
    await state.update_data(project=project)

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    if is_multi(project):
        kb = kb_clients(project, include_all_for_status=False, admin_flow=True)
        m = await c.message.answer(f"Проект: <b>{project}</b>\nВыберите клиента:", reply_markup=kb)
        await state.update_data(last_menu_msg_id=m.message_id)
        await state.set_state(AdminAdd.waiting_client)
    else:
        m = await c.message.answer("Введите название магазина (текст). Для отмены — /start")
        await state.update_data(client=None, last_menu_msg_id=m.message_id)
        await state.set_state(AdminAdd.waiting_name)

@dp.callback_query(AdminAdd.waiting_client, F.data.startswith("adm:client:"))
async def cb_add_pick_client(c: CallbackQuery, state: FSMContext):
    _, _, project, client = c.data.split(":", 3)
    await c.answer()
    await state.update_data(project=project, client=client)

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    m = await c.message.answer("Введите название магазина (текст). Для отмены — /start")
    await state.update_data(last_menu_msg_id=m.message_id)
    await state.set_state(AdminAdd.waiting_name)

@dp.message(AdminAdd.waiting_name, F.text)
async def cb_add_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    project = data.get("project")
    client  = data.get("client")
    if not name:
        await message.answer("Пустое имя, введите ещё раз.")
        return
    if name.upper() in {"DELETE", "/DELETE", "DEL", "/DEL"}:
        await message.answer("❗️ Нельзя называть магазин «DELETE». Введите другое имя.")
        return

    ok = add_store(project, name, client)
    if ok:
        info = f"Добавлен магазин «{name}» в проект «{project}»"
        if client:
            info += f" (клиент: {client})"
        await message.answer(f"✅ {info}")
    else:
        await message.answer("❌ Не удалось добавить магазин.")
    await state.clear()

# ================= /delstore ===============
@dp.callback_query(AdminDelStore.waiting_project, F.data.startswith("proj:"))
async def cb_delstore_project(c: CallbackQuery, state: FSMContext):
    project = c.data.split(":", 1)[1]
    await c.answer()
    await state.update_data(project=project)

    data = await state.get_data()
    last_menu_id = data.get("last_menu_msg_id")
    if last_menu_id:
        await safe_delete_message(c.message.chat.id, last_menu_id)

    kb = kb_delstore_chooser(project)
    m = await c.message.answer(f"Удаление магазина.\nПроект: <b>{project}</b>\nВыберите магазин:", reply_markup=kb)
    await state.update_data(last_menu_msg_id=m.message_id)
    await state.set_state(AdminDelStore.waiting_store)

@dp.callback_query(AdminDelStore.waiting_store, F.data.startswith("adm:delstore:"))
async def cb_delstore_confirm(c: CallbackQuery, state: FSMContext):
    _, _, project, client, store = c.data.split(":", 4)
    await c.answer()
    ok = del_store(project, store)
    if ok:
        await c.message.edit_text(f"🗑 Удалён магазин <b>{store}</b> из проекта <b>{project}</b>.")
    else:
        await c.message.edit_text("❌ Не удалось удалить магазин.")
    await state.clear()

# ================= /delproject ==============
@dp.callback_query(AdminDelProject.confirm, F.data.startswith("adm:delproject:"))
async def cb_delproject(c: CallbackQuery, state: FSMContext):
    project = c.data.split(":", 2)[2]
    await c.answer()
    ok = del_project(project)
    if ok:
        await c.message.edit_text(f"🧨 Проект <b>{project}</b> удалён вместе со списком магазинов.")
    else:
        await c.message.edit_text("❌ Не удалось удалить проект.")
    await state.clear()

# ================= СТАРТ ====================
async def on_startup():
    ensure_seed()
    log.info("✅ Бот запущен и слушает Telegram...")

if __name__ == "__main__":
    import asyncio
    async def main():
        ensure_seed()
        await on_startup()
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.warning("Bot stopped")
