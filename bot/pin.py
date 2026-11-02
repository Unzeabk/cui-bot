"""Закреплённый пост о письме по субботам: обложка в стиле издания, подпись, кнопка, закрепление.

Один раз: state/pinned.json хранит id поста, повторный запуск ничего не делает.
Запуск: python -m bot.pin (workflow «закреп»).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

from . import brand as B
from .main import STATE, log  # noqa: F401  (main подхватывает CUI_CONFIG)

ENVELOPE = ('<svg viewBox="0 0 46 46" fill="none" stroke="#C9CFD6" stroke-width="2"><rect x="5" y="11" width="36" height="24"/>'
            '<path d="M5 11 23 25 41 11"/></svg>')

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.mid{flex:1;display:flex;align-items:center;justify-content:space-between;gap:40px}
.txt{display:flex;flex-direction:column;gap:26px;max-width:700px}
.days{display:grid;grid-template-columns:repeat(7,52px);gap:8px;font-family:'Plex',monospace;font-size:16px;color:#6B6F72;letter-spacing:.08em;align-self:center}
.days div{height:120px;border:1.5px solid #262B31;display:flex;align-items:flex-end;justify-content:center;padding-bottom:10px}
.days .on{border-color:#EDE9E2;color:#EDE9E2;background:#15191e}
</style></head><body><div class="root">__MAP__
<div class="top"><span class="logo">Cui Bono?</span><span class="tag">Письмо по субботам</span></div>
<div class="rule"></div>
<div class="mid"><div class="txt"><div class="hl" style="font-size:104px">Неделя в одном письме</div><div class="fly">Разборы, позиция, цифры</div></div>
<div class="days">__DAYS__</div></div>
<div class="foot"><span>cuibono.press/subscribe</span>__MARK__</div>
</div></body></html>"""

VERSION = 2  # поменять текст или обложку → поднять номер: старый закреп заменится

CAPTION = ("<code>ПИСЬМО</code> · <i>По субботам</i>\n\n"
           "<b>Неделя в одном письме</b>\n\n"
           "Разборы, позиция редакции и цифры недели. Приходит в субботу днём.\n\n"
           '<a href="https://cuibono.press/subscribe">cuibono.press/subscribe</a>')


def main() -> int:
    f = Path("state") / "pinned.json"
    old = json.loads(f.read_text()) if f.exists() else {}
    if old.get("version") == VERSION:
        log("закреп: уже есть")
        return 0
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"]
    days = "".join(f'<div class="{"on" if d == "СБ" else ""}">{d}</div>' for d in ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"))
    page = (PAGE.replace("__CSS__", B.BASE_CSS).replace("__MAP__", B.map_layer(None)).replace("__DAYS__", days)
            .replace("__MARK__", ENVELOPE))
    png = B.shoot(page, Path("out") / "pin-subscribe.png")
    if os.environ.get("DRY_RUN"):
        log(f"закреп: обложка {png}, не публикую")
        return 0
    from .telegram import send_photo
    msg = send_photo(token, chat, png, CAPTION, ("Подписаться", "https://cuibono.press/subscribe"))
    r = requests.post(f"https://api.telegram.org/bot{token}/pinChatMessage", timeout=30,
                      data={"chat_id": chat, "message_id": msg["message_id"], "disable_notification": True}).json()
    log(f"закреп: пост {msg['message_id']}, закрепление {r}")
    if old.get("subscribe"):  # прошлый закреп убрать
        d = requests.post(f"https://api.telegram.org/bot{token}/deleteMessage", timeout=30,
                          data={"chat_id": chat, "message_id": old["subscribe"]}).json()
        log(f"закреп: старый пост {old['subscribe']} удалён — {d}")
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({"subscribe": msg["message_id"], "version": VERSION}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
