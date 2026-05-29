"""Admin: hub test."""

from __future__ import annotations

import os
import re

from aiogram.types import Message

from yordamchi_push import push_to_yordamchi_hub

BTN_HUB_TEST = "🧪 Test (admin)"


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "1432810519").strip()
    out: set[int] = set()
    for part in re.split(r"[,\s]+", raw):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


async def handle_admin_hub_test(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        return await message.answer("Faqat admin uchun.")

    ok, via = await push_to_yordamchi_hub(
        tg_id=uid,
        bot_key="ishxona",
        summary="[TEST] Shikoyat (test): namuna matn",
    )
    await message.answer(
        f"{'✅' if ok else '❌'} Ishxona → yordamchi hub ({via})\n"
        "Endi davlat-yordamchi botda ✅ Якунлаш yuboring."
    )
