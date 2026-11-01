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
KIND_LABEL = {"note": None, "position": "ПОЗИЦИЯ", "analysis": "РАЗБОР", "week": "НЕДЕЛЯ"}
SECTION_TITLE = {k: v[0][:1] + v[0][1:].lower() for k, v in RUBRICS.items()}
SECTION_TITLE.update({"position": "Позиция", "analysis": "Разбор", "week": "Неделя"})
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
HOME_LIMIT = 60
TOPIC_MIN = 3  # страница темы попадает в индекс, когда по ней не меньше трёх текстов
FOOTER_TOPICS = ""


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
        if kind == "week":
            entry["headline"] = f"Неделя: {d.get('_range') or now.strftime('%d.%m.%Y')}"
            entry["paragraphs"] = [f"**{i.get('subject', '')}.** {i.get('text', '')}" for i in d.get("items", [])]
        if not entry["headline"]:
            return
        entry.update(d.get("_seo") if d.get("_seo") is not None else _seo(entry))
        OUT.mkdir(exist_ok=True)
        (OUT / f"site-entry-{kind}-{now:%Y%m%d-%H%M%S}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # сайт вторичен: пост уже в канале
        log(f"сайт: запись не сохранена ({e})")


SEO_SYSTEM = """Ты — редактор поисковой выдачи издания об экономике и власти. Тебе дают опубликованный текст.
Верни JSON без пояснений:
{"seo_title": "...", "seo_description": "...", "entities": [{"name": "...", "type": "org|place|person|instrument|topic"}]}
- seo_title: 45–65 знаков. Как человек искал бы это событие: главный участник + что произошло + цифра или год, если она есть в тексте. Без кликбейта, вопросов, кавычек-ёлочек вокруг всего заголовка, без названия издания. Только факты из текста.
- seo_description: 120–160 знаков, суть и главная цифра из текста, одно-два предложения.
- entities: 2–5 главных участников и предметов текста (компании, ведомства, страны, города, инструменты вроде «ключевая ставка», устойчивые темы вроде «теневой флот»). Название — в именительном падеже, в самой распространённой форме («Банк России», «Газпром», «Казахстан», «ключевая ставка»). Не выдумывай того, чего нет в тексте."""


def _seo(e: dict) -> dict:
    """SEO-заголовок, описание и сущности — отдельным лёгким запросом. Ошибка не мешает ничему."""
    try:
        from .compose import extract_json
        from .gemini import LIGHT_CHAIN, ask
        text = "\n".join([e["headline"], e.get("subhead", "")] + [plain(p) for p in e.get("paragraphs", [])])
        raw, _ = ask(LIGHT_CHAIN, SEO_SYSTEM, text[:6000], temperature=0.2)
        d = extract_json(raw)
        out = {}
        t = (d.get("seo_title") or "").strip().strip("«»\"")
        if 25 <= len(t) <= 80:
            out["seo_title"] = t
        ds = (d.get("seo_description") or "").strip()
        if 60 <= len(ds) <= 220:
            out["seo_description"] = ds
        ents = []
        for x in d.get("entities") or []:
            name = (x.get("name") or "").strip()
            if 2 <= len(name) <= 40 and _translit(name):
                ents.append({"name": name, "type": x.get("type", "topic")})
        if ents:
            out["entities"] = ents[:5]
        return out
    except Exception as ex:
        log(f"сайт: SEO-поля не получены ({ex})")
        return {}


def prepare_seo(kind: str, d: dict) -> dict:
    """SEO-поля до публикации: из них же берутся теги в конце поста. Результат кладётся в d["_seo"]."""
    e = {"headline": plain(d.get("headline", "") or ""), "subhead": plain(d.get("subhead", "") or ""),
         "paragraphs": list(d.get("paragraphs") or [])}
    if kind == "analysis" and d.get("lead"):
        e["paragraphs"] = [d["lead"]] + e["paragraphs"]
    d["_seo"] = _seo(e) if e["headline"] else {}
    return d["_seo"]


def hashtags(seo: dict, limit: int = 3) -> str:
    """Теги для конца поста: те же темы, что на сайте. #ЛукОйл → #Лукойл, «ключевая ставка» → #КлючеваяСтавка."""
    tags = []
    for x in (seo or {}).get("entities") or []:
        words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", x["name"])
        tag = "".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)
        if 2 <= len(tag) <= 24 and not tag.isdigit() and tag not in tags:
            tags.append(tag)
    return " ".join(f"#{t}" for t in tags[:limit])


def with_tags(caption: str, seo: dict, max_len: int = 1024) -> str:
    """Добавляет строку тегов, только если пост остаётся в лимите подписи Telegram."""
    from .compose import visible_length
    for n in (3, 2, 1):
        tags = hashtags(seo, n)
        if tags and visible_length(caption) + len(tags) + 2 <= max_len - 10:
            return f"{caption}\n\n{tags}"
    return caption


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def _translit(name: str) -> str:
    t = "".join(_TR.get(c, c) for c in name.lower())
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:60]


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


_ANY_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]+)\)")


def _inline(s: str) -> str:
    out, pos = [], 0
    for m in _ANY_LINK.finditer(s or ""):
        out.append(html.escape(s[pos:m.start()], quote=False))
        rel = "" if m.group(2).startswith("/") else ' rel="nofollow noopener"'
        out.append(f'<a href="{_esc(m.group(2))}"{rel}>{html.escape(m.group(1), quote=False)}</a>')
        pos = m.end()
    out.append(html.escape((s or "")[pos:], quote=False))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", "".join(out))


def _link_topics(text: str, topics: dict[str, str], used: set[str]) -> str:
    """Первое упоминание темы в тексте становится ссылкой на страницу темы (не больше 3 на текст)."""
    for name, slug in topics.items():
        if len(used) >= 3 or slug in used:
            continue
        m = re.search(rf"(?<![\w\[])({re.escape(name[:max(3, len(name) - 2)])}\w*)", text)
        if not m or "](" in text[m.start():m.start() + 200].split(" ")[0]:
            continue
        before = text[:m.start()]
        if before.count("[") > before.count("]"):  # внутри ссылки
            continue
        text = f"{before}[{m.group(1)}](/t/{slug}){text[m.end():]}"
        used.add(slug)
    return text


def _section(e: dict) -> str:
    return e["kind"] if e.get("kind") in ("position", "analysis", "week") else (e.get("rubric") or "world")


def _kicker(e: dict) -> str:
    if e.get("kind") == "position":
        return "ПОЗИЦИЯ · Мнение редакции"
    if e.get("kind") == "week":
        return "НЕДЕЛЯ"
    label = KIND_LABEL.get(e.get("kind")) or RUBRICS.get(e.get("rubric"), ("", ""))[0]
    fly = e.get("fly") or ""
    return f"{label} · {fly}" if label and fly else (label or fly)


def _kicker_html(e: dict, up: str) -> str:
    sec = _section(e)
    if sec in SECTION_TITLE:
        return f'<a class="kicker" href="/r/{sec}">{_esc(_kicker(e))}</a>'
    return f'<div class="kicker">{_esc(_kicker(e))}</div>'


def _desc(e: dict) -> str:
    if e.get("seo_description"):
        return e["seo_description"]
    parts = [e.get("subhead", "")] + [plain(p) for p in (e.get("paragraphs") or [])[:1]]
    text = " ".join(p.strip() for p in parts if p).strip()
    return (text[:157].rsplit(" ", 1)[0] + "…") if len(text) > 160 else text


def _has_body(e: dict) -> bool:
    return bool(e.get("paragraphs"))


def _indexable(e: dict) -> bool:
    """В индекс — только страницы с настоящим текстом: короткая пустышка тянет вниз весь сайт."""
    if e.get("telegraph"):
        return True
    return len(" ".join(plain(p) for p in e.get("paragraphs") or [])) >= 350


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
.more{margin-top:28px;font-size:15px;color:var(--cold)}
.related{margin-top:30px}
.related h3{font:12px "IBM Plex Mono",monospace;color:var(--mute);letter-spacing:.08em;font-weight:400;margin:0 0 8px}
.related a{display:block;margin:8px 0;font-size:16px}
nav.sec{font:12px "IBM Plex Mono",monospace;line-height:2.1;margin:-14px 0 30px}
nav.sec a{color:var(--cold);text-decoration:none;margin-right:14px;white-space:nowrap;display:inline-block}
ul.list{list-style:none;padding:0;margin:0}
ul.list li{border-bottom:1px solid var(--rule);padding:18px 0}ul.list li:last-child{border-bottom:none}
footer p:first-child{margin-top:0}
ul.list h2 a{text-decoration:none}ul.list h2 a:hover{text-decoration:underline}
ul.list .sub{font-size:16px;margin:4px 0 0}
.all{display:block;margin:26px 0;font:13px "IBM Plex Mono",monospace;color:var(--cold)}
footer.wrap{color:var(--mute);font:12px/1.9 "IBM Plex Mono",monospace;margin-top:8px;padding-top:0;padding-bottom:48px;width:100%}
footer.wrap>div{border-top:1px solid var(--rule);padding-top:20px}
footer .topics a{display:inline-block;margin:0 14px 4px 0;text-decoration:none}
footer p{margin:14px 0 0}
body{display:flex;flex-direction:column;min-height:100vh}main{flex:1 0 auto;width:100%}
.topics-line{font:12px "IBM Plex Mono",monospace;color:var(--mute);margin:-12px 0 22px}
.topics-line a{color:var(--cold);text-decoration:none;margin-right:12px}
article b{font-weight:600}
.lead{font-size:20px;margin:0 0 22px}.lead b{font-weight:600}
svg.chart{width:100%;height:auto;margin:6px 0 26px;display:block}
h2.dh{font:12px "IBM Plex Mono",monospace;letter-spacing:.08em;color:var(--mute);font-weight:400;text-transform:uppercase;margin:30px 0 10px}
table.data{width:100%;border-collapse:collapse;font:14px "IBM Plex Mono",monospace}
table.data td,table.data th{border-bottom:1px solid var(--rule);padding:7px 0;text-align:left;font-weight:400}
table.data th{color:var(--mute)}table.data td:not(:first-child),table.data th:not(:first-child){text-align:right}
.src{font-size:14px;color:var(--cold);margin-top:22px}
footer a{color:var(--cold)}
"""

CSS_V = ""  # версия стилей: браузер берёт свежий файл после каждого изменения

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Commissioner:wght@400;600&'
         'family=IBM+Plex+Mono&family=Newsreader:wght@500&display=swap" rel="stylesheet">')

ORG = {"@type": "NewsMediaOrganization", "name": TITLE, "description": DESCRIPTION,
       "url": SITE_URL or CHANNEL_URL, "sameAs": [CHANNEL_URL],
       "logo": {"@type": "ImageObject", "url": f"{SITE_URL}/icon-512.png", "width": 512, "height": 512},
       "publishingPrinciples": f"{SITE_URL}/about"}


def _page(title: str, desc: str, body: str, canonical: str, image: str = "", depth: int = 0,
          extra_head: str = "", og_type: str = "website", noindex: bool = False) -> str:
    up = "../" * depth
    metrika = ""
    mid = os.environ.get("METRIKA_ID", "").strip()
    if mid.isdigit():
        metrika = (f'<script>(function(m,e,t,r,i,k,a){{m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};'
                   f'm[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)}})'
                   f'(window,document,"script","https://mc.yandex.ru/metrika/tag.js","ym");'
                   f'ym({mid},"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:true}});'
                   f'document.addEventListener("click",function(e){{var a=e.target.closest("a");'
                   f'if(a&&a.href.indexOf("t.me/")>-1)ym({mid},"reachGoal","telegram")}});</script>'
                   f'<noscript><div><img src="https://mc.yandex.ru/watch/{mid}" style="position:absolute;left:-9999px" alt=""></div></noscript>')
    robots = ('<meta name="robots" content="noindex,follow">' if noindex
              else '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">')
    verify = ""
    if os.environ.get("YANDEX_VERIFICATION"):
        verify += f'<meta name="yandex-verification" content="{_esc(os.environ["YANDEX_VERIFICATION"])}">'
    zen = os.environ.get("ZEN_VERIFICATION") or "8u6kyMv5LBaBbm0wI8BxocAyG2SIgh6MkNUI1YayGGhLSnESTV7Og38O7xs6LyHY"
    verify += f'<meta name="zen-verification" content="{_esc(zen)}">'
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
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png"><meta name="theme-color" content="#0B0D10">
{verify}{metrika}{FONTS}<link rel="stylesheet" href="/style.css?v={CSS_V}">{extra_head}
</head><body>
<header class="top"><div class="wrap"><a class="mark" href="/">Cui Bono?</a>
<a class="chan" href="{CHANNEL_URL}">t.me/{CHANNEL}</a></div></header>
<main class="wrap">{body}</main>
<footer class="wrap"><div>{FOOTER_TOPICS}<p>{DESCRIPTION}. Telegram: <a href="{CHANNEL_URL}">@{CHANNEL}</a> · <a href="/data">Данные</a> · <a href="/archive">Архив</a> · <a href="/about">Об издании</a> · <a href="/feed.xml">RSS</a></p></div></footer>
</body></html>"""


def _texts_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "текст"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "текста"
    return "текстов"


def _ld(obj: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(obj, ensure_ascii=False)}</script>'


def _nav(sections: list[str], up: str) -> str:
    return '<nav class="sec"><a href="/data">Данные</a>' + "".join(f'<a href="/r/{s}">{_esc(SECTION_TITLE[s])}</a>' for s in sections) + "</nav>"


def _list(posts: list[dict], up: str) -> str:
    return '<ul class="list">' + "".join(
        f'<li>{_kicker_html(p, up)}<h2><a href="/p/{p["slug"]}">{_esc(p["headline"])}</a></h2>'
        f'<p class="sub">{_esc(p.get("subhead", ""))}</p></li>' for p in posts) + "</ul>"


# ---------- страницы ----------

def _article(e: dict, related: list[dict], topics: dict[str, str] | None = None) -> str:
    topics = topics or {}
    slug = e["slug"]
    canonical = _abs(f"p/{slug}.html")
    image = _abs(f"img/{e['cover']}") if e.get("cover") else ""
    sec = _section(e)
    used: set[str] = set()
    paras = "".join(f"<p>{_inline(_link_topics(p, topics, used))}</p>" for p in e.get("paragraphs") or [])
    ents = [(x["name"], _translit(x["name"])) for x in e.get("entities") or []]
    ents = [(n, sl) for n, sl in ents if sl in topics.values()]
    tline = ('<div class="topics-line">' + "".join(f'<a href="/t/{sl}">{_esc(n)}</a>' for n, sl in ents) + "</div>") if ents else ""
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
        rel = ('<div class="related"><h3>ПО ТЕМЕ</h3>'
               + "".join(f'<a href="/p/{r["slug"]}">{_esc(r["headline"])}</a>' for r in related) + "</div>")
    # обложка повторяет заголовок: на странице её нет, она остаётся в og:image и разметке — для поиска и превью ссылок
    cover = ""
    article_ld = {
        "@context": "https://schema.org", "@type": "NewsArticle", "headline": (e.get("seo_title") or e["headline"])[:110],
        "alternativeHeadline": e["headline"], "about": [{"@type": "Thing", "name": n} for n, _ in ents],
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
            f'<div class="meta"><time datetime="{_esc(e["time_iso"])}">{_date_ru(e["time_iso"])}</time></div>{tline}'
            f'{cover}{paras}<div class="more">{more}</div>{rel}</article>')
    title = f'{e["seo_title"]} — {TITLE}' if e.get("seo_title") else (
        f'{e["headline"]} — {SECTION_TITLE.get(sec, "")} — {TITLE}' if sec in SECTION_TITLE else f'{e["headline"]} — {TITLE}')
    extra = (f'<meta property="article:published_time" content="{_esc(e["time_iso"])}">'
             f'<meta property="article:section" content="{_esc(SECTION_TITLE.get(sec, ""))}">' + _ld(article_ld) + _ld(crumbs))
    return _page(title, _desc(e), body, canonical, image, depth=1, extra_head=extra, og_type="article",
                 noindex=not _indexable(e))


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
            break
        if not need or min(ids) <= min(need):
            break
        before = min(ids)
    for p in posts:
        p.pop("_tried", None)
        p.pop("_debug", None)
    if found:
        log(f"сайт: тексты {found} постов взяты из канала")
    return found


def _zen_feed(site: Path, posts: list[dict]) -> None:
    """Лента для Дзена (zen.xml) по «Техническим требованиям к RSS-ленте» Дзена:
    contentType blogs_only — статьи публикуются в канале Дзена; рубрика сайта, index, comment-all;
    только материалы за последние 7 дней (старше Дзен не берёт); заголовок без точки в конце;
    обложка через enclosure (у нас 1280×720, минимум — 480×320)."""
    from datetime import timedelta
    since = datetime.now(TZ) - timedelta(days=7)
    items = []
    for p in posts:
        if not (_has_body(p) and _indexable(p)) or _dt(p["time_iso"]) < since:
            continue
        url = _abs(f"p/{p['slug']}.html")
        title = re.sub(r"\.+$", "", plain(p["headline"]).strip())[:200]
        body = "".join(f"<p>{_inline(x)}</p>" for x in p.get("paragraphs") or [])
        if p.get("final") and p["final"] not in " ".join(p.get("paragraphs") or []):
            body += f"<p>{_inline(p['final'])}</p>"
        if p.get("subhead"):
            body = f"<p><i>{_esc(p['subhead'])}</i></p>" + body
        body += (f'<p><i>Оригинал — <a href="{url}">cuibono.press</a>. '
                 f'Cui Bono? в <a href="{CHANNEL_URL}">Telegram</a>.</i></p>')
        body = body.replace(' rel="nofollow noopener"', "")
        encl = ""
        if p.get("cover") and (site / "img" / p["cover"]).exists():
            f = site / "img" / p["cover"]
            mime = "image/jpeg" if f.suffix == ".jpg" else "image/png"
            encl = f'<enclosure url="{_abs("img/" + p["cover"])}" length="{f.stat().st_size}" type="{mime}"/>'
        items.append(
            f"<item><title>{_esc(title)}</title><link>{url}</link><guid>{url}</guid>"
            f"<pubDate>{_dt(p['time_iso']).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>"
            f"<author>{TITLE}</author><category>{_esc(SECTION_TITLE.get(_section(p), 'Мир'))}</category>"
            f"<category>index</category><category>comment-all</category><contentType>blogs_only</contentType>{encl}"
            f"<description>{_esc(_desc(p))}</description>"
            f"<content:encoded><![CDATA[{body}]]></content:encoded></item>")
    (site / "zen.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:content="https://purl.org/rss/1.0/modules/content/" '
        'xmlns:media="https://search.yahoo.com/mrss/">'
        f"<channel><title>{TITLE}</title><link>{SITE_URL or 'https://cuibono.press'}</link>"
        f"<description>{DESCRIPTION}</description><language>ru</language>{''.join(items)}</channel></rss>\n",
        encoding="utf-8")


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
    global CSS_V
    CSS_V = hashlib.sha1(CSS.encode()).hexdigest()[:8]
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
        posts = [p for p in posts if not (
            (e.get("message_id") and p.get("message_id") == e["message_id"])
            or (p.get("kind") == e.get("kind") and p.get("topic_key") and p.get("topic_key") == e.get("topic_key")
                and p["time_iso"][:10] == e["time_iso"][:10]))]
        posts.append(e)
        new_ids.append(id(e))

    _backfill(posts)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("CUI_CONFIG"):
        n = 0
        for p in posts:
            if n >= 30:
                break
            if _has_body(p) and "seo_title" not in p and not p.get("_seo_tried"):
                p.update(_seo(p) or {"_seo_tried": True})
                n += 1
    # чистка ленты: адреса или их хвосты из state/site-remove.txt — по одному в строке
    rm_file = STATE.parent / "site-remove.txt"
    remove = [l.strip().rstrip("/").split("/")[-1] for l in rm_file.read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.startswith("#")] if rm_file.exists() else []
    if os.environ.get("SITE_CLEAR"):
        posts = []
    posts.sort(key=lambda p: p["time_iso"], reverse=True)
    seen: set[str] = set()
    for p in posts:
        s = _slug(p)
        while s in seen:
            s += "-2"
        seen.add(s)
        p["slug"] = s

    if remove:
        posts = [p for p in posts if p["slug"] not in remove and str(p.get("message_id")) not in remove]
    for d in ("p", "t", "r", "a"):  # старые страницы удалённых текстов не остаются на сайте
        shutil.rmtree(site / d, ignore_errors=True)
        (site / d).mkdir(parents=True, exist_ok=True)

    by_sec: dict[str, list[dict]] = defaultdict(list)
    by_month: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        by_sec[_section(p)].append(p)
        by_month[p["time_iso"][:7]].append(p)
    sections = [s for s in ("analysis", "position", "week", *RUBRICS.keys()) if by_sec.get(s)]

    # темы: сущности, о которых написано не меньше TOPIC_MIN текстов
    global FOOTER_TOPICS
    by_topic: dict[str, list[dict]] = defaultdict(list)
    topic_name: dict[str, str] = {}
    for p in posts:
        for x in p.get("entities") or []:
            sl = _translit(x["name"])
            if sl and p not in by_topic[sl]:
                by_topic[sl].append(p)
                topic_name.setdefault(sl, x["name"])
    live = {sl: ps for sl, ps in by_topic.items() if sum(1 for q in ps if _indexable(q)) >= TOPIC_MIN}
    topics = {topic_name[sl]: sl for sl in sorted(live, key=lambda k: -len(live[k]))}
    top = sorted(live, key=lambda k: -len(live[k]))[:20]
    FOOTER_TOPICS = ('<div class="topics">' + "".join(f'<a href="/t/{sl}">{_esc(topic_name[sl])}</a>' for sl in top)
                     + "</div>") if top else ""
    (site / "t").mkdir(exist_ok=True)
    for sl, ps in by_topic.items():
        name = topic_name[sl]
        first, last = _date_ru(ps[-1]["time_iso"]), _date_ru(ps[0]["time_iso"])
        intro = (f'<p class="sub">{len(ps)} {_texts_word(len(ps))} · {first}' + (f' — {last}' if first != last else "") + "</p>")
        ld = _ld({"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "url": _abs(f"t/{sl}"),
                  "about": {"@type": "Thing", "name": name}, "publisher": ORG})
        (site / "t" / f"{sl}.html").write_text(
            _page(f"{name}: новости и анализ — {TITLE}",
                  f"{name}: все тексты {TITLE} — что происходит, кто выигрывает и во что это обходится. Последнее — {ps[0]['headline']}"[:200],
                  f'<h1 class="sec">{_esc(name)}</h1>{intro}' + _list(ps, "../"), _abs(f"t/{sl}"), depth=1,
                  extra_head=ld, noindex=sl not in live), encoding="utf-8")

    # статьи: «По теме» — сначала общие сущности, потом та же рубрика
    for p in posts:
        mine = {_translit(x["name"]) for x in p.get("entities") or []}
        def score(q):
            return len(mine & {_translit(x["name"]) for x in q.get("entities") or []})
        cands = [q for q in posts if q is not p and _indexable(q)]
        same = sorted([q for q in cands if score(q)], key=lambda q: (-score(q), q["time_iso"]), reverse=False)[:5]
        if len(same) < 5:
            same += [q for q in by_sec[_section(p)] if q is not p and _indexable(q) and q not in same][:5 - len(same)]
        (site / "p" / f"{p['slug']}.html").write_text(_article(p, same, topics), encoding="utf-8")

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

    # об издании
    about = (f'<h1 class="sec">{TITLE}</h1><article><p>{DESCRIPTION}.</p>'
             f'<p>Telegram: <a href="{CHANNEL_URL}">@{CHANNEL}</a></p></article>')
    (site / "about.html").write_text(_page(f"Об издании — {TITLE}", f"{TITLE} — {DESCRIPTION.lower()}.", about, _abs("about")),
                                     encoding="utf-8")

    # служебное
    (site / "404.html").write_text(_page(f"Страница не найдена — {TITLE}", DESCRIPTION,
                                         '<h1 class="sec">Такой страницы нет</h1><p><a href="/">На главную</a></p>',
                                         _abs("404.html"), noindex=True), encoding="utf-8")
    (site / "style.css").write_text(CSS.strip(), encoding="utf-8")
    icons = Path(__file__).resolve().parent.parent / "assets" / "site"
    for f in icons.glob("*"):
        shutil.copy(f, site / f.name)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    # вечные страницы данных
    try:
        from .data_pages import build_data
        data_urls = build_data(site, _page, _abs, _list, posts, _ld)
    except Exception as ex:
        log(f"данные: сборка не удалась ({ex})")
        data_urls = []
    if SITE_URL:
        host = urlparse(SITE_URL).netloc
        if host and not host.endswith("github.io"):
            (site / "CNAME").write_text(host, encoding="utf-8")
        urls = [(_abs("index.html"), posts[0]["time_iso"][:10] if posts else "")]
        urls += [(_abs(f"r/{s}.html"), by_sec[s][0]["time_iso"][:10]) for s in sections]
        urls += [(_abs(f"a/{m}.html"), by_month[m][0]["time_iso"][:10]) for m in months]
        urls += [(_abs(f"t/{sl}"), live[sl][0]["time_iso"][:10]) for sl in live]
        urls += [(_abs(f"p/{p['slug']}.html"), p["time_iso"][:10]) for p in posts if _indexable(p)]
        urls += [(_abs("about"), "")] + data_urls
        (site / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>" for u, d in urls) + "\n</urlset>\n",
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

    _zen_feed(site, posts)

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
