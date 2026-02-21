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


# ===================== CONFIG (ENV) =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "-1001877019294").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "1432810519").strip()

TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"
DB_PATH = os.getenv("DB_PATH", "complaints.sqlite3").strip()
TZ_NAME = os.getenv("TZ", "Asia/Tashkent").strip()

ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "3").strip() or "3")  # default 3

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
if not ADMIN_IDS_RAW:
    raise RuntimeError("ADMIN_IDS is empty. Set Railway variable ADMIN_IDS to your Telegram ID.")
for x in ADMIN_IDS_RAW.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS parsed empty. Example: ADMIN_IDS=123456789")

TZ = ZoneInfo(TZ_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("nazorat")


# ===================== BOT / ROUTER / SCHED =====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
scheduler = AsyncIOScheduler(timezone=TZ)

# user_id -> chosen employee
PENDING: dict[int, str] = {}


# ===================== HELPERS =====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def now_tz() -> datetime:
    return datetime.now(TZ)

def today_key() -> str:
    return now_tz().strftime("%Y-%m-%d")

def month_key_for(dt: datetime) -> str:
    # New cycle starts on day 2
    if dt.day >= 2:
        return dt.strftime("%Y-%m")
    prev = (dt.replace(day=1) - timedelta(days=1))
    return prev.strftime("%Y-%m")

def short(s: str, n: int = 120) -> str:
    s = clean_text(s)
    return s if len(s) <= n else (s[: n - 1] + "…")


# ===================== DB =====================
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
                status TEXT NOT NULL DEFAULT 'open',          -- open|done|rejected|deleted
                decided_at TEXT,
                decided_by INTEGER,
                group_message_id INTEGER,
                group_chat_id INTEGER
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_compl_month ON complaints(month_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_compl_status ON complaints(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_compl_user ON complaints(from_user_id)")

        # Alert history (so we don't spam)
        con.execute("""
            CREATE TABLE IF NOT EXISTS alerts_sent(
                day_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(day_key, kind)
            )
        """)
        con.commit()

def db_add_complaint(m: Message, employee: str, text: str) -> int:
    dt = now_tz()
    mk = month_key_for(dt)
    with db_conn() as con:
        cur = con.execute("""
            INSERT INTO complaints(created_at, month_key, from_user_id, from_username, from_fullname, employee, text, status)
            VALUES(?,?,?,?,?,?,?, 'open')
        """, (
            dt.isoformat(),
            mk,
            m.from_user.id,
            (m.from_user.username if m.from_user else None),
            (m.from_user.full_name if m.from_user else None),
            employee,
            text,
        ))
        con.commit()
        return int(cur.lastrowid)

def db_attach_group_message(complaint_id: int, group_chat_id: int, group_message_id: int):
    with db_conn() as con:
        con.execute("""
            UPDATE complaints
            SET group_chat_id=?, group_message_id=?
            WHERE id=?
        """, (group_chat_id, group_message_id, complaint_id))
        con.commit()

def db_get(complaint_id: int):
    with db_conn() as con:
        return con.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()

def db_set_status(complaint_id: int, status: str, decided_by: int) -> bool:
    dt = now_tz()
    with db_conn() as con:
        row = con.execute("SELECT status FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        if not row:
            return False
        if row["status"] in ("done", "rejected", "deleted"):
            return False
        con.execute("""
            UPDATE complaints
            SET status=?, decided_at=?, decided_by=?
            WHERE id=?
        """, (status, dt.isoformat(), decided_by, complaint_id))
        con.commit()
        return True

def db_delete_mark(complaint_id: int, decided_by: int) -> bool:
    dt = now_tz()
    with db_conn() as con:
        row = con.execute("SELECT status FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        if not row:
            return False
        if row["status"] == "deleted":
            return False
        con.execute("""
            UPDATE complaints
            SET status='deleted', decided_at=?, decided_by=?
            WHERE id=?
        """, (dt.isoformat(), decided_by, complaint_id))
        con.commit()
        return True

def db_today_total_non_deleted() -> int:
    dt = now_tz()
    start = datetime.combine(dt.date(), dtime(0, 0), tzinfo=TZ)
    end = start + timedelta(days=1)
    with db_conn() as con:
        return int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status!='deleted'
        """, (start.isoformat(), end.isoformat())).fetchone()["c"])

def db_today_stats():
    dt = now_tz()
    start = datetime.combine(dt.date(), dtime(0, 0), tzinfo=TZ)
    end = start + timedelta(days=1)
    mk = month_key_for(dt)

    with db_conn() as con:
        total_today = int(con.execute("""
            SELECT COUNT(*) AS c FROM complaints
            WHERE created_at >= ? AND created_at < ? AND status!='deleted'
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
            SELECT COUNT(*) AS c FROM complaints WHERE month_key=? AND status!='deleted'
        """, (mk,)).fetchone()["c"])

    return {
        "date": dt.strftime("%Y-%m-%d"),
        "month_key": mk,
        "total_today": total_today,
        "done_today": done_today,
        "rejected_today": rejected_today,
        "open_now": open_now,
        "month_total": month_total,
    }

def db_month_top_employees(mk: str, limit: int = 10):
    with db_conn() as con:
        rows = con.execute("""
            SELECT employee, COUNT(*) AS c
            FROM complaints
            WHERE month_key=? AND status!='deleted'
            GROUP BY employee
            ORDER BY c DESC, employee ASC
            LIMIT ?
        """, (mk, limit)).fetchall()
        return [(r["employee"], int(r["c"])) for r in rows]

def db_list_open(limit: int = 20):
    with db_conn() as con:
        return con.execute("""
            SELECT id, created_at, employee, text, from_fullname, from_username, from_user_id
            FROM complaints
            WHERE status='open'
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

def db_alert_already_sent(day: str, kind: str) -> bool:
    with db_conn() as con:
        row = con.execute("SELECT 1 FROM alerts_sent WHERE day_key=? AND kind=?", (day, kind)).fetchone()
        return row is not None

def db_mark_alert_sent(day: str, kind: str):
    with db_conn() as con:
        con.execute("""
            INSERT OR IGNORE INTO alerts_sent(day_key, kind, sent_at)
            VALUES(?,?,?)
        """, (day, kind, now_tz().isoformat()))
        con.commit()


# ===================== KEYBOARDS =====================
def kb_employees():
    b = InlineKeyboardBuilder()
    for name in EMPLOYEES:
        b.button(text=name, callback_data=f"emp:{name}")
    b.adjust(2)
    return b.as_markup()

def kb_group_actions(complaint_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Хато бартараф этилди", callback_data=f"act:done:{complaint_id}")
    b.button(text="❌ Асосли эмас (рад)", callback_data=f"act:rejected:{complaint_id}")
    b.button(text="🗑 Ўчириш", callback_data=f"act:delete:{complaint_id}")
    b.adjust(1)
    return b.as_markup()

def kb_admin_panel():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="📥 Очиқ жалобалар", callback_data="adm:open")
    b.button(text="🏆 Топ ходимлар (ой)", callback_data="adm:top")
    b.adjust(1)
    return b.as_markup()


# ===================== SOFT USER NOTIFICATIONS =====================
REJECT_TEXT = (
    "Сизнинг мурожаатингиз кўриб чиқилди ✅\n\n"
    "Ҳозирча ушбу ҳолат тасдиқланмади.\n"
    "Барча мурожаатлар диққат билан текширилади.\n\n"
    "Фаоллигингиз учун раҳмат 🤝"
)
DONE_TEXT = (
    "Сизнинг мурожаатингиз қабул қилинди ✅\n\n"
    "Муаммо бартараф этилди.\n"
    "Эътиборингиз учун раҳмат 🙌"
)


# ===================== ALERT (3 complaints/day) =====================
async def maybe_send_daily_alert():
    day = today_key()
    kind = "threshold3"
    if db_alert_already_sent(day, kind):
        return

    total = db_today_total_non_deleted()
    if total < ALERT_THRESHOLD:
        return

    db_mark_alert_sent(day, kind)

    msg = (
        f"🚨 <b>ALERT</b>\n"
        f"Бугун мурожаатлар сони <b>{total}</b> тага етди.\n"
        f"Чора кўриш керак: сабабларни тез текшириб чиқамиз."
        + ("\n\n🧪 <b>TEST MODE</b>" if TEST_MODE else "")
    )
    await bot.send_message(GROUP_ID, msg)

    # optional: DM admin too (first admin)
    try:
        admin_id = next(iter(ADMIN_IDS))
        await bot.send_message(admin_id, f"🚨 ALERT: bugun {total} ta murojaat (threshold {ALERT_THRESHOLD}).")
    except Exception:
        pass


# ===================== HANDLERS =====================
@router.message(Command("start"))
async def cmd_start(m: Message):
    if m.chat.type != "private":
        await m.answer("✅ Nazorat bot ишлаяпти.")
        return
    await m.answer(
        "Салом! 👋\n\n"
        "Бу бот орқали хатолик/камчилик ҳақида мурожаат қолдиришингиз мумкин.\n\n"
        "Мурожаат ёзиш: /complaint\n"
        "ID кўриш: /myid\n"
        + ("\n\n🧪 TEST MODE ON" if TEST_MODE else "")
    )

@router.message(Command("myid"))
async def cmd_myid(m: Message):
    await m.answer(f"🆔 Сизнинг ID: <code>{m.from_user.id}</code>")

@router.message(Command("complaint"))
async def cmd_complaint(m: Message):
    if m.chat.type != "private":
        return
    PENDING[m.from_user.id] = ""
    await m.answer("Ким ҳақида мурожаат? Танланг:", reply_markup=kb_employees())

@router.message(Command("admin"))
async def cmd_admin(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("⛔ Бу бўлим фақат админ учун.")
        return
    await m.answer("🛠 Админ панель:", reply_markup=kb_admin_panel())

@router.message(Command("del"))
async def cmd_del(m: Message):
    # /del 123  -> delete complaint by id (admin only)
    if not is_admin(m.from_user.id):
        await m.answer("⛔ Фақат админ.")
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Формат: /del 123")
        return

    cid = int(parts[1])
    row = db_get(cid)
    if not row:
        await m.answer("Топилмади.")
        return

    ok = db_delete_mark(cid, m.from_user.id)
    if not ok:
        await m.answer("Аллақачон ўчирилган.")
        return

    # try to delete group message
    deleted_msg = False
    try:
        if row["group_chat_id"] and row["group_message_id"]:
            await bot.delete_message(int(row["group_chat_id"]), int(row["group_message_id"]))
            deleted_msg = True
    except Exception:
        deleted_msg = False

    await m.answer("🗑 Ўчирилди." + (" (Гуруҳдан ҳам ўчди ✅)" if deleted_msg else " (Гуруҳда ўчмаслиги мумкин — ботга delete permission керак)"))

@router.callback_query(F.data.startswith("adm:"))
async def cb_admin(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Фақат админ", show_alert=True)
        return

    action = c.data.split("adm:", 1)[1]
    if action == "stats":
        st = db_today_stats()
        mk = st["month_key"]
        msg = (
            f"📊 <b>Статистика</b>\n"
            f"Сана: <b>{st['date']}</b>\n"
            f"Ой цикли: <b>{mk}</b> (2-санадан)\n\n"
            f"Бугун жами: <b>{st['total_today']}</b>\n"
            f"✅ Бартараф: <b>{st['done_today']}</b>\n"
            f"❌ Рад: <b>{st['rejected_today']}</b>\n"
            f"⏳ Очиқ: <b>{st['open_now']}</b>\n"
            f"📌 Ой бўйича жами: <b>{st['month_total']}</b>"
        )
        await c.message.answer(msg)
        await c.answer()
        return

    if action == "top":
        st = db_today_stats()
        mk = st["month_key"]
        tops = db_month_top_employees(mk)
        lines = [f"🏆 <b>Топ ходимлар (ой: {mk})</b>"]
        if not tops:
            lines.append("Ҳозирча маълумот йўқ.")
        else:
            for i, (name, cnt) in enumerate(tops, 1):
                lines.append(f"{i}) {name}: <b>{cnt}</b>")
        await c.message.answer("\n".join(lines))
        await c.answer()
        return

    if action == "open":
        rows = db_list_open(limit=20)
        if not rows:
            await c.message.answer("📥 Очиқ мурожаат йўқ ✅")
            await c.answer()
            return
        lines = ["📥 <b>Очиқ мурожаатлар (охирги 20)</b>"]
        for r in rows:
            uname = f"@{r['from_username']}" if r["from_username"] else "—"
            lines.append(
                f"• <code>{r['id']}</code> | {r['employee']} | {short(r['text'], 60)}\n"
                f"  {r['from_fullname']} ({uname}) | <code>{r['from_user_id']}</code>"
            )
        lines.append("\n🗑 Ўчириш: /del ID ёки гуруҳдаги “Ўчириш” тугмаси.")
        await c.message.answer("\n".join(lines))
        await c.answer()
        return

    await c.answer("Номаълум", show_alert=True)

@router.callback_query(F.data.startswith("emp:"))
async def cb_employee(c: CallbackQuery):
    if c.message.chat.type != "private":
        await c.answer("Фақат личкада.", show_alert=True)
        return
    employee = c.data.split("emp:", 1)[1]
    PENDING[c.from_user.id] = employee
    await c.message.edit_text(
        f"✅ Танланди: <b>{employee}</b>\n\nЭнди мурожаат матнини ёзинг (1 хабарда)."
    )
    await c.answer()

@router.message(F.text)
async def handle_text(m: Message):
    if m.chat.type != "private":
        return

    employee = PENDING.get(m.from_user.id)
    if not employee:
        return

    text = clean_text(m.text)
    if not text:
        await m.answer("Матн бўш бўлмасин.")
        return

    complaint_id = db_add_complaint(m, employee, text)
    PENDING.pop(m.from_user.id, None)

    uname = f"@{m.from_user.username}" if m.from_user.username else "—"
    msg = (
        f"🆕 <b>Янги мурожаат</b>\n"
        f"🧾 ID: <code>{complaint_id}</code>\n"
        f"👤 Ходим: <b>{employee}</b>\n"
        f"✍️ Кимдан: <b>{m.from_user.full_name}</b> ({uname}) | <code>{m.from_user.id}</code>\n"
        f"⏱ Вақт: <b>{now_tz().strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"📝 <b>Тавсиф:</b>\n{text}"
        + ("\n\n🧪 <b>TEST MODE</b>" if TEST_MODE else "")
    )

    sent = await bot.send_message(GROUP_ID, msg, reply_markup=kb_group_actions(complaint_id))
    db_attach_group_message(complaint_id, GROUP_ID, sent.message_id)

    await m.answer("✅ Қабул қилинди. Раҳмат!")

    # check alert immediately after new complaint
    try:
        await maybe_send_daily_alert()
    except Exception as e:
        log.warning("alert check failed: %s", e)

@router.callback_query(F.data.startswith("act:"))
async def cb_action(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Фақат админ", show_alert=True)
        return

    try:
        _, act, cid_s = c.data.split(":")
        cid = int(cid_s)
    except Exception:
        await c.answer("Хато data", show_alert=True)
        return

    row = db_get(cid)
    if not row:
        await c.answer("Топилмади", show_alert=True)
        return

    if act == "done":
        ok = db_set_status(cid, "done", c.from_user.id)
        if not ok:
            await c.answer("Аллақачон ёпилган/ўчирилган.", show_alert=True)
            return

        try:
            await bot.send_message(int(row["from_user_id"]), DONE_TEXT)
        except Exception:
            pass

        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await c.message.reply(f"✅ Бартараф этилди | ID: <code>{cid}</code>")
        await c.answer("OK")
        return

    if act == "rejected":
        ok = db_set_status(cid, "rejected", c.from_user.id)
        if not ok:
            await c.answer("Аллақачон ёпилган/ўчирилган.", show_alert=True)
            return

        try:
            await bot.send_message(int(row["from_user_id"]), REJECT_TEXT)
        except Exception:
            pass

        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await c.message.reply(f"❌ Рад этилди | ID: <code>{cid}</code>")
        await c.answer("OK")
        return

    if act == "delete":
        ok = db_delete_mark(cid, c.from_user.id)
        if not ok:
            await c.answer("Аллақачон ўчирилган.", show_alert=True)
            return

        # try delete group message
        try:
            if row["group_chat_id"] and row["group_message_id"]:
                await bot.delete_message(int(row["group_chat_id"]), int(row["group_message_id"]))
                await c.answer("Ўчирилди")
                return
        except Exception:
            # cannot delete in group -> at least remove buttons and mark
            try:
                await c.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await c.message.reply(f"🗑 Админ томонидан ўчирилди (гуруҳдан ўчириш учун ботга delete permission керак) | ID: <code>{cid}</code>")
            await c.answer("Ўчирилди")
            return

    await c.answer("Номаълум амал", show_alert=True)


# ===================== SCHEDULED REPORTS =====================
def motivation_text(has_errors: bool, when_label: str) -> str:
    if has_errors:
        return (
            f"⚠️ <b>{when_label}</b>\n"
            f"Бугун мурожаатлар бор. Диққатни ошириб ишлаймиз.\n"
            f"💪 Тартиб — натижа!"
        )
    return (
        f"✅ <b>{when_label}</b>\n"
        f"Бугунча мурожаат йўқ! 👏\n"
        f"🚀 Эртанги кунга ҳам шу темп!"
    )

async def send_report(when_label: str):
    st = db_today_stats()
    txt = (
        motivation_text(st["total_today"] > 0, when_label)
        + "\n\n"
        f"📌 Бугунги мурожаатлар: <b>{st['total_today']}</b>\n"
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
    scheduler.add_job(lambda: asyncio.create_task(send_report("08:00 назорат")), "cron", hour=8, minute=0)
    scheduler.add_job(lambda: asyncio.create_task(send_report("21:00 назорат")), "cron", hour=21, minute=0)

    # periodic alert check (backup)
    scheduler.add_job(lambda: asyncio.create_task(maybe_send_daily_alert()), "interval", minutes=10)

    if TEST_MODE:
        scheduler.add_job(lambda: asyncio.create_task(test_ping()), "interval", hours=2)


# ===================== MAIN =====================
async def main():
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)

    dp.include_router(router)
    setup_jobs()
    scheduler.start()

    try:
        await bot.send_message(
            GROUP_ID,
            "✅ <b>Nazorat bot ишга тушди</b>"
            + ("\n🧪 TEST MODE ON" if TEST_MODE else "")
            + f"\n🚨 Alert threshold: {ALERT_THRESHOLD}"
        )
    except Exception as e:
        log.warning("Startup message failed: %s", e)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
