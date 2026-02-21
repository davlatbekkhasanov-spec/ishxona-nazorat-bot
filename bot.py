import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))  # keyin to'g'rilaymiz
DB_PATH = os.getenv("DB_PATH", "complaints.sqlite3")

TZ = ZoneInfo("Asia/Tashkent")

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

# кимlar status yopishi mumkin (bo'sh bo'lsa — hamma bosadi)
ALLOWED_CLOSERS = set()
_raw = os.getenv("ALLOWED_CLOSERS", "").strip()
if _raw:
    for x in _raw.split(","):
        x = x.strip()
        if x.isdigit():
            ALLOWED_CLOSERS.add(int(x))


# ---------------- DB ----------------
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_user_id INTEGER NOT NULL,
        tg_username TEXT,
        employee TEXT NOT NULL,
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        closed_at TEXT,
        closed_by_id INTEGER,
        closed_by_username TEXT,
        group_message_id INTEGER
    )
    """)
    con.commit()
    con.close()

def insert_complaint(user_id, username, employee, desc, created_at, group_message_id=None):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO complaints (tg_user_id, tg_username, employee, description, created_at, status, group_message_id)
        VALUES (?, ?, ?, ?, ?, 'open', ?)
    """, (user_id, username, employee, desc, created_at, group_message_id))
    con.commit()
    row_id = cur.lastrowid
    con.close()
    return row_id

def set_group_message_id(complaint_id, msg_id):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE complaints SET group_message_id=? WHERE id=?", (msg_id, complaint_id))
    con.commit()
    con.close()

def close_complaint(complaint_id, status, closed_by_id, closed_by_username, closed_at):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE complaints
        SET status=?, closed_at=?, closed_by_id=?, closed_by_username=?
        WHERE id=? AND status='open'
    """, (status, closed_at, closed_by_id, closed_by_username, complaint_id))
    con.commit()
    changed = cur.rowcount
    con.close()
    return changed > 0

def get_day_stats(day: date):
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=TZ).isoformat()
    end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=TZ).isoformat()
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT
          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_cnt,
          SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) as resolved_cnt,
          SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected_cnt,
          COUNT(*) as total_cnt
        FROM complaints
        WHERE created_at BETWEEN ? AND ?
    """, (start, end))
    row = cur.fetchone()
    con.close()
    return {
        "open": row[0] or 0,
        "resolved": row[1] or 0,
        "rejected": row[2] or 0,
        "total": row[3] or 0,
    }

def period_start_for(dt: datetime) -> datetime:
    # hisob oyimiz 2-sanadan boshlanadi
    if dt.day >= 2:
        return datetime(dt.year, dt.month, 2, 0, 0, 0, tzinfo=TZ)
    first_of_month = datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=TZ)
    prev_month_last = first_of_month - timedelta(days=1)
    return datetime(prev_month_last.year, prev_month_last.month, 2, 0, 0, 0, tzinfo=TZ)

def get_period_stats(now: datetime):
    start_dt = period_start_for(now)
    start = start_dt.isoformat()
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT
          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_cnt,
          SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) as resolved_cnt,
          SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected_cnt,
          COUNT(*) as total_cnt
        FROM complaints
        WHERE created_at >= ?
    """, (start,))
    row = cur.fetchone()
    con.close()
    return {
        "open": row[0] or 0,
        "resolved": row[1] or 0,
        "rejected": row[2] or 0,
        "total": row[3] or 0,
        "start": start_dt,
    }


# ---------------- UI ----------------
def employees_kb():
    rows = []
    row = []
    for i, name in enumerate(EMPLOYEES, start=1):
        row.append(InlineKeyboardButton(text=name, callback_data=f"emp:{name}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def close_kb(complaint_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Хато бартараф этилди", callback_data=f"close:{complaint_id}:resolved"),
        InlineKeyboardButton(text="❌ Асосли эмас (рад)", callback_data=f"close:{complaint_id}:rejected"),
    ]])

def closer_allowed(user_id: int) -> bool:
    return (not ALLOWED_CLOSERS) or (user_id in ALLOWED_CLOSERS)


# ---------------- FSM ----------------
class ComplaintFlow(StatesGroup):
    enter_description = State()


# ---------------- BOT ----------------
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())


@dp.message(CommandStart())
async def start(m: Message):
    if m.chat.type != "private":
        await m.answer("Салом! Мен ишлайман ✅\nГуруҳ ID олиш учун: /chatid")
        return

    await m.answer(
        "Салом! 👋\n"
        "Хато/шикоят ёзиш учун масъул ходимни танланг:",
        reply_markup=employees_kb()
    )

@dp.message(F.text.in_({"/help", "help"}))
async def help_cmd(m: Message):
    await m.answer(
        "Командалар:\n"
        "• /start — личкада шикоят бошлаш\n"
        "• /chatid — гуруҳда чат ID чиқаради\n"
    )

@dp.message(F.text == "/chatid")
async def chatid(m: Message):
    await m.answer(f"✅ Chat ID: <code>{m.chat.id}</code>\nType: <b>{m.chat.type}</b>")

@dp.callback_query(F.data.startswith("emp:"))
async def choose_employee(cb: CallbackQuery, state: FSMContext):
    if cb.message.chat.type != "private":
        await cb.answer("Бу танлаш фақат личкада.", show_alert=True)
        return

    employee = cb.data.split(":", 1)[1]
    await state.update_data(employee=employee)
    await state.set_state(ComplaintFlow.enter_description)
    await cb.message.edit_text(
        f"✅ Танланди: <b>{employee}</b>\n\n"
        f"Энди хатолик тавсифини ёзинг (қанча аниқ бўлса, шунча яхши)."
    )
    await cb.answer()

@dp.message(ComplaintFlow.enter_description)
async def receive_description(m: Message, state: FSMContext):
    if not BOT_TOKEN:
        await m.answer("BOT_TOKEN топилмади. Railway Variables текшир.")
        return

    if GROUP_ID == 0:
        await m.answer("Ҳозирча гуруҳ ID қўйилмаган. Админ /chatid қилиб GROUP_ID ни қўйсин.")
        await state.clear()
        return

    data = await state.get_data()
    employee = data["employee"]
    desc = (m.text or "").strip()
    if not desc:
        await m.answer("Тавсиф бўш бўлмасин. Қайта ёзинг.")
        return

    created_at = datetime.now(TZ).isoformat()
    username = m.from_user.username or ""
    user_id = m.from_user.id

    complaint_id = insert_complaint(user_id, username, employee, desc, created_at, None)

    user_tag = f"@{username}" if username else f"ID:{user_id}"
    text = (
        f"🚨 <b>Янги хатолик аниқланди</b>\n\n"
        f"👤 <b>Ким ёзди:</b> {user_tag}\n"
        f"🧑‍💼 <b>Ходим:</b> {employee}\n"
        f"📝 <b>Тавсиф:</b>\n{desc}\n\n"
        f"🕒 <b>Вақт:</b> {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 <b>Статус:</b> ⏳ Кутилмоқда"
    )

    sent = await bot.send_message(chat_id=GROUP_ID, text=text, reply_markup=close_kb(complaint_id))
    set_group_message_id(complaint_id, sent.message_id)

    await m.answer("Қабул қилинди ✅\nШикоят гуруҳга чиқарилди.")
    await state.clear()

@dp.callback_query(F.data.startswith("close:"))
async def close_in_group(cb: CallbackQuery):
    if cb.message.chat.id != GROUP_ID:
        await cb.answer("Бу тугма фақат асосий гуруҳда ишлайди.", show_alert=True)
        return

    if not closer_allowed(cb.from_user.id):
        await cb.answer("Сизда буни ёпиш ҳуқуқи йўқ.", show_alert=True)
        return

    _, cid, status = cb.data.split(":")
    cid = int(cid)
    if status not in ("resolved", "rejected"):
        await cb.answer("Нотўғри статус.", show_alert=True)
        return

    closed_at = datetime.now(TZ).isoformat()
    closer_username = cb.from_user.username or ""
    ok = close_complaint(cid, status, cb.from_user.id, closer_username, closed_at)
    if not ok:
        await cb.answer("Бу хатолик аллақачон ёпилган.", show_alert=True)
        return

    status_text = "✅ Бартараф этилди" if status == "resolved" else "❌ Асосли эмас (рад)"
    closer_tag = f"@{closer_username}" if closer_username else f"ID:{cb.from_user.id}"

    new_text = cb.message.html_text.replace(
        "📌 <b>Статус:</b> ⏳ Кутилмоқда",
        f"📌 <b>Статус:</b> {status_text}\n"
        f"🔒 <b>Ёпди:</b> {closer_tag}\n"
        f"🕒 <b>Ёпилган вақт:</b> {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}"
    )

    await cb.message.edit_text(new_text, reply_markup=None)
    await cb.answer("Ёпилди ✅")


# ---------------- SCHEDULER ----------------
async def send_motivation_report(time_label: str):
    if GROUP_ID == 0:
        return

    now = datetime.now(TZ)
    today = now.date()
    day_stats = get_day_stats(today)
    period_stats = get_period_stats(now)

    if day_stats["total"] == 0:
        text = (
            f"🌟 <b>{time_label} — Бугунча ҳолат</b>\n\n"
            f"Хатолик йўқ ✅\n"
            f"Шунақа давом этамиз! Эртага яна ҳам тоза ишлаймиз 💪"
        )
    else:
        text = (
            f"📊 <b>{time_label} — Бугунча ҳисобот</b>\n\n"
            f"Жами шикоят: <b>{day_stats['total']}</b>\n"
            f"Очиқ: <b>{day_stats['open']}</b>\n"
            f"Бартараф: <b>{day_stats['resolved']}</b>\n"
            f"Рад: <b>{day_stats['rejected']}</b>\n\n"
            f"⚡ Мотивация: хатоликни эртагага қолдирмай, шу заҳоти ёпамиз!"
        )

    text += (
        f"\n\n📅 <b>Ойлик ҳисоб (2-санадан)</b>\n"
        f"Бошланиш: <b>{period_stats['start'].strftime('%d.%m.%Y')}</b>\n"
        f"Жами: <b>{period_stats['total']}</b> | "
        f"Очиқ: <b>{period_stats['open']}</b> | "
        f"Бартараф: <b>{period_stats['resolved']}</b> | "
        f"Рад: <b>{period_stats['rejected']}</b>"
    )

    await bot.send_message(GROUP_ID, text)

async def new_period_announcement():
    if GROUP_ID == 0:
        return
    now = datetime.now(TZ)
    start = period_start_for(now)
    await bot.send_message(
        GROUP_ID,
        f"🆕 <b>Янги ҳисоб ойи бошланди!</b>\n"
        f"📅 Бошланиш: <b>{start.strftime('%d.%m.%Y')}</b>\n\n"
        f"Ишни янги ойда тоза бошлаймиз 💪"
    )

def setup_scheduler():
    sch = AsyncIOScheduler(timezone=TZ)
    sch.add_job(send_motivation_report, "cron", hour=8, minute=0, args=["08:00"])
    sch.add_job(send_motivation_report, "cron", hour=21, minute=0, args=["21:00"])
    sch.add_job(new_period_announcement, "cron", day=2, hour=0, minute=5)
    sch.start()
    return sch

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set it in Railway Variables.")
    init_db()
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
