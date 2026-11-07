"""Проверка заметки и сборка подписи к посту в HTML-разметке Telegram."""

import html
import json
import re
from urllib.parse import unquote

from .rubrics import RUBRICS, FORMS, LAYOUTS, OPTIC_FAMILY

CAPTION_LIMIT = 1024  # лимит подписи к фото в Bot API
CAPTION_TARGET = 520  # telegram.md: короткий пост до 520 знаков; полная версия — на сайте

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
    (re.compile(r"кастодиальн|\bпериметр|\bдрайвер|\bкейс", re.I), "профжаргон или англицизм"),
    (re.compile(r"стройк[аиуе] ИИ", re.I), "калька «стройка ИИ»"),
    (re.compile(r"\bшок", re.I), "«шок»"),
    (re.compile(r"вы не поверите", re.I), "«вы не поверите»"),
    (re.compile(r"на самом деле", re.I), "«на самом деле»"),
    (re.compile(r"не только\s+[^.!?;:]{1,60}?,\s+это\s+", re.I), "качели «не только X, это Y»"),
    (re.compile(r"кто контролирует [^.!?]{1,40}?[—–-]\s*контролирует", re.I), "слоган"),
    # качели наоборот: «X, а не Y»
    (re.compile(r",\s+а\s+не\s+", re.I), "качели «X, а не Y»"),
    (re.compile(r"\bдело\s+(?:не\s+)?в\s+", re.I), "«дело в…» — детективная лексика и штамп"),
    # качели «не X, а Y»
    (re.compile(r"(?:^|[\s,.—–-])не\s+[^.!?;:]{1,60}?,\s+а\s+", re.I), "качели «не X, а Y»"),
]

REQUIRED = ["rubric", "fly", "headline", "subhead", "paragraphs", "tg", "form", "optics", "topic_key", "cover"]


def extract_json(text: str) -> dict:
    m = re.search(r"<json>\s*(\{.*\})\s*</json>", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return text_lists(json.loads(raw))


def text_lists(d):
    """Модель иногда кладёт абзац объектом {"text": "..."} вместо строки — тогда в пост и на сайт уходит сырой JSON
    (6 ноября: заметка про вербовку мигрантов). Приводим абзацы к строкам."""
    def one(x):
        if isinstance(x, dict):
            return str(x.get("text") or x.get("paragraph") or next((v for v in x.values() if isinstance(v, str)), ""))
        if isinstance(x, str) and x.strip().startswith("{"):
            # абзац пришёл строкой с JSON внутри: {"text": "..."} (6 ноября, вербовка мигрантов) — достаём текст
            try:
                return one(json.loads(x))
            except Exception:  # noqa: BLE001
                m = re.search(r'"(?:text|paragraph)"\s*:\s*"(.*)"\s*}\s*$', x, re.S)
                if m:
                    return m.group(1).replace('\\"', '"').strip()
        return x if isinstance(x, str) else str(x)
    if isinstance(d, dict):
        for k in ("paragraphs", "tg"):
            if isinstance(d.get(k), list):
                d[k] = [one(x) for x in d[k] if one(x).strip()]
        if isinstance(d.get("tg"), list) and len(d["tg"]) > 1:  # формат A: короткий пост — один абзац
            d["tg"] = [" ".join(p.strip() for p in d["tg"])]
    return d


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
    parts = [note.get("headline", ""), note.get("subhead", ""), note.get("final") or ""] + list(note.get("paragraphs", [])) + list(note.get("tg") or [])
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
    short = note.get("tg") or []
    paras = [_inline(p) for p in (short or note["paragraphs"])]
    final = (note.get("final") or "").strip() if not short else ""  # финал — только в полной версии
    if final:  # последняя фраза — продолжение последнего абзаца, без цитаты и без отдельной «подписи»
        paras[-1] = f"{paras[-1]} {_inline(final)}"
    body = "\n\n".join(paras)
    if short:  # формат A: шапка, заголовок, один плотный абзац; подзаголовок — только на сайте
        return f"{head}\n\n<b>{_inline(note['headline'])}</b>\n\n{body}"
    return (
        f"{head}\n\n"
        f"<b>{_inline(note['headline'])}</b>\n\n"
        f"<i>{_inline(note['subhead'])}</i>\n\n"
        f"{body}"
    )


def visible_length(caption_html: str) -> int:
    text = re.sub(r"<[^>]+>", "", caption_html)
    return len(html.unescape(text))


def fix_links(obj, allowed, drop_unknown: bool = True):
    """Чинит адреса, которые модель переписала с ошибкой, по всему JSON:
    1) почти совпадающий адрес (кириллическая «с» вместо s) — подставляется настоящий;
    2) тот же сайт и тот же номер статьи (у DW, РБК, Интерфакса он в адресе) — подставляется настоящий;
    3) адрес, которого нет среди материалов, становится обычным текстом (drop_unknown): факт всё равно
       проверит фактчек по материалам, а выдуманная ссылка в канал не уйдёт."""
    import difflib

    norm = {_norm(u): u for u in allowed}

    def ids(u: str) -> set[str]:
        return set(re.findall(r"\d{5,}", u))

    def host(u: str) -> str:
        return u.split("/", 1)[0]

    def one(m):
        text, url = m.group(1), m.group(2)
        n = _norm(url)
        if n in norm:
            return m.group(0)
        best = difflib.get_close_matches(n, list(norm), n=1, cutoff=.88)
        if not best:
            best = [a for a in norm if host(a) == host(n) and ids(a) & ids(n)][:1]
        if best:
            return f"[{text}]({norm[best[0]]})"
        return text if drop_unknown else m.group(0)

    if isinstance(obj, str):
        return LINK_RE.sub(one, obj)
    if isinstance(obj, list):
        return [fix_links(x, allowed, drop_unknown) for x in obj]
    if isinstance(obj, dict):
        return {k: fix_links(v, allowed, drop_unknown) for k, v in obj.items()}
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
    if note.get("tg"):  # в коротком посте свой счёт ссылок: каждый источник — один раз
        seen = set()
        note["tg"] = [fix(p) for p in note["tg"]]
    return note


# Примеры из свода: модель любит переписывать их дословно. Список собирается сам из всех цитат
# «…» в system.md и telegram.md, так что новый пример в документе сразу попадает под запрет.
def _doc_quotes() -> list[str]:
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = "".join((root / n).read_text(encoding="utf-8") for n in ("system.md", "telegram.md") if (root / n).exists())
    out = set()
    found = re.findall(r"«([^«»]{2,120})»", text)
    # Примеры заголовков в таблице форм стоят без кавычек: второй столбец таблицы «Форма | Пример | …»
    sec = text.split("## Формы заголовка", 1)[-1].split("\n## ", 1)[0]
    found += [row.split("|")[2] for row in sec.splitlines() if row.startswith("| ") and row.count("|") >= 4]
    for q in found:
        q = re.sub(r"\s+", " ", q).strip(" .,!?…").lower().replace("ё", "е")
        if q and "[" not in q and not re.search(r"\b[xy]\b", q):
            out.add(q)
    return sorted(out)


DOC_QUOTES = _doc_quotes()


def _low(t: str) -> str:
    return re.sub(r"\s+", " ", plain(t or "")).strip(" .,!?…«»\"").lower().replace("ё", "е")


def copied_examples(note: dict, recent: list[dict] | None = None) -> list[str]:
    """Пример из свода можно взять, но не чаще раза в неделю: иначе модель тянется к нему в каждом посте.
    recent — посты за 7 дней из state (main.load_state). Любой флай тоже не повторяется 7 дней."""
    out = []
    recent = recent or []
    fly, head, sub = _low(note.get("fly")), _low(note.get("headline")), _low(note.get("subhead"))
    past_flies = {_low(r.get("fly")) for r in recent}
    past_text = " | ".join(_low(r.get(k)) for r in recent for k in ("fly", "headline", "subhead"))
    if fly and fly in past_flies:
        out.append(f"флай «{note['fly']}» уже был на этой неделе — нужен другой")
    for name, val in (("заголовок", head), ("подзаголовок", sub)):
        hit = next((q for q in DOC_QUOTES if len(q.split()) >= 3 and q in val and q in past_text), None)
        if hit:
            out.append(f"{name} повторяет пример из свода «{hit}», который уже брали на этой неделе — нужен свой")
    if sub.startswith("подсказка") and "подсказка" in past_text:
        out.append("подзаголовок «Подсказка: …» уже был на этой неделе — возьми другую форму")
    return out


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u25A0-\u25FF\u2190-\u21FF]")
_NUM = re.compile(r"\d[\d\s,.]*\d|\d")


def note_rules(note: dict) -> list[str]:
    """Правила свода и telegram.md, которые проверяются по одной заметке."""
    out = []
    fly = plain(note.get("fly") or "")
    if not 2 <= len(fly.split()) <= 5:
        out.append(f"во флае {len(fly.split())} слов, нужно 2–5")
    body = " ".join(plain(p) for p in note.get("paragraphs", [])) + " " + plain(note.get("final") or "")
    if len(fly) > 6 and fly.lower().strip(" .«»") in body.lower():
        out.append("флай повторяется в тексте: он стоит только над заголовком")
    optics = [str(o).strip().lower() for o in note.get("optics") or []]
    if len(optics) != 2 or any(o not in OPTIC_FAMILY for o in optics):
        out.append(f"оптик должно быть две из таблицы «Оптики», получено: {optics}")
    elif OPTIC_FAMILY[optics[0]] == OPTIC_FAMILY[optics[1]]:
        out.append(f"вторая оптика из того же семейства, что главная ({optics[0]}, {optics[1]}): нужна из другого")
    text = " ".join(plain(note.get(k) or "") for k in ("fly", "headline", "subhead")) + " " + body
    for o in OPTIC_FAMILY:
        if o not in ("спектакль", "обменник") and o in text.lower():
            out.append(f"название оптики «{o}» в тексте: названия оптик не появляются никогда")
    if note.get("layout") and note["layout"] not in LAYOUTS:
        out.append(f"раскладка «{note['layout']}» не из трёх: {', '.join(LAYOUTS)}")
    if _EMOJI.search(text):
        out.append("в тексте эмодзи или значок: в посте их нет")
    for sent in re.split(r"(?<=[.!?])\s+", body):
        nums = [n for n in _NUM.findall(sent) if not re.fullmatch(r"(19|20)\d\d", n.strip())]
        if len(nums) >= 3:
            out.append(f"в одном предложении {len(nums)} цифры: одна цифра на предложение — «{sent[:80]}…»")
            break
    return out


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
    if not isinstance(note["paragraphs"], list) or not (4 <= len(note["paragraphs"]) <= 5):
        problems.append("в полной версии (paragraphs) должно быть 4–5 абзацев")
    elif len(" ".join(plain(p) for p in note["paragraphs"])) > 1700:
        problems.append("полная версия длиннее 1600 знаков: сократи")
    if not isinstance(note.get("tg"), list) or len(note.get("tg") or []) != 1:
        problems.append("короткий пост (tg) — ровно 1 абзац: факт, поворот, механизм, точка")
    elif plain(note["tg"][0]).strip()[:60] == plain(note["paragraphs"][0]).strip()[:60]:
        problems.append("короткий пост копирует начало полной версии: напиши его отдельно")

    words = len(plain(note["headline"]).split())
    if not 6 <= words <= 9:
        problems.append(f"в заголовке {words} слов, нужно 6–9")
    if plain(note["headline"]).count(",") > 1:
        problems.append("в заголовке больше одной запятой: нужно одно простое предложение")
    problems += note_rules(note)


    text_all = " ".join([note["fly"], note["headline"], note["subhead"], note.get("final") or "", *note["paragraphs"], *(note.get("tg") or [])])
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
