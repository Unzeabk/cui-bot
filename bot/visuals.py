"""Визуальный слой сайта: картинки, которые добавляют информацию, а не повторяют заголовок.

- Заметка и разбор: полоса-карта места события (ночной снимок, контуры, перекрестие), рядом город, регион,
  расстояние до Москвы и координаты — то, чего нет в тексте.
- Разбор: график в духе The Economist, «цифра» между абзацами, «было → стало». Данные — только цифры,
  которые уже стоят в тексте: лёгкая модель их размечает, код проверяет, что каждое число есть в тексте.
- «Неделя»: карта мира с пронумерованными событиями без заголовка и оглавления.
- «Позиция»: плашка «ПОЗИЦИЯ · Мнение редакции» вместо картинки.

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

SYSTEM = """Ты — бильд-редактор издания об экономике (стиль The Economist). Тебе дают опубликованный текст.
Подбери к нему визуальные блоки. Правило одно: ТОЛЬКО цифры, которые уже есть в тексте, слово в слово. Ничего не досчитывай,
не округляй, не добавляй годы и значения, которых в тексте нет. Если подходящих цифр нет — блок null.

Блоки:
- "chart": график, если в тексте есть ряд из 3–8 сопоставимых значений (по годам, странам, компаниям).
  {"type": "bar" | "line", "title": "вывод одной фразой, 4–8 слов, не повторяет заголовок текста",
   "unit": "что и в чём измерено, напр. «млрд ₽» или «% к прошлому году»",
   "points": [["подпись", число], ...], "highlight": индекс точки, которую выделить (обычно последняя),
   "source": "название источника из текста"}
- "figure": одна главная цифра текста. {"value": "9,4 трлн ₽" (как в тексте), "caption": "что это, до 90 знаков", "source": "..."}
- "before_after": сдвиг «было → стало», если в тексте есть две цифры одного показателя в разное время.
  {"title": "что сдвинулось, 3–7 слов", "before": {"label": "2024", "value": "38%", "caption": "..."},
   "after": {"label": "2026", "value": "61%", "caption": "..."}, "source": "..."}
- "place": карта места — ТОЛЬКО если география и есть суть истории: событие физически произошло в конкретной точке
  (завод, порт, месторождение, город с очередями, граница, регион с особым режимом) и читателю важно, где это.
  null, если место — просто столица как синоним правительства (Вашингтон = власти США, Москва = Кремль/Минфин,
  Пекин = власти Китая, Брюссель = ЕС), если речь о мере на всю страну, о рынке, компании или деньгах без точки на карте.
  Сомневаешься — null: лишняя карта хуже, чем никакой.
  {"name": "город или место", "region": "регион/страна", "lat": 0.0, "lon": 0.0, "scale": "city" | "region" | "country" | "macro"}

Подписи по-русски, холодно, без оценок, без слов «важно», «ключевой», «рекордный», если их нет в тексте.
ОТВЕТ — только JSON: {"chart": ... | null, "figure": ... | null, "before_after": ... | null, "place": ... | null}"""


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


def _check(d: dict, text: str) -> dict:
    out = {}
    c = d.get("chart")
    if isinstance(c, dict):
        pts = [(str(a)[:18], float(b)) for a, b in (c.get("points") or []) if isinstance(b, (int, float))]
        if 3 <= len(pts) <= 8 and all(_num_in(text, v) for _, v in pts) and c.get("title"):
            out["chart"] = {"type": "line" if c.get("type") == "line" else "bar", "title": str(c["title"])[:80],
                            "unit": str(c.get("unit") or "")[:60], "points": pts,
                            "highlight": int(c.get("highlight", len(pts) - 1)) % len(pts), "source": str(c.get("source") or "")[:80]}
    f = d.get("figure")
    if isinstance(f, dict) and f.get("value") and _digits(f["value"]) and _num_in(text, f["value"]):
        out["figure"] = {"value": str(f["value"])[:20], "caption": str(f.get("caption") or "")[:120], "source": str(f.get("source") or "")[:80]}
    ba = d.get("before_after")
    if isinstance(ba, dict) and all(isinstance(ba.get(k), dict) for k in ("before", "after")):
        b, a = ba["before"], ba["after"]
        if _num_in(text, b.get("value", "")) and _num_in(text, a.get("value", "")) and b.get("value") != a.get("value"):
            out["before_after"] = {"title": str(ba.get("title") or "")[:70], "source": str(ba.get("source") or "")[:80],
                                   "before": {k: str(b.get(k) or "")[:90] for k in ("label", "value", "caption")},
                                   "after": {k: str(a.get(k) or "")[:90] for k in ("label", "value", "caption")}}
    p = B.place_ok(d.get("place"))
    if p:
        p["region"] = str((d.get("place") or {}).get("region") or "")[:60]
        out["place"] = p
    return out


def plan(e: dict) -> dict:
    """Разметка визуальных блоков для текста (один лёгкий запрос). Пустой dict — нечего показывать."""
    try:
        from .compose import extract_json
        from .gemini import LIGHT_CHAIN, ask
        text = _plain(e)
        raw, _ = ask(LIGHT_CHAIN, SYSTEM, f"Жанр: {e.get('kind')}\n\n{text[:12000]}", temperature=0.2)
        d = _check(extract_json(raw), text)
        if e.get("kind") != "analysis":  # в заметке только карта: текст короткий, картинки его перегрузят
            d = {k: v for k, v in d.items() if k == "place"}
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
    if c["type"] == "bar":
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
.v-pos{background:var(--ink);color:var(--bg);padding:12px 16px;margin:0 0 22px;font-size:14px;display:flex;align-items:center;gap:14px}
.v-pos span{font:600 12px "IBM Plex Mono",monospace;letter-spacing:.2em;background:var(--bg);color:var(--ink);padding:5px 10px}
@media(max-width:560px){.v-num{font-size:40px}.v-val{font-size:28px}}
"""


def build(e: dict, img_dir: Path, slug: str) -> dict:
    """Готовит картинки (если их ещё нет) и возвращает HTML-блоки: {"top": ..., "after": {номер абзаца: html}}."""
    out = {"top": "", "after": {}}
    v = e.get("visuals") or {}
    kind = e.get("kind")
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "position":
            out["top"] = POSITION_PLATE
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
            n = len(e.get("paragraphs") or [])
            if v.get("figure") and n > 3:
                out["after"][1] = figure_html(v["figure"])
            if v.get("chart") and n > 4:
                f = img_dir / f"{slug}-chart.jpg"
                if not f.exists():
                    chart_img(v["chart"], f)
                c = v["chart"]
                alt = f'{c["title"]}. {c["unit"]}: ' + ", ".join(f"{a} — {b:g}" for a, b in c["points"])
                out["after"][max(2, n * 2 // 5)] = img_html(f"/img/v/{f.name}", alt, 720, 380, "v-chart")
            if v.get("before_after") and n > 6:
                out["after"][max(4, n * 7 // 10)] = before_after_html(v["before_after"])
    except Exception as ex:  # noqa: BLE001
        log(f"визуал: {slug} не собран ({ex})")
    return out


def to_json(v: dict) -> str:
    return json.dumps(v, ensure_ascii=False)
