import os
import re
import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from yordamchi_push import push_to_yordamchi_hub_background


# ===================== CONFIG (Railway env) =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8381505129:AAG0X7jwRHUScfwFrsxi5C5QTwGuwfn3RIE").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "-1001877019294").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "1432810519").strip()
TEST_MODE = os.getenv("TEST_MODE", "0").strip() == "1"
RESET_CODE = os.getenv("RESET_CODE", "BRON-2026-RESET").strip()
DB_PATH = os.getenv("DB_PATH", "/data/complaints.sqlite3").strip() or "/data/complaints.sqlite3"
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
TZ_NAME = os.getenv("TZ", "Asia/Tashkent").strip()

TZ = ZoneInfo(TZ_NAME)

# Ходимлар: 5 та (сен айтганингдек). Истасанг env орқали ҳам берса бўлади.
# Формат: EMPLOYEES="Сагдуллаев Юнус;Самадов Тулкин;Тохиров Муслимбек;Шерназаров Толиб;Рахаббоев Пулат"
EMPLOYEES_ENV = os.getenv("EMPLOYEES", "").strip()
if EMPLOYEES_ENV:
    EMPLOYEES = [x.strip() for x in EMPLOYEES_ENV.split(";") if x.strip()]
else:
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
    out = set()
    for part in re.split(r"[,\s]+", raw.strip()):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out

ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set Railway variable BOT_TOKEN.")
if not GROUP_ID_RAW:
    raise RuntimeError("GROUP_ID is empty. Set Railway variable GROUP_ID (e.g. -100123...).")

try:
    GROUP_ID = int(GROUP_ID_RAW)
except ValueError:
    raise RuntimeError("GROUP_ID must be integer (e.g. -1001877019294).")

if not ADMIN_IDS:
    # сен биринчиси бўлиб қолсин деб, мажбурий қиляпман:
    raise RuntimeError("ADMIN_IDS is empty. Set Railway variable ADMIN_IDS (your Telegram numeric id).")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nazorat-bot")


# ===================== DB =====================
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL,
            from_user_id INTEGER NOT NULL,
            from_user_name TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'NEW',  -- NEW / DONE / REJECT
            decided_by INTEGER,
            decided_at TEXT,
            decision_note TEXT,
            group_chat_id INTEGER,
            group_message_id INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_complaints_employee ON complaints(employee)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status)")
    con.commit()
    con.close()

def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def short_now() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M")

def add_complaint(employee: str, from_user_id: int, from_user_name: str, text: str) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO complaints(employee, from_user_id, from_user_name, text, created_at, status)
        VALUES(?,?,?,?,?, 'NEW')
    """, (employee, from_user_id, from_user_name, text, now_str()))
    cid = cur.lastrowid
    con.commit()
    con.close()
    return int(cid)

def set_group_message(cid: int, chat_id: int, msg_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE complaints
        SET group_chat_id=?, group_message_id=?
        WHERE id=?
    """, (chat_id, msg_id, cid))
    con.commit()
    con.close()

def get_complaint(cid: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM complaints WHERE id=?", (cid,))
    row = cur.fetchone()
    con.close()
    return row

def update_status(cid: int, status: str, decided_by: int, note: str = ""):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE complaints
        SET status=?, decided_by=?, decided_at=?, decision_note=?
        WHERE id=?
    """, (status, decided_by, now_str(), note, cid))
    con.commit()
    con.close()

def stats():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM complaints")
    total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='NEW'")
    new = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='DONE'")
    done = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM complaints WHERE status='REJECT'")
    rej = cur.fetchone()["c"]
    con.close()
    return total, new, done, rej

def list_by_employee(employee: str, status: str | None = None, limit: int = 10, offset: int = 0):
    con = db()
    cur = con.cursor()
    if status:
        cur.execute("""
            SELECT * FROM complaints
            WHERE employee=? AND status=?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (employee, status, limit, offset))
    else:
        cur.execute("""
            SELECT * FROM complaints
            WHERE employee=?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (employee, limit, offset))
    rows = cur.fetchall()
    con.close()
    return rows

def count_by_employee(employee: str, status: str | None = None) -> int:
    con = db()
    cur = con.cursor()
    if status:
        cur.execute("SELECT COUNT(*) AS c FROM complaints WHERE employee=? AND status=?", (employee, status))
    else:
        cur.execute("SELECT COUNT(*) AS c FROM complaints WHERE employee=?", (employee,))
    c = cur.fetchone()["c"]
    con.close()
    return int(c)

def reset_all():
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM complaints")
    cur.execute("DELETE FROM sqlite_sequence WHERE name='complaints'")
    con.commit()
    con.close()


# ===================== UI helpers =====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def fmt_user_name(m: Message) -> str:
    # Ism фамилия бўлса — шуни оламиз
    name = (m.from_user.full_name or "").strip() if m.from_user else ""
    if not name:
        name = "Unknown"
    return name

def admin_card(row) -> str:
    # GROUP message format
    # Сўзларни сен айтганингдек:
    return (
        "📌 <b>Янги шикоят</b>\n"
        f"ID: <b>{row['id']}</b>\n"
        f"Ходим: <b>{row['employee']}</b>\n"
        f"Кимдан: <b>{row['from_user_name']}</b> | <code>{row['from_user_id']}</code>\n"
        f"Вақт: <b>{datetime.fromisoformat(row['created_at']).astimezone(TZ).strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"<b>Шикоят мазмуни:</b>\n{escape_html(row['text'])}"
    )

def escape_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def kb_employee_select():
    kb = InlineKeyboardBuilder()
    for i, emp in enumerate(EMPLOYEES):
        kb.button(text=emp, callback_data=f"emp:{i}")
    kb.adjust(1)
    return kb.as_markup()

def kb_admin_actions(cid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Бартараф этилди", callback_data=f"done:{cid}")
    kb.button(text="❌ Рад этилди", callback_data=f"reject:{cid}")
    kb.adjust(2)
    return kb.as_markup()

def kb_admin_panel_employees():
    kb = InlineKeyboardBuilder()
    for i, emp in enumerate(EMPLOYEES):
        kb.button(text=f"📂 {emp}", callback_data=f"panel_emp:{i}:0")
    kb.adjust(1)
    return kb.as_markup()

def kb_panel_pager(emp_index: int, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="⬅️ Олдинги", callback_data=f"panel_emp:{emp_index}:{page-1}")
    if page < total_pages - 1:
        kb.button(text="Кейинги ➡️", callback_data=f"panel_emp:{emp_index}:{page+1}")
    kb.button(text="🔙 Орқага", callback_data="panel_back")
    kb.adjust(2, 1)
    return kb.as_markup()


# ===================== Runtime state (simple) =====================
@dataclass
class Draft:
    employee: str

DRAFTS: dict[int, Draft] = {}  # user_id -> Draft


# ===================== Bot setup =====================
rt = Router()

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
dp.include_router(rt)


# ===================== Commands =====================
@rt.message(Command("start"))
async def cmd_start(m: Message):
    # меню командаларини Telegram’га тўғри ўтказиш учун
    text = (
        "Ассалому алайкум! 👋\n\n"
        "Бу <b>Ishxona Nazorat Bot</b>.\n"
        "Шикоят қолдириш учун аввал ходимни танланг, кейин матн ёзинг.\n\n"
        "📌 Командалар:\n"
        "• /panel — админ панель\n"
        "• /stats — статистика\n"
        "• /reset CODE — тозалаш (фақат админ)\n\n"
        "Энг аввало ходимни танлаймиз 👇"
    )
    await m.answer(text, reply_markup=kb_employee_select())

@rt.message(Command("panel"))
async def cmd_panel(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Бу бўлим фақат раҳбарият учун.")
    await m.answer("📌 <b>Админ панель</b>\nҚайси ходим бўйича шикоятларни кўрамиз?", reply_markup=kb_admin_panel_employees())

@rt.message(Command("admin"))
async def cmd_admin_alias(m: Message):
    # сен кўп ёзган /admin учун alias
    await cmd_panel(m)

@rt.message(Command("stats"))
async def cmd_stats(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Бу бўлим фақат раҳбарият учун.")
    total, new, done, rej = stats()
    await m.answer(
        "📊 <b>Статистика</b>\n"
        f"Жами: <b>{total}</b>\n"
        f"Янги: <b>{new}</b>\n"
        f"Бартараф этилди: <b>{done}</b>\n"
        f"Рад этилди: <b>{rej}</b>\n"
        f"\nТест режим: <b>{'ON' if TEST_MODE else 'OFF'}</b>"
    )

@rt.message(Command("reset"))
async def cmd_reset(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Бу бўлим фақат раҳбарият учун.")
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Формат: <code>/reset BRON-CODE</code>")
    code = parts[1].strip()
    if code != RESET_CODE:
        return await m.answer("❌ Код нотўғри. (Reset рад этилди)")
    reset_all()
    await m.answer("✅ База тозаланди. Энди ҳаммаси 0 дан бошланади.")


# ===================== Callbacks: employee choose =====================
@rt.callback_query(F.data.startswith("emp:"))
async def cb_emp(c: CallbackQuery):
    idx = int(c.data.split(":")[1])
    employee = EMPLOYEES[idx]
    DRAFTS[c.from_user.id] = Draft(employee=employee)
    await c.message.answer(
        f"✅ Танланди: <b>{employee}</b>\n\n"
        "Энди шикоят матнини ёзинг.\n"
        "Масалан: <i>\"0015-ракамли перемещения (11-позиция хато уткизилган!)\"</i>"
    )
    await c.answer()

# ===================== Receive complaint text =====================
@rt.message(F.text, F.chat.type == "private")
async def any_text(m: Message):
    if not m.from_user:
        return
    d = DRAFTS.get(m.from_user.id)
    if not d:
        return await m.answer("Ходимни танланг 👇", reply_markup=kb_employee_select())

    text = (m.text or "").strip()
    if len(text) < 3:
        return await m.answer("Матн жуда қисқа. Илтимос, аниқроқ ёзинг.")

    from_name = fmt_user_name(m)
    cid = add_complaint(d.employee, m.from_user.id, from_name, text)

    row = get_complaint(cid)
    msg = await bot.send_message(
        chat_id=GROUP_ID,
        text=admin_card(row),
        reply_markup=kb_admin_actions(cid),
    )
    set_group_message(cid, GROUP_ID, msg.message_id)

    preview = text[:140].replace("\n", " ")
    push_to_yordamchi_hub_background(
        tg_id=m.from_user.id,
        bot_key="ishxona",
        summary=f"Shikoyat ({d.employee}): {preview}",
    )

    await m.answer("✅ Қабул қилинди. Раҳбарият кўриб чиқади.")
    DRAFTS.pop(m.from_user.id, None)

# ===================== Admin actions: DONE / REJECT =====================
async def notify_user_reject(user_id: int):
    # Психологик юмшоқ, қисқа
    text = (
        "✅ Мурожаатингиз кўриб чиқилди.\n"
        "Ҳозирча бу масала бўйича қўшимча далил/аниқлик керак бўлди, шу сабаб рад этилди.\n"
        "Истасангиз, фактлар/расм/скрин билан қайта юборинг — албатта кўриб чиқилади."
    )
    try:
        await bot.send_message(user_id, text)
    except Exception:
        pass

@rt.callback_query(F.data.startswith("done:"))
async def cb_done(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Рухсат йўқ", show_alert=True)
    cid = int(c.data.split(":")[1])
    row = get_complaint(cid)
    if not row:
        return await c.answer("Топилмади", show_alert=True)

    if row["status"] != "NEW":
        return await c.answer("Аллақачон қарор қилинган", show_alert=True)

    update_status(cid, "DONE", c.from_user.id, "")
    row2 = get_complaint(cid)

    # group message edit
    try:
        await c.message.edit_text(admin_card(row2) + "\n\n✅ <b>Бартараф этилди</b>")
    except Exception:
        pass

    await c.answer("OK ✅")

@rt.callback_query(F.data.startswith("reject:"))
async def cb_reject(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Рухсат йўқ", show_alert=True)
    cid = int(c.data.split(":")[1])
    row = get_complaint(cid)
    if not row:
        return await c.answer("Топилмади", show_alert=True)

    if row["status"] != "NEW":
        return await c.answer("Аллақачон қарор қилинган", show_alert=True)

    update_status(cid, "REJECT", c.from_user.id, "")
    row2 = get_complaint(cid)

    # group message edit
    try:
        await c.message.edit_text(admin_card(row2) + "\n\n❌ <b>Рад этилди</b>")
    except Exception:
        pass

    # notify user softly
    await notify_user_reject(int(row2["from_user_id"]))
    await c.answer("OK ❌")


# ===================== Admin panel callbacks =====================
@rt.callback_query(F.data == "panel_back")
async def cb_panel_back(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Рухсат йўқ", show_alert=True)
    await c.message.edit_text("📌 <b>Админ панель</b>\nҚайси ходим бўйича шикоятларни кўрамиз?", reply_markup=kb_admin_panel_employees())
    await c.answer()

@rt.callback_query(F.data.startswith("panel_emp:"))
async def cb_panel_emp(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Рухсат йўқ", show_alert=True)

    _, idx_s, page_s = c.data.split(":")
    emp_index = int(idx_s)
    page = int(page_s)
    employee = EMPLOYEES[emp_index]

    per_page = 5
    total = count_by_employee(employee)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    rows = list_by_employee(employee, status=None, limit=per_page, offset=page * per_page)

    lines = [f"📂 <b>{employee}</b>\nЖами шикоят: <b>{total}</b>\nСаҳифа: <b>{page+1}/{total_pages}</b>\n"]
    if not rows:
        lines.append("Ҳали шикоят йўқ.")
    else:
        for r in rows:
            status = r["status"]
            st = "🆕 NEW" if status == "NEW" else ("✅ DONE" if status == "DONE" else "❌ REJECT")
            created = datetime.fromisoformat(r["created_at"]).astimezone(TZ).strftime("%d.%m %H:%M")
            # 1 қаторасига қисқартириб:
            preview = (r["text"] or "").strip().replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "…"
            lines.append(
                f"\n<b>ID {r['id']}</b> | {st} | <i>{created}</i>\n"
                f"Кимдан: <code>{r['from_user_id']}</code>\n"
                f"Мазмун: {escape_html(preview)}"
            )

    await c.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_panel_pager(emp_index, page, total_pages),
    )
    await c.answer()


# ===================== Scheduler: 2 soat + alertlar =====================
async def heartbeat():
    # Тест учун: админга “бот тирик” деган хабар (TEST_MODE=1 бўлса)
    if not TEST_MODE:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"🟢 Bot online (heartbeat) — {short_now()}")
        except Exception:
            pass

def setup_scheduler():
    sch = AsyncIOScheduler(timezone=TZ)
    # сен айтган “иккала соат” — 07:30 ва 19:30 (TEST_MODE да ишлатиш учун)
    sch.add_job(lambda: asyncio.create_task(heartbeat()), "cron", hour=7, minute=30)
    sch.add_job(lambda: asyncio.create_task(heartbeat()), "cron", hour=19, minute=30)
    sch.start()


# ===================== Commands menu =====================
async def set_commands():
    from aiogram.types import BotCommand
    cmds = [
        BotCommand(command="start", description="Ботни ишга тушириш / ходим танлаш"),
        BotCommand(command="panel", description="Админ панель (ходимлар бўйича)"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="reset", description="Тозалаш (фақат админ)"),
        BotCommand(command="whoami", description="ID ва admin текшириш"),
        BotCommand(command="factory_reset", description="Тўлиқ reset + restart (фақат админ)"),
    ]
    try:
        await bot.set_my_commands(cmds)
    except Exception as e:
        log.warning("set_my_commands failed: %r", e)


# ===================== /whoami =====================
@rt.message(Command("whoami"))
async def cmd_whoami(m: Message):
    if not m.from_user:
        return
    await m.answer(
        "🪪 <b>Sizning ma'lumotlaringiz</b>\n"
        f"ID: <code>{m.from_user.id}</code>\n"
        f"Ism: <b>{escape_html(m.from_user.full_name or 'Unknown')}</b>\n"
        f"Admin: <b>{'YES' if is_admin(m.from_user.id) else 'NO'}</b>\n"
        f"ADMIN_IDS: <code>{', '.join(str(x) for x in sorted(ADMIN_IDS))}</code>"
    )


# ===================== Factory reset helpers =====================
FACTORY_RESET_CODE = os.getenv("FACTORY_RESET_CODE", "").strip()

def _safe_remove_db_files(db_path: str) -> int:
    removed = 0
    if not db_path:
        return 0
    for p in (db_path, db_path + "-wal", db_path + "-shm"):
        try:
            if os.path.exists(p):
                os.remove(p)
                removed += 1
        except Exception as e:
            log.warning("DB file remove failed for %s: %s", p, e)
    return removed


# ===================== /factory_reset =====================
@rt.message(Command("factory_reset"))
async def cmd_factory_reset(m: Message):
    if not m.from_user or not is_admin(m.from_user.id):
        return

    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Формат: <code>/factory_reset FACTORY_RESET_CODE</code>")

    code = parts[1].strip()

    if not FACTORY_RESET_CODE:
        return await m.answer("❗ Railway Variables'да <b>FACTORY_RESET_CODE</b> қўйилмаган.")

    if code != FACTORY_RESET_CODE:
        return await m.answer("❌ Код нотўғри.")

    removed = _safe_remove_db_files(DB_PATH)

    await m.answer(
        "✅ <b>Factory Reset</b> бажарилди.\n"
        f"🗑 Ўчирилди: <b>{removed}</b> та DB файл.\n"
        "♻️ Бот қайта ишга тушяпти..."
    )

    await asyncio.sleep(0.6)
    raise SystemExit("FACTORY_RESET triggered")


# ===================== Main =====================
async def main():
    init_db()
    setup_scheduler()
    await set_commands()
    log.info("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
