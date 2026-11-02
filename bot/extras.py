"""Дополнительные страницы сайта, которые растят издание без денег и без спама.

- /en/...           — английская версия разборов (перевод сильной моделью, кэш в site-posts.json, поле "en");
- /data/akcii/      — крупнейшие компании Мосбиржи: цена, график за год, капитализация (ISS API);
- /calendar, calendar.ics — заседания ЦБ, недельная инфляция, отсечки дивидендов; подписка в любом календаре;
- /data/igra        — «угадай цифру» недели по курсу юаня;
- /perepechatka     — открытая лицензия на перепечатку со ссылкой;
- llms.txt          — карта сайта для нейропоиска.

Каждый блок в своём try: если источник не ответил, остальное собирается. Модель цифры не трогает.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .main import TZ, log

UA = {"User-Agent": "Mozilla/5.0 (compatible; CuiBonoBot/1.0)"}
TITLE = "Cui Bono?"
EN_PER_BUILD = int(os.environ.get("EN_PER_BUILD") or 2)

# Календарь решений ЦБ (cbr.ru/dkp/cal_mp). Пресс-релиз в 13:30 МСК. Обновлять раз в год, когда ЦБ публикует новый.
CBR_MEETINGS = ["2026-12-18", "2027-02-12", "2027-03-19", "2027-04-30", "2027-06-11",
                "2027-07-30", "2027-09-10", "2027-10-22", "2027-12-17"]

# Крупнейшие эмитенты Мосбиржи: тикер → название
ISSUERS = {
    "SBER": "Сбербанк", "LKOH": "Лукойл", "GAZP": "Газпром", "ROSN": "Роснефть", "NVTK": "Новатэк",
    "GMKN": "Норникель", "TATN": "Татнефть", "SNGSP": "Сургутнефтегаз (преф.)", "PLZL": "Полюс",
    "MGNT": "Магнит", "MTSS": "МТС", "CHMF": "Северсталь", "NLMK": "НЛМК", "MAGN": "ММК",
    "PHOR": "ФосАгро", "ALRS": "Алроса", "VTBR": "ВТБ", "MOEX": "Московская биржа", "IRAO": "Интер РАО",
    "HYDR": "РусГидро", "RTKM": "Ростелеком", "TRNFP": "Транснефть (преф.)", "AFLT": "Аэрофлот",
    "PIKK": "ПИК", "SIBN": "Газпром нефть", "BSPB": "Банк Санкт-Петербург", "SVCB": "Совкомбанк",
    "BANEP": "Башнефть (преф.)", "AKRN": "Акрон", "FEES": "Россети ФСК", "UPRO": "Юнипро", "MSNG": "Мосэнерго",
}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def _ru(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _n(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


# ---------- английская версия разборов ----------

EN_SYSTEM = """You translate a Russian analytical article on economics and power into English for an international
readership (analysts, funds, journalists). House style: The Economist — cold, precise, understated, British spelling,
short declarative sentences, no hype, no moralising, no added facts. Keep every number, name and link exactly.
Links in the text look like [text](url): keep the url, translate the text. Russian institutions get their usual
English names (Bank of Russia, Finance Ministry, Rosstat). Keep the headline's form (question stays a question).
Return JSON: {"title": str, "subtitle": str, "description": str (max 160 chars, for search), "paragraphs": [str]}"""


def _translate(p: dict) -> dict | None:
    from .gemini import LIGHT_CHAIN, STRONG_CHAIN, ask
    src = {"title": p["headline"], "subtitle": p.get("subhead", ""), "paragraphs": p.get("paragraphs") or []}
    try:
        text, model = ask(STRONG_CHAIN + LIGHT_CHAIN, EN_SYSTEM, json.dumps(src, ensure_ascii=False), temperature=0.3)
        d = json.loads(re.sub(r"^```(json)?|```$", "", text.strip()).strip())
        if not d.get("title") or len(d.get("paragraphs") or []) < max(1, len(src["paragraphs"]) // 2):
            return None
        d["model"] = model
        return d
    except Exception as e:  # noqa: BLE001
        log(f"en: перевод не удался ({e})")
        return None


_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]+)\)")


def _en_inline(s: str) -> str:
    out, last = [], 0
    for m in _LINK.finditer(s):
        out.append(html.escape(s[last:m.start()]))
        out.append(f'<a href="{html.escape(m.group(2))}" rel="noopener">{html.escape(m.group(1))}</a>')
        last = m.end()
    out.append(html.escape(s[last:]))
    return "".join(out)


def en_page(title: str, desc: str, body: str, canonical: str, ru_url: str = "", ld: str = "") -> str:
    alt = (f'<link rel="alternate" hreflang="ru" href="{html.escape(ru_url)}">'
           f'<link rel="alternate" hreflang="en" href="{html.escape(canonical)}">') if ru_url else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><meta name="description" content="{html.escape(desc)}">
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">
<link rel="canonical" href="{html.escape(canonical)}">{alt}
<meta property="og:site_name" content="{TITLE}"><meta property="og:locale" content="en_GB"><meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}"><meta property="og:url" content="{html.escape(canonical)}">
<link rel="alternate" type="application/rss+xml" title="{TITLE} in English" href="/en/feed.xml">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><meta name="theme-color" content="#0B0D10">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Commissioner:wght@500;700&family=Onest:wght@400;500&family=IBM+Plex+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">{ld}
</head><body>
<header class="top"><div class="wrap"><a class="mark" href="/en/">Cui Bono?</a><a class="chan" href="/">На русском</a></div></header>
<main class="wrap">{body}</main>
<footer class="wrap"><div><p>Economics and power in Russia, explained by its numbers. · <a href="/en/">All articles</a> · <a href="/en/feed.xml">RSS</a> · <a href="/">Русская версия</a></p></div></footer>
</body></html>"""


def build_en(site: Path, posts: list[dict], abs_url, ld) -> list[tuple[str, str]]:
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("EN_OFF"):
        n = 0
        for p in posts:  # свежие первыми
            if n >= EN_PER_BUILD:
                break
            if p.get("kind") == "analysis" and p.get("paragraphs") and "en" not in p and not p.get("_en_tried"):
                d = _translate(p)
                n += 1
                if d:
                    p["en"] = d
                else:
                    p["_en_tried"] = True
    done = [p for p in posts if p.get("en") and p.get("slug")]
    (site / "en").mkdir(exist_ok=True)
    urls = []
    for p in done:
        e = p["en"]
        url, ru = abs_url(f"en/{p['slug']}"), abs_url(f"p/{p['slug']}.html")
        d = p["time_iso"][:10]
        paras = "".join(f"<p>{_en_inline(x)}</p>" for x in e["paragraphs"])
        body = (f'<article><div class="kicker">ANALYSIS</div><h1>{html.escape(e["title"])}</h1>'
                f'<p class="sub">{html.escape(e.get("subtitle", ""))}</p>'
                f'<div class="meta"><time datetime="{p["time_iso"]}">{datetime.fromisoformat(p["time_iso"]).strftime("%-d %B %Y")}</time></div>'
                f'{paras}<div class="more"><p><a href="{ru}">Read in Russian</a> · <a href="/en/">More analysis</a></p></div></article>')
        art = ld({"@context": "https://schema.org", "@type": "NewsArticle", "headline": e["title"][:110],
                  "description": e.get("description", ""), "datePublished": p["time_iso"], "inLanguage": "en",
                  "mainEntityOfPage": url, "isAccessibleForFree": True, "translationOfWork": {"@id": ru},
                  "publisher": {"@type": "NewsMediaOrganization", "name": TITLE, "url": abs_url("index.html")}})
        (site / "en" / f"{p['slug']}.html").write_text(
            en_page(f"{e['title']} — {TITLE}", e.get("description") or e.get("subtitle", ""), body, url, ru, art),
            encoding="utf-8")
        urls.append((url, d))
    items = "".join(f'<li><div class="kicker">ANALYSIS</div><h2><a href="/en/{p["slug"]}">{html.escape(p["en"]["title"])}</a></h2>'
                    f'<p class="sub">{html.escape(p["en"].get("subtitle", ""))}</p></li>' for p in done)
    (site / "en" / "index.html").write_text(en_page(
        f"{TITLE} — Russia's economy and power, explained by its numbers",
        "Analysis of Russia's economy, budget, central bank, sanctions and money flows — translated from the Russian edition.",
        f'<h1 class="sec">Analysis</h1><ul class="list">{items}</ul>', abs_url("en/")), encoding="utf-8")
    rss = "".join(f"<item><title>{html.escape(p['en']['title'])}</title><link>{abs_url('en/' + p['slug'])}</link>"
                  f"<guid>{abs_url('en/' + p['slug'])}</guid><description>{html.escape(p['en'].get('description', ''))}</description>"
                  f"<pubDate>{datetime.fromisoformat(p['time_iso']).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate></item>"
                  for p in done[:50])
    (site / "en" / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{TITLE} in English</title>'
        f"<link>{abs_url('en/')}</link><description>Russia's economy and power</description><language>en</language>{rss}</channel></rss>",
        encoding="utf-8")
    log(f"en: {len(done)} разборов на английском")
    return urls + [(abs_url("en/"), done[0]["time_iso"][:10] if done else "")]


# ---------- акции: страницы компаний Мосбиржи ----------
# Эндпоинт дивидендов ISS отдаёт описание бумаги вместо выплат, поэтому страницы строятся на ценах (они работают).

def _board() -> dict:
    r = requests.get("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json",
                     params={"iss.meta": "off", "securities": ",".join(ISSUERS)}, headers=UA, timeout=40).json()
    sec = {x[0]: dict(zip(r["securities"]["columns"], x)) for x in r["securities"]["data"]}
    md = {x[r["marketdata"]["columns"].index("SECID")]: dict(zip(r["marketdata"]["columns"], x)) for x in r["marketdata"]["data"]}
    return {k: {**sec.get(k, {}), **md.get(k, {})} for k in ISSUERS if k in sec}


def _share_history(secid: str, days: int = 370) -> list[tuple[date, float]]:
    now = datetime.now(TZ).date()
    out, start = [], 0
    while start < 1000:
        r = requests.get(f"https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json",
                         params={"from": (now - timedelta(days=days)).isoformat(), "iss.meta": "off",
                                 "history.columns": "TRADEDATE,CLOSE", "start": start}, headers=UA, timeout=40).json()
        rows = r["history"]["data"]
        out += [(date.fromisoformat(d), float(c)) for d, c in rows if c]
        if len(rows) < 100:
            break
        start += len(rows)
    return sorted(out)


def build_dividends(site: Path, page, abs_url, list_posts, posts, ld) -> tuple[list[tuple[str, str]], list]:
    from .data_pages import svg_chart
    out = site / "data" / "akcii"
    out.mkdir(parents=True, exist_ok=True)
    board = _board()
    today = datetime.now(TZ).date()
    urls, cards = [], []
    for secid, name in ISSUERS.items():
        b = board.get(secid)
        if not b:
            continue
        try:
            pts = _share_history(secid)
        except Exception as e:  # noqa: BLE001
            log(f"акции: {secid} история не получена ({e})")
            pts = []
        last = b.get("LAST") or b.get("LCURRENTPRICE") or b.get("PREVPRICE") or (pts[-1][1] if pts else None)
        if not last:
            continue
        cap = b.get("ISSUECAPITALIZATION")
        y = [p for p in pts if p[0] <= today - timedelta(days=365)] or pts[:1]
        ch = (last / y[-1][1] - 1) * 100 if y else None
        slug = secid.lower()
        lead = (f"Акция {name} ({secid}) на Мосбирже — <b>{_n(float(last))} ₽</b>"
                + (f", за год {'+' if ch > 0 else '−' if ch < 0 else ''}{_n(abs(ch), 1)}%" if ch is not None else "")
                + (f". Капитализация — {_n(cap / 1e9, 0)} млрд ₽." if cap else "."))
        words = [name.split(" (")[0].lower(), secid.lower()]
        rel = [p for p in posts if p.get("paragraphs") and any(w in (p["headline"] + " " + " ".join(
            x["name"] for x in p.get("entities") or [])).lower() for w in words)][:6]
        body = (f'<a class="kicker" href="/data">ПОКАЗАТЕЛИ · АКЦИИ</a><h1>{html.escape(name)}: акции {secid} сегодня</h1>'
                f'<p class="lead">{lead}</p>' + (svg_chart(pts) if len(pts) > 2 else "")
                + f'<p class="src">Источник: <a href="https://www.moex.com/ru/issue.aspx?code={secid}" rel="nofollow noopener">Московская биржа</a>. '
                f'Обновляется автоматически. <a href="/data/akcii">Все компании</a></p>'
                + (f'<h2 class="dh">Тексты по теме</h2>{list_posts(rel, "../../")}' if rel else ""))
        desc = re.sub(r"<[^>]+>", "", lead)[:200]
        (out / f"{slug}.html").write_text(page(f"Акции {name} ({secid}) сегодня: цена, график за год — {TITLE}",
                                               desc, body, abs_url(f"data/akcii/{slug}"), depth=2), encoding="utf-8")
        urls.append((abs_url(f"data/akcii/{slug}"), today.isoformat()))
        cards.append((slug, name, secid, f"{_n(float(last))} ₽", ch, cap or 0))
    if cards:
        trs = "".join(f'<tr><td><a href="/data/akcii/{s}">{html.escape(n)}</a></td><td>{t}</td><td>{v}</td>'
                      f'<td>{"" if c is None else ("+" if c > 0 else "") + _n(c, 1) + "%"}</td></tr>'
                      for s, n, t, v, c, _ in sorted(cards, key=lambda x: -x[5]))
        body = ('<h1 class="sec">Крупнейшие компании Мосбиржи</h1>'
                f'<table class="data"><thead><tr><th>Компания</th><th>Тикер</th><th>Цена</th><th>За год</th></tr></thead><tbody>{trs}</tbody></table>'
                '<p class="src">Источник: Московская биржа. Обновляется автоматически.</p>')
        (out / "index.html").write_text(page(f"Акции крупнейших российских компаний сегодня — {TITLE}",
                                             "Цены и изменение за год крупнейших акций Мосбиржи: Сбербанк, Лукойл, Газпром и другие.",
                                             body, abs_url("data/akcii"), depth=2), encoding="utf-8")
        urls.append((abs_url("data/akcii"), today.isoformat()))
    log(f"акции: {len(cards)} компаний")
    return urls, []


# ---------- календарь ----------

def _ics_event(uid: str, start: datetime, minutes: int, summary: str, url: str, rrule: str = "") -> str:
    st = start.astimezone(timezone.utc)
    return ("BEGIN:VEVENT\r\n"
            f"UID:{uid}@cuibono.press\r\nDTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}\r\n"
            f"DTSTART:{st:%Y%m%dT%H%M%SZ}\r\nDTEND:{st + timedelta(minutes=minutes):%Y%m%dT%H%M%SZ}\r\n"
            + (f"RRULE:{rrule}\r\n" if rrule else "")
            + f"SUMMARY:{summary}\r\nDESCRIPTION:{url}\r\nURL:{url}\r\nEND:VEVENT\r\n")


def build_calendar(site: Path, page, abs_url, upcoming: list[tuple[date, str, str]]) -> list[tuple[str, str]]:
    msk = TZ
    today = datetime.now(TZ).date()
    events, rows = [], []
    for d in CBR_MEETINGS:
        dd = date.fromisoformat(d)
        events.append(_ics_event(f"cbr-{d}", datetime(dd.year, dd.month, dd.day, 13, 30, tzinfo=msk), 30,
                                 "Банк России: решение по ключевой ставке", abs_url("data/klyuchevaya-stavka")))
        if dd >= today:
            rows.append((dd, "13:30", "Банк России: решение по ключевой ставке", abs_url("data/klyuchevaya-stavka")))
    # недельная инфляция Росстата: каждая среда, 19:00 МСК
    nxt = today + timedelta(days=(2 - today.weekday()) % 7)
    events.append(_ics_event("rosstat-weekly-cpi", datetime(nxt.year, nxt.month, nxt.day, 19, 0, tzinfo=msk), 15,
                             "Росстат: недельная инфляция", abs_url("data"), "FREQ=WEEKLY;BYDAY=WE"))
    for d, t, u in upcoming:
        events.append(_ics_event(f"div-{d}-{abs(hash(t)) % 10**8}", datetime(d.year, d.month, d.day, 10, 0, tzinfo=msk), 15, t, u))
        rows.append((d, "", t, u))
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Cui Bono?//calendar//RU\r\nCALSCALE:GREGORIAN\r\n"
           "X-WR-CALNAME:Cui Bono? — экономика\r\nX-WR-TIMEZONE:Europe/Moscow\r\nREFRESH-INTERVAL;VALUE=DURATION:P1D\r\n"
           + "".join(events) + "END:VCALENDAR\r\n")
    (site / "calendar.ics").write_text(ics, encoding="utf-8")
    ics_url = abs_url("calendar.ics")
    webcal = "webcal://" + ics_url.split("://", 1)[-1]
    trs = "".join(f'<tr><td>{d:%d.%m.%Y}</td><td>{t}</td><td><a href="{u}">{html.escape(s)}</a></td></tr>'
                  for d, t, s, u in sorted(rows)[:60])
    body = (f'<a class="kicker" href="/data">ПОКАЗАТЕЛИ · КАЛЕНДАРЬ</a><h1>Календарь: заседания ЦБ и инфляция</h1>'
            f'<p class="lead">Следующее решение Банка России по ключевой ставке — '
            f'<b>{_ru(min(date.fromisoformat(d) for d in CBR_MEETINGS if date.fromisoformat(d) >= today))}</b>, пресс-релиз в 13:30 МСК. '
            f'Недельную инфляцию Росстат публикует по средам в 19:00.</p>'
            f'<p><a href="{webcal}">Подписаться в календаре</a> · <a href="https://calendar.google.com/calendar/r?cid={html.escape(webcal)}">Google Календарь</a> · '
            f'<a href="/calendar.ics">Файл .ics</a></p>'
            f'<table class="data"><thead><tr><th>Дата</th><th>Время</th><th>Событие</th></tr></thead><tbody>{trs}</tbody></table>'
            f'<p class="src">Источники: <a href="https://www.cbr.ru/dkp/cal_mp/" rel="nofollow noopener">календарь Банка России</a>, Московская биржа.</p>')
    (site / "calendar.html").write_text(page(
        f"Заседания ЦБ по ключевой ставке {today.year}–{today.year + 1}: даты и инфляция — {TITLE}",
        "Даты заседаний Банка России по ключевой ставке, публикации инфляции и отсечки дивидендов. Подписка на календарь.",
        body, abs_url("calendar")), encoding="utf-8")
    return [(abs_url("calendar"), today.isoformat())]


# ---------- «угадай цифру» ----------

def _cny_points() -> list[tuple[date, float]]:
    """Курс юаня ЦБ. cbr.ru из GitHub Actions часто отдаёт заглушку — тогда зеркало cbr-xml-daily.ru."""
    try:
        from .data_pages import currency_history
        pts = currency_history("R01375", days=30)
        if len(pts) >= 6:
            return pts
    except Exception as e:  # noqa: BLE001
        log(f"игра: cbr.ru не ответил ({e}), беру зеркало")
    out, d = [], datetime.now(TZ).date()
    for _ in range(16):
        try:
            r = requests.get(f"https://www.cbr-xml-daily.ru/archive/{d:%Y/%m/%d}/daily_json.js", headers=UA, timeout=20)
            if r.ok:
                v = r.json()["Valute"]["CNY"]
                out.append((d, v["Value"] / v["Nominal"]))
        except Exception:  # noqa: BLE001
            pass
        d -= timedelta(days=1)
    return sorted(out)


def build_quiz(site: Path, page, abs_url) -> list[tuple[str, str]]:
    pts = _cny_points()
    if len(pts) < 6:
        return []
    (d1, v1) = pts[-1]
    base = [p for p in pts if p[0] <= d1 - timedelta(days=7)]
    if not base:
        return []
    d0, v0 = base[-1]
    real = (v1 / v0 - 1) * 100
    rnd = random.Random(d1.isoformat())
    opts = {round(real, 1)}
    while len(opts) < 3:
        opts.add(round(real + rnd.choice([-1, 1]) * rnd.uniform(0.6, 2.5), 1))
    opts = sorted(opts)
    right = round(real, 1)
    fmt = lambda x: ("+" if x > 0 else "") + _n(x, 1) + "%"  # noqa: E731
    btns = "".join(f'<button class="q" data-ok="{1 if o == right else 0}">{fmt(o)}</button>' for o in opts)
    answer = (f"Юань у ЦБ: {_n(v0, 4)} ₽ на {d0:%d.%m} и {_n(v1, 4)} ₽ на {d1:%d.%m}, изменение {fmt(right)}.")
    body = (f'<a class="kicker" href="/data">ЦИФРА НЕДЕЛИ</a><h1>Как изменился курс юаня за неделю?</h1>'
            f'<p class="sub">Официальный курс Банка России, {d0:%d.%m} — {d1:%d.%m.%Y}</p>'
            f'<div class="quiz">{btns}</div><p id="ans" class="lead" hidden>{html.escape(answer)} '
            f'<a href="/data/kurs-yuanya">График и история</a></p>'
'<script>document.querySelectorAll(".q").forEach(b=>b.onclick=()=>{document.querySelectorAll(".q").forEach(x=>'
            'x.classList.add(x.dataset.ok==="1"?"ok":"no"));document.getElementById("ans").hidden=false;'
            'try{ym&&0}catch(e){}})</script>')
    (site / "data" / "igra.html").write_text(page(f"Цифра недели: курс юаня за неделю — {TITLE}",
                                                  "Угадайте, как изменился официальный курс юаня за неделю. Ответ — с цифрами ЦБ.",
                                                  body, abs_url("data/igra"), depth=1), encoding="utf-8")
    return [(abs_url("data/igra"), d1.isoformat())]


# ---------- перепечатка и llms.txt ----------

def build_static(site: Path, page, abs_url, posts: list[dict]) -> list[tuple[str, str]]:
    body = ('<h1 class="sec">Перепечатка</h1><article>'
            '<p>Заметки, позиции и разборы издания можно перепечатывать целиком, без изменений, с активной ссылкой '
            'на оригинал на cuibono.press. Цифры со страниц раздела «Показатели» можно использовать с той же ссылкой.</p>'
            '<p>Лицензия: <a href="https://creativecommons.org/licenses/by-nd/4.0/deed.ru" rel="license noopener">CC BY-ND 4.0</a>.</p>'
            '</article>')
    (site / "perepechatka.html").write_text(page(f"Перепечатка — {TITLE}", "Условия перепечатки материалов издания.",
                                                 body, abs_url("perepechatka")), encoding="utf-8")
    top = [p for p in posts if p.get("kind") == "analysis" and p.get("slug")][:20]
    llms = ("# Cui Bono?\n\n> Аналитическое издание об экономике и власти (Россия). Ежедневные разборы, "
            "позиция редакции и страницы официальных данных (ЦБ, Мосбиржа), обновляемые автоматически.\n\n"
            "## Показатели\n"
            f"- [Ключевая ставка ЦБ]({abs_url('data/klyuchevaya-stavka')})\n- [Курс доллара ЦБ]({abs_url('data/kurs-dollara')})\n"
            f"- [Курс юаня ЦБ]({abs_url('data/kurs-yuanya')})\n- [Индекс Мосбиржи]({abs_url('data/indeks-mosbirzhi')})\n"
            f"- [Акции крупнейших компаний]({abs_url('data/akcii')})\n- [Календарь заседаний ЦБ]({abs_url('calendar')})\n"
            f"- [Индекс жёсткости ЦБ]({abs_url('data/zhestkost-cb')})\n- [Новые законопроекты о деньгах]({abs_url('data/dokumenty')})\n"
            f"- [Регионы]({abs_url('region/')})\n\n"
            "## Разборы\n" + "".join(f"- [{p['headline']}]({abs_url('p/' + p['slug'] + '.html')})\n" for p in top)
            + f"\n## English\n- [Analysis in English]({abs_url('en/')})\n")
    (site / "llms.txt").write_text(llms, encoding="utf-8")
    return [(abs_url("perepechatka"), "")]


def build_extras(site, page, abs_url, list_posts, posts, ld) -> list[tuple[str, str]]:
    site = Path(site)
    urls: list[tuple[str, str]] = []
    upcoming: list = []
    for name, fn in [
        ("en", lambda: build_en(site, posts, abs_url, ld)),
        ("акции", lambda: build_dividends(site, page, abs_url, list_posts, posts, ld)),
        ("игра", lambda: build_quiz(site, page, abs_url)),
        ("перепечатка", lambda: build_static(site, page, abs_url, posts)),
        ("жёсткость ЦБ", lambda: build_hawk(site, page, abs_url, ld)),
        ("документы", lambda: build_docs(site, page, abs_url)),
        ("регионы", lambda: build_regions(site, page, abs_url, list_posts, posts)),
        ("рассылка", lambda: build_subscribe(site, page, abs_url)),
    ]:
        try:
            r = fn()
            if isinstance(r, tuple):
                r, upcoming = r
            urls += r
        except Exception as e:  # noqa: BLE001
            log(f"{name}: сборка не удалась ({e})")
    try:
        urls += build_calendar(site, page, abs_url, upcoming)
    except Exception as e:  # noqa: BLE001
        log(f"календарь: сборка не удалась ({e})")
    try:
        build_hub(site, page, abs_url)
    except Exception as e:  # noqa: BLE001
        log(f"витрина данных: сборка не удалась ({e})")
    return urls


def build_hub(site: Path, page, abs_url) -> None:
    """/data — витрина всех данных: плитки по группам, только страницы, которые реально есть."""
    old = (site / "data.html").read_text(encoding="utf-8") if (site / "data.html").exists() else ""
    vals = dict(re.findall(r'<a href="/data/([a-z-]+)">[^<]*</a></h2><p class="sub">([^<]*)</p>', old))
    def lead(path):
        f = site / path
        if not f.exists():
            return None
        m = re.search(r'class="lead">(.*?)</p>', f.read_text(encoding="utf-8"), re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""
    def bval(path):
        m = re.search(r'class="lead">.*?<b>([^<]+)</b>', (site / path).read_text(encoding="utf-8"), re.S) if (site / path).exists() else None
        return m.group(1) if m else ""
    today = datetime.now(TZ).date()
    nxt = min((date.fromisoformat(d) for d in CBR_MEETINGS if date.fromisoformat(d) >= today), default=None)
    next_meeting = f"{nxt.day} {MONTHS[nxt.month - 1]}" if nxt else ""
    best = None
    ak = site / "data" / "akcii" / "index.html"
    if ak.exists():
        rows = re.findall(r'<td><a href="/data/akcii/[a-z]+">([^<]+)</a></td><td>[A-Z]+</td><td>[^<]*</td><td>([+-][0-9 ,]+)%</td>',
                          ak.read_text(encoding="utf-8"))
        if rows:
            n, ch = max(rows, key=lambda r: float(r[1].replace(" ", "").replace(",", ".")))
            best = (ch.replace("-", "−") + "%", f"{n} — лучший рост за год среди 32 крупнейших")
    groups = [
        ("Банк России и рынок", [
            ("data/klyuchevaya-stavka", "Ключевая ставка", vals.get("klyuchevaya-stavka"), "Решения ЦБ и история с 2013 года"),
            ("data/zhestkost-cb", "Жёсткость ЦБ", None, "Риторика пресс-релизов: что добавилось, что ушло"),
            ("data/kurs-dollara", "Доллар", vals.get("kurs-dollara"), "Официальный курс ЦБ"),
            ("data/kurs-yuanya", "Юань", vals.get("kurs-yuanya"), "Официальный курс ЦБ"),
            ("data/kurs-evro", "Евро", vals.get("kurs-evro"), "Официальный курс ЦБ"),
            ("data/indeks-mosbirzhi", "Индекс Мосбиржи", vals.get("indeks-mosbirzhi"), "IMOEX за год"),
            ("data/zoloto", "Золото", vals.get("zoloto"), "Учётная цена ЦБ за грамм"),
            ("calendar", "Следующее заседание ЦБ", next_meeting, "Решение по ставке в 13:30 МСК"),
        ]),
        ("Компании и даты", [
            ("data/akcii/index", "Акции", None, "32 крупнейшие компании: цена и год"),
            ("calendar", "Календарь", None, "Заседания ЦБ и инфляция — с подпиской"),
        ]),
        ("Власть и регионы", [
            ("data/dokumenty", "Что готовят", None, "Новые проекты законов о налогах, банках, ценах"),
            ("region/index", "Регионы", None, "Экономика и бюджеты регионов"),
        ]),
        ("Цифра недели", [("data/igra", "Угадайте цифру", None, "Как изменился юань за неделю?"),
                          ("data/akcii/index", "Акция года", best[0], best[1])] if best else
                         [("data/igra", "Угадайте цифру", None, "Как изменился юань за неделю?")]),
    ]
    body = '<h1 class="sec">Показатели</h1>'
    for name, items in groups:
        tiles = ""
        for path, title, val, desc in items:
            if not (site / f"{path}.html").exists():
                continue
            if not val and name == "Банк России и рынок":
                val = bval(f"{path}.html")
            href = "/" + path.removesuffix("/index").removesuffix("index")
            tiles += (f'<a href="{href}"><div class="k">{html.escape(title)}</div>'
                      + (f'<div class="v">{html.escape(val)}</div>' if val else "")
                      + f'<div class="d">{html.escape(desc)}</div></a>')
        if tiles:
            body += f'<h2 class="hubh">{name}</h2><div class="hub">{tiles}</div>'
    (site / "data.html").write_text(page(
        "Показатели: ставка ЦБ, курсы, индекс Мосбиржи, дивиденды, календарь — Cui Bono?",
        "Ключевая ставка и риторика ЦБ, официальные курсы, индекс Мосбиржи, золото, дивиденды, календарь заседаний и новые законопроекты.",
        body, abs_url("data")), encoding="utf-8")


# ---------- «Жёсткость ЦБ»: дифф пресс-релизов о ставке ----------

CBR_RSS = ["https://www.cbr.ru/rss/RssPress", "https://www.cbr.ru/rss/eventrss"]
HAWK = ["проинфляционн", "жёстк", "жестк", "повышен", "ускорен", "перегрев", "дефицит рабочей силы", "рост кредитования",
        "высок", "устойчиво высок", "необходимость", "продолжительн", "дополнительн", "риски смещены в сторону проинфляционных",
        "ослаблени рубл", "ослабление рубля", "бюджетн", "инфляционные ожидания остаются"]
DOVE = ["дезинфляц", "снижени", "замедлен", "охлаждени", "смягчен", "снизилась", "ниже", "умерен", "возвращени",
        "укреплени", "сбалансирован", "риски смещены в сторону дезинфляционных", "пространство для снижения"]


def _clean_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    m = re.search(r'(?is)<div[^>]+class="[^"]*(landing-text|press_release|text-block|lead-text)[^"]*"[^>]*>(.*)', raw)
    t = re.sub(r"<[^>]+>", " ", m.group(2) if m else raw)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z«])", t) if len(s.strip()) > 30]


def _score(t: str) -> float:
    low = t.lower()
    h = sum(low.count(w) for w in HAWK)
    d = sum(low.count(w) for w in DOVE)
    return round(100 * (h - d) / max(1, h + d), 0)


def _fetch_releases(cache: dict) -> dict:
    import xml.etree.ElementTree as ET
    for feed in CBR_RSS:
        try:
            root = ET.fromstring(requests.get(feed, headers=UA, timeout=30).content)
        except Exception as e:  # noqa: BLE001
            log(f"жёсткость ЦБ: лента {feed} не ответила ({e})")
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            if "ключевую ставку" not in title.lower() and "ключевой ставк" not in title.lower():
                continue
            link = (it.findtext("link") or "").strip()
            if not link or link in cache:
                continue
            try:
                from email.utils import parsedate_to_datetime
                d = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
            except Exception:  # noqa: BLE001
                d = datetime.now(TZ).date().isoformat()
            try:
                text = _clean_text(requests.get(link, headers=UA, timeout=30).text)
            except Exception as e:  # noqa: BLE001
                log(f"жёсткость ЦБ: релиз не скачан ({e})")
                continue
            if len(text) > 500:
                cache[link] = {"date": d, "title": title, "text": text[:20000]}
    return cache


def build_hawk(site: Path, page, abs_url, ld) -> list[tuple[str, str]]:
    f = Path("state") / "cbr-releases.json"
    cache = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    cache = _fetch_releases(cache)
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    rel = sorted(cache.items(), key=lambda kv: kv[1]["date"])
    if not rel:
        return []
    for _, r in rel:
        r["score"] = _score(r["text"])
    last_url, last = rel[-1]
    diff = ""
    if len(rel) > 1:
        prev = rel[-2][1]
        a, b = set(_sentences(prev["text"])), _sentences(last["text"])
        new = [s for s in b if s not in a][:12]
        gone = [s for s in _sentences(prev["text"]) if s not in set(b)][:12]
        diff = ('<h2 class="dh">Что появилось в тексте</h2><ul>' + "".join(f"<li>{html.escape(s)}</li>" for s in new) + "</ul>"
                '<h2 class="dh">Что из текста ушло</h2><ul>' + "".join(f"<li><s>{html.escape(s)}</s></li>" for s in gone) + "</ul>")
    trend = "".join(f'<tr><td>{date.fromisoformat(r["date"]):%d.%m.%Y}</td><td>{int(r["score"]):+d}</td>'
                    f'<td><a href="{html.escape(u)}" rel="nofollow noopener">пресс-релиз</a></td></tr>' for u, r in reversed(rel))
    d = date.fromisoformat(last["date"])
    delta = f", было {int(rel[-2][1]['score']):+d}" if len(rel) > 1 else ""
    lead = (f"Жёсткость риторики Банка России в пресс-релизе от {_ru(d)} — <b>{int(last['score']):+d}</b>{delta}. "
            "Шкала от −100 (только слова о замедлении инфляции) до +100 (только слова о проинфляционных рисках).")
    body = (f'<a class="kicker" href="/data">ПОКАЗАТЕЛИ · ИНДЕКС</a><h1>Жёсткость ЦБ: что изменилось в словах регулятора</h1>'
            f'<p class="lead">{lead}</p>{diff}<h2 class="dh">История</h2><table class="data"><thead><tr><th>Дата</th>'
            f'<th>Индекс</th><th>Источник</th></tr></thead><tbody>{trend}</tbody></table>'
            '<p class="src">Расчёт Cui Bono? по текстам пресс-релизов Банка России: доля «жёстких» и «мягких» формулировок. '
            'Страница обновляется автоматически. <a href="/data/klyuchevaya-stavka">Ключевая ставка</a> · <a href="/calendar">Календарь заседаний</a></p>')
    ds = ld({"@context": "https://schema.org", "@type": "Dataset", "name": "Индекс жёсткости ЦБ (Cui Bono?)",
             "description": re.sub(r"<[^>]+>", "", lead), "url": abs_url("data/zhestkost-cb"), "dateModified": d.isoformat(),
             "isAccessibleForFree": True})
    (site / "data" / "zhestkost-cb.html").write_text(page(
        f"Индекс жёсткости ЦБ: риторика пресс-релиза от {_ru(d)} — {TITLE}", re.sub(r"<[^>]+>", "", lead)[:200],
        body, abs_url("data/zhestkost-cb"), depth=1, extra_head=ds), encoding="utf-8")
    log(f"жёсткость ЦБ: {len(rel)} релизов, последний {int(last['score']):+d}")
    return [(abs_url("data/zhestkost-cb"), d.isoformat())]


# ---------- трекер документов ----------

DOC_FEEDS = [
    ("Проекты актов", "https://regulation.gov.ru/api/public/Rss/"),
    ("Проекты актов", "https://regulation.gov.ru/rss"),
    ("Законопроекты", "https://sozd.duma.gov.ru/rss/bills"),
    ("Минфин", "https://minfin.gov.ru/ru/press-center/rss"),
]
DOC_WORDS = ["налог", "бюджет", "пошлин", "банк", "валют", "вклад", "ипотек", "акци", "дивиденд", "санкц", "экспорт",
             "импорт", "собственност", "национализ", "приватизац", "тариф", "цен", "кредит", "криптовалют", "цифров рубл",
             "майнинг", "страхов", "пенси", "зарплат", "мигрант", "релокант", "иностран", "нефт", "газ", "уголь", "золот"]


def build_docs(site: Path, page, abs_url) -> list[tuple[str, str]]:
    import xml.etree.ElementTree as ET
    f = Path("state") / "docs.json"
    seen = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    known = {x["link"] for x in seen}
    today = datetime.now(TZ).date().isoformat()
    added = 0
    for src, url in DOC_FEEDS:
        try:
            root = ET.fromstring(requests.get(url, headers=UA, timeout=30).content)
        except Exception as e:  # noqa: BLE001
            log(f"документы: {url} не ответил ({e})")
            continue
        for it in root.iter("item"):
            title = re.sub(r"\s+", " ", (it.findtext("title") or "")).strip()
            link = (it.findtext("link") or "").strip()
            if not title or not link or link in known:
                continue
            if not any(w in title.lower() for w in DOC_WORDS):
                continue
            seen.append({"date": today, "src": src, "title": title[:400], "link": link})
            known.add(link)
            added += 1
    seen = sorted(seen, key=lambda x: x["date"], reverse=True)[:500]
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")
    if not seen:
        return []
    rows = "".join(f'<tr><td>{date.fromisoformat(x["date"]):%d.%m}</td><td>{html.escape(x["src"])}</td>'
                   f'<td><a href="{html.escape(x["link"])}" rel="nofollow noopener">{html.escape(x["title"])}</a></td></tr>'
                   for x in seen[:200])
    body = ('<a class="kicker" href="/data">ПОКАЗАТЕЛИ · ДОКУМЕНТЫ</a><h1>Что готовят: новые проекты законов и решений о деньгах</h1>'
            f'<p class="lead">Новые проекты актов, законопроекты и решения ведомств о налогах, банках, ценах и собственности. '
            f'За последнюю сборку добавлено: {added}.</p>'
            f'<table class="data"><thead><tr><th>Дата</th><th>Источник</th><th>Документ</th></tr></thead><tbody>{rows}</tbody></table>'
            '<p class="src">Источники: regulation.gov.ru, СОЗД Госдумы, Банк России, Минфин. Обновляется автоматически.</p>')
    (site / "data" / "dokumenty.html").write_text(page(
        f"Новые законопроекты о налогах, банках и бизнесе — {TITLE}",
        "Новые проекты законов и решений о налогах, банках, ценах и собственности: ежедневная подборка из первоисточников.",
        body, abs_url("data/dokumenty"), depth=1), encoding="utf-8")
    log(f"документы: всего {len(seen)}, новых {added}")
    return [(abs_url("data/dokumenty"), today)]


# ---------- регионы: страницы из текстов издания + ссылки на официальные данные ----------

REGIONS = ["Москва", "Санкт-Петербург", "Московская область", "Татарстан", "Башкортостан", "Свердловская область",
           "Краснодарский край", "Новосибирская область", "Тюменская область", "ХМАО", "ЯНАО", "Красноярский край",
           "Челябинская область", "Самарская область", "Нижегородская область", "Ростовская область", "Пермский край",
           "Кемеровская область", "Кузбасс", "Иркутская область", "Приморский край", "Якутия", "Белгородская область",
           "Курская область", "Брянская область", "Воронежская область", "Калининградская область", "Дагестан", "Чечня",
           "Крым", "Омская область", "Хабаровский край", "Мурманская область", "Архангельская область", "Сахалин"]


def build_regions(site: Path, page, abs_url, list_posts, posts) -> list[tuple[str, str]]:
    from .site import _translit
    (site / "region").mkdir(exist_ok=True)
    urls, cards = [], []
    for r in REGIONS:
        stem = r.split()[0][:-2].lower() if len(r.split()[0]) > 6 else r.lower()
        ps = [p for p in posts if p.get("paragraphs") and stem in (p["headline"] + " " + p.get("subhead", "") + " "
              + " ".join(p.get("paragraphs") or []) + " " + " ".join(x["name"] for x in p.get("entities") or [])).lower()]
        if len(ps) < 2:
            continue
        sl = _translit(r)
        body = (f'<a class="kicker" href="/r/regions">РЕГИОНЫ</a><h1>{html.escape(r)}: экономика и бюджет</h1>'
                f'<p class="lead">{len(ps)} текстов Cui Bono? о том, что происходит с деньгами региона. '
                f'Официальные цифры — <a href="https://www.roskazna.gov.ru/ispolnenie-byudzhetov/konsolidirovannye-byudzhety-subektov/" rel="nofollow noopener">исполнение бюджетов субъектов (Казначейство)</a>, '
                f'<a href="https://minfin.gov.ru/ru/perfomance/public_debt/subdbt" rel="nofollow noopener">госдолг регионов (Минфин)</a>.</p>'
                + list_posts(ps, "../"))
        (site / "region" / f"{sl}.html").write_text(page(
            f"{r}: экономика, бюджет, госдолг — {TITLE}", f"{r}: тексты о бюджете, долге, бизнесе и деньгах региона.",
            body, abs_url(f"region/{sl}"), depth=1, noindex=len(ps) < 3), encoding="utf-8")
        if len(ps) >= 3:
            urls.append((abs_url(f"region/{sl}"), ps[0]["time_iso"][:10]))
        cards.append((sl, r, len(ps)))
    if cards:
        items = "".join(f'<li><h2><a href="/region/{s}">{html.escape(n)}</a></h2><p class="sub">{c} текстов</p></li>'
                        for s, n, c in sorted(cards, key=lambda x: -x[2]))
        (site / "region" / "index.html").write_text(page(f"Регионы России: экономика и бюджет — {TITLE}",
                                                         "Экономика и бюджеты регионов России: все тексты издания по регионам.",
                                                         f'<h1 class="sec">Регионы</h1><ul class="list">{items}</ul>', abs_url("region/"), depth=1),
                                                    encoding="utf-8")
        urls.append((abs_url("region/"), ""))
    return urls


# ---------- рассылка (Brevo) ----------
# vars: BREVO_FORM_URL (адрес формы подписки из Brevo), BREVO_LIST_ID, BREVO_SENDER (почта отправителя);
# secret: BREVO_API_KEY. Страница /subscribe появляется, когда state/brevo.json готов.

def build_subscribe(site: Path, page, abs_url) -> list[tuple[str, str]]:
    from .brevo import load
    if not load().get("list_id"):
        return []
    form = ('<form class="sub-form" action="/api/subscribe" method="post">'
            '<input type="email" name="email" placeholder="почта" required autocomplete="email">'
            '<input type="text" name="website" tabindex="-1" autocomplete="off" style="display:none">'
            '<button type="submit">Подписаться</button></form>'
            '<p id="sub-msg" class="src"></p><script>var q=location.search;var m=document.getElementById("sub-msg");'
            'if(q.indexOf("ok=1")>-1)m.textContent="Готово. Первое письмо придёт в субботу.";'
            'else if(q.indexOf("err=1")>-1)m.textContent="Не получилось. Проверьте адрес и попробуйте ещё раз.";</script>')
    body = ('<h1 class="sec">Письмо по субботам</h1><article><p>Раз в неделю: главное за неделю, разборы и позиция редакции. '
            f'Приходит в субботу днём.</p>{form}</article>')
    (site / "podpiska.html").unlink(missing_ok=True)
    (site / "_redirects").write_text("/podpiska /subscribe 301\n", encoding="utf-8")
    (site / "subscribe.html").write_text(page(f"Рассылка — {TITLE}", "Еженедельное письмо: главное за неделю, разборы, позиция.",
                                             body, abs_url("subscribe")), encoding="utf-8")
    return [(abs_url("subscribe"), "")]


def send_newsletter() -> int:
    """Субботнее письмо через Brevo. Запуск: python -m bot.extras newsletter"""
    from .brevo import load
    st = load()
    key = os.environ.get("BREVO_API_KEY", "").strip()
    list_id, sender = str(st.get("list_id") or ""), st.get("sender") or ""
    if not (key and list_id and sender):
        log("рассылка: Brevo не настроен (ключ или state/brevo.json), пропускаю")
        return 0
    site_url = (os.environ.get("SITE_URL") or "https://cuibono.press").rstrip("/")
    from .site import _slug
    posts = json.loads((Path("state") / "site-posts.json").read_text(encoding="utf-8"))
    since = (datetime.now(TZ) - timedelta(days=7)).isoformat()
    week = sorted([p for p in posts if p["time_iso"] >= since and p.get("paragraphs")], key=lambda p: p["time_iso"], reverse=True)
    def block(kind, name, n):
        ps = [p for p in week if p.get("kind") == kind][:n]
        if not ps:
            return ""
        items = "".join(f'<p style="margin:0 0 20px;font-family:Helvetica,Arial,sans-serif"><a href="{site_url}/p/{_slug(p)}" style="font-family:Helvetica,Arial,sans-serif;color:#0B0D10;font-weight:bold;font-size:18px;line-height:24px;text-decoration:none">'
                        f'{html.escape(p["headline"])}</a><br><span style="font-family:Helvetica,Arial,sans-serif;color:#555555;font-size:15px;line-height:22px;font-style:italic">{html.escape(p.get("subhead", ""))}</span></p>' for p in ps)
        return f'<p style="font-family:monospace;font-size:12px;letter-spacing:1px;color:#6B6F72;margin:32px 0 14px">{name.upper()}</p>{items}'
    def week_block():
        w = next((p for p in sorted(posts, key=lambda p: p["time_iso"], reverse=True)
                  if p.get("kind") == "week" and p["time_iso"] >= since and p.get("paragraphs")), None)
        if not w:
            return ""
        def para(t):
            t = html.escape(t)
            t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
            t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{html.unescape(m.group(2))}" style="color:#0B0D10">{m.group(1)}</a>', t)
            return f'<p style="margin:0 0 14px;font-family:Helvetica,Arial,sans-serif;font-size:16px;line-height:24px;color:#0B0D10">{t}</p>'
        return (f'<p style="font-family:monospace;font-size:12px;letter-spacing:1px;color:#6B6F72;margin:32px 0 14px">ГЛАВНОЕ ЗА НЕДЕЛЮ</p>'
                + "".join(para(x) for x in w["paragraphs"]))
    body = week_block() + block("analysis", "Разборы", 10) + block("position", "Позиция", 14)
    if not body:
        log("рассылка: за неделю нечего отправлять")
        return 0
    today = datetime.now(TZ).date()
    host = site_url.split("//")[-1]
    htm = (f'<div style="max-width:600px;margin:0 auto;padding:24px;font-family:Helvetica,Arial,sans-serif;color:#0B0D10">'
           f'<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:30px;line-height:34px;margin:0">Cui Bono?</p>'
           f'<p style="font-family:monospace;font-size:12px;letter-spacing:1px;color:#6B6F72;margin:6px 0 0">НЕДЕЛЯ ДО {today:%d.%m.%Y}</p>'
           f'{body}<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;line-height:20px;color:#6B6F72;margin:36px 0 0;border-top:1px solid #DDDDDD;padding-top:14px">'
           f'Еженедельное письмо Cui Bono? — <a href="{site_url}/subscribe" style="color:#6B6F72">{host}/subscribe</a> · '
           f'<a href="https://t.me/cuibono_press" style="color:#6B6F72">Telegram</a> · '
           f'<a href="{{{{ unsubscribe }}}}" style="color:#6B6F72">отписаться</a></p></div>')
    h = {"api-key": key, "content-type": "application/json", "accept": "application/json"}
    test = os.environ.get("TEST_EMAIL", "").strip()
    if test:  # пробное письмо одному адресу, списку не уходит
        r = requests.post("https://api.brevo.com/v3/smtp/email", headers=h, timeout=60, json={
            "sender": {"name": "Cui Bono?", "email": sender}, "to": [{"email": test}],
            "subject": f"[проба] Cui Bono? — неделя до {_ru(today)}", "htmlContent": htm.replace("{{ unsubscribe }}", "#")})
        log(f"рассылка: проба {r.status_code} {r.text[:200]}")
        return 0 if r.ok else 1
    # письмо приходит дважды — от будильника Cloudflare и от запасного расписания GitHub; уходит только одно в неделю
    mark = Path("state") / "newsletter-sent.txt"
    week = f"{today:%G-W%V}"
    if mark.exists() and mark.read_text(encoding="utf-8").strip() == week:
        log(f"рассылка: письмо за {week} уже ушло — пропускаю")
        return 0
    # вторая страховка — сам Brevo: кампания этой недели уже создана или отправлена
    try:
        got = requests.get("https://api.brevo.com/v3/emailCampaigns", headers=h, timeout=60,
                           params={"limit": 20, "sort": "desc"}).json().get("campaigns") or []
        if any(c.get("name") == f"Неделя {today:%Y-%m-%d}" for c in got):
            log("рассылка: в Brevo кампания этой недели уже есть — пропускаю")
            mark.write_text(week, encoding="utf-8")
            return 0
    except Exception as e:  # noqa: BLE001
        log(f"рассылка: Brevo не ответил на проверку ({e}) — без проверки не отправляю")
        return 1
    r = requests.post("https://api.brevo.com/v3/emailCampaigns", headers=h, timeout=60, json={
        "name": f"Неделя {today:%Y-%m-%d}", "subject": f"Cui Bono? — неделя до {_ru(today)}",
        "sender": {"name": "Cui Bono?", "email": sender}, "htmlContent": htm,
        "recipients": {"listIds": [int(list_id)]}})
    if not r.ok:
        log(f"рассылка: кампания не создана {r.status_code} {r.text[:300]}")
        return 1
    cid = r.json()["id"]
    r = requests.post(f"https://api.brevo.com/v3/emailCampaigns/{cid}/sendNow", headers=h, timeout=60)
    log(f"рассылка: кампания {cid}, отправка {r.status_code} {r.text[:200]}")
    if r.ok:
        mark.write_text(week, encoding="utf-8")
    return 0 if r.ok else 1


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["newsletter"]:
        sys.exit(send_newsletter())
