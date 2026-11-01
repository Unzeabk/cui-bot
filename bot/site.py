"""Сайт-зеркало канала: каждая заметка, позиция и разбор становятся страницей, которую находят поисковики.

Как работает:
1. После публикации в канал бот кладёт в out/ файл site-entry-*.json (write_entry).
2. Шаг workflow «Сайт» запускает `python -m bot.site site`: новые записи добавляются
   в state/site-posts.json, обложки (JPEG) — в site/img/, все страницы пересобираются.
3. Папка site/ выкладывается на Cloudflare Pages (бесплатно). Публичного репозитория нет,
   имя владельца на GitHub нигде не видно. Новые адреса сразу уходят в Яндекс и Bing (IndexNow).

Что сделано для поисковиков:
- у каждой страницы свой title, description, canonical, Open Graph, разметка NewsArticle и хлебные крошки;
- страницы рубрик и помесячный архив: ни одна страница не дальше двух кликов от главной;
- «Ещё в рубрике» под каждым текстом — внутренняя перелинковка;
- sitemap.xml, feed.xml (RSS), robots.txt, 404.html;
- страницы без текста (первые посты, до появления сайта) закрыты от индекса, чтобы не тянуть сайт вниз;
- свой домен: если SITE_URL не на github.io, пишется файл CNAME.

Каждая страница ведёт в канал. Голос издания не меняется: на сайте тот же текст, что в канале.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .compose import LINK_RE, plain
from .main import OUT, STATE, TZ, log
from .rubrics import RUBRICS

CHANNEL = (os.environ.get("TELEGRAM_CHANNEL_PUBLIC") or "cuibono_press").lstrip("@")
CHANNEL_URL = f"https://t.me/{CHANNEL}"
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
TITLE = "Cui Bono?"
DESCRIPTION = "Аналитическое издание об экономике и власти"
KIND_LABEL = {"note": None, "position": "ПОЗИЦИЯ", "analysis": "РАЗБОР"}
SECTION_TITLE = {k: v[0][:1] + v[0][1:].lower() for k, v in RUBRICS.items()}
SECTION_TITLE.update({"position": "Позиция", "analysis": "Разбор"})
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
HOME_LIMIT = 60


# ---------- запись после публикации ----------

def write_entry(kind: str, d: dict, res: dict | None, cover: Path | None, url: str = "") -> None:
    """Кладёт в out/ запись для сайта. Никогда не роняет публикацию."""
    try:
        now = datetime.now(TZ)
        entry = {
            "kind": kind,
            "time_iso": now.isoformat(),
            "rubric": d.get("rubric", ""),
            "fly": plain(d.get("fly", "") or ""),
            "headline": plain(d.get("headline", "") or ""),
            "subhead": plain(d.get("subhead", "") or ""),
            "paragraphs": list(d.get("paragraphs") or []),
            "final": d.get("final") or "",
            "topic_key": d.get("topic_key", ""),
            "message_id": (res or {}).get("message_id"),
            "cover": cover.name if cover else "",
            "telegraph": url,
        }
        if not entry["headline"]:
            return
        OUT.mkdir(exist_ok=True)
        (OUT / f"site-entry-{kind}-{now:%Y%m%d-%H%M%S}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # сайт вторичен: пост уже в канале
        log(f"сайт: запись не сохранена ({e})")


# ---------- помощники ----------

def _slug(e: dict) -> str:
    base = re.sub(r"[^a-z0-9-]+", "-", (e.get("topic_key") or "").lower()).strip("-")[:70] or "post"
    return f"{e['time_iso'][:10]}-{base}"


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(TZ)


def _date_ru(iso: str) -> str:
    dt = _dt(iso)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _inline(s: str) -> str:
    out, pos = [], 0
    for m in LINK_RE.finditer(s or ""):
        out.append(html.escape(s[pos:m.start()], quote=False))
        out.append(f'<a href="{_esc(m.group(2))}" rel="nofollow noopener">{html.escape(m.group(1), quote=False)}</a>')
        pos = m.end()
    out.append(html.escape((s or "")[pos:], quote=False))
    return "".join(out)


def _section(e: dict) -> str:
    return e["kind"] if e.get("kind") in ("position", "analysis") else (e.get("rubric") or "world")


def _kicker(e: dict) -> str:
    if e.get("kind") == "position":
        return "ПОЗИЦИЯ · Мнение редакции"
    label = KIND_LABEL.get(e.get("kind")) or RUBRICS.get(e.get("rubric"), ("", ""))[0]
    fly = e.get("fly") or ""
    return f"{label} · {fly}" if label and fly else (label or fly)


def _kicker_html(e: dict, up: str) -> str:
    sec = _section(e)
    if sec in SECTION_TITLE:
        return f'<a class="kicker" href="/r/{sec}">{_esc(_kicker(e))}</a>'
    return f'<div class="kicker">{_esc(_kicker(e))}</div>'


def _desc(e: dict) -> str:
    parts = [e.get("subhead", "")] + [plain(p) for p in (e.get("paragraphs") or [])[:1]]
    text = " ".join(p.strip() for p in parts if p).strip()
    return (text[:157].rsplit(" ", 1)[0] + "…") if len(text) > 160 else text


def _has_body(e: dict) -> bool:
    return bool(e.get("paragraphs"))


def _post_url(e: dict) -> str:
    return f"{CHANNEL_URL}/{e['message_id']}" if e.get("message_id") else CHANNEL_URL


def _clean(path: str) -> str:
    """Чистый адрес: Cloudflare Pages отдаёт /p/slug без .html, а /index.html — как /."""
    path = path[:-5] if path.endswith(".html") else path
    return "" if path == "index" else path


def _abs(path: str) -> str:
    return f"{SITE_URL}/{_clean(path)}" if SITE_URL else "/" + _clean(path)


CSS = """
:root{--bg:#0B0D10;--ink:#EDE9E2;--mute:#6B6F72;--cold:#C9CFD6;--rule:#262B31}
*{box-sizing:border-box}html{background:var(--bg);color:var(--ink)}
body{margin:0;font:18px/1.6 Commissioner,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--ink);text-decoration-color:var(--mute);text-underline-offset:3px}
.wrap{max-width:720px;margin:0 auto;padding:0 20px}
header.top{border-bottom:1px solid var(--rule);padding:18px 0;margin-bottom:36px}
header.top .wrap{display:flex;justify-content:space-between;align-items:baseline}
.mark{font:500 28px Newsreader,Georgia,serif;text-decoration:none}
.chan{font:13px "IBM Plex Mono",monospace;color:var(--cold);text-decoration:none;letter-spacing:.04em}
.kicker{font:12px "IBM Plex Mono",monospace;letter-spacing:.08em;color:var(--cold);text-decoration:none;display:inline-block}
h1{font:600 34px/1.2 Commissioner,sans-serif;margin:10px 0 12px}
h1.sec{font-size:26px;margin:0 0 24px}
h2{font:600 22px/1.3 Commissioner,sans-serif;margin:6px 0}
.sub{font-style:italic;color:var(--cold);margin:0 0 24px;font-size:19px}
.meta{font:12px "IBM Plex Mono",monospace;color:var(--mute);margin-bottom:22px}
.cover{width:100%;height:auto;border:1px solid var(--rule);margin:0 0 26px;display:block}
article p{margin:0 0 18px}
.more{border-top:1px solid var(--rule);margin-top:36px;padding-top:18px;font-size:15px;color:var(--cold)}
.related{margin-top:30px}
.related h3{font:12px "IBM Plex Mono",monospace;color:var(--mute);letter-spacing:.08em;font-weight:400;margin:0 0 8px}
.related a{display:block;margin:8px 0;font-size:16px}
nav.sec{font:12px "IBM Plex Mono",monospace;line-height:2.1;margin:-14px 0 30px}
nav.sec a{color:var(--cold);text-decoration:none;margin-right:14px;white-space:nowrap;display:inline-block}
ul.list{list-style:none;padding:0;margin:0}
ul.list li{border-bottom:1px solid var(--rule);padding:18px 0}
ul.list h2 a{text-decoration:none}ul.list h2 a:hover{text-decoration:underline}
ul.list .sub{font-size:16px;margin:4px 0 0}
.all{display:block;margin:26px 0;font:13px "IBM Plex Mono",monospace;color:var(--cold)}
footer{color:var(--mute);font:12px "IBM Plex Mono",monospace;padding:40px 0}
footer a{color:var(--cold)}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Commissioner:wght@400;600&'
         'family=IBM+Plex+Mono&family=Newsreader:wght@500&display=swap" rel="stylesheet">')

ORG = {"@type": "NewsMediaOrganization", "name": TITLE, "description": DESCRIPTION,
       "url": SITE_URL or CHANNEL_URL, "sameAs": [CHANNEL_URL]}


def _page(title: str, desc: str, body: str, canonical: str, image: str = "", depth: int = 0,
          extra_head: str = "", og_type: str = "website", noindex: bool = False) -> str:
    up = "../" * depth
    robots = ('<meta name="robots" content="noindex,follow">' if noindex
              else '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">')
    verify = ""
    if os.environ.get("YANDEX_VERIFICATION"):
        verify += f'<meta name="yandex-verification" content="{_esc(os.environ["YANDEX_VERIFICATION"])}">'
    if os.environ.get("GOOGLE_VERIFICATION"):
        verify += f'<meta name="google-site-verification" content="{_esc(os.environ["GOOGLE_VERIFICATION"])}">'
    img = (f'<meta property="og:image" content="{_esc(image)}"><meta property="og:image:width" content="1280">'
           f'<meta property="og:image:height" content="720"><meta name="twitter:card" content="summary_large_image">'
           if image else '<meta name="twitter:card" content="summary">')
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(desc)}">
{robots}<link rel="canonical" href="{_esc(canonical)}">
<meta property="og:site_name" content="{TITLE}"><meta property="og:locale" content="ru_RU"><meta property="og:type" content="{og_type}">
<meta property="og:title" content="{_esc(title)}"><meta property="og:description" content="{_esc(desc)}">
<meta property="og:url" content="{_esc(canonical)}">{img}
<meta name="twitter:title" content="{_esc(title)}"><meta name="twitter:description" content="{_esc(desc)}">
<link rel="alternate" type="application/rss+xml" title="{TITLE}" href="/feed.xml">
{verify}{FONTS}<link rel="stylesheet" href="/style.css">{extra_head}
</head><body>
<header class="top"><div class="wrap"><a class="mark" href="/">Cui Bono?</a>
<a class="chan" href="{CHANNEL_URL}">t.me/{CHANNEL}</a></div></header>
<main class="wrap">{body}</main>
<footer class="wrap">{DESCRIPTION}. Telegram: <a href="{CHANNEL_URL}">@{CHANNEL}</a> · <a href="/archive">Архив</a> · <a href="/feed.xml">RSS</a></footer>
</body></html>"""


def _ld(obj: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(obj, ensure_ascii=False)}</script>'


def _nav(sections: list[str], up: str) -> str:
    return '<nav class="sec">' + "".join(f'<a href="/r/{s}">{_esc(SECTION_TITLE[s])}</a>' for s in sections) + "</nav>"


def _list(posts: list[dict], up: str) -> str:
    return '<ul class="list">' + "".join(
        f'<li>{_kicker_html(p, up)}<h2><a href="/p/{p["slug"]}">{_esc(p["headline"])}</a></h2>'
        f'<p class="sub">{_esc(p.get("subhead", ""))}</p></li>' for p in posts) + "</ul>"


# ---------- страницы ----------

def _article(e: dict, related: list[dict]) -> str:
    slug = e["slug"]
    canonical = _abs(f"p/{slug}.html")
    image = _abs(f"img/{e['cover']}") if e.get("cover") else ""
    sec = _section(e)
    paras = "".join(f"<p>{_inline(p)}</p>" for p in e.get("paragraphs") or [])
    if e.get("final") and e["final"] not in " ".join(e.get("paragraphs") or []):
        paras += f"<p>{_inline(e['final'])}</p>"
    more = ""
    if e.get("telegraph"):
        more += f'<p><a href="{_esc(e["telegraph"])}">Полный текст разбора</a></p>'
    if not paras and not e.get("telegraph"):
        paras = f'<p><a href="{_esc(_post_url(e))}">Текст заметки в Telegram</a></p>'
    more += f'<p><a href="{_esc(_post_url(e))}">Этот текст в канале</a> · <a href="{CHANNEL_URL}">@{CHANNEL}</a></p>'
    rel = ""
    if related:
        rel = ('<div class="related"><h3>ЕЩЁ · ' + _esc(SECTION_TITLE.get(sec, "").upper()) + "</h3>"
               + "".join(f'<a href="/p/{r["slug"]}">{_esc(r["headline"])}</a>' for r in related) + "</div>")
    cover = (f'<img class="cover" src="/img/{_esc(e["cover"])}" alt="{_esc(e["headline"])}" width="1280" height="720">'
             if e.get("cover") else "")
    article_ld = {
        "@context": "https://schema.org", "@type": "NewsArticle", "headline": e["headline"][:110],
        "description": _desc(e), "datePublished": e["time_iso"], "dateModified": e["time_iso"], "inLanguage": "ru",
        "articleSection": SECTION_TITLE.get(sec, ""), "mainEntityOfPage": canonical,
        "author": ORG, "publisher": ORG, "isAccessibleForFree": True,
    }
    if image:
        article_ld["image"] = [image]
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": TITLE, "item": _abs("index.html")},
        {"@type": "ListItem", "position": 2, "name": SECTION_TITLE.get(sec, ""), "item": _abs(f"r/{sec}.html")},
        {"@type": "ListItem", "position": 3, "name": e["headline"]}]}
    body = (f'<article>{_kicker_html(e, "../")}<h1>{_esc(e["headline"])}</h1>'
            f'<p class="sub">{_esc(e.get("subhead", ""))}</p>'
            f'<div class="meta"><time datetime="{_esc(e["time_iso"])}">{_date_ru(e["time_iso"])}</time></div>'
            f'{cover}{paras}<div class="more">{more}</div>{rel}</article>')
    title = f'{e["headline"]} — {SECTION_TITLE.get(sec, "")} — {TITLE}' if sec in SECTION_TITLE else f'{e["headline"]} — {TITLE}'
    extra = (f'<meta property="article:published_time" content="{_esc(e["time_iso"])}">'
             f'<meta property="article:section" content="{_esc(SECTION_TITLE.get(sec, ""))}">' + _ld(article_ld) + _ld(crumbs))
    return _page(title, _desc(e), body, canonical, image, depth=1, extra_head=extra, og_type="article",
                 noindex=not _has_body(e) and not e.get("telegraph"))


def _seed_from_state() -> list[dict]:
    """Первые записи — из журнала ленты: шапка и ссылка на пост, без тела."""
    if not STATE.exists():
        return []
    out = []
    for r in json.loads(STATE.read_text(encoding="utf-8")):
        if r.get("rubric") in ("indicators", "week") or not r.get("headline") or not r.get("message_id"):
            continue
        kind = r["rubric"] if r.get("rubric") in ("position", "analysis") else "note"
        out.append({"kind": kind, "time_iso": r["time_iso"], "rubric": r.get("rubric", ""), "fly": r.get("fly", ""),
                    "headline": r["headline"], "subhead": r.get("subhead", ""), "paragraphs": [], "final": "",
                    "topic_key": r.get("topic_key", ""), "message_id": r["message_id"], "cover": "", "telegraph": ""})
    return out


_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_A_RE = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BR2_RE = re.compile(r'(?:<br\s*/?>\s*){2,}')


def _paragraphs_from_html(raw: str) -> list[str]:
    """Текст поста из веб-версии канала: шапка (рубрика, заголовок, подзаголовок) отбрасывается, ссылки сохраняются."""
    m = _TEXT_RE.search(raw)
    if not m:
        return []
    blocks = _BR2_RE.split(m.group(1))[3:]
    out = []
    for b in blocks:
        b = _A_RE.sub(lambda a: f"[{re.sub(r'<[^>]+>', '', a.group(2))}]({html.unescape(a.group(1))})", b)
        b = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", " ", b))).strip()
        if b:
            out.append(b)
    return out


def _backfill(posts: list[dict], pages: int = 10) -> int:
    """Достаёт тексты первых постов (до появления сайта) из публичной веб-ленты канала t.me/s/."""
    need = {p["message_id"]: p for p in posts
            if p.get("message_id") and not p.get("paragraphs") and p.get("kind") != "analysis"}
    if not need:
        return 0
    try:
        import requests
    except Exception:
        return 0
    found, before = 0, None
    for _ in range(pages):
        url = f"https://t.me/s/{CHANNEL}" + (f"?before={before}" if before else "")
        try:
            raw = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}).text
        except Exception as e:
            posts[0]["_debug"] = repr(e)
            break
        chunks = re.split(r'(?=<div class="tgme_widget_message_wrap)', raw)
        ids = []
        for c in chunks:
            m = re.search(rf'data-post="{re.escape(CHANNEL)}/(\d+)"', c)
            if not m:
                continue
            mid = int(m.group(1))
            ids.append(mid)
            if mid in need:
                paras = _paragraphs_from_html(c)
                if paras:
                    need.pop(mid)["paragraphs"] = paras
                    found += 1
        if not ids:
            posts[0]["_debug"] = raw[:200] + " … " + raw[raw.find("tgme_channel"):][:1500]
            break
        if not need or min(ids) <= min(need):
            break
        before = min(ids)
    for p in posts:
        p.pop("_tried", None)
    if not found and need:
        sample = next((c for c in chunks if "message_text" in c), raw[:1500]) if 'chunks' in locals() else ""
        posts[0]["_debug"] = f"{datetime.now(TZ):%H:%M} ids={len(ids) if 'ids' in locals() else '-'} " + sample[:2500]
    if found:
        posts[0].pop("_debug", None)
        log(f"сайт: тексты {found} постов взяты из канала")
    return found


def _jpeg(src: Path, dst_dir: Path) -> str:
    """Обложка в JPEG: в 3–4 раза легче PNG, сайт быстрее, репозиторий не пухнет."""
    name = src.stem + ".jpg"
    try:
        from PIL import Image
        Image.open(src).convert("RGB").save(dst_dir / name, "JPEG", quality=82, optimize=True, progressive=True)
    except Exception:
        name = src.name
        shutil.copy(src, dst_dir / name)
    return name


def _indexnow(site: Path, urls: list[str]) -> None:
    """Сообщает Яндексу, Bing и другим поисковикам о новых страницах сразу после публикации."""
    if not SITE_URL or not urls:
        return
    key = hashlib.sha1(f"cuibono:{SITE_URL}".encode()).hexdigest()
    (site / f"{key}.txt").write_text(key, encoding="utf-8")
    try:
        import requests
        host = urlparse(SITE_URL).netloc
        payload = {"host": host, "key": key, "keyLocation": f"{SITE_URL}/{key}.txt", "urlList": urls[:10000]}
        for endpoint in ("https://yandex.com/indexnow", "https://api.indexnow.org/indexnow"):
            r = requests.post(endpoint, json=payload, timeout=20)
            log(f"сайт: IndexNow {urlparse(endpoint).netloc} → {r.status_code}")
    except Exception as e:
        log(f"сайт: IndexNow не ответил ({e})")


def build(site: Path) -> int:
    data = STATE.parent / "site-posts.json"
    posts = json.loads(data.read_text(encoding="utf-8")) if data.exists() else _seed_from_state()
    for d in ("img", "p", "r", "a"):
        (site / d).mkdir(parents=True, exist_ok=True)

    new_ids = []
    for f in sorted(OUT.glob("site-entry-*.json")) if OUT.exists() else []:
        e = json.loads(f.read_text(encoding="utf-8"))
        if e.get("cover") and (OUT / e["cover"]).exists():
            e["cover"] = _jpeg(OUT / e["cover"], site / "img")
        elif e.get("cover"):
            e["cover"] = ""
        posts = [p for p in posts if not (e.get("message_id") and p.get("message_id") == e["message_id"])]
        posts.append(e)
        new_ids.append(id(e))

    _backfill(posts)
    posts.sort(key=lambda p: p["time_iso"], reverse=True)
    seen: set[str] = set()
    for p in posts:
        s = _slug(p)
        while s in seen:
            s += "-2"
        seen.add(s)
        p["slug"] = s

    by_sec: dict[str, list[dict]] = defaultdict(list)
    by_month: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        by_sec[_section(p)].append(p)
        by_month[p["time_iso"][:7]].append(p)
    sections = [s for s in ("analysis", "position", *RUBRICS.keys()) if by_sec.get(s)]

    # статьи
    for p in posts:
        same = [q for q in by_sec[_section(p)] if q is not p and _has_body(q)][:5]
        (site / "p" / f"{p['slug']}.html").write_text(_article(p, same), encoding="utf-8")

    # главная
    home_ld = _ld({"@context": "https://schema.org", "@type": "WebSite", "name": TITLE, "url": _abs("index.html"),
                   "inLanguage": "ru", "publisher": ORG})
    home = (_nav(sections, "") + _list(posts[:HOME_LIMIT], "")
            + (f'<a class="all" href="/archive">Все тексты →</a>' if len(posts) > HOME_LIMIT else ""))
    (site / "index.html").write_text(
        _page(f"{TITLE} — {DESCRIPTION.lower()}", DESCRIPTION + ": заметки, разборы и позиция редакции каждый день.",
              home, _abs("index.html"), extra_head=home_ld), encoding="utf-8")

    # рубрики
    for sec in sections:
        name = SECTION_TITLE[sec]
        body = f'<h1 class="sec">{_esc(name)}</h1>' + _list(by_sec[sec], "../")
        (site / "r" / f"{sec}.html").write_text(
            _page(f"{name} — {TITLE}", f"{name}: тексты издания {TITLE} — {DESCRIPTION.lower()}.", body,
                  _abs(f"r/{sec}.html"), depth=1), encoding="utf-8")

    # архив по месяцам
    months = sorted(by_month, reverse=True)
    for m in months:
        y, mo = m.split("-")
        name = f"{MONTHS_NOM[int(mo) - 1]} {y}"
        body = f'<h1 class="sec">{name}</h1>' + _list(by_month[m], "../")
        (site / "a" / f"{m}.html").write_text(
            _page(f"{name} — архив {TITLE}", f"Все тексты {TITLE} за {name.lower()}.", body, _abs(f"a/{m}.html"), depth=1),
            encoding="utf-8")
    arch = '<h1 class="sec">Архив</h1>' + _nav(sections, "") + '<ul class="list">' + "".join(
        f'<li><h2><a href="/a/{m}">{MONTHS_NOM[int(m[5:]) - 1]} {m[:4]}</a></h2>'
        f'<p class="sub">{len(by_month[m])} текстов</p></li>' for m in months) + "</ul>"
    (site / "archive.html").write_text(_page(f"Архив — {TITLE}", f"Все тексты {TITLE} по месяцам и рубрикам.", arch,
                                             _abs("archive.html")), encoding="utf-8")

    # служебное
    (site / "404.html").write_text(_page(f"Страница не найдена — {TITLE}", DESCRIPTION,
                                         '<h1 class="sec">Такой страницы нет</h1><p><a href="/">На главную</a></p>',
                                         _abs("404.html"), noindex=True), encoding="utf-8")
    (site / "style.css").write_text(CSS.strip(), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    if SITE_URL:
        host = urlparse(SITE_URL).netloc
        if host and not host.endswith("github.io"):
            (site / "CNAME").write_text(host, encoding="utf-8")
        urls = [(_abs("index.html"), posts[0]["time_iso"][:10] if posts else "")]
        urls += [(_abs(f"r/{s}.html"), by_sec[s][0]["time_iso"][:10]) for s in sections]
        urls += [(_abs(f"a/{m}.html"), by_month[m][0]["time_iso"][:10]) for m in months]
        urls += [(_abs(f"p/{p['slug']}.html"), p["time_iso"][:10]) for p in posts if _has_body(p) or p.get("telegraph")]
        (site / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(f"<url><loc>{u}</loc><lastmod>{d}</lastmod></url>" for u, d in urls) + "\n</urlset>\n",
            encoding="utf-8")
        (site / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {_abs('sitemap.xml')}\n",
                                         encoding="utf-8")
    rss_items = "".join(
        f"<item><title>{_esc(p['headline'])}</title><link>{_abs('p/' + p['slug'] + '.html')}</link>"
        f"<guid>{_abs('p/' + p['slug'] + '.html')}</guid><category>{_esc(SECTION_TITLE.get(_section(p), ''))}</category>"
        f"<description>{_esc(_desc(p))}</description>"
        f"<pubDate>{_dt(p['time_iso']).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate></item>"
        for p in posts[:50])
    (site / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{TITLE}</title>'
        f"<link>{_abs('index.html')}</link><description>{DESCRIPTION}</description><language>ru</language>"
        f"{rss_items}</channel></rss>", encoding="utf-8")

    fresh = [p for p in posts if id(p) in new_ids]
    for p in posts:
        p.pop("slug", None) if id(p) not in new_ids else None
    fresh_urls = [_abs(f"p/{p['slug']}.html") for p in fresh] + ([_abs("index.html")] if fresh else [])
    for p in fresh:
        p.pop("slug", None)
    data.parent.mkdir(exist_ok=True)
    data.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"сайт: {len(posts)} текстов, новых {len(fresh)}, рубрик {len(sections)}")
    if SITE_URL:
        key = hashlib.sha1(f"cuibono:{SITE_URL}".encode()).hexdigest()
        (site / f"{key}.txt").write_text(key, encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    (OUT / "indexnow-urls.json").write_text(json.dumps(fresh_urls), encoding="utf-8")
    return 0


def ping(site: Path) -> int:
    """Запускается после выкладки сайта: новые адреса уже открываются."""
    f = OUT / "indexnow-urls.json"
    _indexnow(site, json.loads(f.read_text(encoding="utf-8")) if f.exists() else [])
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = Path(args[0] if args else "site")
    sys.exit(ping(target) if "--ping" in sys.argv else build(target))
