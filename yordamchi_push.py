"""Boshqa botlardan hub ga event (HTTP yoki Telegram)."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

HUB_URL = os.getenv("YORDAMCHI_HUB_URL", "").strip().rstrip("/")
HUB_SECRET = os.getenv("YORDAMCHI_HUB_SECRET", "").strip()
TG_BOT_TOKEN = os.getenv("YORDAMCHI_BOT_TOKEN", "").strip()
INGEST_CHAT_ID = int(os.getenv("YORDAMCHI_INGEST_CHAT_ID", "0") or "0")
TZ = ZoneInfo(os.getenv("TZ", "Asia/Tashkent"))


def today_iso() -> str:
    return datetime.now(TZ).date().isoformat()


def _post_http(payload: dict) -> bool:
    if not HUB_URL or not HUB_SECRET:
        return False
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/ingest",
        data=body,
        headers={"Content-Type": "application/json", "X-Hub-Secret": HUB_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log.warning("Hub HTTP ingest failed: %s", e)
        return False


def _post_telegram(day: str, tg_id: int, bot_key: str, summary: str) -> bool:
    if not TG_BOT_TOKEN or not INGEST_CHAT_ID:
        return False
    text = f"HUB|{day}|{tg_id}|{bot_key}|{summary[:400]}"
    body = json.dumps(
        {"chat_id": INGEST_CHAT_ID, "text": text, "disable_notification": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log.warning("Hub Telegram ingest failed: %s", e)
        return False


def push_to_yordamchi_hub_background(
    *, tg_id: int, bot_key: str, summary: str, day_iso: str | None = None
) -> None:
    text = " ".join(str(summary or "").split())
    if not text or not tg_id:
        return
    day = day_iso or today_iso()
    payload = {
        "tg_id": int(tg_id),
        "bot_key": str(bot_key or "").strip().lower(),
        "summary": text[:420],
        "day": day,
    }
    if not _post_http(payload):
        _post_telegram(day, int(tg_id), payload["bot_key"], payload["summary"])
