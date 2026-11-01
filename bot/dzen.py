"""Версия поста для Дзена.

Дзен забирает посты из Telegram-канала ботом @zen_sync_bot и строит из поста статью: заголовок — первое
предложение, обложка — первая картинка, жирный и курсив пропадают. Строка рубрики («ПОЗИЦИЯ · Мнение редакции»)
поэтому становится заголовком. Здесь пост собирается так, чтобы статья в Дзене выглядела как статья:
первым предложением идёт заголовок, дальше подзаголовок и текст, в конце — ссылка на полный текст на сайте
и на основной канал.

Пост уходит в отдельный публичный канал (DZEN_CHANNEL), к которому подключён Дзен. Основной канал не меняется.
Ошибка здесь не мешает основной публикации.
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime

from .compose import LINK_RE, plain, visible_length
from .main import TZ, log

LIMIT = 1000  # подпись к фото в Telegram — 1024 видимых знака


def _a(s: str) -> str:
    """[текст](url) → <a>; остальное экранируется. Ссылки Дзен переносит."""
    out, pos = [], 0
    for m in LINK_RE.finditer(s or ""):
        out.append(html.escape(s[pos:m.start()], quote=False))
        out.append(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1), quote=False)}</a>')
        pos = m.end()
    out.append(html.escape((s or "")[pos:], quote=False))
    return "".join(out)


def _sentence(t: str) -> str:
    t = plain(t or "").strip()
    return t if re.search(r"[.!?…]$", t) else t + "."


def page_url(kind: str, d: dict) -> str:
    site = (os.environ.get("SITE_URL") or "https://cuibono.press").rstrip("/")
    slug = re.sub(r"[^a-z0-9-]+", "-", (d.get("topic_key") or "").lower()).strip("-")[:70] or "post"
    return f"{site}/p/{datetime.now(TZ):%Y-%m-%d}-{slug}"


def caption(kind: str, d: dict, url: str = "") -> str:
    channel = (os.environ.get("TELEGRAM_CHANNEL_PUBLIC") or "cuibono_press").lstrip("@")
    url = url or page_url(kind, d)
    head = [html.escape(_sentence(d.get("headline", "")), quote=False)]
    if d.get("subhead"):
        head.append(html.escape(_sentence(d["subhead"]), quote=False))
    body = []
    if kind == "analysis" and d.get("lead"):
        body.append(_a(d["lead"]))
    body += [_a(p) for p in d.get("paragraphs") or []]
    if kind == "week":
        body = [f"{html.escape(i.get('subject', ''))}. {_a(i.get('text', ''))}" for i in d.get("items", [])]
    more = "Разбор целиком" if kind == "analysis" else "На сайте"
    tail = f'{more}: <a href="{url}">{url.split("://", 1)[1]}</a>\nTelegram: @{channel}'
    # абзацы добавляются, пока пост помещается в подпись
    parts = head[:]
    for b in body:
        if visible_length("\n\n".join(parts + [b, tail])) > LIMIT:
            break
        parts.append(b)
    if visible_length("\n\n".join(parts + [tail])) > LIMIT and len(parts) > 1:
        parts = parts[:1] + parts[2:]  # без подзаголовка
    return "\n\n".join(parts + [tail])


def publish(kind: str, d: dict, cover, url: str = "") -> None:
    chat = (os.environ.get("DZEN_CHANNEL") or "").strip()
    if not chat:
        return
    try:
        from .telegram import send_photo
        send_photo(os.environ["TELEGRAM_BOT_TOKEN"], chat, cover, caption(kind, d, url))
        log(f"Дзен: пост ушёл в {chat}")
    except Exception as e:  # noqa: BLE001
        log(f"Дзен: не отправлено ({e})")
