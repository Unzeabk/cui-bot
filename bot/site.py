"""Сайт-зеркало канала: каждая заметка, позиция и разбор становятся страницей, которую находят поисковики.

Как работает:
1. После публикации в канал бот кладёт в out/ файл site-entry-*.json (write_entry).
2. Шаг workflow «Сайт» клонирует публичный репозиторий сайта и запускает
   `python -m bot.site <папка сайта>`: новые записи добавляются в data/posts.json,
   обложки копируются в img/, страницы, sitemap.xml и feed.xml пересобираются.
3. GitHub Pages раздаёт репозиторий сайта бесплатно.

Каждая страница ведёт в канал: в шапке, под текстом и ссылкой на сам пост.
Голос издания не меняется: на сайте тот же текст, что в канале, без призывов.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .compose import LINK_RE, plain
from .main import OUT, STATE, TZ, log
from .rubrics import RUBRICS

CHANNEL = (os.environ.get("TELEGRAM_CHANNEL_PUBLIC") or "cuibono_press").lstrip("@")
CHANNEL_URL = f"https://t.me/{CHANNEL}"
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
TITLE = "Cui Bono?"
DESCRIPTION = "Аналитическое издание об экономике и власти"
KIND_LABEL = {"note": None, "position": "ПОЗИЦИЯ", "analysis": "РАЗБОР"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


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


# ---------- сборка ----------

def _slug(e: dict) -> str:
    base = e.get("topic_key") or ""
    base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:70] or "post"
    return f"{e['time_iso'][:10]}-{base}"


def _date_ru(iso: str) -> str:
    dt = datetime.fromisoformat(iso).astimezone(TZ)
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


def _kicker(e: dict) -> str:
    label = KIND_LABEL.get(e.get("kind")) or RUBRICS.get(e.get("rubric"), ("", ""))[0]
    if e.get("kind") == "position":
        return "ПОЗИЦИЯ · Мнение редакции"
    fly = e.get("fly") or ""
    return f"{label} · {fly}" if label and fly else (label or fly)


def _post_url(e: dict) -> str:
    return f"{CHANNEL_URL}/{e['message_id']}" if e.get("message_id") else CHANNEL_URL


def _abs(path: str) -> str:
    return f"{SITE_URL}/{path}" if SITE_URL else path


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
.kicker{font:12px "IBM Plex Mono",monospace;letter-spacing:.08em;color:var(--cold);text-transform:none}
.kicker i{font-family:Commissioner,sans-serif;font-size:14px;letter-spacing:0}
h1{font:600 34px/1.2 Commissioner,sans-serif;margin:10px 0 12px}
h2{font:600 22px/1.3 Commissioner,sans-serif;margin:6px 0}
.sub{font-style:italic;color:var(--cold);margin:0 0 24px;font-size:19px}
.meta{font:12px "IBM Plex Mono",monospace;color:var(--mute);margin-bottom:22px}
.cover{width:100%;height:auto;border:1px solid var(--rule);margin:0 0 26px;display:block}
article p{margin:0 0 18px}
.more{border-top:1px solid var(--rule);margin-top:36px;padding-top:18px;font-size:15px;color:var(--cold)}
ul.list{list-style:none;padding:0;margin:0}
ul.list li{border-bottom:1px solid var(--rule);padding:18px 0}
ul.list h2 a{text-decoration:none}
ul.list h2 a:hover{text-decoration:underline}
ul.list .sub{font-size:16px;margin:4px 0 0}
footer{color:var(--mute);font:12px "IBM Plex Mono",monospace;padding:40px 0}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link href="https://fonts.googleapis.com/css2?family=Commissioner:wght@400;600&'
         'family=IBM+Plex+Mono&family=Newsreader:wght@500&display=swap" rel="stylesheet">')


def _page(title: str, desc: str, body: str, canonical: str, image: str = "", depth: int = 0,
          extra_head: str = "", og_type: str = "website") -> str:
    up = "../" * depth
    verify = ""
    if os.environ.get("YANDEX_VERIFICATION"):
        verify += f'<meta name="yandex-verification" content="{_esc(os.environ["YANDEX_VERIFICATION"])}">'
    if os.environ.get("GOOGLE_VERIFICATION"):
        verify += f'<meta name="google-site-verification" content="{_esc(os.environ["GOOGLE_VERIFICATION"])}">'
    img = (f'<meta property="og:image" content="{_esc(image)}"><meta name="twitter:card" content="summary_large_image">'
           if image else "")
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(desc)}">
<link rel="canonical" href="{_esc(canonical)}">
<meta property="og:site_name" content="{TITLE}"><meta property="og:type" content="{og_type}">
<meta property="og:title" content="{_esc(title)}"><meta property="og:description" content="{_esc(desc)}">
<meta property="og:url" content="{_esc(canonical)}">{img}
<link rel="alternate" type="application/rss+xml" title="{TITLE}" href="{up}feed.xml">
{verify}{FONTS}<link rel="stylesheet" href="{up}style.css">{extra_head}
</head><body>
<header class="top"><div class="wrap"><a class="mark" href="{up}index.html">Cui Bono?</a>
<a class="chan" href="{CHANNEL_URL}">t.me/{CHANNEL}</a></div></header>
<main class="wrap">{body}</main>
<footer class="wrap">{DESCRIPTION}. Telegram: <a href="{CHANNEL_URL}">@{CHANNEL}</a></footer>
</body></html>"""


def _article(e: dict) -> str:
    slug = e["slug"]
    canonical = _abs(f"p/{slug}.html")
    image = _abs(f"img/{e['cover']}") if e.get("cover") else ""
    paras = "".join(f"<p>{_inline(p)}</p>" for p in e.get("paragraphs") or [])
    if e.get("final") and e["final"] not in " ".join(e.get("paragraphs") or []):
        paras += f"<p>{_inline(e['final'])}</p>"
    more = ""
    if e.get("telegraph"):
        more += f'<p><a href="{_esc(e["telegraph"])}">Полный текст разбора</a></p>'
    if not paras and not e.get("telegraph"):
        paras = f'<p><a href="{_esc(_post_url(e))}">Текст заметки в Telegram</a></p>'
    more += f'<p><a href="{_esc(_post_url(e))}">Этот текст в канале</a> · <a href="{CHANNEL_URL}">@{CHANNEL}</a></p>'
    cover = (f'<img class="cover" src="../img/{_esc(e["cover"])}" alt="{_esc(e["headline"])}" width="1280" height="720">'
             if e.get("cover") else "")
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "NewsArticle", "headline": e["headline"][:110],
        "description": e.get("subhead", ""), "datePublished": e["time_iso"], "inLanguage": "ru",
        "image": [image] if image else [], "mainEntityOfPage": canonical,
        "author": {"@type": "Organization", "name": TITLE, "url": CHANNEL_URL},
        "publisher": {"@type": "Organization", "name": TITLE},
    }, ensure_ascii=False)
    body = (f'<article><div class="kicker">{_esc(_kicker(e))}</div><h1>{_esc(e["headline"])}</h1>'
            f'<p class="sub">{_esc(e.get("subhead", ""))}</p>'
            f'<div class="meta"><time datetime="{_esc(e["time_iso"])}">{_date_ru(e["time_iso"])}</time></div>'
            f'{cover}{paras}<div class="more">{more}</div></article>')
    desc = e.get("subhead") or plain((e.get("paragraphs") or [""])[0])[:160]
    return _page(f'{e["headline"]} — {TITLE}', desc, body, canonical, image, depth=1,
                 extra_head=f'<script type="application/ld+json">{ld}</script>', og_type="article")


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


def build(site: Path) -> int:
    data = site / "data" / "posts.json"
    posts = json.loads(data.read_text(encoding="utf-8")) if data.exists() else _seed_from_state()
    (site / "img").mkdir(parents=True, exist_ok=True)
    (site / "p").mkdir(exist_ok=True)

    added = 0
    for f in sorted(OUT.glob("site-entry-*.json")) if OUT.exists() else []:
        e = json.loads(f.read_text(encoding="utf-8"))
        if e.get("cover") and (OUT / e["cover"]).exists():
            shutil.copy(OUT / e["cover"], site / "img" / e["cover"])
        elif e.get("cover"):
            e["cover"] = ""
        # тот же пост мог записаться из журнала — заменяем
        posts = [p for p in posts if not (e.get("message_id") and p.get("message_id") == e["message_id"])]
        posts.append(e)
        added += 1

    posts.sort(key=lambda p: p["time_iso"], reverse=True)
    seen = set()
    for p in posts:
        s = _slug(p)
        while s in seen:
            s += "-2"
        seen.add(s)
        p["slug"] = s
        (site / "p" / f"{s}.html").write_text(_article(p), encoding="utf-8")

    items = "".join(
        f'<li><div class="kicker">{_esc(_kicker(p))}</div>'
        f'<h2><a href="p/{p["slug"]}.html">{_esc(p["headline"])}</a></h2>'
        f'<p class="sub">{_esc(p.get("subhead", ""))}</p></li>' for p in posts)
    (site / "index.html").write_text(
        _page(f"{TITLE} — {DESCRIPTION.lower()}", DESCRIPTION, f'<ul class="list">{items}</ul>', _abs("index.html") if SITE_URL else "index.html"),
        encoding="utf-8")
    (site / "style.css").write_text(CSS.strip(), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")

    if SITE_URL:
        urls = [f"<url><loc>{_abs('index.html')}</loc></url>"] + [
            f"<url><loc>{_abs('p/' + p['slug'] + '.html')}</loc><lastmod>{p['time_iso'][:10]}</lastmod></url>" for p in posts]
        (site / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls) + "</urlset>", encoding="utf-8")
        (site / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {_abs('sitemap.xml')}\n", encoding="utf-8")
    rss_items = "".join(
        f"<item><title>{_esc(p['headline'])}</title><link>{_abs('p/' + p['slug'] + '.html')}</link>"
        f"<guid>{_abs('p/' + p['slug'] + '.html')}</guid><description>{_esc(p.get('subhead', ''))}</description>"
        f"<pubDate>{datetime.fromisoformat(p['time_iso']).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate></item>"
        for p in posts[:50])
    (site / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{TITLE}</title>'
        f"<link>{_abs('index.html')}</link><description>{DESCRIPTION}</description><language>ru</language>"
        f"{rss_items}</channel></rss>", encoding="utf-8")

    for p in posts:
        p.pop("slug", None)
    data.parent.mkdir(exist_ok=True)
    data.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"сайт: {len(posts)} страниц, новых {added}")
    return 0


if __name__ == "__main__":
    sys.exit(build(Path(sys.argv[1] if len(sys.argv) > 1 else "site")))
