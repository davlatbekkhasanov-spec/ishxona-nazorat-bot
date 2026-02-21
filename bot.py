import os
import re
import asyncio
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "-1001877019294").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "1432810519").strip()  # "143,144" etc
TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"
DB_PATH = os.getenv("DB_PATH", "complaints.sqlite3").strip()
TZ_NAME = os.getenv("TZ", "Asia/Tashkent").strip()
RESET_CODE = os.getenv("RESET_CODE", "BRON-2026-RESET").strip()  # ўзинг алмаштир

TZ = ZoneInfo(TZ_NAME)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN.")
if not GROUP_ID_RAW:
    raise RuntimeError("GROUP_ID is empty. Set Railway variable GROUP_ID.")

try:
    GROUP_ID = int(GROUP_ID_RAW)
except ValueError:
    raise RuntimeError("GROUP_ID must be integer like -100....")

ADMIN_IDS = set()
for x in ADMIN_IDS_RAW.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))

if not ADMIN_IDS:
    # Агар ADMIN_IDS қўйилмаган бўлса ҳам бот ишлайди, лекин панел/статистика ишламайди.
    # Яхшиси ADMIN_IDS қўй.
    pass

# Ходимлар рўйхати (сен хоҳласанг кейин кенгайтирамиз)
EMPLOYEES = [
    "Сагдуллаев Юнус",
    "Самадов Тулкин",
    "Тохиров Муслимбек",
    "Мустафоев Абдулло",
    "Ражаббоев Пулат",
]

# ===================== LOGGING =====================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nazorat-bot")

# ===================== BOT / DP =====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

scheduler = AsyncIOScheduler(timezone=TZ)


# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL,
            from_user TEXT NOT NULL,
            from_user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',   -- new/done/rejected
            created_at TEXT NOT NULL,
            closed_at TEXT,
            admin_action_by INTEGER,
            admin_action_note TEXT
        );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_emp ON complaints(employee);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_status ON complaints(status);")

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


# ===================== HELPERS =====================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def user_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📝 Янги шикоят")
    kb.button(text="ℹ️ Ёрдам")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

def employee_kb(prefix: str):
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(EMPLOYEES):
        kb.button(text=name, callback_data=f"{prefix}:{i}")
    kb.adjust(1)
    return kb.as_markup()

def complaint_actions_kb(cid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ёпилди (DONE)", callback_data=f"act:done:{cid}")
    kb.button(text="❌ Рад этилди (REJECT)", callback_data=f"act:rej:{cid}")
    kb.adjust(2)
    return kb.as_markup()

def nav_kb(employee: str, pos: int, total: int, cid: int):
    kb = InlineKeyboardBuilder()
    # Навигация
    if pos > 1:
        kb.button(text="⬅️ Олдинги", callback_data=f"nav:prev:{employee}:{pos}")
    if pos < total:
        kb.button(text="➡️ Кейинги", callback_data=f"nav:next:{employee}:{pos}")
    kb.adjust(2)

    # Амаллар
    kb.row(
        InlineKeyboardBuilder().button(text="✅ DONE", callback_data=f"act:done:{cid}").as_markup().inline_keyboard[0][0],
        InlineKeyboardBuilder().button(text="❌ REJECT", callback_data=f"act:rej:{cid}").as_markup().inline_keyboard[0][0],
    )

    kb.row(
        InlineKeyboardBuilder().button(text="🔙 Панел", callback_data="panel:open").as_markup().inline_keyboard[0][0]
    )
    return kb.as_markup()

def panel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Ходим танлаш", callback_data="panel:employees")
    kb.button(text="📊 Статистика", callback_data="panel:stats")
    kb.button(text="🧹 База тозалаш + рестарт (BRON)", callback_data="panel:reset_info")
    kb.adjust(1)
    return kb.as_markup()

async def notify_group(text: str):
    try:
        await bot.send_message(GROUP_ID, text)
    except Exception as e:
        log.warning("notify_group error: %s", e)

async def notify_admins(text: str, reply_markup=None):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, reply_markup=reply_markup)
        except Exception as e:
            log.warning("notify_admins error to %s: %s", aid, e)

def format_admin_card(row: sqlite3.Row) -> str:
    # Талаб: "янги мурожат эмас - Янги шикоят", "тавсиф эмас - Шикоят мазмуни"
    created = row["created_at"]
    return (
        f"<b>Янги шикоят</b>\n"
        f"ID: <code>{row['id']}</code>\n"
        f"Ходим: <b>{row['employee']}</b>\n"
        f"Кимдан: <b>{row['from_user']}</b> | <code>{row['from_user_id']}</code>\n"
        f"Вақт: <b>{created}</b>\n\n"
        f"<b>Шикоят мазмуни:</b>\n{row['text']}"
    )

def psych_reject_text() -> str:
    # “қисқа ва чиройли, психологик таъсир”
    # (ҳақоратсиз, лекин қатъий)
    return (
        "Шикоятингиз қабул қилинмади.\n"
        "Илтимос, фактлар ва аниқ далиллар билан қайта юборинг. "
        "Нотўғри маълумот юбориш назоратда қайд этилади."
    )


# ===================== COMMANDS =====================
async def set_commands():
    # /start босганда командалар чиқиши учун
    try:
        await bot.set_my_commands([
            ("start", "Ботни ишга тушириш"),
            ("help", "Ёрдам"),
            ("panel", "Админ панел (фақат админ)"),
            ("stats", "Статистика (фақат админ)"),
            ("ping", "Бот тирикми текшириш"),
            ("bron", "BRON reset info (фақат админ)"),
        ])
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)


# ===================== USER FLOW (NO FSM, SIMPLE) =====================
# Юзта FSM қилмай, “professional” ва барқарор вариант:
# 1) user: "📝 Янги шикоят" -> ходим танлайди
# 2) user: шикоят матнини ёзади
# 3) db save -> админ/группага юборилади

USER_STATE = {}  # user_id -> {"step": "...", "employee": "..."}

@rt.message(Command("start"))
async def cmd_start(m: Message):
    USER_STATE.pop(m.from_user.id, None)
    await m.answer(
        "Ассалому алайкум.\n"
        "Бу — <b>Ishxona Nazorat Bot</b>.\n\n"
        "Шикоят қолдириш учун <b>📝 Янги шикоят</b> тугмасини босинг.",
        reply_markup=user_keyboard()
    )

@rt.message(Command("help"))
@rt.message(F.text == "ℹ️ Ёрдам")
async def cmd_help(m: Message):
    await m.answer(
        "Қоидалар:\n"
        "1) Ходимни танланг\n"
        "2) Шикоятни аниқ ва қисқа ёзинг\n\n"
        "Админлар шикоятни кўриб чиқади."
    )

@rt.message(Command("ping"))
async def cmd_ping(m: Message):
    await m.answer("✅ Online")

@rt.message(F.text == "📝 Янги шикоят")
async def new_complaint(m: Message):
    USER_STATE[m.from_user.id] = {"step": "pick_employee"}
    await m.answer("Ким ҳақида шикоят? Ходимни танланг:", reply_markup=employee_kb("uemp"))

@rt.callback_query(F.data.startswith("uemp:"))
async def user_pick_employee(c: CallbackQuery):
    uid = c.from_user.id
    st = USER_STATE.get(uid)
    if not st or st.get("step") != "pick_employee":
        await c.answer("Қайтадан /start қилинг", show_alert=True)
        return

    idx = int(c.data.split(":")[1])
    if idx < 0 or idx >= len(EMPLOYEES):
        await c.answer("Хато танлов", show_alert=True)
        return

    emp = EMPLOYEES[idx]
    USER_STATE[uid] = {"step": "enter_text", "employee": emp}
    await c.message.edit_text(f"Ходим: <b>{emp}</b>\n\nЭнди <b>Шикоят мазмуни</b>ни ёзинг:")
    await c.answer()

@rt.message()
async def user_text_router(m: Message):
    uid = m.from_user.id
    st = USER_STATE.get(uid)
    if not st:
        return  # бошқа хабарларга жавоб бермаймиз (сен айтганингдек “ўзгаришлар қилма”)
    if st.get("step") != "enter_text":
        return

    text = (m.text or "").strip()
    if len(text) < 3:
        await m.answer("Шикоят жуда қисқа. Илтимос, тўлиқроқ ёзинг.")
        return

    employee = st["employee"]
    from_user = (m.from_user.full_name or "NoName").strip()

    with db() as con:
        con.execute(
            "INSERT INTO complaints(employee, from_user, from_user_id, text, status, created_at) VALUES(?,?,?,?,?,?)",
            (employee, from_user, uid, text, "new", now_str())
        )
        cid = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        row = con.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()

    admin_text = format_admin_card(row)

    # Админга тугмалар билан
    await notify_admins(admin_text, reply_markup=complaint_actions_kb(cid))
    # Гуруҳга оддий (тугмасиз)
    await notify_group(admin_text)

    USER_STATE.pop(uid, None)
    await m.answer("✅ Шикоят қабул қилинди. Раҳмат.", reply_markup=user_keyboard())


# ===================== ADMIN PANEL =====================
@rt.message(Command("panel"))
async def cmd_panel(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("Админ панел:", reply_markup=panel_kb())

@rt.callback_query(F.data == "panel:open")
async def panel_open(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return
    await c.message.edit_text("Админ панел:", reply_markup=panel_kb())
    await c.answer()

@rt.callback_query(F.data == "panel:employees")
async def panel_employees(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return
    await c.message.edit_text("Ходимни танланг:", reply_markup=employee_kb("aemp"))
    await c.answer()

@rt.callback_query(F.data.startswith("aemp:"))
async def admin_pick_employee(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return

    idx = int(c.data.split(":")[1])
    if idx < 0 or idx >= len(EMPLOYEES):
        await c.answer("Хато", show_alert=True)
        return

    employee = EMPLOYEES[idx]

    with db() as con:
        rows = con.execute(
            "SELECT * FROM complaints WHERE employee=? ORDER BY id DESC",
            (employee,)
        ).fetchall()

    if not rows:
        await c.message.edit_text(f"<b>{employee}</b> бўйича шикоят йўқ.", reply_markup=panel_kb())
        await c.answer()
        return

    # 1-чи (энг охиргиси)
    pos = 1
    total = len(rows)
    row = rows[pos - 1]
    await c.message.edit_text(
        format_admin_card(row) + f"\n\n({pos}/{total})",
        reply_markup=nav_kb(employee, pos, total, row["id"])
    )
    await c.answer()

@rt.callback_query(F.data.startswith("nav:"))
async def admin_nav(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return

    _, direction, employee, pos_s = c.data.split(":", 3)
    pos = int(pos_s)

    with db() as con:
        rows = con.execute(
            "SELECT * FROM complaints WHERE employee=? ORDER BY id DESC",
            (employee,)
        ).fetchall()

    total = len(rows)
    if total == 0:
        await c.message.edit_text("Шикоятлар топилмади.", reply_markup=panel_kb())
        await c.answer()
        return

    if direction == "prev":
        pos = max(1, pos - 1)
    elif direction == "next":
        pos = min(total, pos + 1)

    row = rows[pos - 1]
    await c.message.edit_text(
        format_admin_card(row) + f"\n\n({pos}/{total})",
        reply_markup=nav_kb(employee, pos, total, row["id"])
    )
    await c.answer()

@rt.message(Command("stats"))
async def cmd_stats(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer(await build_stats_text())

@rt.callback_query(F.data == "panel:stats")
async def panel_stats(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return
    await c.message.edit_text(await build_stats_text(), reply_markup=panel_kb())
    await c.answer()

async def build_stats_text() -> str:
    with db() as con:
        total = con.execute("SELECT COUNT(*) AS n FROM complaints").fetchone()["n"]
        new = con.execute("SELECT COUNT(*) AS n FROM complaints WHERE status='new'").fetchone()["n"]
        done = con.execute("SELECT COUNT(*) AS n FROM complaints WHERE status='done'").fetchone()["n"]
        rej = con.execute("SELECT COUNT(*) AS n FROM complaints WHERE status='rejected'").fetchone()["n"]

    return (
        "<b>📊 Статистика</b>\n"
        f"Жами: <b>{total}</b>\n"
        f"Янги: <b>{new}</b>\n"
        f"Ёпилган: <b>{done}</b>\n"
        f"Рад этилган: <b>{rej}</b>\n"
    )

@rt.callback_query(F.data.startswith("act:"))
async def admin_action(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return

    _, action, cid_s = c.data.split(":")
    cid = int(cid_s)

    with db() as con:
        row = con.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
        if not row:
            await c.answer("Топилмади", show_alert=True)
            return

        if action == "done":
            con.execute(
                "UPDATE complaints SET status='done', closed_at=?, admin_action_by=? WHERE id=?",
                (now_str(), c.from_user.id, cid)
            )
            await c.answer("✅ Ёпилди", show_alert=True)
            await c.message.edit_text(format_admin_card(row) + "\n\n✅ <b>Ёпилди (DONE)</b>")
            return

        if action == "rej":
            con.execute(
                "UPDATE complaints SET status='rejected', closed_at=?, admin_action_by=? WHERE id=?",
                (now_str(), c.from_user.id, cid)
            )
            # шикоят ёзган одамга психологик хабар
            try:
                await bot.send_message(row["from_user_id"], psych_reject_text())
            except Exception as e:
                log.warning("reject notify user failed: %s", e)

            await c.answer("❌ Рад этилди", show_alert=True)
            await c.message.edit_text(format_admin_card(row) + "\n\n❌ <b>Рад этилди (REJECT)</b>")
            return

    await c.answer("OK")

# ===================== BRON RESET =====================
@rt.message(Command("bron"))
async def cmd_bron(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "🧹 <b>BRON тозалаш</b>\n\n"
        "Барча шикоятларни 0 дан бошлаш ва ботни қайта ишга тушириш учун:\n"
        f"<code>/reset {RESET_CODE}</code>\n\n"
        "⚠️ Бу фақат админда ишлайди."
    )

@rt.callback_query(F.data == "panel:reset_info")
async def panel_reset_info(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer("No access", show_alert=True)
        return
    await c.message.edit_text(
        "🧹 <b>База тозалаш + рестарт</b>\n\n"
        "Ишлатиш:\n"
        f"<code>/reset {RESET_CODE}</code>\n\n"
        "⚠️ Барча маълумот ўчади ва Railway ботни қайта ишга туширади.",
        reply_markup=panel_kb()
    )
    await c.answer()

@rt.message(Command("reset"))
async def cmd_reset(m: Message):
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or parts[1].strip() != RESET_CODE:
        await m.answer("❌ BRON код нотўғри.")
        return

    # DB delete
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        init_db()
    except Exception as e:
        await m.answer(f"❌ DB ўчиришда хато: {e}")
        return

    await m.answer("✅ База тозаланди. Бот ҳозир қайта ишга тушади.")
    # Railway рестарт қилиши учун процессни чиқарамиз
    await asyncio.sleep(0.8)
    raise SystemExit("BRON reset triggered")


# ===================== SCHEDULED ALERTS =====================
async def alert_0730():
    # 07:30 текширув (сен айтган “бот 100% текшириб туриш”)
    msg = "✅ Bot online (07:30 текширув)"
    await notify_admins(msg)
    if TEST_MODE:
        await notify_admins("🧪 TEST_MODE=1: 07:30 тест сигнали")

async def alert_1930():
    msg = "✅ Bot online (19:30 текширув)"
    await notify_admins(msg)
    if TEST_MODE:
        await notify_admins("🧪 TEST_MODE=1: 19:30 тест сигнали")

def setup_scheduler():
    scheduler.add_job(alert_0730, "cron", hour=7, minute=30)
    scheduler.add_job(alert_1930, "cron", hour=19, minute=30)


# ===================== MAIN =====================
async def main():
    init_db()
    await set_commands()
    setup_scheduler()
    scheduler.start()

    log.info("Bot started. TEST_MODE=%s", TEST_MODE)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
