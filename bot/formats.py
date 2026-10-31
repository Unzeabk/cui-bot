"""Рубрики-форматы, которые раньше были ручными: Показатели, Позиция, Разбор, Неделя.

Расписание (по Москве): пн 10:07 — Показатели, ср 13:07 — Позиция, пт 13:07 — Разбор, сб 12:07 — Неделя.
Запуск: FORMAT=indicators|position|analysis|week (или auto — по дню недели) python -m bot.formats
"""

import html
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

from .compose import BANNED, LINK_RE, _inline, _norm, dedupe_links, extract_json, links_in, plain, visible_length
from .main import CUSTOM_EMOJI, OUT, SITE, TZ, fresh_items, load_state, log, save_state  # noqa: F401
from .prompts import WRITER_SYSTEM, _recent_txt

FORMATS = {
    "indicators": {"label": "ПОКАЗАТЕЛИ", "weekday": 0, "hours": 168},
    "position": {"label": "ПОЗИЦИЯ", "weekday": 2, "hours": 72},
    "analysis": {"label": "РАЗБОР", "weekday": 4, "hours": 168},
    "week": {"label": "НЕДЕЛЯ", "weekday": 5, "hours": 168},
}

# Общие правила голоса и фактов берём из промпта заметок, формат описываем отдельно
_VOICE = WRITER_SYSTEM[: WRITER_SYSTEM.index("ФОРМАТ ЗАМЕТКИ")]
_LANG = WRITER_SYSTEM[WRITER_SYSTEM.index("ЯЗЫК:"): WRITER_SYSTEM.index("ОБЛОЖКА:")]

PICK = {
    "indicators": "Выбери до 8 новостей недели с цифрами, которые смотрит наш читатель, в таком порядке приоритета: ключевая ставка ЦБ и ожидания по ней; инфляция в России; нефть (Brent, Urals, скидка); индекс Мосбиржи или крупные движения акций; бюджет и налоги; долги и неплатежи компаний; переводы и расчёты за рубежом; зарплаты и рынок труда. Экзотику без связи с Россией и деньгами нашего читателя (бюджет Нигерии, налог в Италии) не бери. Разные цифры, без повторов.",
    "position": "Выбери одно конкретное решение власти, регулятора или крупного игрока за последние три дня, о котором редакции стоит высказать мнение: экономическое решение с понятными последствиями для денег, активов и планов нашего читателя. Плюс до трёх новостей про то же решение.",
    "analysis": "Выбери главную тему недели для большого разбора: тренд, сдвиг в деньгах или власти, о котором вышло несколько материалов с цифрами. Главная новость и до пяти дополнительных про тот же сюжет с разных сторон.",
    "week": "Выбери 6 главных событий недели для нашего читателя из разных областей: Россия, деньги, регионы, мир, бизнес, релоканты. По одной новости на событие, без повторов.",
}

PICK_SYSTEM = """Ты — выпускающий редактор Cui Bono?, аналитического издания об экономике и власти. Читатель: инвесторы, релоканты, топ-менеджеры, криптаны, крупный бизнес.
{task}
Ответ — только JSON-объект: {{"main": 12, "extra": [3, 40, 7]}}. Для списков (показатели, неделя) первое событие — в "main", остальные — в "extra"."""

WRITE = {
    "indicators": """ФОРМАТ: «Показатели» — 5–7 цифр недели, которые смотрит наш читатель.
- Флай: знакомое русское выражение, слегка погнутое под неделю. 2–5 слов.
- Заголовок: 6–9 слов, про главное движение недели в цифрах. Подзаголовок не отвечает на заголовок.
- Пункты: 5–7, в порядке важности для читателя. Первым — курс доллара или юаня по ЦБ из материала «Официальные курсы ЦБ», затем ставка, инфляция, нефть, рынок — что есть в материалах.
- "value" — сама цифра коротко («14%», «80,56 ₽», «$63,4»); "label" — что это, со строчной буквы, до 5 слов («курс доллара ЦБ»); "comment" — одна сухая фраза с заглавной: насколько изменилось за неделю и что это значит для денег читателя, со ссылкой [текст](url). Не пиши «вырос на две копейки», если это ничего не значит: тогда лучше другая цифра.
- Без оценок и прогнозов. Весь пост до 950 знаков.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "items": [{"value": "...", "label": "...", "comment": "..."}], "topic_key": "pokazateli-YYYY-WW"}""",
    "position": """ФОРМАТ: «Позиция» — мнение редакции о конкретном решении или игроке. Это единственная рубрика, где у редакции есть позиция.
- Позиция про экономику и деньги: что решение даст и кому, чего стоит, что следовало бы сделать иначе. Аргументы — только факты из материалов. Без политических лозунгов, без оценок войны, без призывов к действиям, без оскорблений и обвинений людей.
- Голос тот же: холодно, точно, без восклицаний.
- Флай: знакомое выражение, погнутое под тему. Заголовок 6–9 слов в форме вердикта: что стоит сделать или чего не стоит («Изъятое нужно продавать, пока оно ещё что-то стоит»). Подзаголовок — второй слой, не повтор.
- Тело: 3 коротких абзаца: что решили и почему это кажется разумным; цифра, о которую это ломается; что было бы разумнее. Последняя фраза — только если хорошая, без шаблонных афоризмов.
- Весь пост до 950 знаков.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "paragraphs": ["..."], "topic_key": "..."}""",
    "analysis": """ФОРМАТ: «Разбор» — большой материал недели, 1200–1800 слов. Публикуется отдельной статьёй, в канал идёт анонс.
- Драматургия без скелета: вход через предмет, человека или документ; мысль не позже второго-третьего абзаца; очевидное объяснение ломается об одну цифру; второй слой даёт вторая оптика; параллель, которую узнает русский читатель; короткий финал, возвращающийся к началу. Подзаголовков внутри нет, ход держится на фразах-связках.
- Оптики выбери, как в заметке: главную и вторую из другого семейства. В тексте не называй.
- Флай, заголовок 6–9 слов, подзаголовок не отвечает на заголовок.
- "lead" — анонс для канала: 2–3 предложения, которые открывают загадку и не отвечают на неё, до 450 знаков.
- "paragraphs" — сама статья, 12–25 абзацев, ссылки [текст](url) на цифрах.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "lead": "...", "paragraphs": ["..."], "optics": ["...", "..."], "topic_key": "..."}""",
    "week": """ФОРМАТ: «Неделя» — главное за неделю сжатыми абзацами, без оценок.
- Флай: знакомое выражение, погнутое под неделю. Заголовок 6–9 слов — что связывает события недели (без оценок). Подзаголовок — второй слой.
- "items": 5–6 событий, каждое — 1–2 фразы: что произошло и одна цифра, со ссылкой [текст](url). Сухо, по-русски, без оценок и без «на этой неделе».
- Весь пост до 950 знаков.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "items": ["..."], "topic_key": "nedelya-YYYY-WW"}""",
}


def system_for(fmt: str) -> str:
    return _VOICE + WRITE[fmt] + "\n\n" + _LANG + """
ФАКТЫ: пиши только по материалам, которые дал пользователь. Каждый факт и цифра — из них. Ссылку ставь на цифру в формате [текст](url), URL — ровно тот, что указан у материала, каждый URL один раз. Не уверен — убери."""


# --- материалы ---------------------------------------------------------------

def cbr_rates() -> dict | None:
    """Официальные курсы ЦБ сегодня и неделю назад (USD, EUR, CNY)."""
    import xml.etree.ElementTree as ET

    def day(d: datetime) -> dict:
        r = requests.get("https://www.cbr.ru/scripts/XML_daily.asp", params={"date_req": d.strftime("%d/%m/%Y")}, timeout=20)
        root = ET.fromstring(r.content)
        out = {}
        for v in root.findall("Valute"):
            code = v.findtext("CharCode")
            if code in ("USD", "EUR", "CNY"):
                out[code] = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
        return out

    try:
        now = datetime.now(TZ)
        a, b = day(now), day(now - timedelta(days=7))
        text = "Официальные курсы ЦБ: " + "; ".join(
            f"{c}: {a[c]:.2f} ₽ (неделю назад {b[c]:.2f} ₽)" for c in ("USD", "EUR", "CNY") if c in a and c in b
        )
        return {"title": "Официальные курсы ЦБ", "link": "https://www.cbr.ru/currency_base/daily/", "source": "Банк России", "text": text, "summary": text, "ts": time.time()}
    except Exception as e:  # noqa: BLE001
        log(f"курсы ЦБ недоступны: {e}")
        return None


def gather(fmt: str, recent: list[dict]) -> list[dict]:
    from .gemini import LIGHT_CHAIN, ask
    from .news import collect, fetch_text
    from .prompts import topic_user_message

    items = collect(hours=FORMATS[fmt]["hours"], limit=320)
    if fmt == "position":
        items = fresh_items(items, recent)  # позиция — не про то, о чём уже писали заметкой
    if not items:
        return []
    text, model = ask(LIGHT_CHAIN, PICK_SYSTEM.format(task=PICK[fmt]), topic_user_message(items, recent), temperature=0.3)
    pick = extract_json(text)
    idx = [pick.get("main")] + list(pick.get("extra") or [])
    idx = [i for i in dict.fromkeys(idx) if isinstance(i, int) and 0 <= i < len(items)]
    cap = {"indicators": 8, "position": 4, "analysis": 6, "week": 6}[fmt]
    per = 3500 if fmt in ("indicators", "week") else 7000
    mats = [{**items[i], "text": fetch_text(items[i]["link"], fallback=items[i]["summary"], max_chars=per)} for i in idx[:cap]]
    if fmt == "indicators":
        r = cbr_rates()
        if r:
            mats.insert(0, r)
    log(f"материалы ({model}): " + " | ".join(m["title"][:60] for m in mats))
    return mats


def user_message(fmt: str, recent: list[dict], mats: list[dict]) -> str:
    blocks = [f"=== Материал {i + 1} ===\nИсточник: {m['source']}\nURL: {m['link']}\nЗаголовок: {m['title']}\nТекст:\n{m['text']}" for i, m in enumerate(mats)]
    week = ""
    if fmt == "week":
        week = "Что мы сами писали за неделю (не обязательно повторять, но это фон):\n" + _recent_txt(recent[-40:]) + "\n\n"
    return f"Сегодня {datetime.now(TZ).strftime('%d.%m.%Y')}. Рубрика «{FORMATS[fmt]['label'].capitalize()}».\n\n{week}" + "\n\n".join(blocks)


# --- проверка ----------------------------------------------------------------

def _texts(fmt: str, d: dict) -> list[str]:
    parts = [d.get("fly", ""), d.get("headline", ""), d.get("subhead", ""), d.get("lead", "")]
    if fmt == "indicators":
        parts += [f"{i.get('value', '')} {i.get('label', '')} {i.get('comment', '')}" for i in d.get("items", [])]
    elif fmt == "week":
        parts += list(d.get("items", []))
    else:
        parts += list(d.get("paragraphs", []))
    return parts


def validate_format(fmt: str, d: dict, allowed: set[str]) -> list[str]:
    probs = []
    for k in ("fly", "headline", "subhead"):
        if not d.get(k):
            probs.append(f"нет поля {k}")
    words = len(plain(d.get("headline", "")).split())
    if not 5 <= words <= 9:
        probs.append(f"в заголовке {words} слов, нужно 6–9")
    body = " ".join(plain(t) for t in _texts(fmt, d))
    for rx, label in BANNED:
        if rx.search(body):
            probs.append(f"запрещённый оборот: {label}")
    allowed_n = {_norm(u) for u in allowed}
    links = [u for t in _texts(fmt, d) for _, u in LINK_RE.findall(t or "")]
    if not links:
        probs.append("нет ни одной ссылки на источник")
    for u in links:
        if _norm(u) not in allowed_n:
            probs.append(f"ссылка не из материалов: {u}")
    if fmt == "analysis":
        n = sum(len(plain(p).split()) for p in d.get("paragraphs", []))
        if n < 900:
            probs.append(f"в разборе {n} слов, нужно 1200–1800")
    if not probs:
        cap = visible_length(caption(fmt, d, "https://telegra.ph/x"))
        if cap > 1000:
            probs.append(f"пост длиной {cap} знаков, лимит 1000: сократи")
    return probs


def _dedupe(fmt: str, d: dict) -> dict:
    """Каждый источник — одна ссылка: повторы превращаются в текст."""
    if fmt == "indicators":
        tmp = {"headline": d.get("headline"), "subhead": d.get("subhead"), "paragraphs": [i.get("comment", "") for i in d.get("items", [])]}
        tmp = dedupe_links(tmp)
        for i, c in zip(d.get("items", []), tmp["paragraphs"]):
            i["comment"] = c
    elif fmt == "week":
        tmp = dedupe_links({"headline": d.get("headline"), "subhead": d.get("subhead"), "paragraphs": d.get("items", [])})
        d["items"] = tmp["paragraphs"]
    elif fmt == "position":
        d.update({k: v for k, v in dedupe_links(dict(d)).items() if k in ("headline", "subhead", "paragraphs")})
    else:  # в длинном разборе ссылку на один источник можно ставить в разных местах
        d["headline"], d["subhead"] = plain(d.get("headline", "")), plain(d.get("subhead", ""))
    return d


CHECK = """Ты — фактчекер издания Cui Bono?. Тебе дают текст рубрики «{label}» в JSON и материалы.
1. Сверь КАЖДЫЙ факт и КАЖДУЮ цифру с материалами: число, единицы, дату, кто что сделал. Чего нет в материалах — не подтверждено.
2. Строже всего — заголовок и подзаголовок: они не должны утверждать больше, чем в материалах.
3. Обвинений людей сверх материалов быть не должно.{extra}
Решение: "ok" — всё подтверждено; "fix" — верни исправленный JSON целиком в том же виде, меняя только ошибочное; "reject" — главное не подтверждается.
Ответ — только JSON: {{"verdict": "ok" | "fix" | "reject", "problems": ["..."], "fixed": null | {{...}}}}"""


def check(fmt: str, d: dict, mats: list[dict]) -> dict:
    from .gemini import CHECK_CHAIN, ask

    extra = "\n4. Это мнение редакции: мнение допустимо, но факты под ним должны быть точными, без политических лозунгов, оценок войны и призывов." if fmt == "position" else ""
    sysmsg = CHECK.format(label=FORMATS[fmt]["label"].capitalize(), extra=extra)
    m = "\n\n".join(f"=== {x['link']} ===\n{x['text']}" for x in mats)
    text, model = ask(CHECK_CHAIN, sysmsg, f"Текст:\n{json.dumps(d, ensure_ascii=False, indent=1)}\n\nМатериалы:\n{m}", temperature=0.1)
    log(f"фактчек: {model}")
    return extract_json(text)


# --- вёрстка и публикация ----------------------------------------------------

def _cap(t: str) -> str:
    t = (t or "").strip()
    return t[:1].upper() + t[1:]


def _low(t: str) -> str:
    t = (t or "").strip().rstrip(".")
    return t[:1].lower() + t[1:] if t[1:2].islower() or len(t) < 2 else t


def caption(fmt: str, d: dict, url: str = "") -> str:
    label = FORMATS[fmt]["label"]
    head = f"<code>{label}</code> · <i>{_inline(_cap(d['fly']))}</i>\n\n<b>{_inline(plain(d['headline']))}</b>\n\n<i>{_inline(plain(d['subhead']))}</i>\n\n"
    if fmt == "indicators":
        body = "\n\n".join(
            f"<b>{html.escape(i.get('value', ''))}</b> · {html.escape(_low(i.get('label', '')))}\n{_inline(_cap(i.get('comment', '')))}"
            for i in d.get("items", [])
        )
    elif fmt == "week":
        body = "\n\n".join(_inline(_cap(t)) for t in d.get("items", []))
    elif fmt == "analysis":
        body = f"{_inline(d.get('lead', ''))}\n\n<a href=\"{html.escape(url, quote=True)}\">Читать разбор</a>"
    else:
        body = "\n\n".join(_inline(p) for p in d.get("paragraphs", []))
    return head + body


def telegraph(d: dict) -> str:
    """Публикует разбор на telegra.ph и возвращает ссылку."""
    acc = requests.post("https://api.telegra.ph/createAccount",
                        data={"short_name": "CuiBono", "author_name": "Cui Bono?"}, timeout=30).json()
    token = acc["result"]["access_token"]

    def nodes(p: str) -> list:
        out, pos = [], 0
        for m in LINK_RE.finditer(p):
            if m.start() > pos:
                out.append(p[pos:m.start()])
            out.append({"tag": "a", "attrs": {"href": m.group(2)}, "children": [m.group(1)]})
            pos = m.end()
        if pos < len(p):
            out.append(p[pos:])
        return out

    content = [{"tag": "p", "children": [{"tag": "i", "children": [plain(d["subhead"])]}]}]
    content += [{"tag": "p", "children": nodes(p)} for p in d["paragraphs"]]
    r = requests.post("https://api.telegra.ph/createPage", data={
        "access_token": token, "title": plain(d["headline"])[:256], "author_name": "Cui Bono?",
        "content": json.dumps(content, ensure_ascii=False), "return_content": "false",
    }, timeout=60).json()
    if not r.get("ok"):
        raise RuntimeError(f"Telegraph отказал: {r.get('error')}")
    return r["result"]["url"]


def produce(fmt: str, recent: list[dict]) -> dict | None:
    from .gemini import WRITER_CHAIN, ask

    mats = gather(fmt, recent)
    if not mats:
        log("нет материалов")
        return None
    allowed = {m["link"] for m in mats}
    feedback = ""
    for attempt in (1, 2, 3):
        user = user_message(fmt, recent, mats) + (f"\n\nПрошлая версия не прошла проверку. Исправь:\n{feedback}" if feedback else "")
        try:
            text, model = ask(WRITER_CHAIN, system_for(fmt), user, temperature=0.8)
            d = _dedupe(fmt, extract_json(text))
        except Exception as e:  # noqa: BLE001
            feedback = f"Ответ не разобрался: {e}"
            log(feedback)
            continue
        log(f"текст написала {model}")
        probs = validate_format(fmt, d, allowed)
        if probs:
            feedback = "\n".join(f"- {p}" for p in probs)
            log("валидация не прошла:\n" + feedback)
            continue
        try:
            v = check(fmt, d, mats)
        except Exception as e:  # noqa: BLE001
            log(f"фактчек недоступен ({e}) — без проверки не публикуем")
            return None
        log("вердикт:", v.get("verdict"), "|", "; ".join(v.get("problems") or []))
        if v.get("verdict") == "ok":
            d["_sources"] = list(allowed)
            return d
        if v.get("verdict") == "fix" and v.get("fixed"):
            f = _dedupe(fmt, v["fixed"])
            if not validate_format(fmt, f, allowed):
                f["_sources"] = list(allowed)
                return f
        feedback = "Фактчек нашёл ошибки:\n" + "\n".join(f"- {p}" for p in (v.get("problems") or []))
    return None


def publish(fmt: str, d: dict, dry: bool) -> dict:
    from .render import render_cover
    from .telegram import send_photo

    url = ""
    if fmt == "analysis":
        url = "https://telegra.ph/(пробный-запуск)" if dry else telegraph(d)
    cover_note = {
        "rubric": "world", "fly": d["fly"], "headline": plain(d["headline"]),
        "_label": FORMATS[fmt]["label"], "_mark": fmt if fmt != "position" else "position",
        "_foot": FORMATS[fmt]["label"] if fmt != "position" else "",
        "cover": {"type": "position" if fmt == "position" else "ivory"},
    }
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    OUT.mkdir(exist_ok=True)
    cover = render_cover(cover_note, OUT / f"cover-{fmt}-{stamp}.png", site=SITE)
    cap = caption(fmt, d, url)
    (OUT / f"{fmt}-{stamp}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"caption-{fmt}-{stamp}.html").write_text(cap, encoding="utf-8")
    log(f"{FORMATS[fmt]['label']}: подпись {visible_length(cap)} знаков" + (f", статья {url}" if url else ""))
    if dry:
        log("DRY_RUN: в канал не отправляю")
        return {}
    return send_photo(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], cover, cap)


def run_indicators(state: list[dict], dry: bool) -> int:
    from . import indicators as I
    from .telegram import send_photo

    d = I.build(state)
    if not d:
        log("табло не собралось — в этот раз не публикуем")
        return 1
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    OUT.mkdir(exist_ok=True)
    cover = OUT / f"cover-indicators-{stamp}.png"
    I.render_board(d, cover)
    cap = I.caption(d)
    (OUT / f"indicators-{stamp}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"caption-indicators-{stamp}.html").write_text(cap, encoding="utf-8")
    log(f"ПОКАЗАТЕЛИ: подпись {visible_length(cap)} знаков")
    if dry:
        log("DRY_RUN: в канал не отправляю")
        return 0
    res = send_photo(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], cover, cap)
    now = datetime.now(TZ)
    state.append({"time_iso": now.isoformat(), "time": now.strftime("%d.%m %H:%M"), "rubric": "indicators", "form": "показатели",
                  "optics": [], "topic_key": f"pokazateli-{now:%Y-%W}", "headline": "Цифра недели — " + d["week_figure"]["label"],
                  "fly": "", "final": "", "main_title": "", "sources": d.get("_sources", []), "message_id": res.get("message_id")})
    save_state(state)
    log("готово")
    return 0


def main() -> int:
    fmt = (os.environ.get("FORMAT") or "auto").strip()
    if fmt == "auto":
        wd = datetime.now(TZ).weekday()
        fmt = next((k for k, v in FORMATS.items() if v["weekday"] == wd), "")
        if not fmt:
            log("сегодня по расписанию рубрик нет")
            return 0
    if fmt not in FORMATS:
        log(f"неизвестная рубрика {fmt}")
        return 1
    dry = bool(os.environ.get("DRY_RUN"))
    state = load_state()
    if fmt == "indicators":
        return run_indicators(state, dry)
    d = produce(fmt, state)
    if not d:
        log("рубрика не прошла проверку — в этот раз не публикуем")
        return 1
    res = publish(fmt, d, dry)
    if not dry:
        now = datetime.now(TZ)
        state.append({
            "time_iso": now.isoformat(), "time": now.strftime("%d.%m %H:%M"), "rubric": fmt, "form": FORMATS[fmt]["label"].lower(),
            "optics": d.get("optics", []), "topic_key": d.get("topic_key", ""), "headline": plain(d["headline"]),
            "fly": d.get("fly", ""), "final": "", "main_title": "", "sources": d.get("_sources", []),
            "message_id": res.get("message_id"),
        })
        save_state(state)
    log("готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
