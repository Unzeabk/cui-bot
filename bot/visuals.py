"""Визуальный слой сайта: картинки, которые добавляют информацию, а не повторяют заголовок.

- Заметка и разбор: полоса-карта места события (ночной снимок, контуры, перекрестие), рядом город, регион,
  расстояние до Москвы и координаты — то, чего нет в тексте.
- Разбор: график в духе The Economist, «цифра» между абзацами, «было → стало». Данные — только цифры,
  которые уже стоят в тексте: лёгкая модель их размечает, код проверяет, что каждое число есть в тексте.
- «Неделя»: карта мира с пронумерованными событиями без заголовка и оглавления.
- «Позиция»: без картинки — строка «ПОЗИЦИЯ · Мнение редакции» над заголовком уже всё говорит.

Карты и графики снимаются в PNG фирменными шрифтами (как обложки в Telegram) и кладутся в site/img/v/ —
так они попадают в поиск по картинкам. «Цифра» и «было → стало» — HTML шрифтами сайта.
Любая ошибка здесь не мешает сборке: блок просто не появляется.
"""
from __future__ import annotations

import html
import json
import math
import re
import tempfile
from pathlib import Path

from . import brand as B
from .main import log

MOSCOW = (55.7558, 37.6173)

SYSTEM = """Ты — бильд-редактор издания об экономике уровня The Economist. Тебе дают опубликованный текст, абзацы пронумерованы [0], [1]…
Визуал не украшает, а доказывает ход драматургии там, где он стоит. Правило цифр: ТОЛЬКО цифры, которые уже есть в тексте, слово в слово;
ничего не досчитывай, не округляй, не добавляй. Нет подходящих цифр — блока нет. Лучше ноль блоков, чем слабый.

Ходы разбора и что к ним ставить (поле "para" — номер абзаца, ПОСЛЕ которого встаёт блок; это абзац с нужными цифрами):
- поворот (очевидное объяснение ломается об одну цифру) → "vs" (ожидание против факта) или "figure" (одна цифра);
- механизм (кто кому и сколько платит) → "flow" (цепочка потоков с суммами);
- другая сторона, сравнение с соседями → "compare" (3–6 сопоставимых: страны, компании, регионы);
- подъём, закон во времени → "chart" (ряд за 3–8 лет/кварталов) или "before_after" (было → стало);
- вход и финал — без блоков.
Не больше 3 блоков на текст, каждый к своему ходу, не два подряд.

Форматы блоков (в "blocks"):
{"type": "figure", "para": 1, "value": "9,4 трлн ₽", "caption": "что это, до 90 знаков", "source": "..."}
{"type": "vs", "para": 1, "title": "4–7 слов", "expected": {"value": "...", "caption": "чего ждали"}, "actual": {"value": "...", "caption": "что вышло"}, "source": "..."}
{"type": "flow", "para": 4, "title": "кто кому платит, 4–7 слов", "steps": [{"name": "Покупатель в Китае"}, {"name": "Посредник в Гонконге", "amount": "4,2 млрд $", "label": "за что"}, {"name": "Экспортёр"}], "source": "..."}
   (2–4 участника по цепочке; "amount" у участника — сумма, пришедшая к нему от предыдущего)
{"type": "compare", "para": 7, "title": "вывод одной фразой", "unit": "в чём", "items": [["Россия", 33], ["Индия", 48]], "highlight": 0, "source": "..."}
{"type": "chart", "para": 10, "kind": "bar" | "line", "title": "вывод одной фразой, не повторяет заголовок", "unit": "в чём", "points": [["2021", 62], ...], "highlight": 5, "source": "..."}
{"type": "before_after", "para": 10, "title": "...", "before": {"label": "2024", "value": "38%", "caption": "..."}, "after": {"label": "2026", "value": "61%", "caption": "..."}, "source": "..."}

"place" — карта места, ТОЛЬКО если география и есть суть истории: событие физически произошло в конкретной точке
(завод, порт, месторождение, город с очередями, граница, регион с особым режимом) и читателю важно, где это.
null, если место — столица как синоним власти (Вашингтон, Москва, Пекин, Брюссель), мера на всю страну, рынок, компания или деньги без точки.
Сомневаешься — null. {"name": "...", "region": "регион/страна", "lat": 0.0, "lon": 0.0, "scale": "city" | "region" | "country" | "macro"}

Подписи по-русски, холодно, без оценок. ОТВЕТ — только JSON: {"blocks": [...], "place": {...} | null}"""


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def _tokens(text: str) -> set[str]:
    flat = re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d{3}\b)", "", text)
    return {t.replace(".", ",") for t in re.findall(r"\d+(?:[.,]\d+)?", flat)}


def _num_in(text: str, v) -> bool:
    """Число есть в тексте (31,5 = «31,5» = «31.5»; 1200 = «1 200»)."""
    if isinstance(v, (int, float)):
        cand = f"{v:g}".replace(".", ",")
    else:
        m = re.search(r"\d+(?:[.,]\d+)?", re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d{3}\b)", "", str(v)))
        if not m:
            return False
        cand = m.group(0).replace(".", ",")
    return cand in _tokens(text)


def _plain(e: dict) -> str:
    from .compose import plain
    return "\n".join([e.get("headline", ""), e.get("subhead", "")] + [plain(p) for p in e.get("paragraphs") or []])


def _s(x, n=90) -> str:
    return str(x or "").strip()[:n]


def _check(d: dict, text: str, n_par: int) -> dict:
    out, used = [], set()
    for b in (d.get("blocks") or [])[:4]:
        if not isinstance(b, dict):
            continue
        t, para = b.get("type"), b.get("para")
        if not isinstance(para, int) or not (0 <= para < n_par - 1) or para in used or (para - 1) in used or (para + 1) in used:
            continue
        x = None
        if t == "figure" and _num_in(text, b.get("value", "")):
            x = {"value": _s(b["value"], 20), "caption": _s(b.get("caption"), 120)}
        elif t in ("vs", "before_after"):
            a, c = (b.get("expected"), b.get("actual")) if t == "vs" else (b.get("before"), b.get("after"))
            if isinstance(a, dict) and isinstance(c, dict) and _num_in(text, a.get("value", "")) and _num_in(text, c.get("value", "")) \
                    and a.get("value") != c.get("value"):
                x = {"title": _s(b.get("title"), 70), "a": {k: _s(a.get(k)) for k in ("label", "value", "caption")},
                     "b": {k: _s(c.get(k)) for k in ("label", "value", "caption")}}
        elif t == "flow":
            st = [z for z in (b.get("steps") or []) if isinstance(z, dict) and z.get("name")][:4]
            if len(st) >= 2 and all(_num_in(text, z["amount"]) for z in st if z.get("amount")) and any(z.get("amount") for z in st):
                x = {"title": _s(b.get("title"), 70), "steps": [{k: _s(z.get(k), 60) for k in ("name", "amount", "label")} for z in st]}
        elif t in ("compare", "chart"):
            key = "items" if t == "compare" else "points"
            pts = [(_s(a, 22), float(v)) for a, v in (b.get(key) or []) if isinstance(v, (int, float))]
            lo, hi = (3, 6) if t == "compare" else (3, 8)
            if lo <= len(pts) <= hi and all(_num_in(text, v) for _, v in pts) and b.get("title"):
                x = {"title": _s(b["title"], 80), "unit": _s(b.get("unit"), 60), "points": pts,
                     "highlight": int(b.get("highlight", 0 if t == "compare" else len(pts) - 1) or 0) % len(pts)}
                if t == "chart":
                    x["kind"] = "line" if b.get("kind") == "line" else "bar"
        if x:
            x.update(type=t, para=para, source=_s(b.get("source"), 80))
            out.append(x)
            used.add(para)
    res = {"blocks": out[:3]}
    p = B.place_ok(d.get("place"))
    if p:
        p["region"] = _s((d.get("place") or {}).get("region"), 60)
        res["place"] = p
    return res


def plan(e: dict) -> dict:
    """Разметка визуальных блоков для текста (один лёгкий запрос). Пустой dict — нечего показывать."""
    try:
        from .compose import extract_json
        from .gemini import LIGHT_CHAIN, ask
        from .compose import plain
        text = _plain(e)
        pars = e.get("paragraphs") or []
        numbered = "\n\n".join(f"[{i}] {plain(p)}" for i, p in enumerate(pars))
        raw, _ = ask(LIGHT_CHAIN, SYSTEM, f"Жанр: {e.get('kind')}\n{e.get('headline', '')}\n{e.get('subhead', '')}\n\n{numbered[:14000]}",
                     temperature=0.2)
        d = _check(extract_json(raw), text, len(pars))
        if e.get("kind") != "analysis":  # в заметке только карта: текст короткий, блоки его перегрузят
            d["blocks"] = []
        return d
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: разметка не получена ({ex})")
        return {}


# ---------- съёмка ----------

def _shoot(page: str, path: Path, w: int, h: int) -> Path:
    from playwright.sync_api import sync_playwright
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page)
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            pg.goto(tmp.as_uri(), wait_until="load")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(250)
            png = path.with_suffix(".png")
            pg.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": w, "height": h})
            b.close()
        from PIL import Image
        Image.open(png).convert("RGB").save(path, "JPEG", quality=86, optimize=True, progressive=True)
        png.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    return path


CSS = B.FONT_CSS + """
*{box-sizing:border-box;margin:0}body{background:#0B0D10;color:#EDE9E2;font-family:'Head',sans-serif;-webkit-font-smoothing:antialiased}
.k{font-family:'Plex',monospace;font-weight:500;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#6B6F72}
.mono{font-family:'Plex',monospace}
"""


def _km(a, b) -> int:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return round(2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                                + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)))


def map_strip(place: dict, path: Path) -> Path:
    W, H = 720, 260
    m = B.map_layer(place, w=W * 2, h=H * 2, fx=.66, fy=.5, fade=None)
    lines = []
    d = _km((place["lat"], place["lon"]), MOSCOW)
    if d > 15:
        lines.append(f"{d:,} км до Москвы".replace(",", " "))
    lines.append(B.coords(place["lat"], place["lon"]))
    region = html.escape(place.get("region") or "")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.root{{position:relative;width:{W}px;height:{H}px;overflow:hidden}}
.map{{position:absolute;inset:0;transform:scale(.5);transform-origin:0 0}}
.shade{{position:absolute;inset:0;background:linear-gradient(90deg,#0B0D10 0%,rgba(11,13,16,.92) 34%,rgba(11,13,16,0) 62%)}}
.txt{{position:absolute;left:24px;top:24px;bottom:24px;display:flex;flex-direction:column}}
.name{{font-weight:620;font-size:28px;line-height:1.1;margin-top:10px;font-variation-settings:'FLAR' 100,'VOLM' 100}}
.reg{{font-family:'Onest',sans-serif;font-size:15px;color:#C9CFD6;margin-top:6px}}
.facts{{margin-top:auto;font-family:'Plex',monospace;font-size:12.5px;line-height:21px;color:#C9CFD6}}
</style></head><body><div class="root"><div class="map">{m}</div><div class="shade"></div>
<div class="txt"><div class="k">Место события</div><div class="name">{html.escape(place.get("name") or "")}</div>
{f'<div class="reg">{region}</div>' if region else ''}<div class="facts">{"<br>".join(html.escape(x) for x in lines)}</div></div>
</div></body></html>"""
    return _shoot(page, path, W, H)


def chart_img(c: dict, path: Path) -> Path:
    W, H = 720, 380
    pts, hi = c["points"], c["highlight"]
    vals = [v for _, v in pts]
    lo, top = min(0, min(vals)), max(vals) * 1.15 or 1
    x0, x1, y0, y1 = 56, W - 28, H - 70, 128
    def Y(v): return y0 - (v - lo) / (top - lo) * (y0 - y1)
    step = (x1 - x0) / len(pts)
    def fmt(v): return (f"{v:,.1f}" if v != int(v) else f"{int(v):,}").replace(",", " ").replace(".", ",")
    g = "".join(f'<line x1="{x0}" x2="{x1}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="#1C2026"/>'
                f'<text x="{x0 - 8}" y="{Y(t) + 4:.1f}" class="ax" text-anchor="end">{fmt(t)}</text>'
                for t in _ticks(lo, top))
    body = ""
    if c.get("kind", c.get("type")) == "bar":
        bw = step * .58
        for i, (lab, v) in enumerate(pts):
            x = x0 + i * step + (step - bw) / 2
            on = i == hi
            body += (f'<rect x="{x:.1f}" y="{min(Y(v), Y(0)):.1f}" width="{bw:.1f}" height="{abs(Y(0) - Y(v)):.1f}" fill="{"#EDE9E2" if on else "#2F343C"}"/>'
                     f'<text x="{x + bw / 2:.1f}" y="{Y(v) - 8:.1f}" class="val{" on" if on else ""}" text-anchor="middle">{fmt(v)}</text>'
                     f'<text x="{x + bw / 2:.1f}" y="{y0 + 20}" class="ax" text-anchor="middle">{html.escape(lab)}</text>')
    else:
        xy = [(x0 + i * step + step / 2, Y(v)) for i, (_, v) in enumerate(pts)]
        body += f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in xy)}" fill="none" stroke="#C9CFD6" stroke-width="2.5"/>'
        for i, ((x, y), (lab, v)) in enumerate(zip(xy, pts)):
            on = i == hi
            body += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if on else 3}" fill="{"#EDE9E2" if on else "#C9CFD6"}"/>'
                     + (f'<text x="{x:.1f}" y="{y - 12:.1f}" class="val on" text-anchor="middle">{fmt(v)}</text>' if on or i == 0 else "")
                     + f'<text x="{x:.1f}" y="{y0 + 20}" class="ax" text-anchor="middle">{html.escape(lab)}</text>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.root{{position:relative;width:{W}px;height:{H}px;background:#0E1114;border-top:3px solid #EDE9E2;padding:22px 24px}}
.t{{font-weight:620;font-size:21px;line-height:1.2;margin-top:10px;font-variation-settings:'FLAR' 100,'VOLM' 100}}
.u{{font-family:'Onest',sans-serif;font-size:14px;color:#C9CFD6;margin-top:6px}}
svg{{position:absolute;left:0;top:0}}
.ax{{font-family:'Plex',monospace;font-size:11.5px;fill:#6B6F72}}
.val{{font-family:'Plex',monospace;font-size:12.5px;fill:#C9CFD6}}.val.on{{fill:#EDE9E2;font-weight:600}}
.src{{position:absolute;left:24px;bottom:16px}}
</style></head><body><div class="root"><div class="k" style="color:#C9CFD6">График</div>
<div class="t">{html.escape(c["title"])}</div><div class="u">{html.escape(c["unit"])}</div>
<svg width="{W}" height="{H}">{g}{body}<line x1="{x0}" x2="{x1}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#6B6F72"/></svg>
<div class="k src">{("Источник: " + html.escape(c["source"]) + " · ") if c["source"] else ""}Cui Bono?</div></div></body></html>"""
    return _shoot(page, path, W, H)


def _ticks(lo: float, top: float) -> list[float]:
    span = top - lo
    raw = span / 3
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    st = min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    out, t = [], math.ceil(lo / st) * st
    while t < top:
        if abs(t) > 1e-9:
            out.append(t)
        t += st
    return out[:4]


def week_map(places: list, path: Path) -> Path:
    W, H = 720, 320
    m = B.world_layer([B.place_ok(p) for p in places], w=W * 2, h=H * 2, area=(80, 70, W * 2 - 80, H * 2 - 90))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
.root{{position:relative;width:{W}px;height:{H}px;overflow:hidden}}.map{{position:absolute;inset:0;transform:scale(.5);transform-origin:0 0}}
.lab{{position:absolute;left:20px;bottom:16px}}</style></head><body><div class="root"><div class="map">{m}</div>
<div class="k lab">{len([p for p in places if p])} событий недели · номера — как в тексте</div></div></body></html>"""
    return _shoot(page, path, W, H)


# ---------- HTML-блоки страницы ----------

def figure_html(f: dict) -> str:
    return (f'<figure class="v-fig"><div class="v-num">{html.escape(f["value"])}</div>'
            f'<figcaption>{html.escape(f["caption"])}' + (f'<span>{html.escape(f["source"])}</span>' if f.get("source") else "")
            + "</figcaption></figure>")


def pair_html(b: dict) -> str:
    """«Против ожидания» и «было → стало»: две цифры и стрелка."""
    k = "Ожидание и факт" if b["type"] == "vs" else "Было и стало"
    def side(x):
        return ((f'<div class="v-lab">{html.escape(x["label"])}</div>' if x.get("label") else "")
                + f'<div class="v-val">{html.escape(x["value"])}</div><div class="v-cap">{html.escape(x["caption"])}</div>')
    return (f'<figure class="v-ba"><div class="v-k">{k}</div>' + (f'<div class="v-t">{html.escape(b["title"])}</div>' if b.get("title") else "")
            + f'<div class="v-row"><div class="{"v-dim" if b["type"] == "vs" else ""}">{side(b["a"])}</div><div class="v-arr">→</div><div>{side(b["b"])}</div></div>'
            + (f'<div class="v-src">Источник: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def flow_html(b: dict) -> str:
    parts = []
    for i, z in enumerate(b["steps"]):
        if i:
            parts.append(f'<div class="v-edge"><span>{html.escape(z.get("amount") or "")}</span>'
                         + (f'<i>{html.escape(z["label"])}</i>' if z.get("label") else "") + "</div>")
        parts.append(f'<div class="v-node">{html.escape(z["name"])}</div>')
    return (f'<figure class="v-flow"><div class="v-k">Как идут деньги</div><div class="v-t">{html.escape(b["title"])}</div>'
            f'<div class="v-chain">{"".join(parts)}</div>'
            + (f'<div class="v-src">Источник: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def compare_html(b: dict) -> str:
    mx = max(abs(v) for _, v in b["points"]) or 1
    def fmt(v): return (f"{v:,.1f}" if v != int(v) else f"{int(v):,}").replace(",", " ").replace(".", ",")
    rows = "".join(f'<div class="v-bar{" on" if i == b["highlight"] else ""}"><span>{html.escape(n)}</span>'
                   f'<div><b style="width:{abs(v) / mx * 100:.1f}%"></b></div><em>{fmt(v)}</em></div>'
                   for i, (n, v) in enumerate(b["points"]))
    return (f'<figure class="v-cmp"><div class="v-k">Сравнение</div><div class="v-t">{html.escape(b["title"])}</div>'
            + (f'<div class="v-u">{html.escape(b["unit"])}</div>' if b.get("unit") else "") + rows
            + (f'<div class="v-src">Источник: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def before_after_html(b: dict) -> str:
    def side(x):
        return (f'<div><div class="v-lab">{html.escape(x["label"])}</div><div class="v-val">{html.escape(x["value"])}</div>'
                f'<div class="v-cap">{html.escape(x["caption"])}</div></div>')
    return (f'<figure class="v-ba"><div class="v-k">Было и стало</div><div class="v-t">{html.escape(b["title"])}</div>'
            f'<div class="v-row">{side(b["before"])}<div class="v-arr">→</div>{side(b["after"])}</div>'
            + (f'<div class="v-src">Источник: {html.escape(b["source"])}</div>' if b.get("source") else "") + "</figure>")


def img_html(src: str, alt: str, w: int, h: int, cls: str) -> str:
    return f'<figure class="{cls}"><img src="{src}" alt="{html.escape(alt)}" width="{w}" height="{h}" loading="lazy"></figure>'


POSITION_PLATE = ('<div class="v-pos"><span>ПОЗИЦИЯ</span>Мнение редакции</div>')

CSS_SITE = """
.v-map,.v-chart,.v-week{margin:0 0 26px}.v-map img,.v-chart img,.v-week img{width:100%;height:auto;display:block;border:1px solid var(--rule)}
.v-chart{margin:28px 0}
.v-fig{margin:28px 0;padding:20px 0 20px 22px;border-left:4px solid #2F343C}
.v-num{font:600 52px/1 Commissioner,sans-serif;letter-spacing:-.02em}
.v-fig figcaption{font-size:16px;color:var(--cold);margin-top:10px;max-width:560px}
.v-fig figcaption span,.v-src{display:block;font:11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-top:8px}
.v-ba{margin:28px 0;padding:20px 22px;background:#0E1114;border-top:3px solid var(--ink)}
.v-k,.v-lab{font:11px "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}.v-k{color:var(--cold)}
.v-t{font:600 19px/1.25 Commissioner,sans-serif;margin:8px 0 16px}
.v-row{display:grid;grid-template-columns:1fr 36px 1fr;align-items:center;gap:10px}
.v-val{font:600 36px/1.1 Commissioner,sans-serif;margin:4px 0}.v-cap{font-size:14px;color:var(--cold)}
.v-arr{font:26px "IBM Plex Mono",monospace;color:var(--mute);text-align:center}
.v-pos{border:1px solid var(--rule);border-left:3px solid var(--ink);color:var(--cold);padding:10px 14px;margin:0 0 22px;font-size:14px;display:flex;align-items:center;gap:14px}
.v-pos span{font:600 12px "IBM Plex Mono",monospace;letter-spacing:.2em;color:var(--ink)}
.v-dim .v-val{color:var(--mute)}
.v-flow,.v-cmp{margin:28px 0;padding:20px 22px;background:#0E1114;border-top:3px solid var(--ink)}
.v-chain{display:flex;flex-direction:column;align-items:flex-start;margin-top:4px}
.v-node{border:1px solid #2F343C;padding:11px 14px;font:600 15px/1.25 Commissioner,sans-serif;width:100%;max-width:380px}
.v-edge{position:relative;padding:8px 0 12px 204px;width:100%;max-width:380px;min-height:58px;display:flex;flex-direction:column;justify-content:center}
.v-edge::before{content:"";position:absolute;left:190px;top:0;bottom:8px;border-left:1px solid #6B6F72}
.v-edge::after{content:"";position:absolute;left:185px;bottom:0;border:5px solid transparent;border-top:7px solid #6B6F72}
.v-edge span{font:600 15px "IBM Plex Mono",monospace}.v-edge i{font:normal 12px Onest,sans-serif;color:var(--mute)}
.v-u{font-size:14px;color:var(--cold);margin:-8px 0 14px}
.v-bar{display:grid;grid-template-columns:150px 1fr 80px;align-items:center;gap:12px;margin:8px 0;font-size:14px;color:var(--cold)}
.v-bar div{height:14px}.v-bar b{display:block;height:100%;background:#2F343C}.v-bar.on b{background:var(--ink)}.v-bar.on{color:var(--ink)}
.v-bar em{font:normal 13px "IBM Plex Mono",monospace;text-align:right}
@media(max-width:560px){.v-edge{padding-left:calc(50% + 14px)}.v-edge::before{left:50%}.v-edge::after{left:calc(50% - 5px)}.v-bar{grid-template-columns:100px 1fr 64px}}
@media(max-width:560px){.v-num{font-size:40px}.v-val{font-size:28px}}
"""


def build(e: dict, img_dir: Path, slug: str) -> dict:
    """Готовит картинки (если их ещё нет) и возвращает HTML-блоки: {"top": ..., "after": {номер абзаца: html}}."""
    out = {"top": "", "after": {}}
    v = e.get("visuals") or {}
    kind = e.get("kind")
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        place = B.place_ok(v.get("place"))  # место от автора текста — для обложки; на сайте карта, только если место — суть истории
        if place and kind in ("note", "analysis"):
            place["region"] = (v.get("place") or {}).get("region") or ""
            f = img_dir / f"{slug}-map.jpg"
            if not f.exists():
                map_strip(place, f)
            alt = f"{place.get('name', '')}" + (f", {place['region']}" if place.get("region") else "") + " — место события на карте"
            out["top"] = img_html(f"/img/v/{f.name}", alt, 720, 260, "v-map")
        if kind == "week" and any(e.get("places") or []):
            f = img_dir / f"{slug}-week.jpg"
            if not f.exists():
                week_map(e["places"], f)
            out["top"] = img_html(f"/img/v/{f.name}", "Карта событий недели", 720, 320, "v-week")
        if kind == "analysis":
            for b in v.get("blocks") or []:
                t, para = b.get("type"), b.get("para")
                if t == "figure":
                    h = figure_html(b)
                elif t in ("vs", "before_after"):
                    h = pair_html(b)
                elif t == "flow":
                    h = flow_html(b)
                elif t == "compare":
                    h = compare_html(b)
                elif t == "chart":
                    f = img_dir / f"{slug}-chart-{para}.jpg"
                    if not f.exists():
                        chart_img(b, f)
                    alt = f'{b["title"]}. {b["unit"]}: ' + ", ".join(f"{a} — {x:g}" for a, x in b["points"])
                    h = img_html(f"/img/v/{f.name}", alt, 720, 380, "v-chart")
                else:
                    continue
                out["after"][para] = h
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: {slug} не собран ({ex})")
    return out


def to_json(v: dict) -> str:
    return json.dumps(v, ensure_ascii=False)
