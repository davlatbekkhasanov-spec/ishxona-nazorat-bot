import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===================== CONFIG =====================
BOT_TOKEN = (os.getenv(8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE) or "").strip()
GROUP_ID_RAW = (os.getenv(-1001877019294) or "").strip()

# optional
TEST_MODE = (os.getenv(1) or "0").strip() == "1"
ADMIN_IDS_RAW = (os.getenv(1432810519) or "").strip()  # "123,456"
DB_PATH = (os.getenv(complaints.sqlite3) or "complaints.sqlite3").strip()
TZ_NAME = (os.getenv(TZ) or "Asia/Tashkent").strip()

# employees list (buttons)
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

# ===================== VALIDATION =====================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in env variables (Railway Variables).")

if not GROUP_ID_RAW:
    raise RuntimeError("GROUP_ID is missing in env variables (Railway Variables).")

try:
    GROUP_ID = int(GROUP_ID_RAW)
except Exception as e:
    raise RuntimeError(f"GROUP_ID must be integer, got: {GROUP_ID_RAW!r}") from e

ADMIN_IDS = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

TZ = ZoneInfo(TZ_NAME)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("nazorat-bot")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

scheduler = AsyncIOScheduler(timezone=TZ)


# ===================== DB =====================
def db_conn():
    return sqlite3.connect(DB_PATH)


def db_init():
    with db_conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS complaints(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                month_key TEXT NOT NULL,
                from_user_id INTEGER NOT NULL,
                from_user_name TEXT,
                from_username TEXT,
                employee TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                decided_at TEXT,
                decided_by INTEGER
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_compl_month ON complaints(month_key)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_compl_status ON complaints(status)"
        )


def month_key_for(dt: datetime) -> str:
    """
    Rule: new month cycle starts every 2nd day.
    Example: Feb 1 -> belongs to previous month cycle (Jan)
             Feb 2+ -> belongs to Feb
    """
    if dt.day < 2:
        prev = (dt.replace(day=1) - timedelta(days=1))
        return prev.strftime("%Y-%m")
    return dt.strftime("%Y-%m")


def db_add_complaint(user: Message, employee: str, text: str) -> int:
    now = datetime.now(TZ)
    mk = month_key_for(now)
    with db_conn() as con:
        cur = con.execute(
            """
            INSERT INTO complaints(created_at, month_key, from_user_id, from_user_name, from_username, employee, text, status)
            VALUES(?,?,?,?,?,?,?, 'open')
            """,
            (
                now.isoformat(),
                mk,
                user.from_user.id,
                user.from_user.full_name if user.from_user else None,
                user.from_user.username if user.from_user else None,
                employee,
                text,
            ),
        )
        return int(cur.lastrowid)


def db_set_status(complaint_id: int, status: str, decided_by: int):
    now = datetime.now(TZ)
    with db_conn() as con:
        con.execute(
            """
            UPDATE complaints
            SET status=?, decided_at=?, decided_by=?
            WHERE id=?
            """,
            (status, now.isoformat(), decided_by, complaint_id),
        )


def db_stats_for_day(day: datetime) -> dict:
    mk = month_key_for(day)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    with db_conn() as con:
        total = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE created_at >= ? AND created_at < ?
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]

        open_cnt = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE status='open'
            """,
        ).fetchone()[0]

        done = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status='done'
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]

        rejected = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status='rejected'
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]

        month_total = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE month_key=?
            """,
            (mk,),
        ).fetchone()[0]

        return {
            "date": start.strftime("%Y-%m-%d"),
            "month_key": mk,
            "total_today": total,
            "done_today": done,
            "rejected_today": rejected,
            "open_now": open_cnt,
            "month_total": month_total,
        }


def db_month_employee_counts(mk: str) -> list[tuple[str, int]]:
    with db_conn() as con:
        rows = con.execute(
            """
            SELECT employee, COUNT(*) as c
            FROM complaints
            WHERE month_key=?
            GROUP BY employee
            ORDER BY c DESC, employee ASC
            """,
            (mk,),
        ).fetchall()
        return [(r[0], int(r[1])) for r in rows]


# ===================== KEYBOARDS =====================
def kb_employees():
    b = InlineKeyboardBuilder()
    for name in EMPLOYEES:
        b.button(text=name, callback_data=f"emp:{name}")
    b.adjust(2)  # 2 per row
    return b.as_markup()


def kb_decision(complaint_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Хато бартараф этилди", callback_data=f"dec:done:{complaint_id}")
    b.button(text="❌ Асосли эмас (рад)", callback_data=f"dec:rejected:{complaint_id}")
    b.adjust(1)
    return b.as_markup()


# ===================== STATE (simple memory) =====================
# user_id -> {"employee": str}
PENDING = {}


# ===================== COMMANDS =====================
@dp.message(Command("start"))
async def cmd_start(m: Message):
    txt = (
        "👋 Салом! Бу <b>Nazorat</b> боти.\n\n"
        "Шикоят қолдириш учун: /complaint\n"
        "ID чиқариш: /myid\n"
        "Статистика: /stats\n"
    )
    if TEST_MODE:
        txt += "\n🧪 <b>TEST MODE ON</b>"
    await m.answer(txt)


@dp.message(Command("myid"))
async def cmd_myid(m: Message):
    await m.answer(f"🆔 Сизнинг ID: <code>{m.from_user.id}</code>")


@dp.message(Command("complaint"))
async def cmd_complaint(m: Message):
    PENDING[m.from_user.id] = {"employee": None}
    await m.answer("Ким ҳақида шикоят? Танланг:", reply_markup=kb_employees())


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    now = datetime.now(TZ)
    st = db_stats_for_day(now)
    mk = st["month_key"]
    per_emp = db_month_employee_counts(mk)

    lines = [
        f"📊 <b>Статистика</b>",
        f"Сана: <b>{st['date']}</b>",
        f"Ой цикли: <b>{mk}</b> (2-санадан бошланади)",
        "",
        f"Бугунги шикоятлар: <b>{st['total_today']}</b>",
        f"✅ Бартараф: <b>{st['done_today']}</b>",
        f"❌ Рад: <b>{st['rejected_today']}</b>",
        f"⏳ Ҳозир очиқ: <b>{st['open_now']}</b>",
        "",
        f"📌 Ой бўйича жами: <b>{st['month_total']}</b>",
    ]
    if per_emp:
        lines.append("\n👥 <b>Ой бўйича кимга кўп тушган:</b>")
        for name, c in per_emp[:10]:
            lines.append(f"• {name}: <b>{c}</b>")
    await m.answer("\n".join(lines))


# ===================== CALLBACKS =====================
@dp.callback_query(F.data.startswith("emp:"))
async def cb_employee(c: CallbackQuery):
    user_id = c.from_user.id
    if user_id not in PENDING:
        PENDING[user_id] = {"employee": None}

    employee = c.data.split("emp:", 1)[1]
    PENDING[user_id]["employee"] = employee

    await c.message.edit_text(
        f"✅ Танланди: <b>{employee}</b>\n\nЭнди шикоятни матнда ёзинг (1 та хабарда)."
    )
    await c.answer()


@dp.callback_query(F.data.startswith("dec:"))
async def cb_decision(c: CallbackQuery):
    # allow anyone in group to press (as you requested)
    try:
        _, status, cid = c.data.split(":")
        complaint_id = int(cid)
    except Exception:
        await c.answer("Хато кнопка", show_alert=True)
        return

    if status not in ("done", "rejected"):
        await c.answer("Нотўғри статус", show_alert=True)
        return

    db_set_status(complaint_id, status=status, decided_by=c.from_user.id)

    if status == "done":
        mark = "✅ <b>Бартараф этилди</b>"
    else:
        mark = "❌ <b>Асосли эмас (рад)</b>"

    # update message
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await c.message.reply(
        f"{mark}\n"
        f"Қарор қилган: <code>{c.from_user.id}</code>\n"
        f"ID: <code>{complaint_id}</code>"
        + ("\n🧪 TEST" if TEST_MODE else "")
    )
    await c.answer("Қабул қилинди")


# ===================== MESSAGE HANDLER =====================
@dp.message()
async def any_message(m: Message):
    # if user is writing complaint text after choosing employee
    st = PENDING.get(m.from_user.id)
    if not st or not st.get("employee"):
        return

    employee = st["employee"]
    text = (m.text or "").strip()
    if not text:
        await m.answer("Фақат матн ёзинг.")
        return

    cid = db_add_complaint(m, employee, text)
    PENDING.pop(m.from_user.id, None)

    # send to group
    uname = f"@{m.from_user.username}" if m.from_user.username else "—"
    msg = (
        f"🆕 <b>Янги шикоят</b>\n"
        f"ID: <code>{cid}</code>\n"
        f"Ходим: <b>{employee}</b>\n"
        f"Юборган: <b>{m.from_user.full_name}</b> ({uname}) | <code>{m.from_user.id}</code>\n\n"
        f"📝 <b>Тавсиф:</b>\n{text}"
    )
    if TEST_MODE:
        msg += "\n\n🧪 <b>TEST MODE</b>"

    await bot.send_message(GROUP_ID, msg, reply_markup=kb_decision(cid))
    await m.answer("✅ Қабул қилинди. Раҳмат!" + (" 🧪 TEST" if TEST_MODE else ""))


# ===================== SCHEDULED MESSAGES =====================
def motivation_text(has_errors: bool, when: str) -> str:
    if has_errors:
        return (
            f"⏰ <b>{when}</b>\n"
            f"Бугун хатолар бор. Илтимос, диққатни оширинг ва хатони такрорламанг.\n"
            f"Кучли жамоа — тартибли иш!"
            + ("\n🧪 TEST" if TEST_MODE else "")
        )
    return (
        f"⏰ <b>{when}</b>\n"
        f"Бугун хатолар йўқ! 👏\n"
        f"Шу темпда давом этамиз — эртанги кунга ҳам шундай кайфият!"
        + ("\n🧪 TEST" if TEST_MODE else "")
    )


async def send_daily_report(when: str):
    now = datetime.now(TZ)
    st = db_stats_for_day(now)
    has_errors = st["total_today"] > 0
    text = (
        motivation_text(has_errors, when)
        + "\n\n"
        f"📌 Бугунги шикоятлар: <b>{st['total_today']}</b>\n"
        f"✅ Бартараф: <b>{st['done_today']}</b>\n"
        f"❌ Рад: <b>{st['rejected_today']}</b>\n"
        f"⏳ Очиқ: <b>{st['open_now']}</b>\n"
        f"🗓 Ой цикли ({st['month_key']}): <b>{st['month_total']}</b>"
    )
    await bot.send_message(GROUP_ID, text)


# ===================== STARTUP =====================
async def on_startup():
    db_init()
    await bot.send_message(
        GROUP_ID,
        "✅ <b>Nazorat bot ishga tushdi</b>" + ("\n🧪 TEST MODE ON" if TEST_MODE else ""),
    )


def setup_jobs():
    # 08:00 and 21:00 Tashkent time
    scheduler.add_job(send_daily_report, "cron", hour=8, minute=0, args=["08:00"])
    scheduler.add_job(send_daily_report, "cron", hour=21, minute=0, args=["21:00"])

    # optional health ping every 2 hours (for monitoring)
    scheduler.add_job(
        lambda: bot.send_message(GROUP_ID, "✅ Bot alive (2h ping)" + (" 🧪" if TEST_MODE else "")),
        "interval",
        hours=2,
        next_run_time=datetime.now(TZ) + timedelta(minutes=2),
    )


async def main():
    await on_startup()
    setup_jobs()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
