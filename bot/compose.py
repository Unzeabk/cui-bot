"""Проверка заметки и сборка подписи к посту в HTML-разметке Telegram."""

import html
import json
import re
from urllib.parse import unquote

from .rubrics import RUBRICS, FORMS

CAPTION_LIMIT = 1024  # лимит подписи к фото в Bot API
CAPTION_TARGET = 1000

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

BANNED = [
    (re.compile(r"это не просто", re.I), "«это не просто»"),
    (re.compile(r"важно понимать", re.I), "«важно понимать»"),
    (re.compile(r"принято считать", re.I), "«принято считать»"),
    (re.compile(r"стоит отметить", re.I), "«стоит отметить»"),
    (re.compile(r"\bулик", re.I), "детективная лексика: улика"),
    (re.compile(r"\bдосье", re.I), "детективная лексика: досье"),
    (re.compile(r"\bследстви[ея]\b", re.I), "детективная лексика: следствие"),
    (re.compile(r"\bдетектив", re.I), "детективная лексика: детектив"),
    (re.compile(r"\bбайбэк", re.I), "англицизм «байбэк»"),
    (re.compile(r"кастодиальн|\bp2p\b|\bпериметр|\bдрайвер|\bкейс", re.I), "профжаргон или англицизм"),
    # качели «не X, а Y»
    (re.compile(r"(?:^|[\s,.—–-])не\s+[^.!?;:]{1,60}?,\s+а\s+", re.I), "качели «не X, а Y»"),
]

REQUIRED = ["rubric", "fly", "headline", "subhead", "paragraphs", "form", "optics", "topic_key", "cover"]


def extract_json(text: str) -> dict:
    m = re.search(r"<json>\s*(\{.*\})\s*</json>", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def _norm(url: str) -> str:
    """Адрес для сравнения: без схемы, www, параметров и якоря, кириллица раскодирована.
    Модель часто пишет адрес с кириллицей, а лента отдаёт его закодированным (%D0%...) или с метками ?maca=."""
    u = unquote(url.strip()).split("#")[0].split("?")[0].rstrip("/").lower()
    return re.sub(r"^https?://(www\.)?", "", u)


def banned_hits(text: str) -> list[str]:
    """Запрещённые обороты с найденным словом, чтобы модель знала, что именно убрать."""
    out = []
    for rx, label in BANNED:
        m = rx.search(text)
        if m:
            out.append(f"запрещённый оборот: {label} — «{m.group(0)}»")
    return out


def links_in(note: dict) -> list[str]:
    parts = [note.get("headline", ""), note.get("subhead", ""), note.get("final") or ""] + list(note.get("paragraphs", []))
    return [u for p in parts for _, u in LINK_RE.findall(p)]


def plain(s: str) -> str:
    """Видимый текст без markdown-ссылок."""
    return LINK_RE.sub(r"\1", s)


def _inline(s: str) -> str:
    """Экранирует текст и превращает [текст](url) в <a>."""
    out, pos = [], 0
    for m in LINK_RE.finditer(s):
        out.append(html.escape(s[pos:m.start()], quote=False))
        out.append(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1), quote=False)}</a>')
        pos = m.end()
    out.append(html.escape(s[pos:], quote=False))
    return "".join(out)


def build_caption(note: dict, custom_emoji: dict | None = None) -> str:
    name, mark = RUBRICS[note["rubric"]]
    emoji_id = (custom_emoji or {}).get(note["rubric"])
    prefix = f'<tg-emoji emoji-id="{emoji_id}">{mark}</tg-emoji> ' if emoji_id else ""
    fly = (note["fly"] or "").strip()
    head = f"{prefix}<code>{html.escape(name)}</code> · <i>{_inline(fly[:1].upper() + fly[1:])}</i>"
    paras = [_inline(p) for p in note["paragraphs"]]
    final = (note.get("final") or "").strip()
    if final:  # последняя фраза — продолжение последнего абзаца, без цитаты и без отдельной «подписи»
        paras[-1] = f"{paras[-1]} {_inline(final)}"
    body = "\n\n".join(paras)
    return (
        f"{head}\n\n"
        f"<b>{_inline(note['headline'])}</b>\n\n"
        f"<i>{_inline(note['subhead'])}</i>\n\n"
        f"{body}"
    )


def visible_length(caption_html: str) -> int:
    text = re.sub(r"<[^>]+>", "", caption_html)
    return len(html.unescape(text))


def fix_links(obj, allowed):
    """Чинит адреса, в которых модель ошиблась на символ (например, кириллическая «с» вместо латинской s):
    если ссылки нет среди материалов, но есть очень похожая, подставляет настоящую. Работает по всему JSON."""
    import difflib

    allowed = list(allowed)
    norm = {_norm(u): u for u in allowed}

    def one(m):
        text, url = m.group(1), m.group(2)
        if _norm(url) in norm:
            return m.group(0)
        best = difflib.get_close_matches(_norm(url), list(norm), n=1, cutoff=.88)
        return f"[{text}]({norm[best[0]]})" if best else m.group(0)

    if isinstance(obj, str):
        return LINK_RE.sub(one, obj)
    if isinstance(obj, list):
        return [fix_links(x, allowed) for x in obj]
    if isinstance(obj, dict):
        return {k: fix_links(v, allowed) for k, v in obj.items()}
    return obj


def dedupe_links(note: dict) -> dict:
    seen: set[str] = set()

    def fix(s: str) -> str:
        def repl(m):
            url = _norm(m.group(2))
            if url in seen:
                return m.group(1)
            seen.add(url)
            return m.group(0)
        return LINK_RE.sub(repl, s or "")

    for k in ("headline", "subhead"):
        note[k] = LINK_RE.sub(r"\1", note.get(k) or "")  # в заголовке ссылок не бывает
    note["paragraphs"] = [fix(p) for p in note.get("paragraphs", [])]
    if note.get("final"):
        note["final"] = fix(note["final"])
    return note


def validate(note: dict, seen_urls: set[str] | None) -> list[str]:
    """Возвращает список проблем. Пустой список — заметка проходит."""
    problems = []
    for k in REQUIRED:
        if k not in note or note[k] in (None, "", []):
            problems.append(f"нет поля {k}")
    if problems:
        return list(dict.fromkeys(problems))

    if note["rubric"] not in RUBRICS:
        problems.append(f"неизвестная рубрика {note['rubric']}")
    if note["form"] not in FORMS:
        problems.append(f"неизвестная форма заголовка {note['form']}")
    if not isinstance(note["paragraphs"], list) or not (1 <= len(note["paragraphs"]) <= 4):
        problems.append("абзацев должно быть 1–4")

    words = len(plain(note["headline"]).split())
    if not 5 <= words <= 9:
        problems.append(f"в заголовке {words} слов, нужно 6–9")

    text_all = " ".join([note["fly"], note["headline"], note["subhead"], note.get("final") or "", *note["paragraphs"]])
    problems += banned_hits(plain(text_all))

    links = links_in(note)
    if not links:
        problems.append("в тексте нет ни одной ссылки на источник")
    if seen_urls is not None:
        seen = {_norm(u) for u in seen_urls}
        for u in links:
            if _norm(u) not in seen:
                problems.append(f"ссылка не из найденных источников: {u}")

    cover = note.get("cover") or {}
    if cover.get("type") not in ("ivory", "data"):
        problems.append("cover.type должен быть ivory или data")
    if cover.get("type") == "data" and not cover.get("number"):
        problems.append("для обложки data нужна цифра")

    if not problems:
        n = visible_length(build_caption(note))
        if n > CAPTION_TARGET:
            problems.append(f"пост длиной {n} знаков, лимит {CAPTION_TARGET}: сократи")
    return list(dict.fromkeys(problems))
