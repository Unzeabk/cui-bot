"""Отправка поста в канал через Bot API."""

from pathlib import Path

import requests


def send_photo(token: str, chat_id: str, photo: Path, caption_html: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML"},
            files={"photo": (photo.name, f, "image/png")},
            timeout=60,
        )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram отказал: {data.get('description')}")
    return data["result"]
