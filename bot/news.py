"""Сбор новостей из RSS изданий и загрузка текстов статей. Без платных API."""

import calendar
import os
import time
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests
import trafilatura

# Список можно менять переменной NEWS_FEEDS (через запятую). Недоступные ленты пропускаются.
DEFAULT_FEEDS = [
    # Россия: ленты и деловая пресса
    "https://www.interfax.ru/rss.asp",
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.kommersant.ru/RSS/section-economics.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.forbes.ru/newrss.xml",
    "https://frankmedia.ru/feed",
    "https://www.banki.ru/xml/news.rss",
    "https://expert.ru/rss/",
    "https://www.ng.ru/rss/",
    "https://lenta.ru/rss/news",
    "https://www.cnews.ru/inc/rss/news.xml",
    "https://www.fontanka.ru/rss-feeds/rss.xml",
    # Первоисточники
    "https://www.cbr.ru/rss/RssPress",
    # Независимые и зарубежные на русском
    "https://meduza.io/rss/all",
    "https://thebell.io/feed",
    "https://ru.themoscowtimes.com/rss/news",
    "https://novayagazeta.eu/feed/rss",
    "https://theins.ru/feed",
    "https://feeds.bbci.co.uk/russian/rss.xml",
    "https://rss.dw.com/rdf/rss-ru-all",
    # Постсоветские страны
    "https://kun.uz/ru/news/rss",
    "https://24.kg/rss/",
    "https://eurasianet.org/rss",
    "https://civil.ge/feed",
    "https://oc-media.org/feed/",
    # Аналитика по российской экономике, в том числе оборонной
    "https://re-russia.net/feed/",
    # Мир: экономика и рынки
    "https://www.themoscowtimes.com/rss/news",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/world/rss",
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://www.economist.com/business/rss.xml",
    "https://www.economist.com/international/rss.xml",
    "https://www.ft.com/rss/home",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://oilprice.com/rss/main",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    # Азия и Китай
    "https://www.scmp.com/rss/92/feed",
    "https://asia.nikkei.com/rss/feed/nar",
    "https://thediplomat.com/feed/",
]

UA = {"User-Agent": "Mozilla/5.0 (compatible; CuiBonoBot/1.0)"}


def _feeds() -> list[str]:
    env = os.environ.get("NEWS_FEEDS")
    return [u.strip() for u in env.split(",") if u.strip()] if env else DEFAULT_FEEDS


def _read_feed(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        f = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001
        print(f"[cui] лента недоступна: {url} ({e})", flush=True)
        return []
    source = (f.feed.get("title") or url).strip()
    items = []
    for e in f.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(t) if t else time.time()
        summary = trafilatura.extract(e.get("summary", "") or "", include_comments=False) or e.get("summary", "")
        items.append({
            "title": (e.get("title") or "").strip(),
            "link": e.get("link") or "",
            "summary": (summary or "")[:400],
            "source": source,
            "ts": ts,
        })
    return items


def collect(hours: int = 48, limit: int = 320) -> list[dict]:
    feeds = _feeds()
    with ThreadPoolExecutor(8) as ex:
        batches = list(ex.map(_read_feed, feeds))
    cutoff = time.time() - hours * 3600
    # отчёт по лентам: сколько свежих новостей дала каждая и когда была последняя
    report = []
    for url, b in zip(feeds, batches):
        fresh = [i for i in b if i["ts"] >= cutoff]
        last = time.strftime("%d.%m %H:%M", time.gmtime(max((i["ts"] for i in b), default=0))) if b else "—"
        report.append(f"{'OK ' if fresh else 'НЕТ'} {len(fresh):>3} за {hours} ч, последняя {last} UTC  {url}")
    print("[cui] ленты:\n" + "\n".join(report), flush=True)
    try:
        from pathlib import Path
        Path("out").mkdir(exist_ok=True)
        Path("out/feeds.txt").write_text("\n".join(report), encoding="utf-8")
    except OSError:
        pass
    seen, items = set(), []
    for b in batches:
        b = sorted(b, key=lambda i: i["ts"], reverse=True)[:25]  # одна лента не забивает весь список
        for it in b:
            key = it["title"].lower()[:80]
            if it["link"] and it["title"] and it["ts"] >= cutoff and key not in seen:
                seen.add(key)
                items.append(it)
    items.sort(key=lambda i: i["ts"], reverse=True)
    print(f"[cui] новостей за {hours} ч: {len(items)}", flush=True)
    return items[:limit]


def fetch_text(url: str, fallback: str = "", max_chars: int = 7000) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        text = trafilatura.extract(r.text, url=url, include_comments=False, include_tables=True) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if len(text) < 300:
        text = (text + "\n" + fallback).strip()
    return text[:max_chars]
