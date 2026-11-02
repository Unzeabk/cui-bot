"""Вечные страницы данных: ключевая ставка, курсы ЦБ, индекс Мосбиржи, золото.

По таким запросам («ключевая ставка цб сегодня», «курс доллара цб») ищут каждый день. Страницы живут
по постоянным адресам /data/..., обновляются при каждой сборке сайта из первоисточников (ЦБ, Мосбиржа)
и ведут на тексты издания по теме. Модель цифры не трогает.
Если источник не ответил, страница не перезаписывается: остаётся прошлая версия.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import requests

from .main import TZ, log

UA = {"User-Agent": "Mozilla/5.0 (compatible; CuiBonoBot/1.0)"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def _ru_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _num(x: float, digits: int = 2) -> str:
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


# ---------- источники ----------

def key_rate_history() -> list[tuple[date, float]]:
    """Все изменения ключевой ставки с её введения (сентябрь 2013)."""
    now = datetime.now(TZ).date()
    pts: list[tuple[date, float]] = []
    try:
        r = requests.get("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx/KeyRateXML",
                         params={"fromDate": "2013-09-13", "ToDate": now.isoformat()}, headers=UA, timeout=40)
        for kr in ET.fromstring(r.content).iter():
            if kr.tag.endswith("KR"):
                dt = next((c.text for c in kr if c.tag.endswith("DT")), None)
                rate = next((c.text for c in kr if c.tag.endswith("Rate")), None)
                if dt and rate:
                    pts.append((date.fromisoformat(dt[:10]), float(rate)))
    except Exception as e:  # noqa: BLE001
        log(f"данные: веб-сервис ставки не ответил ({e})")
    if not pts:
        r = requests.get("https://www.cbr.ru/hd_base/KeyRate/", headers=UA, timeout=40, params={
            "UniDbQuery.Posted": "True", "UniDbQuery.From": "13.09.2013", "UniDbQuery.To": now.strftime("%d.%m.%Y")})
        for d, v in re.findall(r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>\s*([\d,]+)\s*</td>", r.text):
            pts.append((datetime.strptime(d, "%d.%m.%Y").date(), float(v.replace(",", "."))))
    pts.sort()
    return pts


def currency_history(code: str, days: int = 400) -> list[tuple[date, float]]:
    now = datetime.now(TZ).date()
    r = requests.get("https://www.cbr.ru/scripts/XML_dynamic.asp", headers=UA, timeout=40, params={
        "date_req1": (now - timedelta(days=days)).strftime("%d/%m/%Y"),
        "date_req2": (now + timedelta(days=1)).strftime("%d/%m/%Y"), "VAL_NM_RQ": code})
    out = []
    for rec in ET.fromstring(r.content).findall("Record"):
        nom = float((rec.findtext("Nominal") or "1").replace(",", "."))
        out.append((datetime.strptime(rec.get("Date"), "%d.%m.%Y").date(),
                    float(rec.findtext("Value").replace(",", ".")) / nom))
    out.sort()
    return out


def moex_history(days: int = 400) -> list[tuple[date, float]]:
    now = datetime.now(TZ).date()
    out, start = [], 0
    while True:
        r = requests.get("https://iss.moex.com/iss/history/engines/stock/markets/index/securities/IMOEX.json",
                         params={"from": (now - timedelta(days=days)).isoformat(), "iss.meta": "off",
                                 "history.columns": "TRADEDATE,CLOSE", "start": start}, headers=UA, timeout=40).json()
        rows = r["history"]["data"]
        out += [(date.fromisoformat(d), float(c)) for d, c in rows if c]
        if len(rows) < 100:
            break
        start += len(rows)
    out.sort()
    return out


def gold_history(days: int = 400) -> list[tuple[date, float]]:
    now = datetime.now(TZ).date()
    r = requests.get("https://www.cbr.ru/scripts/xml_metall.asp", headers=UA, timeout=40, params={
        "date_req1": (now - timedelta(days=days)).strftime("%d/%m/%Y"), "date_req2": now.strftime("%d/%m/%Y")})
    out = [(datetime.strptime(rec.get("Date"), "%d.%m.%Y").date(), float(rec.findtext("Buy").replace(",", ".")))
           for rec in ET.fromstring(r.content).findall("Record") if rec.get("Code") == "1"]
    out.sort()
    return out


# ---------- график ----------

def svg_chart(pts: list[tuple[date, float]], step: bool = False, unit: str = "") -> str:
    if len(pts) < 2:
        return ""
    w, h, pl, pr, pt, pb = 680, 260, 8, 64, 16, 28
    xs = [(d - pts[0][0]).days for d, _ in pts]
    span = max(xs[-1], 1)
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def X(x):
        return pl + (w - pl - pr) * x / span

    def Y(v):
        return pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo))

    pts_xy = []
    for i, (x, (_, v)) in enumerate(zip(xs, pts)):
        if step and i:
            pts_xy.append(f"{X(x):.1f},{Y(pts[i - 1][1]):.1f}")
        pts_xy.append(f"{X(x):.1f},{Y(v):.1f}")
    if step:
        pts_xy.append(f"{X(span):.1f},{Y(pts[-1][1]):.1f}")
    grid = ""
    for k in range(4):
        v = lo + pad + (hi - lo - 2 * pad) * k / 3
        grid += (f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#262B31" stroke-width="1"/>'
                 f'<text x="{w - pr + 8}" y="{Y(v) + 4:.1f}" fill="#6B6F72" font-size="11" font-family="IBM Plex Mono,monospace">{_num(v, 1 if v < 1000 else 0)}{unit}</text>')
    last = pts[-1]
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="График">{grid}'
            f'<polyline fill="none" stroke="#EDE9E2" stroke-width="1.6" points="{" ".join(pts_xy)}"/>'
            f'<circle cx="{X(span):.1f}" cy="{Y(last[1]):.1f}" r="3.5" fill="#EDE9E2"/>'
            f'<text x="{pl}" y="{h - 8}" fill="#6B6F72" font-size="11" font-family="IBM Plex Mono,monospace">{pts[0][0]:%d.%m.%Y}</text>'
            f'<text x="{w - pr}" y="{h - 8}" fill="#6B6F72" font-size="11" text-anchor="end" font-family="IBM Plex Mono,monospace">{last[0]:%d.%m.%Y}</text>'
            "</svg>")


# ---------- страницы ----------

def _change(pts: list[tuple[date, float]], days: int) -> tuple[float, float] | None:
    last_d, last = pts[-1]
    before = next((v for d, v in reversed(pts) if d <= last_d - timedelta(days=days)), None)
    if before is None:
        return None
    return last - before, (last / before - 1) * 100


def _changes_table(pts, digits, unit) -> str:
    rows = ""
    for label, days in (("За день", 1), ("За неделю", 7), ("За месяц", 30), ("За год", 365)):
        c = _change(pts, days)
        if c:
            sign = "+" if c[0] > 0 else ("−" if c[0] < 0 else "")
            rows += f"<tr><td>{label}</td><td>{sign}{_num(abs(c[0]), digits)}{unit}</td><td>{sign}{_num(abs(c[1]), 1)}%</td></tr>"
    return f'<table class="data"><tbody>{rows}</tbody></table>'


def _recent_table(pts, digits, unit, n=30) -> str:
    rows = "".join(f"<tr><td>{d:%d.%m.%Y}</td><td>{_num(v, digits)}{unit}</td></tr>" for d, v in reversed(pts[-n:]))
    return f'<table class="data"><thead><tr><th>Дата</th><th>Значение</th></tr></thead><tbody>{rows}</tbody></table>'


SERIES = [
    # slug, заголовок, имя для текста, источник, ссылка источника, единица, знаков, темы для связанных текстов
    ("key-rate", "Ключевая ставка ЦБ", "ключевая ставка Банка России", "Банк России", "https://www.cbr.ru/hd_base/KeyRate/", "%", 2,
     ["ключевая ставка", "Банк России", "ЦБ", "ставка", "инфляция", "вклады", "ипотека"]),
    ("usd-rate", "Курс доллара ЦБ", "официальный курс доллара", "Банк России", "https://www.cbr.ru/currency_base/dynamics/", " ₽", 4,
     ["доллар", "рубль", "курс", "валюта", "Банк России"]),
    ("eur-rate", "Курс евро ЦБ", "официальный курс евро", "Банк России", "https://www.cbr.ru/currency_base/dynamics/", " ₽", 4,
     ["евро", "рубль", "курс", "валюта", "Европа"]),
    ("cny-rate", "Курс юаня ЦБ", "официальный курс юаня", "Банк России", "https://www.cbr.ru/currency_base/dynamics/", " ₽", 4,
     ["юань", "рубль", "Китай", "расчёты", "курс"]),
    ("moex-index", "Индекс Мосбиржи", "индекс Московской биржи (IMOEX)", "Московская биржа", "https://www.moex.com/ru/index/IMOEX", "", 2,
     ["Мосбиржа", "Московская биржа", "акции", "IMOEX", "рынок"]),
    ("gold", "Цена золота ЦБ", "учётная цена золота Банка России за грамм", "Банк России", "https://www.cbr.ru/hd_base/metall/metall_base_new/", " ₽", 2,
     ["золото", "Банк России", "резервы"]),
]


def _fetch(slug: str):
    if slug == "key-rate":
        return key_rate_history()
    if slug == "usd-rate":
        return currency_history("R01235")
    if slug == "eur-rate":
        return currency_history("R01239")
    if slug == "cny-rate":
        return currency_history("R01375")
    if slug == "moex-index":
        return moex_history()
    if slug == "gold":
        return gold_history()
    return []


def _key_rate_changes(pts):
    out = []
    for d, v in pts:
        if not out or abs(out[-1][1] - v) > 1e-9:
            out.append((d, v))
    return out


def build_data(site, page, abs_url, list_posts, posts, ld) -> list[tuple[str, str]]:
    """Пишет /data/*.html и /data.html. Возвращает адреса для карты сайта."""
    from pathlib import Path
    site = Path(site)
    (site / "data").mkdir(exist_ok=True)
    urls, cards = [], []
    for slug, title, name, src, src_url, unit, digits, words in SERIES:
        try:
            pts = _fetch(slug)
        except Exception as e:  # noqa: BLE001
            log(f"данные: {slug} не получены ({e})")
            pts = []
        f = site / "data" / f"{slug}.html"
        if len(pts) < 2:
            if f.exists():
                urls.append((abs_url(f"data/{slug}"), ""))
                cards.append((slug, title, ""))
            continue
        last_d, last = pts[-1]
        if slug == "key-rate":
            ch = _key_rate_changes(pts)
            since, prev = ch[-1][0], (ch[-2][1] if len(ch) > 1 else None)
            lead = (f"Ключевая ставка Банка России — <b>{_num(last)}%</b> годовых. Действует с {_ru_date(since)}"
                    + (f"; до этого была {_num(prev)}%." if prev is not None else "."))
            start = last_d - timedelta(days=365 * 5)
            before = [p for p in ch if p[0] < start]
            win = ([(start, before[-1][1])] if before else []) + [p for p in ch if p[0] >= start]
            chart = svg_chart(win + [(last_d, last)], step=True, unit="%")
            rows = "".join(f"<tr><td>{d:%d.%m.%Y}</td><td>{_num(v)}%</td></tr>" for d, v in reversed(ch))
            table = f'<h2 class="dh">История решений</h2><table class="data"><thead><tr><th>С даты</th><th>Ставка</th></tr></thead><tbody>{rows}</tbody></table>'
            value = f"{_num(last)}%"
        else:
            c1 = _change(pts, 1)
            sign = "" if not c1 or abs(c1[0]) < 1e-9 else ("выше" if c1[0] > 0 else "ниже")
            lead = (f"{name[:1].upper() + name[1:]} на {_ru_date(last_d)} — <b>{_num(last, digits)}{unit}</b>"
                    + (f", на {_num(abs(c1[0]), digits)}{unit} {sign} предыдущего значения." if sign else "."))
            chart = svg_chart([p for p in pts if p[0] >= last_d - timedelta(days=365)], unit="")
            table = (f'<h2 class="dh">Изменение</h2>{_changes_table(pts, digits, unit)}'
                     f'<h2 class="dh">Последние 30 значений</h2>{_recent_table(pts, digits, unit)}')
            value = f"{_num(last, digits)}{unit}"
        low = [w.lower() for w in words]
        rel = [p for p in posts if p.get("paragraphs") and any(
            w in (" ".join([p["headline"], p.get("subhead", "")] + [x["name"] for x in p.get("entities") or []])).lower()
            for w in low)][:8]
        rel_html = (f'<h2 class="dh">Тексты по теме</h2>{list_posts(rel, "../")}' if rel else "")
        body = (f'<div class="kicker">ПОКАЗАТЕЛИ · обновлено {last_d:%d.%m.%Y}</div><h1>{html.escape(title)} на сегодня</h1>'
                f'<p class="lead">{lead}</p>{chart}{table}'
                f'<p class="src">Источник: <a href="{src_url}" rel="nofollow noopener">{html.escape(src)}</a>. '
                f'Страница обновляется автоматически.</p>{rel_html}')
        desc = re.sub(r"<[^>]+>", "", lead)[:200]
        dataset = ld({"@context": "https://schema.org", "@type": "Dataset", "name": title, "description": desc,
                      "url": abs_url(f"data/{slug}"), "dateModified": last_d.isoformat(),
                      "creator": {"@type": "Organization", "name": src, "url": src_url},
                      "temporalCoverage": f"{pts[0][0].isoformat()}/{last_d.isoformat()}", "isAccessibleForFree": True})
        f.write_text(page(f"{title} на сегодня, {_ru_date(last_d)}: {value} — график и история", desc, body,
                          abs_url(f"data/{slug}"), depth=1, extra_head=dataset), encoding="utf-8")
        urls.append((abs_url(f"data/{slug}"), last_d.isoformat()))
        cards.append((slug, title, value))
    if cards:
        items = "".join(f'<li><div class="kicker">ПОКАЗАТЕЛИ</div><h2><a href="/data/{s}">{html.escape(t)}</a></h2>'
                        f'<p class="sub">{html.escape(v)}</p></li>' for s, t, v in cards)
        (site / "data.html").write_text(page("Показатели: ставка ЦБ, курсы валют, индекс Мосбиржи, золото — Cui Bono?",
                                             "Ключевая ставка, официальные курсы доллара, евро и юаня, индекс Мосбиржи и цена золота — на сегодня и в истории.",
                                             f'<h1 class="sec">Показатели</h1><ul class="list">{items}</ul>', abs_url("data")),
                                        encoding="utf-8")
        urls.append((abs_url("data"), ""))
    log(f"данные: {len([c for c in cards if c[2]])} страниц обновлено")
    return urls
