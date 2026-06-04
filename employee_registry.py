"""Barcha botlar uchun yagona xodim → Telegram ID (Tuvalov Farrux / Pulat migratsiya)."""

from __future__ import annotations

import re

TUVALOV_FARRUX_TG_ID = 8472656729
CANONICAL_TUVALOV = "Tuvalov Farrux"

PULAT_LEGACY_NAMES: frozenset[str] = frozenset(
    {
        "rajabboev pulat",
        "rahabboev pulat",
        "ражаббоев пулат",
        "рахаббоев пулат",
    }
)

TUVALOV_NAME_KEYS: frozenset[str] = frozenset(
    {
        "tuvalov farrux",
        "тувалов фаррух",
        "фаррух",
    }
)

TG_EMPLOYEE: dict[int, str] = {
    924612402: "Yadullaev Umid",
    5412958249: "Ravshanov Oxunjon",
    8547365654: "Ruziboev Sindor",
    6931958983: "Mustafoev Abdullo",
    6991673998: "Sagdullaev Yunus",
    5465963344: "Shernazarov Tolib",
    6001619806: "Samadov Tulqin",
    5732350707: "Toxirov Muslimbek",
    8440127425: "Ravshanov Ziyodullo",
    TUVALOV_FARRUX_TG_ID: CANONICAL_TUVALOV,
}

EMPLOYEE_NAME_ALIASES: dict[str, int] = {
    "Yadullaev Umidjon": 924612402,
    "Yadullaev Umid": 924612402,
    "Samadov To'lqin": 6001619806,
    "Samadov Tulqin": 6001619806,
    "Ravshanov Oxunjon": 5412958249,
    "Oxunjon": 5412958249,
    "Охунжон": 5412958249,
    "Ravshanov Ziyodullo": 8440127425,
    "Ravshanov_Z_": 8440127425,
    "Mustafoev Abdullo": 6931958983,
    "Abdullo Mustafoyev": 6931958983,
    "Ruziboev Sindor": 8547365654,
    "Ruziboev sindorbek": 8547365654,
    "Toxirov Muslimbek": 5732350707,
    "Тохиров Муслимбек": 5732350707,
    "Shernazarov Tolib": 5465963344,
    "Толиб Шерназаров": 5465963344,
    "Sagdullaev Yunus": 6991673998,
    "Sagdullaev": 6991673998,
    CANONICAL_TUVALOV: TUVALOV_FARRUX_TG_ID,
    "Тувалов Фаррух": TUVALOV_FARRUX_TG_ID,
    "Тувалов Farrux": TUVALOV_FARRUX_TG_ID,
    "Rajabboev Pulat": TUVALOV_FARRUX_TG_ID,
    "Rahabboev Pulat": TUVALOV_FARRUX_TG_ID,
    "Ражаббоев Пулат": TUVALOV_FARRUX_TG_ID,
    "Рахаббоев Пулат": TUVALOV_FARRUX_TG_ID,
}

# Guruh kartalari (qisqa ismlar)
SHORT_NAME_ALIASES: dict[str, str] = {
    "охунжон": "Ravshanov Oxunjon",
    "oxunjon": "Ravshanov Oxunjon",
    "ravshanov oxunjon": "Ravshanov Oxunjon",
    "ravshanov_z_": "Ravshanov Ziyodullo",
    "ravshanov z": "Ravshanov Ziyodullo",
    "ziyodullo": "Ravshanov Ziyodullo",
    "abdullo mustafoyev": "Mustafoev Abdullo",
    "mustafoyev abdullo": "Mustafoev Abdullo",
    "mustafoev abdullo": "Mustafoev Abdullo",
    "ruziboev sindorbek": "Ruziboev Sindor",
    "sindorbek": "Ruziboev Sindor",
    "тохиров муслимбек": "Toxirov Muslimbek",
    "toxirov muslimbek": "Toxirov Muslimbek",
    "толиб шерназаров": "Shernazarov Tolib",
    "shernazarov tolib": "Shernazarov Tolib",
    "толиб": "Shernazarov Tolib",
    "tolib": "Shernazarov Tolib",
    "samadov tolqin": "Samadov To'lqin",
    "samadov to'lqin": "Samadov To'lqin",
    "to'lqin": "Samadov To'lqin",
    "sagdullaev": "Sagdullaev Yunus",
    "yunus": "Sagdullaev Yunus",
    "tuvalov farrux": CANONICAL_TUVALOV,
    "farrux": CANONICAL_TUVALOV,
    "тувалов фаррух": CANONICAL_TUVALOV,
    "rajabboev pulat": CANONICAL_TUVALOV,
}

PULAT_DISPLAY_NAMES: tuple[str, ...] = (
    "Rajabboev Pulat",
    "Rahabboev Pulat",
    "Ражаббоев Пулат",
    "Рахаббоев Пулат",
)

TUVALOV_DISPLAY_NAMES: tuple[str, ...] = (
    CANONICAL_TUVALOV,
    "Тувалов Фаррух",
    "Тувалов Farrux",
)


def _alias_key(raw: str) -> str:
    s = (raw or "").strip().lower()
    for ch in ("õ", "ö", "ó", "ô", "'", "'", "`", "ʻ", "ʼ", "’"):
        s = s.replace(ch, "o" if ch in ("õ", "ö", "ó", "ô") else "")
    s = re.sub(r"[_]+", " ", s)
    return " ".join(s.split())


def is_pulat_legacy(name: str) -> bool:
    return _alias_key(name) in PULAT_LEGACY_NAMES


def is_tuvalov_name(name: str) -> bool:
    key = _alias_key(name)
    return key in TUVALOV_NAME_KEYS or name.strip() == CANONICAL_TUVALOV


def canonical_employee_name(name: str) -> str:
    """Pulat → Tuvalov; Farrux allaqachon bo'lsa o'zgartirmaydi."""
    raw = (name or "").strip()
    if not raw:
        return raw
    if is_pulat_legacy(raw) or is_tuvalov_name(raw):
        return CANONICAL_TUVALOV
    return raw


def all_team_tg_ids() -> frozenset[int]:
    return frozenset(TG_EMPLOYEE.keys())


def operator_display_name(tg_id: int) -> str:
    return TG_EMPLOYEE.get(int(tg_id), f"ID {tg_id}")


def resolve_employee_tg_id(name: str) -> int | None:
    """Ism → tg_id (alias + Pulat→Tuvalov)."""
    raw = (name or "").strip()
    if not raw:
        return None
    canon = canonical_employee_name(raw)
    if canon in EMPLOYEE_NAME_ALIASES:
        return int(EMPLOYEE_NAME_ALIASES[canon])
    key = _alias_key(raw)
    if key in SHORT_NAME_ALIASES:
        canon2 = SHORT_NAME_ALIASES[key]
        return int(EMPLOYEE_NAME_ALIASES.get(canon2, 0)) or None
    for alias, tid in EMPLOYEE_NAME_ALIASES.items():
        if _alias_key(alias) == key:
            return int(tid)
    for tid, emp in TG_EMPLOYEE.items():
        if _alias_key(emp) == _alias_key(canon):
            return int(tid)
    return None


def migrate_sqlite_employee_row(
    cursor,
    *,
    default_password: str | None = None,
    now_iso: str = "",
) -> str:
    """
    Sklad DB: Pulat nomi → Tuvalov Farrux; Farrux bo'lsa tegmaydi.
    Qaytaradi: 'renamed' | 'inserted' | 'deactivated_pulat' | 'ok'.
    """
    farrux_id = None
    for nm in TUVALOV_DISPLAY_NAMES:
        cursor.execute("SELECT id FROM employees WHERE name = ?", (nm,))
        row = cursor.fetchone()
        if row:
            farrux_id = int(row["id"])
            break

    pulat_id = None
    for nm in PULAT_DISPLAY_NAMES:
        cursor.execute("SELECT id FROM employees WHERE name = ?", (nm,))
        row = cursor.fetchone()
        if row:
            pulat_id = int(row["id"])
            break

    if pulat_id and not farrux_id:
        cursor.execute(
            "UPDATE employees SET name = ?, telegram_id = ? WHERE id = ?",
            (CANONICAL_TUVALOV, TUVALOV_FARRUX_TG_ID, pulat_id),
        )
        return "renamed"

    if pulat_id and farrux_id and pulat_id != farrux_id:
        cursor.execute("UPDATE employees SET is_active = 0 WHERE id = ?", (pulat_id,))
        cursor.execute(
            "UPDATE employees SET telegram_id = ? WHERE id = ?",
            (TUVALOV_FARRUX_TG_ID, farrux_id),
        )
        return "deactivated_pulat"

    if not farrux_id and default_password is not None:
        cursor.execute(
            """
            INSERT INTO employees (name, role, is_active, created_at, password, telegram_id)
            VALUES (?, 'employee', 1, ?, ?, ?)
            """,
            (CANONICAL_TUVALOV, now_iso, default_password, TUVALOV_FARRUX_TG_ID),
        )
        return "inserted"

    if farrux_id:
        cursor.execute(
            "UPDATE employees SET telegram_id = ? WHERE id = ?",
            (TUVALOV_FARRUX_TG_ID, farrux_id),
        )
    return "ok"


def build_employee_tg_ids_dict() -> dict[str, int]:
    """Ishxona va boshqa botlar uchun: ko'rsatish ismi → tg_id."""
    out: dict[str, int] = {}
    for display in TUVALOV_DISPLAY_NAMES + PULAT_DISPLAY_NAMES:
        out[display] = TUVALOV_FARRUX_TG_ID
    for alias, tid in EMPLOYEE_NAME_ALIASES.items():
        out[alias] = int(tid)
    for tid, emp in TG_EMPLOYEE.items():
        out[emp] = int(tid)
    return out
