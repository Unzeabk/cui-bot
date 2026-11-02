"""Визуальный слой сайта: картинки, которые добавляют информацию, а не повторяют заголовок.

- Заметка и разбор: полоса-карта места события (ночной снимок, контуры, перекрестие), рядом город, регион,
  расстояние до Москвы и координаты — то, чего нет в тексте.
- Разбор: график в духе The Economist, «цифра» между абзацами, «было → стало». Данные — только цифры,
  которые уже стоят в тексте: лёгкая модель их размечает, код проверяет, что каждое число есть в тексте.
- «Неделя»: карта мира с пронумерованными событиями без заголовка и оглавления.
- «Позиция»: без картинки — строка «ПОЗИЦИЯ · Мнение редакции» над заголовком уже всё говорит.

Карты и графики снимаются в PNG фирменными шрифтами (как обложки в Telegram) и кладутся в site/img/v/ —
так они попадают в поиск по картинкам. «Цифра» и «было → стало» — HTML шрифтами сайта.
Любая ошибка здесь не мешает сборке: блок просто не появляется.
"""
from __future__ import annotations

import html
import json
import math
import re
import tempfile
from pathlib import Path

from . import brand as B
from .main import log

MOSCOW = (55.7558, 37.6173)

SYSTEM = """Ты — бильд-редактор издания об экономике уровня The Economist. Тебе дают опубликованный текст, абзацы пронумерованы [0], [1]…
Визуал не украшает, а доказывает ход драматургии там, где он стоит. И НЕ ПОВТОРЯЕТ ТЕКСТ: читатель только что прочёл абзац —
блок должен показать то, что словами видно хуже: соотношение двух-трёх цифр, долю, динамику, цепочку. Одна цифра,
которую абзац уже назвал, крупным шрифтом — это повтор, такой блок запрещён. Подпись не пересказывает фразу из абзаца. Правило цифр: ТОЛЬКО цифры, которые уже есть в тексте, слово в слово;
ничего не досчитывай, не округляй, не добавляй. Нет подходящих цифр — блока нет. Лучше ноль блоков, чем слабый.

Ходы разбора и что к ним ставить (поле "para" — номер абзаца, ПОСЛЕ которого встаёт блок; это абзац с нужными цифрами):
- поворот (очевидное объяснение ломается об одну цифру) → "vs" (две цифры рядом: ожидание и факт, мы и другие);
- механизм (кто кому и сколько платит) → "flow" (цепочка потоков с суммами);
- другая сторона, сравнение с соседями → "compare" (3–6 сопоставимых: страны, компании, регионы);
- подъём, закон во времени → "chart" (ряд за 3–8 лет/кварталов) или "before_after" (было → стало);
- вход → "doc" (точная цитата документа или чиновника из текста, если с неё начинается история) или "timeline" (хронология, если история — цепочка дат);
- поворот, если цифра — доля → "share" (каждый десятый, треть: сетка из 100 клеток);
- механизм без сумм → "flow" без "amount": цепочка участников и что передаётся (полномочия, доступ, товар);
- другая сторона без цифр → "players": кто выигрывает и кто платит — участники из текста;
- длинный кусок без цифр → "pull": самая сильная фраза из нашего же текста, дословно;
- финал — без блоков.
В разборе ставь 2–3 блока: если цифр мало, бери "doc", "flow" без сумм, "players", "pull". Каждый к своему ходу, не два подряд.

Форматы блоков (в "blocks"):
{"type": "doc", "para": 0, "quote": "дословная цитата из текста, 40–220 знаков, без кавычек", "who": "чей документ или кто сказал, как в тексте"}
{"type": "timeline", "para": 0, "title": "4–7 слов", "items": [{"date": "24 октября", "text": "что случилось, до 60 знаков"}, ...]}  (3–6 дат из текста)
{"type": "share", "para": 1, "value": "10%", "caption": "чего доля, до 90 знаков", "source": "..."}  (только проценты до 100;
   подходит любая доля целого: ставка налога 15% — «15% дохода уйдёт в налог», «до 90–95% дохода — пайщикам», доля рынка, доля экспорта)
{"type": "players", "para": 6, "title": "4–7 слов", "wins": [{"name": "как в тексте", "why": "до 70 знаков"}], "pays": [{"name": "...", "why": "..."}]}  (по 1–3 в колонке)
{"type": "pull", "para": 9, "quote": "одна фраза из текста дословно, 60–200 знаков"}
{"type": "figure", "para": 1, "value": "9,4 трлн ₽", "caption": "что это, до 90 знаков", "source": "..."}
{"type": "vs", "para": 1, "title": "4–7 слов", "expected": {"value": "...", "caption": "чего ждали"}, "actual": {"value": "...", "caption": "что вышло"}, "source": "..."}
{"type": "flow", "para": 4, "title": "кто кому платит, 4–7 слов", "steps": [{"name": "Покупатель в Китае"}, {"name": "Посредник в Гонконге", "amount": "4,2 млрд $", "label": "за что"}, {"name": "Экспортёр"}], "source": "..."}
   (2–4 участника по цепочке; "amount" у участника — сумма, пришедшая к нему от предыдущего)
{"type": "compare", "para": 7, "title": "вывод одной фразой", "unit": "в чём", "items": [["Россия", 33], ["Индия", 48]], "highlight": 0, "source": "..."}
{"type": "chart", "para": 10, "kind": "bar" | "line", "title": "вывод одной фразой, не повторяет заголовок", "unit": "в чём", "points": [["2021", 62], ...], "highlight": 5, "source": "..."}
{"type": "before_after", "para": 10, "title": "...", "before": {"label": "2024", "value": "38%", "caption": "..."}, "after": {"label": "2026", "value": "61%", "caption": "..."}, "source": "..."}

"place" — карта места, ТОЛЬКО если география и есть суть истории: событие физически произошло в конкретной точке
(завод, порт, месторождение, город с очередями, граница, регион с особым режимом) и читателю важно, где это.
null, если место — столица как синоним власти (Вашингтон, Москва, Пекин, Брюссель), мера на всю страну, рынок, компания или деньги без точки.
Сомневаешься — null. {"name": "коротко, до 24 знаков: город или объект («Кстово», «Главпочтамт»)", "region": "город/регион, страна", "lat": 0.0, "lon": 0.0, "scale": "city" | "region" | "country" | "macro"}

ЖАНРЫ:
- "analysis" (разбор): 2–3 блока по ходам, как выше.
- "note" (заметка) и "position" (позиция): РОВНО один блок или ноль — тот, что лучше всего доказывает главный поворот:
  "share" (доля), "vs", "before_after", "compare" или "chart" — только если блок сопоставляет хотя бы две цифры
  или показывает долю наглядно. Иначе ноль блоков. Ставь после абзаца с этой цифрой (не после последнего).
  "doc", "pull", "players", "timeline", "flow" в коротких текстах не нужны.
  Если сопоставлять нечего — "figure": главная цифра поворота с подписью-смыслом (она встанет выносом ПЕРЕД абзацем
  со своей цифрой). Только если правда нет ни пары цифр, ни доли.
  Пример: «Указ о резервировании 10% мощностей частных ЦОДов…» → {"type": "share", "para": 0, "value": "10%", "caption": "мощностей частных ЦОДов уходит в госрезерв"}.

"vs", "before_after", "compare", "chart" сравнивают ОДИН показатель в одних единицах: цена с ценой, доля с долей,
выручка этого года с выручкой прошлого. Убыток против цены продажи, штраф против выручки — разные показатели, так нельзя.
Подписи — обычный русский текст, без кодировок и ссылок.

Подписи по-русски, холодно, без оценок. ОТВЕТ — только JSON: {"blocks": [...], "place": {...} | null}"""


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def _tokens(text: str) -> set[str]:
    flat = re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d{3}\b)", "", text)
    return {t.replace(".", ",") for t in re.findall(r"\d+(?:[.,]\d+)?", flat)}


def _num_in(text: str, v) -> bool:
    """Число есть в тексте (31,5 = «31,5» = «31.5»; 1200 = «1 200»)."""
    if isinstance(v, (int, float)):
        cand = f"{v:g}".replace(".", ",")
    else:
        m = re.search(r"\d+(?:[.,]\d+)?", re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d{3}\b)", "", str(v)))
        if not m:
            return False
        cand = m.group(0).replace(".", ",")
    return cand in _tokens(text)


def _plain(e: dict) -> str:
    from .compose import plain
    return "\n".join([e.get("headline", ""), e.get("subhead", "")] + [plain(p) for p in e.get("paragraphs") or []])


def _s(x, n=90) -> str:
    from urllib.parse import unquote
    t = str(x or "")
    if "%D0" in t or "%D1" in t:
        t = unquote(t)
    t = re.sub(r"https?://\S+", "", t)
    return t.strip()[:n]


SHORT_OK = {"share", "vs", "before_after", "compare", "chart", "figure"}
VERSION = 11
RETRY_EMPTY = 6  # поднять — сайт заново попробует разметить только тексты без блоков (остальные не трогает)
RENDER = 3  # поменялась отрисовка картинок → поднять: файлы получат новые имена, браузер и Cloudflare не покажут старые  # поменялись правила бильд-редактора → поднять: сайт сам переразметит все тексты


def _check(d: dict, text: str, n_par: int, kind: str = "analysis", taken: set | None = None) -> dict:
    out, used = [], set(taken or ())
    for b in (d.get("blocks") or [])[:4]:
        if not isinstance(b, dict):
            continue
        t, para = b.get("type"), b.get("para")
        if t == "share" and "%" not in str(b.get("value", "")):  # «доля» без процента — это одна цифра
            t = b["type"] = "figure"
        if kind != "analysis" and t not in SHORT_OK:
            continue
        last_ok = n_par if kind != "analysis" else n_par - 1  # в коротком тексте блок можно и после последнего абзаца
        if not isinstance(para, int) or not (0 <= para < max(1, last_ok)) or para in used or (para - 1) in used or (para + 1) in used:
            continue
        x = None
        if t == "doc":
            q = re.sub(r"\s+", " ", _s(b.get("quote"), 260)).strip(" «»\"")
            if 40 <= len(q) <= 240 and q.lower() in re.sub(r"\s+", " ", text).lower():
                x = {"quote": q, "who": _s(b.get("who"), 80)}
        elif t == "pull":
            q = re.sub(r"\s+", " ", _s(b.get("quote"), 260)).strip(" «»\"")
            if 60 <= len(q) <= 220 and q.lower() in re.sub(r"\s+", " ", text).lower():
                x = {"quote": q}
        elif t == "players":
            def side(lst):
                return [{"name": _s(z.get("name"), 50), "why": _s(z.get("why"), 90)} for z in (lst or [])[:3]
                        if isinstance(z, dict) and z.get("name") and z["name"].split()[0][:5].lower() in text.lower()]
            w, pz = side(b.get("wins")), side(b.get("pays"))
            if w and pz:
                x = {"title": _s(b.get("title"), 70), "wins": w, "pays": pz}
        elif t == "timeline":
            it = [z for z in (b.get("items") or []) if isinstance(z, dict) and z.get("date") and z.get("text")][:6]
            if len(it) >= 3 and all(_num_in(text, z["date"]) or z["date"].lower() in text.lower() for z in it):
                x = {"title": _s(b.get("title"), 70), "items": [{"date": _s(z["date"], 24), "text": _s(z["text"], 80)} for z in it]}
        elif t == "share":
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(b.get("value", "")))
            if m and _num_in(text, b["value"]) and 0 < float(m.group(1).replace(",", ".")) <= 100:
                x = {"value": _s(b["value"], 12), "pct": float(m.group(1).replace(",", ".")), "caption": _s(b.get("caption"), 120)}
        elif t == "figure" and _num_in(text, b.get("value", "")):
            x = {"value": _s(b["value"], 20), "caption": _s(b.get("caption"), 120)}
        elif t in ("vs", "before_after"):
            a, c = (b.get("expected"), b.get("actual")) if t == "vs" else (b.get("before"), b.get("after"))
            if isinstance(a, dict) and isinstance(c, dict) and _num_in(text, a.get("value", "")) and _num_in(text, c.get("value", "")) \
                    and a.get("value") != c.get("value"):
                x = {"title": _s(b.get("title"), 70), "a": {k: _s(a.get(k)) for k in ("label", "value", "caption")},
                     "b": {k: _s(c.get(k)) for k in ("label", "value", "caption")}}
        elif t == "flow":
            st = [z for z in (b.get("steps") or []) if isinstance(z, dict) and z.get("name")][:4]
            if len(st) >= 2 and all(_num_in(text, z["amount"]) for z in st if z.get("amount") and _digits(z["amount"])) \
                    and all(z["name"].split()[0][:5].lower() in text.lower() for z in st):
                x = {"title": _s(b.get("title"), 70), "steps": [{k: _s(z.get(k), 60) for k in ("name", "amount", "label")} for z in st]}
        elif t in ("compare", "chart"):
            key = "items" if t == "compare" else "points"
            pts = [(_s(a, 22), float(v)) for a, v in (b.get(key) or []) if isinstance(v, (int, float))]
            lo, hi = (3, 6) if t == "compare" else (3, 8)
            if lo <= len(pts) <= hi and all(_num_in(text, v) for _, v in pts) and b.get("title"):
                x = {"title": _s(b["title"], 80), "unit": _s(b.get("unit"), 60), "points": pts,
                     "highlight": int(b.get("highlight", 0 if t == "compare" else len(pts) - 1) or 0) % len(pts)}
                if t == "chart":
                    x["kind"] = "line" if b.get("kind") == "line" else "bar"
        if x:
            x.update(type=t, para=para, source=_s(b.get("source"), 80))
            out.append(x)
            used.add(para)
    res = {"blocks": out[:3] if kind == "analysis" else out[:1]}
    p = B.place_ok(d.get("place"))
    if p:
        p["region"] = _s((d.get("place") or {}).get("region"), 60)
        p["title"] = _s((d.get("place") or {}).get("name"), 60)  # полное имя, обычным регистром
        res["place"] = p
    return res


WEEK_SYSTEM = """Тебе дают события недели, пронумерованные [0], [1]… Для каждого — точка на карте мира, где это произошло:
{"name": "город", "lat": 0.0, "lon": 0.0}. Компания — город штаб-квартиры или места события из текста; мера на всю Россию — Москва;
страна — её столица. Не знаешь — null. ОТВЕТ — только JSON: {"places": [{...} | null, ...]} в том же порядке."""


def week_places(e: dict) -> list:
    try:
        from .compose import extract_json, plain
        from .gemini import LIGHT_CHAIN, ask
        pars = e.get("paragraphs") or []
        raw, _ = ask(LIGHT_CHAIN, WEEK_SYSTEM, "\n".join(f"[{i}] {plain(p)}" for i, p in enumerate(pars)), temperature=0.1)
        pl = extract_json(raw).get("places") or []
        return [B.place_ok(x) for x in pl][:len(pars)]
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: места недели не получены ({ex})")
        return []


CRITIC = """Ты — выпускающий редактор уровня The Economist. Тебе дают текст и визуальные блоки, которые к нему предложили.
Пропусти блок, только если ВСЁ верно:
1) он сравнивает один и тот же показатель в одних единицах (цена с ценой, тонны с тоннами, доля с долей);
   литры против тонн, убыток против цены, закупки против прибыли — НЕТ;
2) подписи точно соответствуют тексту, ничего не додумано;
3) заголовок блока не врёт и не пустой.
Снимай только ошибки: разные показатели, неточные подписи, искажение смысла. Вкус не оценивай —
блок, который наглядно сопоставляет цифры из текста, пропускай, даже если цифры есть в соседнем абзаце.
«Было → стало» одного показателя в разных мерах (штраф до 40 тыс. руб. → штраф 50–200% выручки) — это верно, пропускай.
Блоки pull (сильная фраза текста), doc (цитата документа), players, flow, timeline — пропускай, если они точны и по делу:
повторять текст — их суть, важно лишь, что фраза сильная, а участники и связи верны.
ОТВЕТ — только JSON: {"keep": [номера блоков, которые пропускаешь]}"""


def _critic(e: dict, blocks: list[dict]) -> list[dict]:
    if not blocks:
        return blocks
    try:
        from .compose import extract_json
        from .gemini import LIGHT_CHAIN, ask
        listing = "\n".join(f"[{i}] {json.dumps(b, ensure_ascii=False)}" for i, b in enumerate(blocks))
        raw, _ = ask(LIGHT_CHAIN, CRITIC, f"ТЕКСТ:\n{_plain(e)[:12000]}\n\nБЛОКИ:\n{listing}", temperature=0.1)
        keep = {int(i) for i in extract_json(raw).get("keep") or [] if str(i).isdigit()}
        _debug(e, "редактор", raw, {"keep": sorted(keep)})
        out = [b for i, b in enumerate(blocks) if i in keep]
        if len(out) < len(blocks):
            log(f"визуал: редактор снял {len(blocks) - len(out)} из {len(blocks)}")
        return out
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: проверка не прошла ({ex}) — блоки не ставим")
        return []


def _unit(v: str) -> str:
    return re.sub(r"[\d\s.,−\-–+]+", " ", str(v)).strip().lower()


SERIES_KEYS = [  # о чём текст → наш официальный ряд (ЦБ, Мосбиржа) для графика-контекста
    ("klyuchevaya-stavka", r"ключев\w* ставк"),
    ("kurs-yuanya", r"юан"),
    ("kurs-dollara", r"курс\w* доллар|доллар\w* к рубл|рубл\w* к доллар"),
    ("kurs-evro", r"курс\w* евро|евро к рубл"),
    ("indeks-mosbirzhi", r"индекс\w* мосбирж|imoex|индекс\w* московской бирж"),
    ("zoloto", r"цен\w* золот|золот\w* подорож|золот\w* дешев"),
]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _add_series(e: dict, d: dict, pars: list) -> None:
    """Если текст о ставке, курсе, индексе или золоте — график этого показателя за год из официальных данных.
    Это не цифры из текста, а контекст из первоисточника (ЦБ, Мосбиржа) с подписью источника."""
    from datetime import date
    f = Path("state") / "series.json"
    if not f.exists() or e.get("kind") not in ("note", "analysis", "position"):
        return
    blocks = d.get("blocks") or []
    if (e.get("kind") != "analysis" and blocks) or len(blocks) >= 3 or any(b["type"] == "chart" for b in blocks):
        return
    from .compose import plain
    low = [plain(p).lower() for p in pars]
    series = json.loads(f.read_text(encoding="utf-8"))
    for slug, rx in SERIES_KEYS:
        hit = next((i for i, t in enumerate(low) if re.search(rx, t)), None)
        if hit is None or slug not in series:
            continue
        sr = series[slug]
        pts = [(date.fromisoformat(a), b) for a, b in sr["pts"]]
        last = pts[-1][0]
        month_end = {}
        for dt, v in pts:
            if (last - dt).days <= 365:
                month_end[(dt.year, dt.month)] = v
        ms = sorted(month_end.items())[-12:]
        if len(ms) < 6:
            continue
        nd = sr["digits"] if sr["digits"] <= 2 else 2
        pts2 = [(MONTHS_SHORT[m - 1], round(v, nd)) for (y, m), v in ms]
        para = hit if hit < len(pars) - 1 else max(0, hit - 1)
        used = {b["para"] for b in blocks}
        while para in used or para - 1 in used or para + 1 in used:
            para += 2
            if para >= len(pars) - 1:
                return
        unit = {"%": "% годовых, на конец месяца", "₽": "₽, на конец месяца"}.get(sr["unit"], "пункты, на конец месяца")
        blocks.append({"type": "chart", "kind": "line", "para": para, "title": f"{sr['title']} за год",
                       "unit": unit, "points": pts2, "highlight": len(pts2) - 1, "source": sr["src"], "context": True})
        d["blocks"] = sorted(blocks, key=lambda b: b["para"])
        return


def _debug(e: dict, step: str, raw: str, d) -> None:
    """Журнал решений бильд-редактора: state/visuals-log.json, последние 60 записей — чтобы видеть, почему блока нет."""
    try:
        f = Path("state") / "visuals-log.json"
        lg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        lg.append({"text": e.get("headline", "")[:60], "step": step, "raw": (raw or "")[:1500], "kept": d})
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps(lg[-60:], ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def plan(e: dict, recent: list[str] | None = None) -> dict:
    """Разметка визуальных блоков для текста (один лёгкий запрос). Пустой dict — нечего показывать."""
    try:
        from .compose import extract_json
        from .gemini import LIGHT_CHAIN, ask
        from .compose import plain
        text = _plain(e)
        pars = e.get("paragraphs") or []
        numbered = "\n\n".join(f"[{i}] {plain(p)}" for i, p in enumerate(pars))
        from collections import Counter
        tired = [t for t, c in Counter(recent or []).most_common() if c >= 3]
        hint = (f"\nНа сайте сейчас часто: {', '.join(tired)} — если текст позволяет, выбери другой тип." if tired else "")
        head = f"Жанр: {e.get('kind')}{hint}\n{e.get('headline', '')}\n{e.get('subhead', '')}\n\n"
        from .gemini import CHECK_CHAIN
        chain = CHECK_CHAIN + LIGHT_CHAIN  # бильд-редактор — модель посильнее; квоты хватает: текст размечается один раз
        raw, _ = ask(chain, SYSTEM, head + numbered[:14000], temperature=0.3)
        d = _check(extract_json(raw), text, len(pars), e.get("kind") or "note")
        _debug(e, "план", raw, d)
        nums = len(re.findall(r"\d+(?:[.,]\d+)?", text))
        if e.get("kind") in ("note", "position") and not d.get("blocks") and nums >= 2:
            # второй заход для короткого текста: блок почти всегда есть, если в тексте две цифры одного показателя
            more = ("\nПрошлый ответ не дал блока. Посмотри ещё раз: найди две цифры ОДНОГО показателя (было и стало, мы и другие, "
                    "штраф раньше и теперь, цена для одних и для других) или долю — и дай один блок. Ноль, только если таких цифр правда нет.")
            raw2, _ = ask(chain, SYSTEM, head + numbered[:14000] + more, temperature=0.5)
            d["blocks"] = _check(extract_json(raw2), text, len(pars), e.get("kind"))["blocks"]
            _debug(e, "второй заход", raw2, d)
            log(f"визуал: второй заход «{e.get('headline', '')[:40]}» → {[b['type'] for b in d['blocks']]}")
        if e.get("kind") == "analysis" and len(d.get("blocks") or []) < 3 and len(pars) >= 8:
            # второй проход: добрать блоки к другим ходам, другими типами
            have = d.get("blocks") or []
            more = (f"\nУже стоят: " + ", ".join(f"{b['type']} после [{b['para']}]" for b in have)
                    + ". Добавь 1–2 блока ДРУГИХ типов к другим ходам (не рядом с этими абзацами). Без цифр — doc, pull, players, flow без сумм.")
            raw2, _ = ask(chain, SYSTEM, head + numbered[:14000] + more, temperature=0.4)
            d2 = _check({"blocks": extract_json(raw2).get("blocks") or []}, text, len(pars), "analysis",
                        taken={b["para"] for b in have})
            d["blocks"] = sorted([*have, *[b for b in d2["blocks"] if b["type"] not in {h["type"] for h in have}]][:3],
                                 key=lambda b: b["para"])
        # одинаковые единицы у сравнений — проверка кодом, потом редактор
        for b in d.get("blocks") or []:  # разные меры одного показателя (40 тыс. руб. → 50–200% выручки) — это «было и стало»
            if b["type"] == "vs" and _unit(b["a"]["value"]) != _unit(b["b"]["value"]):
                b["type"] = "before_after"
        for b in d["blocks"]:
            if (b.get("source") or "").strip().lower() in ("текст", "текст статьи", "статья", "cui bono?"):
                b["source"] = ""
        d["blocks"] = _critic(e, d["blocks"])
        bl = d.get("blocks") or []
        if any(b["type"] != "figure" for b in bl):  # одна цифра — только когда ничего лучше нет
            bl = [b for b in bl if b["type"] != "figure"]
        for b in bl:
            if b["type"] == "figure":  # вынос перед абзацем со своей цифрой, а не после него
                idx = next((i for i, p in enumerate(pars) if _num_in(plain(p), b["value"])), 0)
                b["para"] = idx - 1
        d["blocks"] = bl[:1] if e.get("kind") != "analysis" else bl
        _add_series(e, d, pars)
        if e.get("kind") == "position" and d.get("blocks"):
            d.pop("place", None)  # у позиции карта — только если нет блока с цифрами
        if e.get("kind") == "week":
            d["blocks"] = []  # у «Недели» своя вёрстка номера
            d.pop("place", None)
        if e.get("kind") == "week" and not any(e.get("places") or []):
            d["places"] = week_places(e)
        d["v"] = VERSION
        d["r"] = RETRY_EMPTY
        return d
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: разметка не получена ({ex})")
        return {}


# ---------- язык подписей ----------

LANG = "ru"
LABELS = {
    "ru": {"km_msk": "км до Москвы", "place": "Место события", "chart": "График", "src": "Источник", "players": "Кто выигрывает и кто платит",
           "wins": "Выигрывает", "pays": "Платит", "doc": "Документ", "timeline": "Хронология", "vs": "Ожидание и факт",
           "ba": "Было и стало", "flow": "Как это устроено", "cmp": "Сравнение", "week_n": "событий недели"},
    "en": {"km_msk": "km from Moscow", "place": "Where", "chart": "Chart", "src": "Source", "players": "Who gains, who pays",
           "wins": "Gains", "pays": "Pays", "doc": "On the record", "timeline": "Timeline", "vs": "Expected and actual",
           "ba": "Before and after", "flow": "How it works", "cmp": "Comparison", "week_n": "events this week"},
}


def L(k: str) -> str:
    return LABELS.get(LANG, LABELS["ru"])[k]


# ---------- съёмка ----------

def _shoot(page: str, path: Path, w: int, h: int) -> Path:
    from playwright.sync_api import sync_playwright
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page)
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            pg.goto(tmp.as_uri(), wait_until="load")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(250)
            png = path.with_suffix(".png")
            pg.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": w, "height": h})
            b.close()
        from PIL import Image
        Image.open(png).convert("RGB").save(path, "JPEG", quality=86, optimize=True, progressive=True)
        png.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    return path


CSS = B.FONT_CSS + """
*{box-sizing:border-box;margin:0}body{background:#0B0D10;color:#EDE9E2;font-family:'Head',sans-serif;-webkit-font-smoothing:antialiased}
.k{font-family:'Plex',monospace;font-weight:500;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#6B6F72}
.mono{font-family:'Plex',monospace}
"""


def _km(a, b) -> int:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return round(2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                                + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)))


def map_strip(place: dict, path: Path, light: bool = False) -> Path:
    W, H = 720, 260
    m = B.map_layer(place, w=W * 2, h=H * 2, fx=.66, fy=.5, fade=None, night=not light)
    bg, fg, sub = ("#FBFAF7", "#0B0D10", "#3A3F45") if light else ("#0B0D10", "#EDE9E2", "#C9CFD6")
    if light:  # спутникового снимка в светлой теме нет — сетка точек и контуры, как на бумажной карте
        m = m.replace("#12161B", "#ECE8E0").replace(B.COLD, "#8A8E92").replace(B.IVORY, "#0B0D10")
        m = m.replace("<g stroke=", B.dot_grid(W * 2, H * 2, step=34, color="#9C978D", opacity=.55) + "<g stroke=", 1)
    lines = []
    d = _km((place["lat"], place["lon"]), MOSCOW)
    if d > 15:
        lines.append(f"{d:,} {L('km_msk')}".replace(",", " "))
    lines.append(B.coords(place["lat"], place["lon"]))
    name = (place.get("title") or place.get("name") or "").strip()
    name = name[:1].upper() + name[1:]
    region = place.get("region") or ""
    region = html.escape(region) if region.strip().lower() not in name.lower() else ""
    size = 28 if len(name) <= 16 else (23 if len(name) <= 26 else 19)
    shade = (f"linear-gradient(90deg,{bg} 0%,{bg}eb 38%,{bg}00 64%)")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body{{background:{bg};color:{fg}}}
.root{{position:relative;width:{W}px;height:{H}px;overflow:hidden;background:{bg}}}
.map{{position:absolute;inset:0;transform:scale(.5);transform-origin:0 0}}
.shade{{position:absolute;inset:0;background:{shade}}}
.txt{{position:absolute;left:24px;top:24px;bottom:24px;width:330px;display:flex;flex-direction:column}}
.name{{font-weight:620;font-size:{size}px;line-height:1.15;margin-top:10px;font-variation-settings:'FLAR' 100,'VOLM' 100;color:{fg}}}
.reg{{font-family:'Onest',sans-serif;font-size:15px;color:{sub};margin-top:6px}}
.facts{{margin-top:auto;font-family:'Plex',monospace;font-size:12.5px;line-height:21px;color:{sub}}}
</style></head><body><div class="root"><div class="map">{m}</div><div class="shade"></div>
<div class="txt"><div class="k">{L('place')}</div><div class="name">{html.escape(name)}</div>
{f'<div class="reg">{region}</div>' if region else ''}<div class="facts">{"<br>".join(html.escape(x) for x in lines)}</div></div>
</div></body></html>"""
    return _shoot(page, path, W, H)


def chart_img(c: dict, path: Path, light: bool = False) -> Path:
    W, H = 720, 380
    pts, hi = c["points"], c["highlight"]
    vals = [v for _, v in pts]
    lo, top = min(0, min(vals)), max(vals) * 1.15 or 1
    x0, x1, y0, y1 = 56, W - 28, H - 70, 128
    def Y(v): return y0 - (v - lo) / (top - lo) * (y0 - y1)
    step = (x1 - x0) / len(pts)
    def fmt(v): return (f"{v:,.1f}" if v != int(v) else f"{int(v):,}").replace(",", " ").replace(".", ",")
    g = "".join(f'<line x1="{x0}" x2="{x1}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="#1C2026"/>'
                f'<text x="{x0 - 8}" y="{Y(t) + 4:.1f}" class="ax" text-anchor="end">{fmt(t)}</text>'
                for t in _ticks(lo, top))
    body = ""
    if c.get("kind", c.get("type")) == "bar":
        bw = step * .58
        for i, (lab, v) in enumerate(pts):
            x = x0 + i * step + (step - bw) / 2
            on = i == hi
            body += (f'<rect x="{x:.1f}" y="{min(Y(v), Y(0)):.1f}" width="{bw:.1f}" height="{abs(Y(0) - Y(v)):.1f}" fill="{"#EDE9E2" if on else "#2F343C"}"/>'
                     f'<text x="{x + bw / 2:.1f}" y="{Y(v) - 8:.1f}" class="val{" on" if on else ""}" text-anchor="middle">{fmt(v)}</text>'
                     f'<text x="{x + bw / 2:.1f}" y="{y0 + 20}" class="ax" text-anchor="middle">{html.escape(lab)}</text>')
    else:
        xy = [(x0 + i * step + step / 2, Y(v)) for i, (_, v) in enumerate(pts)]
        body += f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in xy)}" fill="none" stroke="#C9CFD6" stroke-width="2.5"/>'
        for i, ((x, y), (lab, v)) in enumerate(zip(xy, pts)):
            on = i == hi
            body += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if on else 3}" fill="{"#EDE9E2" if on else "#C9CFD6"}"/>'
                     + (f'<text x="{x:.1f}" y="{y - 12:.1f}" class="val on" text-anchor="middle">{fmt(v)}</text>' if on or i == 0 else "")
                     + f'<text x="{x:.1f}" y="{y0 + 20}" class="ax" text-anchor="middle">{html.escape(lab)}</text>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.root{{position:relative;width:{W}px;height:{H}px;background:#0E1114;border-top:3px solid #EDE9E2;padding:22px 24px}}
.t{{font-weight:620;font-size:21px;line-height:1.2;margin-top:10px;font-variation-settings:'FLAR' 100,'VOLM' 100}}
.u{{font-family:'Onest',sans-serif;font-size:14px;color:#C9CFD6;margin-top:6px}}
svg{{position:absolute;left:0;top:0}}
.ax{{font-family:'Plex',monospace;font-size:11.5px;fill:#6B6F72}}
.val{{font-family:'Plex',monospace;font-size:12.5px;fill:#C9CFD6}}.val.on{{fill:#EDE9E2;font-weight:600}}
.src{{position:absolute;left:24px;bottom:16px}}
</style></head><body><div class="root"><div class="k" style="color:#C9CFD6">{L('chart')}</div>
<div class="t">{html.escape(c["title"])}</div><div class="u">{html.escape(c["unit"])}</div>
<svg width="{W}" height="{H}">{g}{body}<line x1="{x0}" x2="{x1}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#6B6F72"/></svg>
<div class="k src">{(L("src") + ": " + html.escape(c["source"]) + " · ") if c["source"] else ""}Cui Bono?</div></div></body></html>"""
    if light:
        mp = {"#0E1114": "#FBFAF7", "#EDE9E2": "#0B0D10", "#2F343C": "#CFCAC0", "#1C2026": "#E6E2DA", "#C9CFD6": "#3A3F45"}
        page = re.sub("|".join(mp), lambda m: mp[m.group(0)], page)
        page = page.replace("body{background:#FBFAF7;color:#0B0D10", "body{background:#FBFAF7;color:#0B0D10")
    return _shoot(page, path, W, H)


def _ticks(lo: float, top: float) -> list[float]:
    span = top - lo
    raw = span / 3
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    st = min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    out, t = [], math.ceil(lo / st) * st
    while t < top:
        if abs(t) > 1e-9:
            out.append(t)
        t += st
    return out[:4]


def week_map(places: list, path: Path, light: bool = False) -> Path:
    W, H = 720, 320
    m = B.world_layer([B.place_ok(p) for p in places], w=W * 2, h=H * 2, area=(80, 70, W * 2 - 80, H * 2 - 90))
    if light:
        m = m.replace("#12161B", "#ECE8E0").replace(B.COLD, "#8A8E92").replace(B.IVORY, "#0B0D10")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.root{{position:relative;width:{W}px;height:{H}px;overflow:hidden}}.map{{position:absolute;inset:0;transform:scale(.5);transform-origin:0 0}}
.lab{{position:absolute;left:20px;bottom:16px}}</style></head><body><div class="root"><div class="map">{m}</div>
<div class="k lab">{len([p for p in places if p])} {L("week_n")}</div></div></body></html>"""
    if light:
        page = page.replace(".root{", "body{background:#FBFAF7}.root{background:#FBFAF7;", 1)
    return _shoot(page, path, W, H)


# ---------- HTML-блоки страницы ----------

def figure_html(f: dict) -> str:
    return (f'<figure class="v-fig"><div class="v-num">{html.escape(f["value"])}</div>'
            f'<figcaption>{html.escape(f["caption"])}' + (f'<span>{html.escape(f["source"])}</span>' if f.get("source") else "")
            + "</figcaption></figure>")


def pull_html(b: dict) -> str:
    return f'<figure class="v-pull"><blockquote>{html.escape(b["quote"])}</blockquote></figure>'


def players_html(b: dict) -> str:
    def col(name, lst):
        return (f'<div><div class="v-lab">{name}</div>' + "".join(
            f'<div class="v-pl"><b>{html.escape(z["name"])}</b><span>{html.escape(z["why"])}</span></div>' for z in lst) + "</div>")
    return (f'<figure class="v-players"><div class="v-k">{L("players")}</div>'
            + (f'<div class="v-t">{html.escape(b["title"])}</div>' if b.get("title") else "")
            + f'<div class="v-cols">{col(L("wins"), b["wins"])}{col(L("pays"), b["pays"])}</div></figure>')


def doc_html(b: dict) -> str:
    return (f'<figure class="v-doc"><div class="v-k">{L("doc")}</div><blockquote>{html.escape(b["quote"])}</blockquote>'
            + (f'<figcaption>{html.escape(b["who"])}</figcaption>' if b.get("who") else "") + "</figure>")


def timeline_html(b: dict) -> str:
    rows = "".join(f'<li><time>{html.escape(z["date"])}</time><span>{html.escape(z["text"])}</span></li>' for z in b["items"])
    return (f'<figure class="v-tl"><div class="v-k">{L("timeline")}</div>'
            + (f'<div class="v-t">{html.escape(b["title"])}</div>' if b.get("title") else "") + f"<ol>{rows}</ol></figure>")


def share_html(b: dict) -> str:
    n = max(1, round(b["pct"])) if b["pct"] >= .5 else 1
    cells = "".join(f'<i class="{"on" if k < n else ""}"></i>' for k in range(100))
    return (f'<figure class="v-share"><div class="v-grid">{cells}</div><div><div class="v-num">{html.escape(b["value"])}</div>'
            f'<figcaption>{html.escape(b["caption"])}' + (f'<span>{html.escape(b["source"])}</span>' if b.get("source") else "")
            + "</figcaption></div></figure>")


def pair_html(b: dict) -> str:
    """«Против ожидания» и «было → стало»: две цифры и стрелка."""
    k = L("vs") if b["type"] == "vs" else L("ba")
    def side(x):
        return ((f'<div class="v-lab">{html.escape(x["label"])}</div>' if x.get("label") else "")
                + f'<div class="v-val">{html.escape(x["value"])}</div><div class="v-cap">{html.escape(x["caption"])}</div>')
    return (f'<figure class="v-ba"><div class="v-k">{k}</div>' + (f'<div class="v-t">{html.escape(b["title"])}</div>' if b.get("title") else "")
            + f'<div class="v-row"><div class="{"v-dim" if b["type"] == "vs" else ""}">{side(b["a"])}</div><div class="v-arr">→</div><div>{side(b["b"])}</div></div>'
            + (f'<div class="v-src">{L("src")}: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def flow_html(b: dict) -> str:
    parts = []
    for i, z in enumerate(b["steps"]):
        if i:
            parts.append(f'<div class="v-edge"><span>{html.escape(z.get("amount") or "")}</span>'
                         + (f'<i>{html.escape(z["label"])}</i>' if z.get("label") else "") + "</div>")
        parts.append(f'<div class="v-node">{html.escape(z["name"])}</div>')
    return (f'<figure class="v-flow"><div class="v-k">{L("flow")}</div><div class="v-t">{html.escape(b["title"])}</div>'
            f'<div class="v-chain">{"".join(parts)}</div>'
            + (f'<div class="v-src">{L("src")}: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def compare_html(b: dict) -> str:
    mx = max(abs(v) for _, v in b["points"]) or 1
    def fmt(v): return (f"{v:,.1f}" if v != int(v) else f"{int(v):,}").replace(",", " ").replace(".", ",")
    rows = "".join(f'<div class="v-bar{" on" if i == b["highlight"] else ""}"><span>{html.escape(n)}</span>'
                   f'<div><b style="width:{abs(v) / mx * 100:.1f}%"></b></div><em>{fmt(v)}</em></div>'
                   for i, (n, v) in enumerate(b["points"]))
    return (f'<figure class="v-cmp"><div class="v-k">{L("cmp")}</div><div class="v-t">{html.escape(b["title"])}</div>'
            + (f'<div class="v-u">{html.escape(b["unit"])}</div>' if b.get("unit") else "") + rows
            + (f'<div class="v-src">{L("src")}: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def before_after_html(b: dict) -> str:
    def side(x):
        return (f'<div><div class="v-lab">{html.escape(x["label"])}</div><div class="v-val">{html.escape(x["value"])}</div>'
                f'<div class="v-cap">{html.escape(x["caption"])}</div></div>')
    return (f'<figure class="v-ba"><div class="v-k">{L("ba")}</div><div class="v-t">{html.escape(b["title"])}</div>'
            f'<div class="v-row">{side(b["before"])}<div class="v-arr">→</div>{side(b["after"])}</div>'
            + (f'<div class="v-src">{L("src")}: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def img_html(src: str, alt: str, w: int, h: int, cls: str) -> str:
    return f'<figure class="{cls}"><img src="{src}" alt="{html.escape(alt)}" width="{w}" height="{h}" loading="lazy"></figure>'


POSITION_PLATE = ('<div class="v-pos"><span>ПОЗИЦИЯ</span>Мнение редакции</div>')

CSS_SITE = """
.v-map,.v-chart,.v-week{margin:0 0 26px}.v-map img,.v-chart img,.v-week img{width:100%;height:auto;display:block;border:1px solid var(--rule)}
.v-chart{margin:28px 0}
.v-fig{margin:28px 0;padding:20px 0 20px 22px;border-left:4px solid #2F343C}
.v-num{font:600 52px/1 Commissioner,sans-serif;letter-spacing:-.02em}
.v-fig figcaption{font-size:16px;color:var(--cold);margin-top:10px;max-width:560px}
.v-fig figcaption span,.v-src{display:block;font:11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-top:8px}
.v-ba{margin:28px 0;padding:20px 22px;background:#0E1114;border-top:3px solid var(--ink)}
.v-k,.v-lab{font:11px "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}.v-k{color:var(--cold)}
.v-t{font:600 19px/1.25 Commissioner,sans-serif;margin:8px 0 16px}
.v-row{display:grid;grid-template-columns:1fr 36px 1fr;align-items:center;gap:10px}
.v-val{font:600 36px/1.1 Commissioner,sans-serif;margin:4px 0}.v-cap{font-size:14px;color:var(--cold)}
.v-arr{font:26px "IBM Plex Mono",monospace;color:var(--mute);text-align:center}
.v-pos{border:1px solid var(--rule);border-left:3px solid var(--ink);color:var(--cold);padding:10px 14px;margin:0 0 22px;font-size:14px;display:flex;align-items:center;gap:14px}
.v-pos span{font:600 12px "IBM Plex Mono",monospace;letter-spacing:.2em;color:var(--ink)}
.v-dim .v-val{color:var(--mute)}
.v-flow,.v-cmp{margin:28px 0;padding:20px 22px;background:#0E1114;border-top:3px solid var(--ink)}
.v-chain{display:flex;flex-direction:column;align-items:flex-start;margin-top:4px}
.v-node{border:1px solid #2F343C;padding:11px 14px;font:600 15px/1.25 Commissioner,sans-serif;width:100%;max-width:380px}
.v-edge{position:relative;padding:8px 0 12px 204px;width:100%;max-width:380px;min-height:58px;display:flex;flex-direction:column;justify-content:center}
.v-edge::before{content:"";position:absolute;left:190px;top:0;bottom:8px;border-left:1px solid #6B6F72}
.v-edge::after{content:"";position:absolute;left:185px;bottom:0;border:5px solid transparent;border-top:7px solid #6B6F72}
.v-edge span{font:600 15px "IBM Plex Mono",monospace}.v-edge i{font:normal 12px Onest,sans-serif;color:var(--mute)}
.v-u{font-size:14px;color:var(--cold);margin:-8px 0 14px}
.v-bar{display:grid;grid-template-columns:150px 1fr 80px;align-items:center;gap:12px;margin:8px 0;font-size:14px;color:var(--cold)}
.v-bar div{height:14px}.v-bar b{display:block;height:100%;background:#2F343C}.v-bar.on b{background:var(--ink)}.v-bar.on{color:var(--ink)}
.v-bar em{font:normal 13px "IBM Plex Mono",monospace;text-align:right}
@media(max-width:560px){.v-edge{padding-left:calc(50% + 14px)}.v-edge::before{left:50%}.v-edge::after{left:calc(50% - 5px)}.v-bar{grid-template-columns:100px 1fr 64px}}
.v-doc{margin:28px 0;padding:22px 24px;border:1px solid var(--rule);position:relative}
.v-doc blockquote{margin:10px 0 0;font:400 20px/1.45 Commissioner,sans-serif;color:var(--ink)}
.v-doc blockquote::before{content:"«"}.v-doc blockquote::after{content:"»"}
.v-doc figcaption{font:11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-top:12px}
.v-tl{margin:28px 0}.v-tl ol{list-style:none;padding:0;margin:12px 0 0;border-left:1px solid #2F343C}
.v-tl li{position:relative;padding:0 0 14px 22px;display:grid;grid-template-columns:120px 1fr;gap:10px;font-size:15px}
.v-tl li::before{content:"";position:absolute;left:-4px;top:7px;width:7px;height:7px;background:var(--ink)}
.v-tl time{font:13px "IBM Plex Mono",monospace;color:var(--cold)}
.v-share{margin:28px 0;display:grid;grid-template-columns:150px 1fr;gap:24px;align-items:center}
.v-grid{display:grid;grid-template-columns:repeat(10,1fr);gap:3px}.v-grid i{aspect-ratio:1;background:#1C2026}.v-grid i.on{background:var(--ink)}
.v-share figcaption{font-size:16px;color:var(--cold);margin-top:8px}
.v-share figcaption span{display:block;font:11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-top:8px}
.v-pull{margin:30px 0;padding:6px 0 6px 22px;border-left:4px solid var(--ink)}
.v-pull blockquote{margin:0;font:600 22px/1.35 Commissioner,sans-serif;color:var(--ink)}
.v-players{margin:28px 0;padding:20px 22px;background:#0E1114;border-top:3px solid var(--ink)}
.v-cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}.v-cols>div+div{border-left:1px solid var(--rule);padding-left:24px}
.v-pl{margin-top:12px}.v-pl b{display:block;font:600 15px Commissioner,sans-serif}.v-pl span{display:block;font-size:14px;line-height:1.4;color:var(--cold);margin-top:2px}
@media(max-width:560px){.v-cols{grid-template-columns:1fr}.v-cols>div+div{border-left:0;padding-left:0;border-top:1px solid var(--rule);padding-top:12px}}
/* длинное чтение */
.longread>p:first-of-type::first-letter{float:left;font:600 64px/0.86 Commissioner,sans-serif;margin:6px 10px 0 0;color:var(--ink)}
.longread>p.last::after{content:"";display:inline-block;width:.6em;height:.6em;background:var(--ink);margin-left:.35em;vertical-align:baseline}
#rp{position:fixed;left:0;top:0;height:2px;background:var(--ink);width:0;z-index:20}
/* неделя */
.wk-ev{display:grid;grid-template-columns:44px 1fr;gap:12px;padding:16px 0;border-bottom:1px solid var(--rule)}
.wk-ev .n{font:600 15px "IBM Plex Mono",monospace;color:var(--cold);padding-top:3px}.wk-ev p{margin:0}
.wk-sec{font:12px "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:36px 0 10px}
.wk-num{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--rule);border:1px solid var(--rule)}
.wk-num a{background:var(--bg);padding:14px 16px;text-decoration:none}.wk-num a:hover{background:#12161b}
.wk-num .k{font:11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.wk-num .v{font:600 22px Commissioner,sans-serif;margin-top:6px}.wk-num .d{font:13px "IBM Plex Mono",monospace;color:var(--cold);margin-top:2px}
.wk-list a{display:block;padding:10px 0;border-bottom:1px solid var(--rule);text-decoration:none;font-weight:600}
.wk-list a span{display:block;font:11px "IBM Plex Mono",monospace;color:var(--mute);font-weight:400;margin-bottom:3px}
@media(max-width:560px){.v-num{font-size:40px}.v-val{font-size:28px}.v-share{grid-template-columns:110px 1fr}.v-tl li{grid-template-columns:1fr;gap:2px}.wk-num{grid-template-columns:1fr}}
"""


def build(e: dict, img_dir: Path, slug: str) -> dict:
    """Готовит картинки (если их ещё нет) и возвращает HTML-блоки: {"top": ..., "after": {номер абзаца: html}}."""
    out = {"top": "", "after": {}}
    v = e.get("visuals") or {}
    kind = e.get("kind")
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        place = B.place_ok(v.get("place")) if not (kind != "analysis" and v.get("blocks")) else None  # в коротком тексте один элемент: блок важнее карты
        # место от автора текста — для обложки; на сайте карта, только если место — суть истории
        if place and kind in ("note", "analysis", "position"):
            place["region"] = (v.get("place") or {}).get("region") or ""
            place["title"] = (v.get("place") or {}).get("title") or ""
            f = img_dir / f"{slug}-map-r{RENDER}.jpg"
            if not f.exists():
                map_strip(place, f)
            alt = f"{place.get('name', '')}" + (f", {place['region']}" if place.get("region") else "") + " — место события на карте"
            out["top"] = img_html(f"/img/v/{f.name}", alt, 720, 260, "v-map")
        if kind == "week" and not any(e.get("places") or []) and any(v.get("places") or []):
            e = {**e, "places": v["places"]}
        if kind == "week" and any(e.get("places") or []):
            f = img_dir / f"{slug}-week-r{RENDER}.jpg"
            if not f.exists():
                week_map(e["places"], f)
            out["top"] = img_html(f"/img/v/{f.name}", "Карта событий недели", 720, 320, "v-week")
        if True:
            for b in v.get("blocks") or []:
                t, para = b.get("type"), b.get("para")
                if t == "figure":
                    h = figure_html(b)
                    if b.get("para", 0) < 0:  # цифра из первого абзаца — выносом над текстом
                        out["top"] = h + out["top"] if kind == "analysis" else h
                        continue
                elif t == "doc":
                    h = doc_html(b)
                elif t == "pull":
                    h = pull_html(b)
                elif t == "players":
                    h = players_html(b)
                elif t == "timeline":
                    h = timeline_html(b)
                elif t == "share":
                    h = share_html(b)
                elif t in ("vs", "before_after"):
                    h = pair_html(b)
                elif t == "flow":
                    h = flow_html(b)
                elif t == "compare":
                    h = compare_html(b)
                elif t == "chart":
                    f = img_dir / f"{slug}-chart-{para}-r{RENDER}.jpg"
                    if not f.exists():
                        chart_img(b, f)
                    alt = f'{b["title"]}. {b["unit"]}: ' + ", ".join(f"{a} — {x:g}" for a, x in b["points"])
                    h = img_html(f"/img/v/{f.name}", alt, 720, 380, "v-chart")
                else:
                    continue
                out["after"][para] = h
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: {slug} не собран ({ex})")
    return out


def to_json(v: dict) -> str:
    return json.dumps(v, ensure_ascii=False)


def week_page(e: dict, posts: list[dict], site: Path, inline) -> tuple[str, str]:
    """«Неделя» на сайте как номер: события с номерами, как на карте; неделя в цифрах; что мы написали за неделю.
    Возвращает (события, хвост страницы)."""
    from datetime import datetime, timedelta
    evs = "".join(f'<div class="wk-ev"><div class="n">{i + 1:02d}</div><p>{inline(p)}</p></div>'
                  for i, p in enumerate(e.get("paragraphs") or []))
    tail = ""
    nums = []
    for slug, name in (("klyuchevaya-stavka", "Ставка ЦБ"), ("kurs-dollara", "Доллар"), ("kurs-yuanya", "Юань"),
                       ("indeks-mosbirzhi", "IMOEX"), ("zoloto", "Золото, г"), ("kurs-evro", "Евро")):
        f = site / "data" / f"{slug}.html"
        if not f.exists():
            continue
        t = f.read_text(encoding="utf-8")
        v = re.search(r'class="lead">.*?<b>([^<]+)</b>', t, re.S)
        w = re.search(r"<td>За неделю</td><td>[^<]*</td><td>([^<]+)</td>", t)
        if v:
            nums.append((slug, name, v.group(1), ("за неделю " + w.group(1)) if w else ("без изменений" if slug == "klyuchevaya-stavka" else "")))
    if nums:
        tail += '<div class="wk-sec">Неделя в цифрах</div><div class="wk-num">' + "".join(
            f'<a href="/data/{sl}"><div class="k">{html.escape(n)}</div><div class="v">{html.escape(v)}</div><div class="d">{html.escape(d)}</div></a>'
            for sl, n, v, d in nums[:6]) + "</div>"
    end = datetime.fromisoformat(e["time_iso"])
    start = end - timedelta(days=7)
    mine = [p for p in posts if p.get("kind") in ("analysis", "position") and p.get("slug")
            and start <= datetime.fromisoformat(p["time_iso"]) <= end]
    for kind, name in (("analysis", "Разборы недели"), ("position", "Позиция редакции за неделю")):
        ps = [p for p in mine if p["kind"] == kind][:7]
        if ps:
            tail += f'<div class="wk-sec">{name}</div><div class="wk-list">' + "".join(
                f'<a href="/p/{p["slug"]}"><span>{datetime.fromisoformat(p["time_iso"]):%d.%m}</span>{html.escape(p["headline"])}</a>'
                for p in ps) + "</div>"
    return evs, tail


# ---------- Дзен: те же блоки в светлой теме, картинками ----------

def _light_css() -> str:
    fd = B.FONTS_DIR
    return (f"@font-face{{font-family:'Commissioner';src:url('{(fd / 'Commissioner.ttf').as_uri()}') format('truetype');font-weight:100 900}}"
            f"@font-face{{font-family:'IBM Plex Mono';src:url('{(fd / 'IBMPlexMono-Regular.ttf').as_uri()}') format('truetype');font-weight:400}}"
            f"@font-face{{font-family:'IBM Plex Mono';src:url('{(fd / 'IBMPlexMono-SemiBold.ttf').as_uri()}') format('truetype');font-weight:600}}"
            f"@font-face{{font-family:'Onest';src:url('{(fd / 'Onest.ttf').as_uri()}') format('truetype');font-weight:100 900}}"
            ":root{--bg:#FBFAF7;--ink:#0B0D10;--mute:#6B6F72;--cold:#3A3F45;--rule:#DDD8CF}"
            "*{box-sizing:border-box}body{margin:0;background:#FBFAF7;color:#0B0D10;font:17px/1.5 Commissioner,sans-serif}"
            "#w{width:680px;padding:2px 20px;display:flow-root}figure{margin:12px 0!important}"
            + CSS_SITE +
            ".v-ba,.v-flow,.v-cmp,.v-players{background:#F2EFE8}.v-grid i{background:#E3DED4}.v-grid i.on{background:#0B0D10}"
            ".v-node{border-color:#C9C4BA;background:#FBFAF7}.v-bar b{background:#C9C4BA}.v-bar.on b{background:#0B0D10}"
            ".v-edge::before{border-left-color:#8A8E92}.v-edge::after{border-top-color:#8A8E92}.v-doc{border-color:#DDD8CF}")


def _shoot_el(block_html: str, path: Path) -> Path:
    from playwright.sync_api import sync_playwright
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(f'<!doctype html><html><head><meta charset="utf-8"><style>{_light_css()}</style></head><body><div id="w">{block_html}</div></body></html>')
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 720, "height": 400}, device_scale_factor=2)
            pg.goto(tmp.as_uri(), wait_until="load")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(200)
            png = path.with_suffix(".png")
            pg.locator("#w").screenshot(path=str(png))
            b.close()
        from PIL import Image
        Image.open(png).convert("RGB").save(path, "JPEG", quality=88, optimize=True, progressive=True)
        png.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def zen_figures(e: dict, img_dir: Path, slug: str, abs_url) -> dict[int, str]:
    """Блоки статьи для ленты Дзена: светлые картинки с подписью, по номеру абзаца. Карты в Дзен не идут:
    там есть обложка."""
    out: dict[int, str] = {}
    v = e.get("visuals") or {}
    render = {"figure": figure_html, "doc": doc_html, "timeline": timeline_html, "share": share_html, "vs": pair_html, "before_after": pair_html,
              "flow": flow_html, "compare": compare_html, "pull": pull_html, "players": players_html}
    kind = e.get("kind")
    try:  # верх статьи — как на сайте: карта места или карта недели, светлые
        place = B.place_ok(v.get("place"))
        if place and kind in ("note", "analysis", "position") and not (kind != "analysis" and v.get("blocks")):
            place.update(region=(v.get("place") or {}).get("region") or "", title=(v.get("place") or {}).get("title") or "")
            f = img_dir / f"{slug}-map-zen-r{RENDER}.jpg"
            if not f.exists():
                map_strip(place, f, light=True)
            out[-1] = f'<figure><img src="{abs_url("img/v/" + f.name)}"><figcaption>{html.escape(place.get("title") or place["name"])}</figcaption></figure>'
        places = e.get("places") or v.get("places") or []
        if kind == "week" and any(places):
            f = img_dir / f"{slug}-week-zen-r{RENDER}.jpg"
            if not f.exists():
                week_map(places, f, light=True)
            out[-1] = f'<figure><img src="{abs_url("img/v/" + f.name)}"><figcaption>События недели на карте</figcaption></figure>'
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: карта для Дзена {slug} не снята ({ex})")
    for b in v.get("blocks") or []:
        t, para = b.get("type"), b.get("para")
        if t not in render and t != "chart":
            continue
        f = img_dir / f"{slug}-{t}-{para}-zen-r{RENDER}.jpg"
        try:
            if not f.exists():
                chart_img(b, f, light=True) if t == "chart" else _shoot_el(render[t](b), f)
        except Exception as ex:  # noqa: BLE001
            log(f"визуал: картинка для Дзена {f.name} не снята ({ex})")
            continue
        cap = b.get("title") or b.get("caption") or ""
        out[para] = (f'<figure><img src="{abs_url("img/v/" + f.name)}">'
                     + (f"<figcaption>{html.escape(cap)}</figcaption>" if cap else "") + "</figure>")
    return out


# ---------- английская версия разборов ----------

EN_SYSTEM = """Translate every human-readable string in this JSON from Russian into English, The Economist house style:
short, precise, British spelling. Keep keys, numbers, types and structure exactly. Values with numbers: keep the number,
translate the unit («9,4 трлн ₽» → «₽9.4trn», «38%» → «38%», «24 октября» → «October 24th»).
Do not translate keys like "type", "para", "kind", "lat", "lon", "scale". Return only the JSON."""


def translate(v: dict) -> dict:
    """Блоки и карта разбора по-английски — для страниц /en/. Один лёгкий запрос на текст."""
    from .compose import extract_json
    from .gemini import LIGHT_CHAIN, ask
    src = {"blocks": v.get("blocks") or [], "place": v.get("place")}
    raw, _ = ask(LIGHT_CHAIN, EN_SYSTEM, json.dumps(src, ensure_ascii=False), temperature=0.2)
    d = extract_json(raw)
    out = {"blocks": [], "place": None}
    for a, b in zip(src["blocks"], d.get("blocks") or []):
        if isinstance(b, dict) and b.get("type") == a.get("type"):
            b["para"] = a["para"]
            if a.get("points"):
                b["points"] = [(str(x[0]), float(y)) for x, (_, y) in zip(b.get("points") or a["points"], a["points"])]
                b["highlight"] = a.get("highlight", 0)
            out["blocks"].append(b)
    if src["place"] and isinstance(d.get("place"), dict):
        out["place"] = {**src["place"], **{k: d["place"].get(k) or src["place"].get(k) for k in ("title", "region")}}
    return out


def build_lang(e: dict, en_vis: dict, img_dir: Path, slug: str) -> dict:
    """То же, что build, но английскими подписями и с отдельными картинками."""
    global LANG
    LANG = "en"
    try:
        return build({**e, "visuals": en_vis}, img_dir, slug + "-en")
    finally:
        LANG = "ru"
