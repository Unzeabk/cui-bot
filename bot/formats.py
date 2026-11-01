"""Рубрики-форматы, которые раньше были ручными: Показатели, Позиция, Разбор, Неделя.

Расписание (по Москве): пн 10:52 Показатели, вт и чт 13:52 Позиция, ср, пт и вс 11:22 Разбор, сб 12:52 Неделя.
Запуск: FORMAT=indicators|position|analysis|week (или auto — по дню недели) python -m bot.formats
"""

import html
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import requests

from .compose import BANNED, banned_hits, fix_links, LINK_RE, _inline, _norm, dedupe_links, extract_json, links_in, plain, visible_length
from .main import CUSTOM_EMOJI, OUT, SITE, TZ, _similar, fresh_items, load_state, log, save_state  # noqa: F401
from .prompts import BASE, WRITER_SYSTEM, _recent_txt

FORMATS = {
    "indicators": {"label": "ПОКАЗАТЕЛИ", "days": [0], "hours": 168},
    "position": {"label": "ПОЗИЦИЯ", "days": [1, 3], "hours": 72},
    "analysis": {"label": "РАЗБОР", "days": [2, 4, 6], "hours": 168},
    "week": {"label": "НЕДЕЛЯ", "days": [5], "hours": 168},
}

# Общие правила голоса и фактов берём из промпта заметок, формат описываем отдельно
_VOICE = BASE + "=== ЗАДАНИЕ ===\n"  # «Единая система» и поправки Telegram целиком
_PLACE = WRITER_SYSTEM[WRITER_SYSTEM.index("МЕСТО (для карты"): WRITER_SYSTEM.index("ОТВЕТ: только JSON-объект")].replace("{{", "{").replace("}}", "}")

PICK = {
    "indicators": "Выбери до 8 новостей недели с цифрами, которые смотрит наш читатель, в таком порядке приоритета: ключевая ставка ЦБ и ожидания по ней; инфляция в России; нефть (Brent, Urals, скидка); индекс Мосбиржи или крупные движения акций; бюджет и налоги; долги и неплатежи компаний; переводы и расчёты за рубежом; зарплаты и рынок труда. Экзотику без связи с Россией и деньгами нашего читателя (бюджет Нигерии, налог в Италии) не бери. Разные цифры, без повторов.",
    "position": "Выбери одно конкретное решение власти, регулятора или крупного игрока за последние три дня, о котором редакции стоит высказать мнение: экономическое решение с понятными последствиями для денег, активов и планов нашего читателя. Плюс до трёх новостей про то же решение.",
    "analysis": "Выбери главную тему недели для большого разбора: тренд, сдвиг в деньгах или власти, о котором вышло несколько материалов с цифрами. Для разбора нужно много фактуры: главная новость и до 9 дополнительных про тот же сюжет из РАЗНЫХ источников и с разных сторон — предыстория, цифры, участники, похожие случаи в других странах; перепечатки одной и той же новости не бери. Тема — экономика, деньги, собственность, рынки, бюджет, регулирование; не боевые действия, эвакуации и фронт (военная тема годится только как экономика: заказы, бюджеты, зарплаты). Главная новость и до пяти дополнительных про тот же сюжет с разных сторон.",
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
    "position": """ФОРМАТ: «Позиция» — жанр «Позиция» из свода, раскладка из раздела «Позиция» поправок Telegram: ровно 3 абзаца, весь пост до 950 знаков.
- Флай по правилам шапки, 2–5 слов (пойдёт на обложку). Заголовок 6–9 слов — вердикт, подзаголовок — довод, на котором он держится.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "paragraphs": ["...", "...", "..."], "place": {"name": "...", "lat": 0.0, "lon": 0.0, "scale": "country"} | null, "topic_key": "..."}""",
    "analysis": """ФОРМАТ: «Разбор» — жанр «Разбор» из свода (все семь ходов из раздела «Драматургия»), раскладка из раздела «Разбор» поправок Telegram. Статья 1200–1800 слов.
- Оптики — главная и вторая из другого семейства, в тексте не называются. Флай, заголовок и подзаголовок — по правилам шапки.
- "lead" — анонс для канала, до 400 знаков.
- "paragraphs" — сама статья, 12–25 абзацев, ссылки [текст](url) на цифрах.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "lead": "...", "paragraphs": ["..."], "optics": ["...", "..."], "place": {"name": "...", "lat": 0.0, "lon": 0.0, "scale": "country"} | null, "topic_key": "..."}""",
    "week": """ФОРМАТ: «Неделя» — главное за неделю, как «The world this week» у The Economist. Без флая и без заголовка.
- "items": 6 событий из разных областей (Россия, деньги, регионы, мир, бизнес, релоканты), самое важное первым.
- У каждого события "subject" — кто или где, 1–3 слова: страна, город, ведомство или компания («Сербия», «ЦБ», «Мосбиржа», «Кубань»). Не повторяй subject.
- "text" — 1–2 фразы: что произошло и одна цифра, со ссылкой [текст](url) на источник. Сухо, по-русски, без оценок, без «на этой неделе», без вводных. Начинай сразу с действия («Отозвал лицензию у…», «Сохранил ставку…»), потому что subject уже стоит перед текстом.
- "place" у каждого события — точка на карте мира: {"name": "...", "lat": 44.79, "lon": 20.45}, где это произошло; для мер по всей России — Москва.
- Весь пост до 950 знаков.
ОТВЕТ — только JSON: {"items": [{"subject": "...", "text": "...", "place": {"name": "...", "lat": 0.0, "lon": 0.0}}], "topic_key": "nedelya-YYYY-WW"}""",
}


def system_for(fmt: str) -> str:
    place = _PLACE if fmt in ("position", "analysis") else ""
    return _VOICE + WRITE[fmt] + "\n\n" + place + """
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

    items = collect(hours=FORMATS[fmt]["hours"])
    if fmt in ("position", "analysis"):
        items = fresh_items(items, recent)  # не про то, о чём уже писали (рубрик теперь по две в неделю)
    if not items:
        return []
    text, model = ask(LIGHT_CHAIN, PICK_SYSTEM.format(task=PICK[fmt]), topic_user_message(items, recent), temperature=0.3)
    pick = extract_json(text)
    idx = [pick.get("main")] + list(pick.get("extra") or [])
    idx = [i for i in dict.fromkeys(idx) if isinstance(i, int) and 0 <= i < len(items)]
    cap = {"indicators": 8, "position": 4, "analysis": 10, "week": 6}[fmt]
    per = 3500 if fmt in ("indicators", "week") else 7000
    uniq = []
    for i in idx:  # перепечатки одной новости разными сайтами — одна
        if not any(_similar(items[i]["title"], items[j]["title"]) for j in uniq):
            uniq.append(i)
    if fmt == "analysis":
        per = 5000
    mats = [{**items[i], "text": fetch_text(items[i]["link"], fallback=items[i]["summary"], max_chars=per)} for i in uniq[:cap]]
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
        parts += [f"{i.get('subject', '')} {i.get('text', '')}" for i in d.get("items", [])]
    else:
        parts += list(d.get("paragraphs", []))
    return parts


def validate_format(fmt: str, d: dict, allowed: set[str]) -> list[str]:
    probs = []
    if fmt == "week":
        items = d.get("items") or []
        if not 5 <= len(items) <= 7:
            probs.append(f"событий {len(items)}, нужно 6")
        subs = [plain(i.get("subject", "")) for i in items]
        if any(not s or len(s) > 24 for s in subs):
            probs.append("у каждого события нужен короткий subject, 1–3 слова")
        if len(set(subs)) != len(subs):
            probs.append("subject повторяется")
    else:
        for k in ("fly", "headline", "subhead"):
            if not d.get(k):
                probs.append(f"нет поля {k}")
        words = len(plain(d.get("headline", "")).split())
        if not 6 <= words <= 9:
            probs.append(f"в заголовке {words} слов, нужно 6–9")
    if fmt == "position" and len(d.get("paragraphs") or []) != 3:
        probs.append("в позиции ровно три абзаца")
    body = " ".join(plain(t) for t in _texts(fmt, d))
    probs += banned_hits(body)
    allowed_n = {_norm(u) for u in allowed}
    links = [u for t in _texts(fmt, d) for _, u in LINK_RE.findall(t or "")]
    if not links:
        probs.append("нет ни одной ссылки на источник")
    for u in links:
        if _norm(u) not in allowed_n:
            probs.append(f"ссылка не из материалов: {u}")
    if fmt == "analysis":
        n = sum(len(plain(p).split()) for p in d.get("paragraphs", []))
        if n < 800:  # пишется по частям: 5 × 230–320 слов
            probs.append(f"в разборе {n} слов, нужно не меньше 900")
    if not probs:
        cap = visible_length(caption(fmt, d, "https://telegra.ph/x"))
        if cap > 950:  # telegram.md: до 950 знаков
            probs.append(f"пост длиной {cap} знаков, лимит 950: сократи")
    return list(dict.fromkeys(probs))


def _dedupe(fmt: str, d: dict) -> dict:
    """Каждый источник — одна ссылка: повторы превращаются в текст."""
    if fmt == "indicators":
        tmp = {"headline": d.get("headline"), "subhead": d.get("subhead"), "paragraphs": [i.get("comment", "") for i in d.get("items", [])]}
        tmp = dedupe_links(tmp)
        for i, c in zip(d.get("items", []), tmp["paragraphs"]):
            i["comment"] = c
    elif fmt == "week":
        tmp = dedupe_links({"headline": "", "subhead": "", "paragraphs": [i.get("text", "") for i in d.get("items", [])]})
        for i, t in zip(d.get("items", []), tmp["paragraphs"]):
            i["text"] = t
            i["subject"] = plain(i.get("subject", "")).strip().rstrip(".")
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


def reading_minutes(d: dict) -> int:
    words = sum(len(plain(p).split()) for p in d.get("paragraphs", []))
    return max(3, round(words / 180))


def _minutes_word(n: int) -> str:
    return "минута" if n % 10 == 1 and n % 100 != 11 else ("минуты" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "минут")


def caption(fmt: str, d: dict, url: str = "") -> str:
    from .indicators import week_range

    if fmt == "week":
        body = "\n\n".join(f"<b>{html.escape(i['subject'])}.</b> {_inline(_cap(i['text']))}" for i in d.get("items", []))
        return f"<code>НЕДЕЛЯ</code> · <i>{week_range()}</i>\n\n{body}"
    title = f"<b>{_inline(plain(d['headline']))}</b>\n\n<i>{_inline(plain(d['subhead']))}</i>\n\n"
    if fmt == "position":
        body = "\n\n".join(_inline(p) for p in d.get("paragraphs", []))
        return f"<code>ПОЗИЦИЯ</code> · <i>Мнение редакции</i>\n\n{title}{body}"
    if fmt == "analysis":
        n = reading_minutes(d)
        return f"<code>РАЗБОР</code> · <i>{n} {_minutes_word(n)} чтения</i>\n\n{title}{_inline(d.get('lead', ''))}"
    label = FORMATS[fmt]["label"]
    return f"<code>{label}</code> · <i>{_inline(_cap(d['fly']))}</i>\n\n{title}" + "\n\n".join(_inline(p) for p in d.get("paragraphs", []))


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


REPAIR = """Ты — редактор Cui Bono?. Перепиши абзац так, чтобы в нём не было оборота: {what}.
Смысл, факты, цифры и ссылки в формате [текст](url) сохрани без изменений. Не добавляй ничего нового.
Ответ — только исправленный абзац, без пояснений."""


def repair_banned(fmt: str, d: dict) -> dict:
    """Точечно чинит запрещённые обороты в абзацах, вместо того чтобы переписывать весь текст.
    Иначе длинный разбор из-за одной «качели» уходит на новый круг и сжигает квоту."""
    from .gemini import LIGHT_CHAIN, WRITER_CHAIN, ask

    fields = [("paragraphs", i) for i in range(len(d.get("paragraphs") or []))] + [("lead", None), ("subhead", None)]
    for key, i in fields:
        text = d.get(key) if i is None else d[key][i]
        if not text:
            continue
        hits = banned_hits(plain(text))
        if not hits:
            continue
        what = "; ".join(h.split(": ", 1)[1] for h in hits)
        for chain in (WRITER_CHAIN, LIGHT_CHAIN):
            try:
                new, model = ask(chain, REPAIR.format(what=what), text, temperature=0.4)
            except Exception as e:  # noqa: BLE001
                log(f"правка оборота не удалась: {e}")
                continue
            new = new.strip().strip('"')
            if new and not banned_hits(plain(new)) and links_in({"paragraphs": [new]}) == links_in({"paragraphs": [text]}):
                log(f"исправлен оборот ({what}) — {model}")
                if i is None:
                    d[key] = new
                else:
                    d[key][i] = new
                break
    return d


class Postpone(Exception):
    """Рубрику не пишем сейчас (нет сильной модели) — это не ошибка, повтор будет по расписанию."""


ANALYSIS_PLAN = """ФОРМАТ: «Разбор» — жанр «Разбор» из свода, раскладка из раздела «Разбор» поправок Telegram. Сейчас нужен только ПЛАН статьи, сам текст пишется по частям.
- Флай, заголовок и подзаголовок — по правилам шапки.
- "lead" — анонс для канала, до 400 знаков.
- "beats" — ровно 5 частей, в них разложены семь ходов из раздела «Драматургия». У каждой части "goal" (что делает часть) и "facts" (какие факты и цифры из каких материалов в неё идут, с URL):
  1) вход — одна сцена: предмет, человек или документ; затем карта текста: мысль и вопросы, на которые статья ответит;
  2) поворот: очевидное объяснение и цифра, о которую оно ломается;
  3) механизм: как это устроено, кто и сколько на этом теряет или получает;
  4) другая сторона в самой сильной форме и ответ ей; подъём — вторая оптика из другого семейства и параллель, которую узнает русский читатель;
  5) оговорка (один абзац честности, тезис после неё стоит) и финал — кольцо к предмету из входа или другой вариант из «Драматургии».
- Оптики — главная и вторая из другого семейства; в тексте их не называют.
ОТВЕТ — только JSON: {"fly": "...", "headline": "...", "subhead": "...", "lead": "...", "beats": [{"goal": "...", "facts": "..."}], "optics": ["...", "..."], "place": {"name": "...", "lat": 0.0, "lon": 0.0, "scale": "country"} | null, "topic_key": "..."}"""

ANALYSIS_PART = """ФОРМАТ: ты пишешь одну часть «Разбора» — большого материала Cui Bono?. План и уже написанный текст даны ниже.
- Напиши ТОЛЬКО часть {n} из 5: 3–4 абзаца, всего 230–320 слов. Продолжай с того места, где остановился текст, без повторов и без пересказа сказанного.
- Каждый абзац — 3–5 предложений с конкретикой из материалов: цифры, имена, даты, суммы, как устроен механизм. Без подзаголовков.
- Ссылки [текст](url) на цифрах, URL — ровно как у материала, не меняя ни одного символа. Эти адреса уже стоят в тексте, второй раз их не ставь: {used}.
ОТВЕТ — только JSON: {{"paragraphs": ["...", "..."]}}"""


def write_analysis(recent: list[dict], mats: list[dict], feedback: str = "") -> tuple[dict, str]:
    """Разбор пишется по частям: план, потом пять частей по 230–320 слов.
    Одним запросом даже лучшая бесплатная модель даёт 800 слов, облегчённая — 300–400."""
    from .gemini import STRONG_CHAIN as WRITER_CHAIN, ask

    base = _VOICE + "{fmt}\n\n" + _PLACE + """
ФАКТЫ: пиши только по материалам, которые дал пользователь. Каждый факт и цифра — из них. Не уверен — убери."""
    allowed = [m["link"] for m in mats]
    mat_txt = user_message("analysis", recent, mats)
    try:
        text, model = ask(WRITER_CHAIN, base.replace("{fmt}", ANALYSIS_PLAN), mat_txt + (f"\n\nПрошлая версия не прошла проверку:\n{feedback}" if feedback else ""), temperature=0.7)
    except RuntimeError as e:
        raise Postpone(f"сильные модели недоступны ({e})") from e
    plan = fix_links(extract_json(text), allowed)
    log(f"план разбора написала {model}")
    paragraphs: list[str] = []
    for n in range(1, 6):
        used = sorted({u for p in paragraphs for _, u in LINK_RE.findall(p)})
        user = (f"{mat_txt}\n\nПЛАН:\n{json.dumps({k: plan.get(k) for k in ('headline', 'subhead', 'beats')}, ensure_ascii=False)}"
                f"\n\nУЖЕ НАПИСАНО:\n" + ("\n\n".join(paragraphs) or "(ничего, это начало статьи)"))
        sysmsg = base.replace("{fmt}", ANALYSIS_PART.format(n=n, used=", ".join(used) or "пока нет"))
        part, m = [], ""
        for _ in range(2):  # короткую часть (меньше 150 слов) просим ещё раз
            try:
                t, m = ask(WRITER_CHAIN, sysmsg, user, temperature=0.7)
                got = fix_links(extract_json(t), allowed).get("paragraphs") or []
            except Exception as e:  # noqa: BLE001
                log(f"часть {n} не написалась: {e}")
                continue
            if sum(len(plain(p).split()) for p in got) > sum(len(plain(p).split()) for p in part):
                part = got
            if sum(len(plain(p).split()) for p in part) >= 150:
                break
        paragraphs += [p for p in part if isinstance(p, str) and p.strip()]
        log(f"часть {n}: {sum(len(plain(p).split()) for p in part)} слов ({m})")
    plan["paragraphs"] = paragraphs
    plan.pop("beats", None)
    return plan, model


SHORTEN = """Ты — редактор Cui Bono?. Сократи заголовок до 6–9 слов, сохранив смысл, факт и интонацию.
Не добавляй ничего, чего не было. Без ссылок и кавычек вокруг. Ответ — только JSON: {"headline": "..."}"""


def fix_headline(d: dict) -> dict:
    """Длинный заголовок сокращается отдельным коротким запросом, а не переписыванием всего текста."""
    from .gemini import LIGHT_CHAIN, WRITER_CHAIN, ask

    hl = plain(d.get("headline", ""))
    if not hl or len(hl.split()) <= 9:
        return d
    for chain in (WRITER_CHAIN, LIGHT_CHAIN):
        try:
            new = plain(extract_json(ask(chain, SHORTEN, hl, temperature=0.3)[0]).get("headline", "")).strip()
        except Exception as e:  # noqa: BLE001
            log(f"сократить заголовок не вышло: {e}")
            continue
        if 5 <= len(new.split()) <= 9:
            log(f"заголовок сокращён: «{hl}» → «{new}»")
            d["headline"] = new
            break
    return d


def produce(fmt: str, recent: list[dict]) -> dict | None:
    from .gemini import WRITER_CHAIN, ask

    mats = gather(fmt, recent)
    if not mats:
        log("нет материалов")
        return None
    allowed = {m["link"] for m in mats}
    feedback = ""
    for attempt in ((1, 2) if fmt == "analysis" else (1, 2, 3)):
        user = user_message(fmt, recent, mats) + (f"\n\nПрошлая версия не прошла проверку. Исправь:\n{feedback}" if feedback else "")
        try:
            if fmt == "analysis":
                try:
                    d, model = write_analysis(recent, mats, feedback)
                except Postpone:
                    raise
                d = _dedupe(fmt, d)
            else:
                text, model = ask(WRITER_CHAIN, system_for(fmt), user, temperature=0.8)
                d = _dedupe(fmt, fix_links(extract_json(text), allowed))
        except Postpone:
            raise
        except Exception as e:  # noqa: BLE001
            feedback = f"Ответ не разобрался: {e}"
            log(feedback)
            continue
        log(f"текст написала {model}")
        probs = validate_format(fmt, d, allowed)
        if any(p.startswith("запрещённый оборот") or p.startswith("в заголовке") for p in probs):
            d = fix_headline(repair_banned(fmt, d))
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
            f.setdefault("place", d.get("place"))  # фактчек правит текст, место для карты сохраняем
            if fmt == "week":
                for a, b in zip(f.get("items", []), d.get("items", [])):
                    a.setdefault("place", b.get("place"))
            if not validate_format(fmt, f, allowed):
                f["_sources"] = list(allowed)
                return f
        feedback = "Фактчек нашёл ошибки:\n" + "\n".join(f"- {p}" for p in (v.get("problems") or []))
    return None


# Обложки рубрик — в фирменном стиле (bot/brand.py), у каждой своё лицо:
# Неделя — карта мира с пронумерованными событиями и оглавлением; Разбор — карта места крупным планом,
# бордовая плашка и время чтения; Позиция — единственная светлая обложка (bot/render.py).

WEEK_COVER = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.shade{background:linear-gradient(90deg,rgba(11,13,16,0) 55%,rgba(11,13,16,.94) 64%),linear-gradient(0deg,rgba(11,13,16,.95) 0%,rgba(11,13,16,0) 36%),linear-gradient(180deg,rgba(11,13,16,.97) 0%,rgba(11,13,16,0) 19%)}
.mid{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 440px;column-gap:56px;align-items:end;padding-bottom:24px}
.big{font-family:'Head',sans-serif;font-weight:600;font-size:120px;line-height:.92;letter-spacing:-.02em;font-variation-settings:'FLAR' 100,'VOLM' 100}
.dates{font-family:'Head',sans-serif;font-variation-settings:'slnt' -10;font-size:38px;color:#C9CFD6;margin-top:16px}
.list{display:flex;flex-direction:column}
.it{display:grid;grid-template-columns:58px 1fr;align-items:baseline;padding:11px 0;border-bottom:1.5px solid #2A2F35;font-family:'Onest',sans-serif;font-weight:500;font-size:32px}
.it:last-child{border-bottom:none}
.n{font-family:'Plex',monospace;font-size:21px;color:#6B6F72}
</style></head><body><div class="root">__MAP__<div class="layer shade"></div>
<div class="top"><span class="logo">Cui Bono?</span><span class="tag"><b></b>НЕДЕЛЯ</span></div>
<div class="rule"></div>
<div class="mid"><div><div class="big">Неделя</div><div class="dates">__DATES__</div></div><div class="list">__LIST__</div></div>
<div class="foot"><span>Главное за семь дней</span><span>__N__</span></div>
</div></body></html>"""

ANALYSIS_COVER = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.shade{background:linear-gradient(90deg,#0B0D10 0%,rgba(11,13,16,.9) 45%,rgba(11,13,16,.15) 80%)}
.mid{flex:1;display:flex;flex-direction:column;justify-content:center;gap:22px;max-width:800px;padding:20px 0}
.stamp{align-self:flex-start;font-family:'Plex',monospace;font-weight:600;font-size:22px;letter-spacing:.22em;color:#EDE9E2;background:linear-gradient(90deg,#262B31 0%,#2F343C 100%);padding:9px 18px}
</style></head><body><div class="root">__MAP__<div class="layer shade"></div>
<div class="top"><span class="logo">Cui Bono?</span><span class="tag">__DATE__</span></div>
<div class="rule"></div>
<div class="mid"><div class="stamp">РАЗБОР</div>__FLY__<div class="hl" style="font-size:__SIZE__px">__HL__</div></div>
<div class="foot"><span>__MIN__</span><span>__PLACE__</span></div>
</div></body></html>"""


def _events_word(n: int) -> str:
    return "событие" if n % 10 == 1 and n % 100 != 11 else ("события" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "событий")


def render_format_cover(fmt: str, d: dict, path) -> None:
    from . import brand as B
    from .indicators import week_range
    from .render import _fill, _fly, hl_size, place_line, render_cover

    if fmt == "week":
        items = d.get("items", [])[:6]
        lst = "".join(f'<div class="it"><span class="n">{k + 1:02d}</span><span>{html.escape(plain(i["subject"]))}</span></div>' for k, i in enumerate(items))
        page = _fill(WEEK_COVER, css=B.BASE_CSS, map=B.world_layer([B.place_ok(i.get("place")) for i in items]),
                     dates=html.escape(week_range()), list=lst, n=f"{len(items)} {_events_word(len(items))}")
        B.shoot(page, path)
        return
    if fmt == "position":
        note = {"rubric": "world", "fly": d["fly"], "headline": plain(d["headline"]), "_label": "ПОЗИЦИЯ",
                "_mark": "position", "cover": {"type": "position"}}
        render_cover(note, path, site=SITE)
        return
    n = reading_minutes(d)
    hl = plain(d["headline"])
    place = B.place_ok(d.get("place"))
    page = _fill(ANALYSIS_COVER, css=B.BASE_CSS, map=B.map_layer(place, fx=.8, fy=.45),
                 date=datetime.now(TZ).strftime("%d.%m.%Y"), fly=_fly(d.get("fly")), hl=html.escape(hl),
                 size=hl_size(hl, (88, 80, 70, 64)), min=f"{n} {_minutes_word(n)} чтения", place=place_line(place))
    B.shoot(page, path)


def publish(fmt: str, d: dict, dry: bool) -> dict:
    from .telegram import send_photo

    url = ""
    if fmt == "analysis":
        url = "https://telegra.ph/(пробный-запуск)" if dry else telegraph(d)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    OUT.mkdir(exist_ok=True)
    cover = OUT / f"cover-{fmt}-{stamp}.png"
    render_format_cover(fmt, d, cover)
    cap = caption(fmt, d, url)
    (OUT / f"{fmt}-{stamp}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"caption-{fmt}-{stamp}.html").write_text(cap, encoding="utf-8")
    log(f"{FORMATS[fmt]['label']}: подпись {visible_length(cap)} знаков" + (f", статья {url}" if url else ""))
    if dry:
        log("DRY_RUN: в канал не отправляю")
        return {}
    button = ("Читать разбор", url) if fmt == "analysis" else None
    return send_photo(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], cover, cap, button)


def run_indicators(state: list[dict], dry: bool, d: dict | None = None, journal: bool = True) -> int:
    from . import indicators as I
    from .telegram import send_photo

    d = d or I.build(state)
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
    if not journal:
        log("готово (в журнал ленты тестовый пост не записан)")
        return 0
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
        fmt = next((k for k, v in FORMATS.items() if wd in v["days"]), "")
        if not fmt:
            log("сегодня по расписанию рубрик нет")
            return 0
    if fmt not in FORMATS:
        log(f"неизвестная рубрика {fmt}")
        return 1
    dry = bool(os.environ.get("DRY_RUN"))
    state = load_state()
    test_chat = (os.environ.get("TEST_CHAT") or "").strip()
    if test_chat:  # тестовый пост уходит в личку владельцу, а не подписчикам
        os.environ["TELEGRAM_CHANNEL"] = test_chat
    if os.environ.get("TEST_DATA"):
        # готовый текст из examples/<рубрика>-test.json: посмотреть вёрстку, не тратя квоту и не дожидаясь расписания
        path = Path(__file__).resolve().parent.parent / "examples" / f"{fmt}-test.json"
        if not path.exists():
            log(f"тестовых данных для «{fmt}» нет: {path.name}")
            return 1
        d = json.loads(path.read_text(encoding="utf-8"))
        log(f"тестовые данные: {path.name} → {'пробный запуск' if dry else ('личка ' + test_chat if test_chat else 'канал')}")
        if fmt == "indicators":
            return run_indicators(state, dry, d=d, journal=False)
        publish(fmt, d, dry)
        log("готово (в журнал ленты тестовый пост не записан)")
        return 0
    if not dry and os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        # у разбора два запуска в день (сразу после сброса квот и запасной) — второй не повторяет первый
        since = datetime.now(TZ) - timedelta(hours=12)
        if any(e.get("rubric") == fmt and datetime.fromisoformat(e["time_iso"]) > since for e in state if e.get("time_iso")):
            log(f"«{FORMATS[fmt]['label'].capitalize()}» сегодня уже вышла — пропускаю")
            return 0
    if fmt == "indicators":
        return run_indicators(state, dry)
    try:
        d = produce(fmt, state)
    except Postpone as e:
        log(f"Рубрика «{FORMATS[fmt]['label'].capitalize()}» отложена: {e}. Повтор — следующим запуском по расписанию или вручную после 11:00 МСК, когда сбрасываются квоты")
        return 0
    if not d:
        log("рубрика не прошла проверку — в этот раз не публикуем")
        return 1
    res = publish(fmt, d, dry)
    if not dry:
        now = datetime.now(TZ)
        state.append({
            "time_iso": now.isoformat(), "time": now.strftime("%d.%m %H:%M"), "rubric": fmt, "form": FORMATS[fmt]["label"].lower(),
            "optics": d.get("optics", []), "topic_key": d.get("topic_key", ""),
            "headline": plain(d.get("headline") or ("Неделя: " + ", ".join(i["subject"] for i in d.get("items", [])))),
            "fly": d.get("fly", ""), "final": "", "main_title": "", "sources": d.get("_sources", []),
            "message_id": res.get("message_id"),
        })
        save_state(state)
    log("готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
