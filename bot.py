import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE").strip()
GROUP_ID = int(os.getenv("-1001877019294").strip() or 

TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"

ADMIN_IDS = set()
_raw_admins = os.getenv("ADMIN_IDS", "").strip()
if _raw_admins:
    for x in _raw_admins.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

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

START_TS = time.time()


# ===================== DB =====================
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

def insert_complaint(user_id, username, employee, desc, created_at):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO complaints (tg_user_id, tg_username, employee, description, created_at, status)
        VALUES (?, ?, ?, ?, ?, 'open')
    """, (user_id, username, employee, desc, created_at))
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
    # Hisob oyimiz 2-sanadan boshlanadi
    if dt.day >= 2:
        return datetime(dt.year, dt.month, 2, 0, 0, 0, tzinfo=TZ)
    first_of_month = datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=TZ)
    prev_month_last = first_of_month - timedelta(days=1)
    return datetime(prev_month_last.year, prev_month_last.month, 2, 0, 0, 0, tzinfo=TZ)

def get_period_stats(now: datetime):
    start_dt = period_start_for(now)
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
    """, (start_dt.isoformat(),))
    row = cur.fetchone()
    con.close()
    return {
        "open": row[0] or 0,
        "resolved": row[1] or 0,
        "rejected": row[2] or 0,
        "total": row[3] or 0,
        "start": start_dt,
    }


# ===================== UI =====================
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


# ===================== FSM =====================
class ComplaintFlow(StatesGroup):
    enter_description = State()


# ===================== BOT =====================
logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())


def is_admin(uid: int) -> bool:
    return (not ADMIN_IDS) or (uid in ADMIN_IDS)


def uptime_str() -> str:
    sec = int(time.time() - START_TS)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


@dp.message(CommandStart())
async def start(m: Message):
    if m.chat.type != "private":
        await m.answer("✅ Nazorat bot ishlayapti.\nGuruh ID uchun: /chatid")
        return

    await m.answer(
        "Салом! 👋\n"
        "Жалоба/хатони қайси ходимга тегишли эканини танланг:",
        reply_markup=employees_kb()
    )

@dp.message(Command("myid"))
async def myid(m: Message):
    await m.answer(f"✅ Sizning ID: <code>{m.from_user.id}</code>")

@dp.message(Command("chatid"))
async def chatid(m: Message):
    await m.answer(f"✅ Chat ID: <code>{m.chat.id}</code>\nType: <b>{m.chat.type}</b>")

@dp.message(Command("status"))
async def status_cmd(m: Message):
    txt = (
        f"✅ Bot LIVE\n"
        f"⏱ Uptime: <b>{uptime_str()}</b>\n"
        f"🧪 TEST_MODE: <b>{'ON' if TEST_MODE else 'OFF'}</b>\n"
        f"👥 GROUP_ID: <code>{GROUP_ID}</code>"
    )
    await m.answer(txt)

@dp.message(Command("test_report"))
async def test_report(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("❌ Siz admin emassiz.")
    await send_motivation_report("TEST_REPORT (manual)")

@dp.message(Command("test_complaint"))
async def test_complaint(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("❌ Siz admin emassiz.")
    if GROUP_ID == 0:
        return await m.answer("❌ GROUP_ID o‘rnatilmagan.")

    now = datetime.now(TZ)
    employee = EMPLOYEES[0]
    desc = f"TEST жалоба ✅ | uptime={uptime_str()} | {now.strftime('%d.%m.%Y %H:%M:%S')}"
    username = m.from_user.username or ""
    cid = insert_complaint(m.from_user.id, username, employee, desc, now.isoformat())

    who = f"@{username}" if username else f"ID:{m.from_user.id}"
    text = (
        f"🧪 <b>TEST: Янги хатолик</b>\n\n"
        f"👤 <b>Ким ёзди:</b> {who}\n"
        f"🧑‍💼 <b>Ходим:</b> {employee}\n"
        f"📝 <b>Тавсиф:</b>\n{desc}\n\n"
        f"🕒 <b>Вақт:</b> {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 <b>Статус:</b> ⏳ Кутилмоқда"
    )
    sent = await bot.send_message(GROUP_ID, text, reply_markup=close_kb(cid))
    set_group_message_id(cid, sent.message_id)
    await m.answer("✅ TEST жалоба гуруҳга чиқди.")


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
    if GROUP_ID == 0:
        await m.answer("❌ Ҳозирча GROUP_ID қўйилмаган. Гуруҳда /chatid қилиб, Railway’да GROUP_ID ни қўйинг.")
        await state.clear()
        return

    data = await state.get_data()
    employee = data["employee"]
    desc = (m.text or "").strip()
    if not desc:
        await m.answer("Тавсиф бўш бўлмасин. Қайта ёзинг.")
        return

    now = datetime.now(TZ)
    username = m.from_user.username or ""
    user_id = m.from_user.id

    cid = insert_complaint(user_id, username, employee, desc, now.isoformat())

    who = f"@{username}" if username else f"ID:{user_id}"
    prefix = "🧪 <b>TEST</b>\n" if TEST_MODE else ""
    text = (
        f"{prefix}🚨 <b>Янги хатолик аниқланди</b>\n\n"
        f"👤 <b>Ким ёзди:</b> {who}\n"
        f"🧑‍💼 <b>Ходим:</b> {employee}\n"
        f"📝 <b>Тавсиф:</b>\n{desc}\n\n"
        f"🕒 <b>Вақт:</b> {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 <b>Статус:</b> ⏳ Кутилмоқда"
    )

    sent = await bot.send_message(GROUP_ID, text, reply_markup=close_kb(cid))
    set_group_message_id(cid, sent.message_id)

    await m.answer("Қабул қилинди ✅\nШикоят гуруҳга чиқарилди.")
    await state.clear()


@dp.callback_query(F.data.startswith("close:"))
async def close_in_group(cb: CallbackQuery):
    if GROUP_ID != 0 and cb.message.chat.id != GROUP_ID:
        await cb.answer("Бу тугма фақат асосий гуруҳда ишлайди.", show_alert=True)
        return

    _, cid, status = cb.data.split(":")
    cid = int(cid)

    if status not in ("resolved", "rejected"):
        await cb.answer("Нотўғри статус.", show_alert=True)
        return

    now = datetime.now(TZ)
    closer_username = cb.from_user.username or ""
    ok = close_complaint(cid, status, cb.from_user.id, closer_username, now.isoformat())
    if not ok:
        await cb.answer("Бу хатолик аллақачон ёпилган.", show_alert=True)
        return

    status_text = "✅ Бартараф этилди" if status == "resolved" else "❌ Асосли эмас (рад)"
    closer_tag = f"@{closer_username}" if closer_username else f"ID:{cb.from_user.id}"

    new_text = cb.message.html_text.replace(
        "📌 <b>Статус:</b> ⏳ Кутилмоқда",
        f"📌 <b>Статус:</b> {status_text}\n"
        f"🔒 <b>Ёпди:</b> {closer_tag}\n"
        f"🕒 <b>Ёпилган вақт:</b> {now.strftime('%d.%m.%Y %H:%M')}"
    )

    await cb.message.edit_text(new_text, reply_markup=None)
    await cb.answer("Ёпилди ✅")


# ===================== SCHEDULER =====================
async def send_motivation_report(time_label: str):
    if GROUP_ID == 0:
        return

    now = datetime.now(TZ)
    today = now.date()
    day_stats = get_day_stats(today)
    period_stats = get_period_stats(now)

    test_prefix = "🧪 <b>TEST MODE</b>\n" if TEST_MODE else ""
    up = uptime_str()

    if day_stats["total"] == 0:
        text = (
            f"{test_prefix}🌟 <b>{time_label} — Бугунча ҳолат</b>\n\n"
            f"Хатолик йўқ ✅\n"
            f"Шунақа давом этамиз! Эртага яна ҳам тоза ишлаймиз 💪\n\n"
            f"⏱ Uptime: <b>{up}</b>"
        )
    else:
        text = (
            f"{test_prefix}📊 <b>{time_label} — Бугунча ҳисобот</b>\n\n"
            f"Жами шикоят: <b>{day_stats['total']}</b>\n"
            f"Очиқ: <b>{day_stats['open']}</b>\n"
            f"Бартараф: <b>{day_stats['resolved']}</b>\n"
            f"Рад: <b>{day_stats['rejected']}</b>\n\n"
            f"⚡ Мотивация: хатоликни эртагага қолдирмай, шу заҳоти ёпамиз!\n\n"
            f"⏱ Uptime: <b>{up}</b>"
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
    test_prefix = "🧪 <b>TEST MODE</b>\n" if TEST_MODE else ""
    await bot.send_message(
        GROUP_ID,
        f"{test_prefix}🆕 <b>Янги ҳисоб ойи бошланди!</b>\n"
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


# ===================== MAIN =====================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set it in Railway Variables.")

    init_db()
    setup_scheduler()

    # Startup heartbeat (admin + group)
    try:
        start_msg = f"✅ Nazorat bot start/restart\n⏱ Uptime: <b>{uptime_str()}</b>\n🧪 TEST_MODE: <b>{'ON' if TEST_MODE else 'OFF'}</b>"
        # Adminlarga
        for aid in ADMIN_IDS:
            try:
                await bot.send_message(aid, start_msg)
            except:
                pass
        # Guruhga (GROUP_ID bo'lsa)
        if GROUP_ID != 0:
            await bot.send_message(GROUP_ID, start_msg)
    except:
        pass

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
