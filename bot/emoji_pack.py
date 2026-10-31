"""Создаёт набор своих эмодзи Cui Bono? одним запросом Bot API.

Набор принадлежит пользователю TG_USER_ID (он должен один раз нажать /start у бота).
Имя набора: cuibono_marks_by_<имя_бота>.
"""

import json
import os
from pathlib import Path

import requests

from .main import log  # noqa: F401  (подтягивает CUI_CONFIG в окружение)

ROOT = Path(__file__).resolve().parent.parent
EMOJI = {
    "russia": "🇷🇺", "regions": "🗺", "budget": "💰", "property": "🔑", "finance": "💱",
    "economy": "📈", "business": "💼", "sanctions": "🚫", "labour": "🛠", "postsoviet": "🧭",
    "china": "🇨🇳", "newmarkets": "🌍", "europe": "🇪🇺", "usa": "🇺🇸", "world": "🌐",
    "abroad": "✈️", "tech": "🔬", "position": "🔴", "analysis": "🔍", "week": "📅", "indicators": "📊",
}


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    user_id = int(os.environ["TG_USER_ID"])
    api = f"https://api.telegram.org/bot{token}"
    me = requests.get(f"{api}/getMe", timeout=30).json()["result"]["username"]
    name = f"cuibono_marks_by_{me}"

    files, stickers = {}, []
    for key, emoji in EMOJI.items():
        files[key] = (f"{key}.png", open(ROOT / "assets" / "emoji" / f"{key}.png", "rb"), "image/png")
        stickers.append({"sticker": f"attach://{key}", "format": "static", "emoji_list": [emoji]})

    r = requests.post(
        f"{api}/createNewStickerSet",
        data={"user_id": user_id, "name": name, "title": "Cui Bono?", "sticker_type": "custom_emoji",
              "stickers": json.dumps(stickers, ensure_ascii=False)},
        files=files, timeout=120,
    ).json()
    if not r.get("ok"):
        raise SystemExit(f"Telegram отказал: {r.get('description')}")
    print(f"Готово: https://t.me/addemoji/{name}")

    s = requests.get(f"{api}/getStickerSet", params={"name": name}, timeout=30).json()["result"]["stickers"]
    ids = {k: st["custom_emoji_id"] for k, st in zip(EMOJI, s)}
    print("ID знаков для CUSTOM_EMOJI_JSON:")
    print(json.dumps(ids, ensure_ascii=False))


if __name__ == "__main__":
    main()
