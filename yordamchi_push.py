"""davlat-yordamchi hub ga event (ixtiyoriy env)."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import date

log = logging.getLogger(__name__)

HUB_URL = os.getenv("YORDAMCHI_HUB_URL", "").strip().rstrip("/")
HUB_SECRET = os.getenv("YORDAMCHI_HUB_SECRET", "").strip()


def push_to_yordamchi_hub_background(
    *, tg_id: int, bot_key: str, summary: str, day_iso: str | None = None
) -> None:
    text = " ".join(str(summary or "").split())
    if not text or not tg_id or not HUB_URL or not HUB_SECRET:
        return
    payload = {
        "tg_id": int(tg_id),
        "bot_key": bot_key,
        "summary": text[:420],
        "day": day_iso or date.today().isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/ingest",
        data=body,
        headers={"Content-Type": "application/json", "X-Hub-Secret": HUB_SECRET},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        log.warning("Hub ingest failed: %s", e)
