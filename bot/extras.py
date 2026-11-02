"""Дополнительные страницы сайта, которые растят издание без денег и без спама.

- /en/...           — английская версия разборов (перевод сильной моделью, кэш в site-posts.json, поле "en");
- /data/dividendy/  — дивиденды крупных эмитентов Мосбиржи (ISS API), страница на каждую компанию;
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


# ---------- дивиденды ----------

def _dividends(secid: str) -> list[dict]:
    r = requests.get(f"https://iss.moex.com/iss/securities/{secid}/dividends.json",
                     params={"iss.meta": "off"}, headers=UA, timeout=30).json()
    cols = r["dividends"]["columns"]
    rows = [dict(zip(cols, x)) for x in r["dividends"]["data"]]
    return sorted([x for x in rows if x.get("registryclosedate") and x.get("value") is not None],
                  key=lambda x: x["registryclosedate"])


def build_dividends(site: Path, page, abs_url, list_posts, posts, ld) -> tuple[list[tuple[str, str]], list[tuple[date, str, str]]]:
    out = site / "data" / "dividendy"
    out.mkdir(parents=True, exist_ok=True)
    cache_f = Path("state") / "dividends.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    urls, cards, upcoming = [], [], []
    today = datetime.now(TZ).date()
    for secid, name in ISSUERS.items():
        try:
            rows = _dividends(secid)
            cache[secid] = rows
        except Exception as e:  # noqa: BLE001
            log(f"дивиденды: {secid} не получены ({e})")
            rows = cache.get(secid) or []
        if not rows:
            continue
        slug = secid.lower()
        last = rows[-1]
        ld_ = date.fromisoformat(last["registryclosedate"])
        cur = " ₽" if (last.get("currencyid") or "RUB") in ("RUB", "SUR") else f" {last.get('currencyid')}"
        by_year: dict[int, float] = {}
        for x in rows:
            by_year[int(x["registryclosedate"][:4])] = by_year.get(int(x["registryclosedate"][:4]), 0) + float(x["value"])
        for x in rows:
            d = date.fromisoformat(x["registryclosedate"])
            if d >= today:
                upcoming.append((d, f"Отсечка дивидендов: {name} ({secid}) — {_n(float(x['value']))}{cur}",
                                 abs_url(f"data/dividendy/{slug}")))
        when = "закрытие реестра" + (" назначено на " if ld_ >= today else " было ") + _ru(ld_)
        lead = (f"Последний объявленный дивиденд {name} — <b>{_n(float(last['value']))}{cur}</b> на акцию, {when}. "
                f"За {max(by_year)} год — {_n(by_year[max(by_year)])}{cur} на акцию.")
        yrows = "".join(f"<tr><td>{y}</td><td>{_n(v)}{cur}</td></tr>" for y, v in sorted(by_year.items(), reverse=True))
        hrows = "".join(f"<tr><td>{date.fromisoformat(x['registryclosedate']):%d.%m.%Y}</td><td>{_n(float(x['value']))}{cur}</td></tr>"
                        for x in reversed(rows))
        words = [name.split(" (")[0].lower(), secid.lower()]
        rel = [p for p in posts if p.get("paragraphs") and any(w in (p["headline"] + " " + " ".join(
            x["name"] for x in p.get("entities") or [])).lower() for w in words)][:6]
        body = (f'<div class="kicker">ДАННЫЕ · ДИВИДЕНДЫ</div><h1>Дивиденды {html.escape(name)} ({secid})</h1>'
                f'<p class="lead">{lead}</p><h2 class="dh">По годам</h2><table class="data"><thead><tr><th>Год</th><th>На акцию</th></tr></thead><tbody>{yrows}</tbody></table>'
                f'<h2 class="dh">Все выплаты</h2><table class="data"><thead><tr><th>Закрытие реестра</th><th>На акцию</th></tr></thead><tbody>{hrows}</tbody></table>'
                f'<p class="src">Источник: <a href="https://www.moex.com/ru/issue.aspx?code={secid}" rel="nofollow noopener">Московская биржа</a>. '
                f'Страница обновляется автоматически. <a href="/data/dividendy">Все компании</a> · <a href="/calendar">Календарь отсечек</a></p>'
                + (f'<h2 class="dh">Тексты по теме</h2>{list_posts(rel, "../../")}' if rel else ""))
        desc = re.sub(r"<[^>]+>", "", lead)[:200]
        ds = ld({"@context": "https://schema.org", "@type": "Dataset", "name": f"Дивиденды {name}", "description": desc,
                 "url": abs_url(f"data/dividendy/{slug}"), "dateModified": today.isoformat(), "isAccessibleForFree": True,
                 "creator": {"@type": "Organization", "name": "Московская биржа"}})
        (out / f"{slug}.html").write_text(page(f"Дивиденды {name} ({secid}) {today.year}: история выплат и даты отсечки — {TITLE}",
                                               desc, body, abs_url(f"data/dividendy/{slug}"), depth=2, extra_head=ds), encoding="utf-8")
        urls.append((abs_url(f"data/dividendy/{slug}"), today.isoformat()))
        cards.append((slug, name, secid, f"{_n(float(last['value']))}{cur} · {ld_:%d.%m.%Y}"))
    cache_f.parent.mkdir(exist_ok=True)
    cache_f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if cards:
        trs = "".join(f'<tr><td><a href="/data/dividendy/{s}">{html.escape(n)}</a></td><td>{t}</td><td>{v}</td></tr>' for s, n, t, v in cards)
        soon = "".join(f"<li>{d:%d.%m.%Y} — <a href=\"{u}\">{html.escape(t.split(': ', 1)[1])}</a></li>" for d, t, u in sorted(upcoming))
        body = (f'<h1 class="sec">Дивиденды российских компаний</h1>'
                + (f'<h2 class="dh">Ближайшие отсечки</h2><ul>{soon}</ul>' if soon else "")
                + f'<table class="data"><thead><tr><th>Компания</th><th>Тикер</th><th>Последний дивиденд</th></tr></thead><tbody>{trs}</tbody></table>'
                f'<p class="src">Источник: Московская биржа. Обновляется автоматически.</p>')
        (out / "index.html").write_text(page(f"Дивиденды российских акций {today.year}: календарь отсечек и история — {TITLE}",
                                             "Дивиденды крупнейших компаний Мосбиржи: последние выплаты, история по годам, ближайшие даты закрытия реестра.",
                                             body, abs_url("data/dividendy"), depth=2), encoding="utf-8")
        urls.append((abs_url("data/dividendy"), today.isoformat()))
    log(f"дивиденды: {len(cards)} компаний")
    return urls, upcoming


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
    body = (f'<div class="kicker">ДАННЫЕ · КАЛЕНДАРЬ</div><h1>Календарь: заседания ЦБ, инфляция, дивиденды</h1>'
            f'<p class="lead">Следующее решение Банка России по ключевой ставке — '
            f'<b>{_ru(min(date.fromisoformat(d) for d in CBR_MEETINGS if date.fromisoformat(d) >= today))}</b>, пресс-релиз в 13:30 МСК. '
            f'Недельную инфляцию Росстат публикует по средам в 19:00.</p>'
            f'<p><a href="{webcal}">Подписаться в календаре</a> · <a href="https://calendar.google.com/calendar/r?cid={html.escape(webcal)}">Google Календарь</a> · '
            f'<a href="/calendar.ics">Файл .ics</a></p>'
            f'<table class="data"><thead><tr><th>Дата</th><th>Время</th><th>Событие</th></tr></thead><tbody>{trs}</tbody></table>'
            f'<p class="src">Источники: <a href="https://www.cbr.ru/dkp/cal_mp/" rel="nofollow noopener">календарь Банка России</a>, Московская биржа.</p>')
    (site / "calendar.html").write_text(page(
        f"Заседания ЦБ по ключевой ставке {today.year}–{today.year + 1}: даты, инфляция, дивиденды — {TITLE}",
        "Даты заседаний Банка России по ключевой ставке, публикации инфляции и отсечки дивидендов. Подписка на календарь.",
        body, abs_url("calendar")), encoding="utf-8")
    return [(abs_url("calendar"), today.isoformat())]


# ---------- «угадай цифру» ----------

def build_quiz(site: Path, page, abs_url) -> list[tuple[str, str]]:
    from .data_pages import currency_history
    pts = currency_history("R01375", days=30)
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
    body = (f'<div class="kicker">ЦИФРА НЕДЕЛИ</div><h1>Как изменился курс юаня за неделю?</h1>'
            f'<p class="sub">Официальный курс Банка России, {d0:%d.%m} — {d1:%d.%m.%Y}</p>'
            f'<div class="quiz">{btns}</div><p id="ans" class="lead" hidden>{html.escape(answer)} '
            f'<a href="/data/kurs-yuanya">График и история</a></p>'
            '<style>.quiz{display:flex;gap:12px;margin:24px 0}.q{font:600 20px Commissioner,sans-serif;padding:14px 22px;'
            'background:#15191e;color:#EDE9E2;border:1px solid #2F343C;cursor:pointer}.q.ok{border-color:#EDE9E2}.q.no{opacity:.4}</style>'
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
            'на оригинал на cuibono.press. Данные со страниц раздела «Данные» можно использовать с той же ссылкой.</p>'
            '<p>Лицензия: <a href="https://creativecommons.org/licenses/by-nd/4.0/deed.ru" rel="license noopener">CC BY-ND 4.0</a>.</p>'
            '</article>')
    (site / "perepechatka.html").write_text(page(f"Перепечатка — {TITLE}", "Условия перепечатки материалов издания.",
                                                 body, abs_url("perepechatka")), encoding="utf-8")
    top = [p for p in posts if p.get("kind") == "analysis" and p.get("slug")][:20]
    llms = ("# Cui Bono?\n\n> Аналитическое издание об экономике и власти (Россия). Ежедневные разборы, "
            "позиция редакции и страницы официальных данных (ЦБ, Мосбиржа), обновляемые автоматически.\n\n"
            "## Данные\n"
            f"- [Ключевая ставка ЦБ]({abs_url('data/klyuchevaya-stavka')})\n- [Курс доллара ЦБ]({abs_url('data/kurs-dollara')})\n"
            f"- [Курс юаня ЦБ]({abs_url('data/kurs-yuanya')})\n- [Индекс Мосбиржи]({abs_url('data/indeks-mosbirzhi')})\n"
            f"- [Дивиденды российских компаний]({abs_url('data/dividendy')})\n- [Календарь заседаний ЦБ]({abs_url('calendar')})\n\n"
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
        ("дивиденды", lambda: build_dividends(site, page, abs_url, list_posts, posts, ld)),
        ("игра", lambda: build_quiz(site, page, abs_url)),
        ("перепечатка", lambda: build_static(site, page, abs_url, posts)),
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
    return urls
