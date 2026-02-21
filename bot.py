import os
import re
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ------------------ CONFIG (env) ------------------
BOT_TOKEN = os.getenv("8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE", "").strip()
GROUP_ID_RAW = os.getenv("-1001877019294", "").strip()
TEST_MODE = os.getenv("0", "0").strip() == "1"
ADMIN_IDS_RAW = os.getenv("1432810519", "").strip()
DB_PATH = os.getenv("complaints.sqlite3", "complaints.sqlite3").strip()
TZ_NAME = os.getenv("TZ", "Asia/Tashkent").strip()

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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN.")
if not GROUP_ID_RAW:
    raise RuntimeError("GROUP_ID is empty. Set Railway variable GROUP_ID.")
try:
    GROUP_ID = int(GROUP_ID_RAW)
except Exception as e:
    raise RuntimeError(f"GROUP_ID must be integer, got: {GROUP_ID_RAW!r}") from e

ADMIN_IDS: set[int] = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

TZ = ZoneInfo(TZ_NAME)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nazorat")


# ------------------ BOT / ROUTER / SCHED ------------------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
scheduler = AsyncIOScheduler(timezone=TZ)

PENDING: dict[int, str] = {}  # user_id -> chosen employee


# ------------------ DB ------------------
def db_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def db_init():
    with db_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS complaints(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                month_key TEXT NOT NULL,
                from_user_id INTEGER NOT NULL,
                from_username TEXT,
                from_fullname TEXT,
                employee TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                decided_at TEXT,
                decided_by INTEGER
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_compl_month ON complaints(month_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_compl_status ON complaints(status)")

def month_key_for(dt: datetime) -> str:
    if dt.day >= 2:
        return dt.strftime("%Y-%m")
    prev = (dt.replace(day=1) - timedelta(days=1))
    return prev.strftime("%Y-%m")

def add_complaint(m: Message, employee: str, text: str) -> int:
    now = datetime.now(TZ)
    mk = month_key_for(now)
    with db_conn() as con:
        cur = con.execute("""
            INSERT INTO complaints(created_at, month_key, from_user_id, from_username, from_fullname, employee, text, status)
            VALUES(?,?,?,?,?,?,?, 'open')
        """, (
            now.isoformat(),
            mk,
            m.from_user.id,
            (m.from_user.username if m.from_user else None),
            (m.from_user.full_name if m.from_user else None),
            employee,
            text,
        ))
        con.commit()
        return int(cur.lastrowid)

def set_status(complaint_id: int, status: str, decided_by: int) -> bool:
    now = datetime.now(TZ)
    with db_conn() as con:
        row = con.execute("SELECT status FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        if not row:
            return False
        if row["status"] != "open":
            return False
        con.execute("""
            UPDATE complaints
            SET status=?, decided_at=?, decided_by=?
            WHERE id=?
        """, (status, now.isoformat(), decided_by, complaint_id))
        con.commit()
        return True

def today_stats() -> dict:
    now = datetime.now(TZ)
    start = datetime.combine(now.date(), dtime(0, 0), tzinfo=TZ)
    end = start + timedelta(days=1)
    mk = month_key_for(now)

    with db_conn() as con:
        total_today = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints
            WHERE created_at >= ? AND created_at < ?
        """, (start.isoformat(), end.isoformat())).fetchone()["c"])

        done_today = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status='done'
        """, (start.isoformat(), end.isoformat())).fetchone()["c"])

        rejected_today = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status='rejected'
        """, (start.isoformat(), end.isoformat())).fetchone()["c"])

        open_now = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints WHERE status='open'
        """).fetchone()["c"])

        month_total = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints WHERE month_key=?
        """, (mk,)).fetchone()["c"])

    return {
        "date": now.strftime("%Y-%m-%d"),
        "month_key": mk,
        "total_today": total_today,
        "done_today": done_today,
        "rejected_today": rejected_today,
        "open_now": open_now,
        "month_total": month_total,
    }


# ------------------ KEYBOARDS ------------------
def kb_employees():
    b = InlineKeyboardBuilder()
    for name in EMPLOYEES:
        b.button(text=name, callback_data=f"emp:{name}")
    b.adjust(2)
    return b.as_markup()

def kb_decision(complaint_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Хато бартараф этилди", callback_data=f"dec:done:{complaint_id}")
    b.button(text="❌ Асосли эмас (рад)", callback_data=f"dec:rejected:{complaint_id}")
    b.adjust(1)
    return b.as_markup()


# ------------------ HANDLERS ------------------
@router.message(Command("start"))
async def start_cmd(m: Message):
    if m.chat.type != "private":
        await m.answer("✅ Nazorat bot ишлаяпти.")
        return
    await m.answer(
        "Салом! 👋\n\n"
        "Жалоба ёзиш:\n"
        "1) /complaint\n"
        "2) ходимни танлайсиз\n"
        "3) тавсифни ёзасиз\n\n"
        "Командалар: /myid, /stats"
        + ("\n\n🧪 TEST MODE ON" if TEST_MODE else "")
    )

@router.message(Command("myid"))
async def myid_cmd(m: Message):
    await m.answer(f"🆔 Сизнинг ID: <code>{m.from_user.id}</code>")

@router.message(Command("complaint"))
async def complaint_cmd(m: Message):
    if m.chat.type != "private":
        return
    PENDING[m.from_user.id] = ""
    await m.answer("Ким ҳақида жалоба? Танланг:", reply_markup=kb_employees())

@router.message(Command("stats"))
async def stats_cmd(m: Message):
    if ADMIN_IDS and (m.from_user.id not in ADMIN_IDS):
        await m.answer("⛔ Бу команда фақат админ учун.")
        return
    st = today_stats()
    await m.answer(
        f"📊 <b>Статистика</b>\n"
        f"Сана: <b>{st['date']}</b>\n"
        f"Ой цикли: <b>{st['month_key']}</b> (2-санадан)\n\n"
        f"Бугун жами: <b>{st['total_today']}</b>\n"
        f"✅ Бартараф: <b>{st['done_today']}</b>\n"
        f"❌ Рад: <b>{st['rejected_today']}</b>\n"
        f"⏳ Очиқ: <b>{st['open_now']}</b>\n"
        f"📌 Ой бўйича жами: <b>{st['month_total']}</b>"
    )

@router.callback_query(F.data.startswith("emp:"))
async def employee_cb(c: CallbackQuery):
    if c.message.chat.type != "private":
        await c.answer("Фақат личкада.", show_alert=True)
        return
    employee = c.data.split("emp:", 1)[1]
    PENDING[c.from_user.id] = employee
    await c.message.edit_text(
        f"✅ Танланди: <b>{employee}</b>\n\nЭнди тавсифни матнда ёзинг (1 хабарда)."
    )
    await c.answer()

@router.message(F.text)
async def any_text(m: Message):
    if m.chat.type != "private":
        return
    employee = PENDING.get(m.from_user.id)
    if not employee:
        return

    text = re.sub(r"\s+", " ", (m.text or "").strip())
    if not text:
        await m.answer("Матн бўш бўлмасин.")
        return

    cid = add_complaint(m, employee, text)
    PENDING.pop(m.from_user.id, None)

    uname = f"@{m.from_user.username}" if m.from_user.username else "—"
    msg = (
        f"🆕 <b>Янги жалоба</b>\n"
        f"ID: <code>{cid}</code>\n"
        f"Ходим: <b>{employee}</b>\n"
        f"Юборган: <b>{m.from_user.full_name}</b> ({uname}) | <code>{m.from_user.id}</code>\n\n"
        f"📝 <b>Тавсиф:</b>\n{text}"
        + ("\n\n🧪 <b>TEST MODE</b>" if TEST_MODE else "")
    )

    await bot.send_message(GROUP_ID, msg, reply_markup=kb_decision(cid))
    await m.answer("✅ Қабул қилинди. Раҳмат!")

@router.callback_query(F.data.startswith("dec:"))
async def decision_cb(c: CallbackQuery):
    if ADMIN_IDS and (c.from_user.id not in ADMIN_IDS):
        await c.answer("⛔ Фақат админ", show_alert=True)
        return

    try:
        _, status, cid_s = c.data.split(":")
        cid = int(cid_s)
    except Exception:
        await c.answer("Хато кнопка", show_alert=True)
        return

    ok = set_status(cid, status=status, decided_by=c.from_user.id)
    if not ok:
        await c.answer("Аллақачон ёпилган ёки топилмади.", show_alert=True)
        return

    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    mark = "✅ Бартараф этилди" if status == "done" else "❌ Рад этилди"
    await c.message.reply(f"{mark}\nЖалоба ID: <code>{cid}</code>")
    await c.answer("OK")


# ------------------ SCHEDULER ------------------
def motivation(has_errors: bool, when_label: str) -> str:
    if has_errors:
        return (
            f"⚠️ <b>{when_label}</b>\n"
            f"Бугун хатолар бор. Диққатни ошириб ишлаймиз.\n"
            f"💪 Ҳар куни яхшироқ!"
        )
    return (
        f"✅ <b>{when_label}</b>\n"
        f"Бугун хатосиз! 👏\n"
        f"🚀 Эртанги кунга ҳам шу темпда!"
    )

async def send_report(when_label: str):
    st = today_stats()
    txt = (
        motivation(st["total_today"] > 0, when_label)
        + "\n\n"
        f"📌 Бугунги жалобалар: <b>{st['total_today']}</b>\n"
        f"✅ Бартараф: <b>{st['done_today']}</b>\n"
        f"❌ Рад: <b>{st['rejected_today']}</b>\n"
        f"⏳ Очиқ: <b>{st['open_now']}</b>\n"
        f"🗓 Ой цикли ({st['month_key']}): <b>{st['month_total']}</b>"
        + ("\n\n🧪 <b>TEST MODE</b>" if TEST_MODE else "")
    )
    await bot.send_message(GROUP_ID, txt)

async def test_ping():
    await bot.send_message(GROUP_ID, "🟣 TEST: bot ishlayapti (2 soatlik ping).")

def setup_jobs():
    scheduler.add_job(lambda: asyncio.create_task(send_report("08:00")), "cron", hour=8, minute=0)
    scheduler.add_job(lambda: asyncio.create_task(send_report("21:00")), "cron", hour=21, minute=0)
    if TEST_MODE:
        scheduler.add_job(lambda: asyncio.create_task(test_ping()), "interval", hours=2)


# ------------------ MAIN ------------------
async def main():
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)  # important for polling mode
    dp.include_router(router)

    setup_jobs()
    scheduler.start()

    try:
        await bot.send_message(GROUP_ID, "✅ <b>Nazorat bot ишга тушди</b>" + ("\n🧪 TEST MODE ON" if TEST_MODE else ""))
    except Exception as e:
        log.warning("Startup message failed: %s", e)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
