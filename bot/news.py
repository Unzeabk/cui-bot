"""Сбор новостей из RSS изданий и публичных Telegram-каналов, загрузка текстов статей. Без платных API."""

import calendar
import html
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests
import trafilatura

# Список можно менять переменной NEWS_FEEDS (через запятую). Недоступные ленты пропускаются,
# отчёт о каждой — out/feeds.txt в архиве запуска: строки «НЕТ» заменять или удалять.
RUSSIA_FEEDS = [
    # Ленты и деловая пресса
    "https://www.interfax.ru/rss.asp",
    "https://www.finmarket.ru/rss/mainnews.asp",
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.kommersant.ru/RSS/section-economics.xml",
    "https://www.kommersant.ru/RSS/section-business.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.vedomosti.ru/rss/rubric/economics",
    "https://www.vedomosti.ru/rss/rubric/finance",
    "https://www.vedomosti.ru/rss/rubric/business",
    "https://iz.ru/xml/rss/all.xml",
    "https://frankmedia.ru/feed",
    "https://www.bfm.ru/news.rss",
    "https://www.ng.ru/rss/",
    "https://lenta.ru/rss/news",
    "https://www.cnews.ru/inc/rss/news.xml",
    # Первоисточники: ЦБ, правительство, официальные публикации законов
    "https://www.cbr.ru/rss/RssPress",
    "http://government.ru/all/rss/",
    "https://rg.ru/xml/index.xml",
    "https://www.pnp.ru/rss/index.xml",
    # Регионы: молчащие, но платящие
    "https://www.fontanka.ru/rss-feeds/rss.xml",
    "https://ura.news/rss",
    "https://www.e1.ru/rss-feeds/rss.xml",
    "https://ngs.ru/rss-feeds/rss.xml",
    "https://chita.ru/rss-feeds/rss.xml",
    "https://feeds.feedburner.com/amic/news",
    # Независимые и зарубежные на русском
    "https://meduza.io/rss/all",
    "https://thebell.io/feed",
    "https://ru.themoscowtimes.com/rss/news",
    "https://novayagazeta.eu/feed/rss",
    "https://theins.ru/feed",
    "https://feeds.bbci.co.uk/russian/rss.xml",
    "https://rss.dw.com/rdf/rss-ru-all",
    # Аналитика по российской экономике, в том числе оборонной
    "https://re-russia.net/feed/",
    # Постсоветские страны
    "https://kun.uz/ru/news/rss",
    "https://www.gazeta.uz/ru/rss/",
    "https://24.kg/rss/",
    "https://tengrinews.kz/news.rss",
    "https://kursiv.media/feed/",
    "https://news.am/rus/rss/",
    "https://eurasianet.org/rss",
    "https://civil.ge/feed",
    "https://oc-media.org/feed/",
]

# Мир — только экономические разделы: общие ленты (война, происшествия, шоу) съедали места в отборе.
WORLD_FEEDS = [
    # Деловая пресса
    "https://www.themoscowtimes.com/rss/news",
    "https://www.ft.com/rss/home",
    "https://www.ft.com/world?format=rss",
    "https://www.ft.com/markets?format=rss",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
    "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/world/russia/rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.politico.eu/feed/",
    # The Economist по разделам — у каждого раздела своя география
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://www.economist.com/business/rss.xml",
    "https://www.economist.com/international/rss.xml",
    "https://www.economist.com/europe/rss.xml",
    "https://www.economist.com/china/rss.xml",
    "https://www.economist.com/asia/rss.xml",
    "https://www.economist.com/middle-east-and-africa/rss.xml",
    "https://www.economist.com/the-americas/rss.xml",
    "https://www.economist.com/united-states/rss.xml",
    # Центробанки и аналитические центры
    "https://www.ecb.europa.eu/rss/press.html",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    # Сырьё, судоходство, теневой флот
    "https://oilprice.com/rss/main",
    "https://gcaptain.com/feed/",
    "https://splash247.com/feed/",
    "https://www.mining.com/feed/",
    # Деньги через границу, банки, крипта
    "https://www.paymentsdive.com/feeds/news/",
    "https://www.finextra.com/rss/headlines.aspx",
    "https://www.netinterest.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    # Технологии вне Кремниевой долины
    "https://restofworld.org/feed/latest",
    "https://therecord.media/feed",
    "https://feeds.feedburner.com/IeeeSpectrum",
    # Азия и Китай
    "https://www.scmp.com/rss/92/feed",
    "https://asia.nikkei.com/rss/feed/nar",
    "https://thediplomat.com/feed/",
    "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    # Новые рынки: Залив, Турция, Африка, Латинская Америка, Восточная Европа
    "https://www.agbi.com/feed/",
    "https://www.intellinews.com/feed/",
    "https://african.business/feed",
    "https://en.mercopress.com/rss",
]

DEFAULT_FEEDS = RUSSIA_FEEDS + WORLD_FEEDS

# Публичные Telegram-каналы: бот читает их открытую веб-версию t.me/s/<канал>, без ключей и аккаунта.
# Список можно менять переменной TG_CHANNELS (через запятую, без @; «-» выключает каналы). Только первоисточники
# и каналы со своими расчётами: пересказчиков чужих новостей не берём.
DEFAULT_TG = [
    "centralbank_russia",   # Банк России: решения раньше сайта
    "minfin",               # Минфин России
    "markettwits",          # MarketTwits: срочное по рынкам, обычно первыми
    "banksta",              # Банкста: банки и финансы, быстро
    "thebell_io",           # The Bell
    "agentstvonews",        # «Агентство»: расследования на данных
    "istories_media",       # «Важные истории»: деньги и собственность
]

# Не больше стольких новостей с одной ленты в отбор: иначе частые ленты (ТАСС, РИА) вытесняют остальные.
PER_FEED = 15

UA = {"User-Agent": "Mozilla/5.0 (compatible; CuiBonoBot/1.0)"}


def _feeds() -> list[str]:
    env = os.environ.get("NEWS_FEEDS")
    return [u.strip() for u in env.split(",") if u.strip()] if env else DEFAULT_FEEDS


def _channels() -> list[str]:
    env = os.environ.get("TG_CHANNELS")
    names = env.split(",") if env else DEFAULT_TG
    return [n.strip().lstrip("@") for n in names if n.strip() and n.strip() != "-"]


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


_TG_POST = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)
_TG_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TG_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_TG_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')


def _strip(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _read_channel(name: str) -> list[dict]:
    """Последние ~20 постов публичного канала со страницы t.me/s/<канал>."""
    url = f"https://t.me/s/{name}"
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        page = r.text
    except Exception as e:  # noqa: BLE001
        print(f"[cui] канал недоступен: {url} ({e})", flush=True)
        return []
    m = _TG_TITLE.search(page)
    source = f"{html.unescape(m.group(1)).strip()} (Telegram)" if m else f"@{name}"
    items = []
    for post, body in _TG_POST.findall(page):
        tm, tx = _TG_TIME.search(body), _TG_TEXT.search(body)
        if not tm or not tx:
            continue  # пост без текста: фото, опрос, репост без подписи
        text = _strip(tx.group(1))
        if len(text) < 80:
            continue
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(tm.group(1)).timestamp()
        except ValueError:
            ts = time.time()
        first = text.split("\n", 1)[0].strip()
        title = first if 20 <= len(first) <= 160 else text[:140].rsplit(" ", 1)[0] + "…"
        items.append({"title": title, "link": f"https://t.me/{post}", "summary": text[:400], "source": source, "ts": ts})
    return items


def _balance(batches: list[list[dict]], limit: int) -> list[dict]:
    """По кругу: сначала самая свежая новость каждой ленты, потом вторая и так далее, пока не наберётся limit."""
    queues = [sorted(b, key=lambda i: i["ts"], reverse=True)[:PER_FEED] for b in batches]
    out, depth = [], 0
    while len(out) < limit and any(len(q) > depth for q in queues):
        for q in queues:
            if len(q) > depth:
                out.append(q[depth])
                if len(out) >= limit:
                    break
        depth += 1
    return out


def collect(hours: int = 48, limit: int = 420) -> list[dict]:
    feeds, channels = _feeds(), _channels()
    sources = feeds + [f"https://t.me/s/{c}" for c in channels]
    with ThreadPoolExecutor(12) as ex:
        batches = list(ex.map(_read_feed, feeds)) + list(ex.map(_read_channel, channels))
    cutoff = time.time() - hours * 3600
    # отчёт по лентам: сколько свежих новостей дала каждая и когда была последняя
    report = []
    for url, b in zip(sources, batches):
        fresh = [i for i in b if i["ts"] >= cutoff]
        last = time.strftime("%d.%m %H:%M", time.gmtime(max((i["ts"] for i in b), default=0))) if b else "—"
        report.append(f"{'OK ' if fresh else 'НЕТ'} {len(fresh):>3} за {hours} ч, последняя {last} UTC  {url}")
    alive = sum(1 for line in report if line.startswith("OK"))
    print(f"[cui] ленты: живых {alive} из {len(report)}\n" + "\n".join(report), flush=True)
    try:
        from pathlib import Path
        Path("out").mkdir(exist_ok=True)
        Path("out/feeds.txt").write_text(f"живых {alive} из {len(report)}\n" + "\n".join(report), encoding="utf-8")
    except OSError:
        pass
    seen, clean = set(), []
    for b in batches:
        keep = []
        for it in b:
            key = it["title"].lower()[:80]
            if it["link"] and it["title"] and it["ts"] >= cutoff and key not in seen:
                seen.add(key)
                keep.append(it)
        clean.append(keep)
    items = _balance(clean, limit)
    items.sort(key=lambda i: i["ts"], reverse=True)
    print(f"[cui] новостей за {hours} ч: {len(items)} из {sum(map(len, clean))}", flush=True)
    return items


def fetch_text(url: str, fallback: str = "", max_chars: int = 7000) -> str:
    if url.startswith("https://t.me/"):
        return _fetch_tg_post(url, fallback, max_chars)
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        text = trafilatura.extract(r.text, url=url, include_comments=False, include_tables=True) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if len(text) < 300:
        text = (text + "\n" + fallback).strip()
    return text[:max_chars]


def _fetch_tg_post(url: str, fallback: str, max_chars: int) -> str:
    """Полный текст поста: страница встраивания отдаёт его без обрезки. Ссылки из поста дописываем —
    по ним писатель может найти первоисточник."""
    try:
        r = requests.get(url + "?embed=1&mode=tme", headers=UA, timeout=20)
        r.raise_for_status()
        m = _TG_TEXT.search(r.text)
        text = _strip(m.group(1)) if m else ""
        links = re.findall(r'href="(https?://(?!t\.me)[^"]+)"', m.group(1)) if m else []
        if links:
            text += "\n\nСсылки в посте: " + ", ".join(dict.fromkeys(html.unescape(u) for u in links))
    except Exception:  # noqa: BLE001
        text = ""
    return (text or fallback)[:max_chars]
