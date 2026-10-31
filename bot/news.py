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
    "https://www.interfax.ru/rss.asp",
    "https://tass.ru/rss/v2.xml",
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://meduza.io/rss/all",
    "https://thebell.io/feed",
    "https://ru.themoscowtimes.com/rss/news",
    "https://www.forbes.ru/newrss.xml",
    "https://frankmedia.ru/feed",
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


def collect(hours: int = 48, limit: int = 160) -> list[dict]:
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
