import os
import re
import sys
import asyncio
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    BotCommandScopeDefault,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===================== CONFIG (ENV) =====================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "-1001877019294").strip()          # optional: -100....
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "1432810519").strip()        # example: "1432810519,123456789"
DB_PATH = os.getenv("DB_PATH", "complaints.sqlite3").strip()
TZ_NAME = os.getenv("TZ", "Asia/Tashkent").strip()

TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"    # optional
RESET_CODE = os.getenv("RESET_CODE", "BRON-RESET-2026").strip()

TZ = ZoneInfo(TZ_NAME)

# !!! EMPLOYEES ni o'zingniki bilan qoldir / to'ldir
EMPLOYEES = [
     "Сагдуллаев Юнус",
    "Самадов Тулкин",
    "Тохиров Муслимбек",
    "Мустафоев Абдулло",
    "Ражаббоев Пулат",
    "Рузибоев Сардор",
    "Собиров Самандар",
    "Равшанов Зиёдулло",
    "Шерназаров Толиб",
    "Равшанов Охунжон",
]

def parse_admin_ids(raw: str) -> set[int]:
    ids = set()
    if not raw:
        return ids
    for part in re.split(r"[,\s;]+", raw):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)

def parse_group_id(raw: str) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None

GROUP_ID = parse_group_id(GROUP_ID_RAW)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is empty. Set Railway variable ADMIN_IDS (comma separated user IDs).")

# ===================== LOGGING =====================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nazorat-bot")

# ===================== DB =====================

def db_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def db_init():
    with db_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                employee TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',      -- open | done | rejected
                from_user_id INTEGER NOT NULL,
                from_fullname TEXT,
                from_username TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_complaints_employee ON complaints(employee)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status)")
        con.commit()

def db_add_complaint(employee: str, text: str, user_id: int, fullname: str, username: str | None) -> int:
    with db_conn() as con:
        cur = con.execute("""
            INSERT INTO complaints (created_at, employee, text, status, from_user_id, from_fullname, from_username)
            VALUES (?, ?, ?, 'open', ?, ?, ?)
        """, (datetime.now(TZ).isoformat(timespec="seconds"), employee, text, user_id, fullname, username))
        con.commit()
        return int(cur.lastrowid)

def db_get_complaint(cid: int):
    with db_conn() as con:
        return con.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()

def db_set_status(cid: int, status: str):
    with db_conn() as con:
        con.execute("UPDATE complaints SET status=? WHERE id=?", (status, cid))
        con.commit()

def db_stats():
    with db_conn() as con:
        total = con.execute("SELECT COUNT(*) AS c FROM complaints").fetchone()["c"]
        open_ = con.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='open'").fetchone()["c"]
        done = con.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='done'").fetchone()["c"]
        rej = con.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='rejected'").fetchone()["c"]
        return total, open_, done, rej

def db_list_open(limit: int = 20, offset: int = 0):
    with db_conn() as con:
        return con.execute("""
            SELECT * FROM complaints
            WHERE status='open'
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def db_list_by_employee(employee: str, limit: int = 20, offset: int = 0):
    with db_conn() as con:
        return con.execute("""
            SELECT * FROM complaints
            WHERE employee=?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (employee, limit, offset)).fetchall()

def db_reset_all():
    with db_conn() as con:
        con.execute("DELETE FROM complaints")
        con.commit()
        try:
            con.execute("VACUUM")
            con.commit()
        except Exception:
            pass

# ===================== HELPERS =====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def short(text: str, n: int = 140) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"

def fmt_dt(iso_s: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_s[:16] if iso_s else "—"

async def notify_admins(text: str):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            log.warning("notify_admins failed for %s: %s", aid, e)

async def notify_group(text: str):
    if not GROUP_ID:
        return
    try:
        await bot.send_message(GROUP_ID, text)
    except Exception as e:
        log.warning("notify_group failed: %s", e)

async def setup_bot_commands():
    cmds = [
        BotCommand(command="start", description="Бошлаш"),
        BotCommand(command="complaint", description="Шикоят ёзиш"),
        BotCommand(command="myid", description="ID кўриш"),
        BotCommand(command="admin", description="Админ панель (админ)"),
        BotCommand(command="stats", description="Статистика (админ)"),
        BotCommand(command="reset", description="Базани 0 қилиш (админ)"),
    ]
    await bot.set_my_commands(cmds, scope=BotCommandScopeDefault())

# ===================== BOT / DISPATCHER =====================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
router = Router()
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)

# ===================== FSM =====================

class ComplaintFlow(StatesGroup):
    choose_employee = State()
    enter_text = State()

# ===================== KEYBOARDS =====================

def kb_start():
    b = InlineKeyboardBuilder()
    b.button(text="📝 Янги шикоят", callback_data="c:new")
    b.button(text="🆔 Менинг ID", callback_data="u:myid")
    b.adjust(2)
    return b.as_markup()

def kb_employee_pick():
    b = InlineKeyboardBuilder()
    for name in EMPLOYEES:
        b.button(text=name, callback_data=f"c:emp:{name}")
    b.adjust(2)
    return b.as_markup()

def kb_admin_panel():
    b = InlineKeyboardBuilder()
    b.button(text="📨 Очиқ шикоятлар", callback_data="adm:open:0")
    b.button(text="👤 Ходим бўйича шикоятлар", callback_data="adm:byemp")
    b.button(text="📊 /stats", callback_data="adm:stats")
    b.adjust(1)
    return b.as_markup()

def kb_complaint_actions(cid: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ёпиш (DONE)", callback_data=f"adm:done:{cid}")
    b.button(text="❌ Рад этиш (REJECT)", callback_data=f"adm:reject:{cid}")
    b.adjust(2)
    return b.as_markup()

def kb_employee_list_admin():
    b = InlineKeyboardBuilder()
    for name in EMPLOYEES:
        b.button(text=name, callback_data=f"admemp:{name}:0")
    b.adjust(2)
    return b.as_markup()

def kb_more_employee(employee: str, offset: int):
    b = InlineKeyboardBuilder()
    b.button(text="➡️ Яна", callback_data=f"admemp:{employee}:{offset}")
    b.button(text="🔙 Ходим танлаш", callback_data="adm:byemp")
    b.adjust(2)
    return b.as_markup()

def kb_more_open(offset: int):
    b = InlineKeyboardBuilder()
    b.button(text="➡️ Яна", callback_data=f"adm:open:{offset}")
    b.adjust(1)
    return b.as_markup()

# ===================== COMMANDS =====================

@router.message(Command("start"))
async def cmd_start(m: Message):
    txt = (
        "👋 Ассалому алейкум!\n\n"
        "Бу <b>Ishxona Nazorat Bot</b>.\n"
        "Шикоят ёзиш учун <b>📝 Янги шикоят</b> тугмасини босинг.\n\n"
        "Агар сиз админ бўлсангиз: /admin"
    )
    await m.answer(txt, reply_markup=kb_start())

@router.message(Command("complaint"))
async def cmd_complaint(m: Message, state: FSMContext):
    await state.set_state(ComplaintFlow.choose_employee)
    await m.answer("Ким ҳақида шикоят ёзмоқчисиз? (ходимни танланг)", reply_markup=kb_employee_pick())

@router.message(Command("myid"))
async def cmd_myid(m: Message):
    await m.answer(f"🆔 Сизнинг ID: <code>{m.from_user.id}</code>")

@router.message(Command("admin"))
async def cmd_admin(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("⛔ Бу бўлим фақат админлар учун.")
        return
    await m.answer("🛠 Админ панел:", reply_markup=kb_admin_panel())

@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("⛔ Фақат админ.")
        return
    total, open_, done, rej = db_stats()
    await m.answer(
        "📊 <b>Статистика</b>\n"
        f"Жами: <b>{total}</b>\n"
        f"Очиқ: <b>{open_}</b>\n"
        f"Ёпилган: <b>{done}</b>\n"
        f"Рад этилган: <b>{rej}</b>"
    )

@router.message(Command("reset"))
async def cmd_reset(m: Message):
    """
    DELETE emas! Faqat admin va bron code bilan:
    /reset BRON-RESET-2026
    -> bazani 0 qiladi va process exit (Railway restart).
    """
    if not is_admin(m.from_user.id):
        await m.answer("⛔ Фақат админ.")
        return

    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Код керак.\nМисол:\n<code>/reset BRON-RESET-2026</code>")
        return

    code = parts[1].strip()
    if code != RESET_CODE:
        await m.answer("⛔ Нотўғри брон-код.")
        return

    db_reset_all()
    await m.answer("✅ База 0 қилинди. Бот қайта ишга тушяпти…")
    # Railway restart qilish uchun chiqиб кетамиз
    await asyncio.sleep(1)
    raise SystemExit(0)

# ===================== CALLBACKS: START BUTTONS =====================

@router.callback_query(F.data == "c:new")
async def cb_new(c: CallbackQuery, state: FSMContext):
    await state.set_state(ComplaintFlow.choose_employee)
    await c.message.answer("Ким ҳақида шикоят? (ходимни танланг)", reply_markup=kb_employee_pick())
    await c.answer()

@router.callback_query(F.data == "u:myid")
async def cb_myid(c: CallbackQuery):
    await c.message.answer(f"🆔 Сизнинг ID: <code>{c.from_user.id}</code>")
    await c.answer()

# ===================== CALLBACKS: COMPLAINT FLOW =====================

@router.callback_query(F.data.startswith("c:emp:"))
async def cb_choose_employee(c: CallbackQuery, state: FSMContext):
    employee = c.data.split("c:emp:", 1)[1].strip()
    await state.update_data(employee=employee)
    await state.set_state(ComplaintFlow.enter_text)
    await c.message.answer(
        f"✅ Танланди: <b>{employee}</b>\n\n"
        "Энди <b>Шикоят мазмуни</b>ни ёзинг:"
    )
    await c.answer()

@router.message(ComplaintFlow.enter_text)
async def st_enter_text(m: Message, state: FSMContext):
    data = await state.get_data()
    employee = data.get("employee")
    text = (m.text or "").strip()

    if not employee:
        await state.clear()
        await m.answer("Хатолик: ходим танланмаган. /complaint дан қайта бошланг.")
        return

    if not text or len(text) < 3:
        await m.answer("Шикоят мазмуни жуда қисқа. Батафсилроқ ёзинг:")
        return

    cid = db_add_complaint(
        employee=employee,
        text=text,
        user_id=m.from_user.id,
        fullname=(m.from_user.full_name or "").strip(),
        username=m.from_user.username,
    )
    await state.clear()

    # userga tasdiq
    await m.answer(
        "✅ Қабул қилинди.\n"
        f"Шикоят ID: <code>{cid}</code>\n"
        "Текширувдан кейин жавоб берилади."
    )

    # admin/groupga yuboramiz
    uname = f"@{m.from_user.username}" if m.from_user.username else "—"
    admin_text = (
        "📩 <b>Янги шикоят</b>\n"
        f"ID: <code>{cid}</code>\n"
        f"Ходим: <b>{employee}</b>\n"
        f"Кимдан: {m.from_user.full_name} ({uname}) | <code>{m.from_user.id}</code>\n"
        f"Вақт: <b>{fmt_dt(datetime.now(TZ).isoformat(timespec='seconds'))}</b>\n\n"
        f"<b>Шикоят мазмуни:</b>\n{text}"
    )

    await notify_admins(admin_text)
    await notify_group(admin_text)

    # test mode bo'lsa qo'shimcha ping
    if TEST_MODE:
        await notify_admins("🧪 TEST_MODE: шикоят юборилди ва админга етказилди.")

# ===================== ADMIN: CALLBACKS =====================

@router.callback_query(F.data.startswith("adm:"))
async def cb_admin(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Фақат админ", show_alert=True)
        return

    parts = c.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "stats":
        total, open_, done, rej = db_stats()
        await c.message.answer(
            "📊 <b>Статистика</b>\n"
            f"Жами: <b>{total}</b>\n"
            f"Очиқ: <b>{open_}</b>\n"
            f"Ёпилган: <b>{done}</b>\n"
            f"Рад этилган: <b>{rej}</b>"
        )
        await c.answer()
        return

    if action == "byemp":
        await c.message.answer("Кимнинг шикоятларини кўрамиз? Танланг:", reply_markup=kb_employee_list_admin())
        await c.answer()
        return

    if action == "open":
        offset = 0
        if len(parts) >= 3 and parts[2].isdigit():
            offset = int(parts[2])

        rows = db_list_open(limit=10, offset=offset)
        if not rows:
            await c.message.answer("📭 Очиқ шикоят йўқ.")
            await c.answer()
            return

        for r in rows:
            uname = f"@{r['from_username']}" if r["from_username"] else "—"
            text = (
                "📨 <b>Очиқ шикоят</b>\n"
                f"ID: <code>{r['id']}</code>\n"
                f"Ходим: <b>{r['employee']}</b>\n"
                f"Вақт: <b>{fmt_dt(r['created_at'])}</b>\n"
                f"Кимдан: {r['from_fullname']} ({uname}) | <code>{r['from_user_id']}</code>\n\n"
                f"<b>Шикоят мазмуни:</b>\n{r['text']}"
            )
            await c.message.answer(text, reply_markup=kb_complaint_actions(int(r["id"])))

        # pagination
        await c.message.answer("⬇️ Кейингилар:", reply_markup=kb_more_open(offset + 10))
        await c.answer()
        return

    if action in ("done", "reject"):
        if len(parts) < 3 or not parts[2].isdigit():
            await c.answer("Хато ID", show_alert=True)
            return
        cid = int(parts[2])
        row = db_get_complaint(cid)
        if not row:
            await c.message.answer("Бу ID топилмади.")
            await c.answer()
            return

        if action == "done":
            db_set_status(cid, "done")
            await c.message.answer(f"✅ ID <code>{cid}</code> ёпилди (DONE).")
            # userga xabar (muloyim)
            try:
                await bot.send_message(
                    int(row["from_user_id"]),
                    "✅ Мурожаат кўриб чиқилди.\n"
                    "Раҳмат. Тартиб-интизом ҳаммамиз учун муҳим."
                )
            except Exception:
                pass
            await c.answer()
            return

        if action == "reject":
            db_set_status(cid, "rejected")
            await c.message.answer(f"❌ ID <code>{cid}</code> рад этилди (REJECT).")
            # userga "psixologik ta'sirli" qisqa rad javob
            try:
                await bot.send_message(
                    int(row["from_user_id"]),
                    "❌ Мурожаат рад этилди.\n"
                    "Сабаб: далил/аниқ маълумот етарли эмас.\n"
                    "Агар ҳақиқатан муҳим бўлса — фактлар билан қайта ёзинг."
                )
            except Exception:
                pass
            await c.answer()
            return

    await c.answer("Номаълум буйруқ", show_alert=True)

@router.callback_query(F.data.startswith("admemp:"))
async def cb_admin_employee_list(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Фақат админ", show_alert=True)
        return

    # format: admemp:{employee}:{offset}
    try:
        _, employee, offset_s = c.data.split(":", 2)
        offset = int(offset_s)
    except Exception:
        await c.answer("Хато data", show_alert=True)
        return

    rows = db_list_by_employee(employee, limit=10, offset=offset)
    if not rows:
        await c.message.answer(f"📭 <b>{employee}</b> бўйича шикоят йўқ.")
        await c.answer()
        return

    lines = [f"👤 <b>{employee}</b> — шикоятлар (охирги 10)"]
    for r in rows:
        st = r["status"]
        st_icon = "⏳" if st == "open" else ("✅" if st == "done" else "❌")
        uname = f"@{r['from_username']}" if r["from_username"] else "—"
        lines.append(
            f"\n{st_icon} <code>{r['id']}</code> | {fmt_dt(r['created_at'])}\n"
            f"{short(r['text'], 110)}\n"
            f"{r['from_fullname']} ({uname}) | <code>{r['from_user_id']}</code>"
        )

    await c.message.answer(
        "\n".join(lines),
        reply_markup=kb_more_employee(employee, offset + 10),
    )
    await c.answer()

# ===================== SCHEDULER: HEALTH ALERTS =====================

async def scheduled_ping():
    total, open_, done, rej = db_stats()
    msg = (
        "✅ <b>Bot ishlayapti</b>\n"
        f"Вақт: <b>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"Очиқ: <b>{open_}</b> | Ёпилган: <b>{done}</b> | Рад: <b>{rej}</b> | Жами: <b>{total}</b>"
    )
    await notify_admins(msg)
    # groupga majburiy emas; xohlasang yoqamiz:
    # await notify_group(msg)

# ===================== MAIN =====================

async def main():
    db_init()
    await setup_bot_commands()

    scheduler = AsyncIOScheduler(timezone=TZ)
    # 08:00 ва 20:00 — сен айтган 2 та вақт
    scheduler.add_job(scheduled_ping, "cron", hour=7, minute=30)
    scheduler.add_job(scheduled_ping, "cron", hour=19, minute=30)

    if TEST_MODE:
        # test rejimda har 30 daqiqada ping (xohlasang o'zgartirasan)
        scheduler.add_job(lambda: asyncio.create_task(notify_admins("🧪 TEST_MODE: бот тирик.")), "cron", minute="*/30")

    scheduler.start()

    log.info("Bot started. TZ=%s DB=%s GROUP_ID=%s ADMINS=%s", TZ_NAME, DB_PATH, GROUP_ID, list(ADMIN_IDS))
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
