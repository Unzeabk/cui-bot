"""Обложки заметок 1280×720 в фирменном стиле (см. bot/brand.py).

Обычная заметка: тёмный фон, справа ночной снимок и контуры вокруг места события с перекрестием,
внизу координаты места моноширинным. Нет места — точечная сетка без координат.
«Одна цифра»: та же сетка, слева крупная цифра. «Позиция» — светлая, как бумага: в тёмной ленте её видно сразу.
"""

import html
from datetime import datetime
from pathlib import Path

from . import brand as B
from .compose import plain
from .rubrics import RUBRICS, SVG

NOTE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.mid{flex:1;display:flex;flex-direction:column;justify-content:center;gap:22px;max-width:__W__px;padding:20px 0}
.shade{background:linear-gradient(90deg,#0B0D10 0%,rgba(11,13,16,.92) 40%,rgba(11,13,16,0) 72%)}
</style></head><body><div class="root">__MAP__<div class="layer shade"></div>
<div class="top"><span class="logo">Cui Bono?</span><span class="tag"><b></b>__RUBRIC__ · __DATE__</span></div>
<div class="rule"></div>
<div class="mid">__FLY__<div class="hl" style="font-size:__SIZE__px">__HL__</div></div>
<div class="foot"><span>__PLACE__</span>__MARK__</div>
</div></body></html>"""

DATA = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.mid{flex:1;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);column-gap:56px;align-items:center;padding:16px 0}
.num{font-family:'Head',sans-serif;font-weight:600;line-height:.92;letter-spacing:-.03em;white-space:nowrap;font-variation-settings:'FLAR' 100,'VOLM' 100}
.bar{width:120px;height:8px;background:linear-gradient(90deg,#1D2126 0%,#2F343C 100%);margin:26px 0 22px}
.cap{font-family:'Onest',sans-serif;font-weight:400;font-size:28px;line-height:1.3;color:#C9CFD6;max-width:520px;text-wrap:balance}
.txt{display:flex;flex-direction:column;gap:18px}
.fly{font-size:34px}
</style></head><body><div class="root">__MAP__
<div class="top"><span class="logo">Cui Bono?</span><span class="tag"><b></b>__RUBRIC__ · __DATE__</span></div>
<div class="rule"></div>
<div class="mid"><div><div class="num" style="font-size:__NUMSIZE__px">__NUM__</div><div class="bar"></div><div class="cap">__CAP__</div></div>
<div class="txt">__FLY__<div class="hl" style="font-size:__SIZE__px">__HL__</div></div></div>
<div class="foot"><span>__PLACE__</span>__MARK__</div>
</div></body></html>"""

# «Позиция» — единственная светлая обложка: мнение редакции на бумаге цвета слоновой кости
POSITION = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.root{background:#EDE9E2;color:#0B0D10}
.logo{color:#0B0D10} .tag{color:#6B6F72} .rule{background:#0B0D10;height:2px}
.foot{border-top:2px solid #0B0D10;color:#0B0D10}
.mid{flex:1;display:grid;grid-template-columns:10px 1fr;column-gap:44px;padding:40px 0 34px}
.vbar{background:linear-gradient(180deg,#2F343C 0%,#1D2126 100%);}
.txt{display:flex;flex-direction:column;justify-content:center;gap:24px}
.stamp{align-self:flex-start;font-family:'Plex',monospace;font-weight:600;font-size:22px;letter-spacing:.2em;color:#EDE9E2;background:#0B0D10;padding:8px 16px}
.fly{color:#6B6F72}
</style></head><body><div class="root">
<div class="top"><span class="logo">Cui Bono?</span><span class="tag">__DATE__</span></div>
<div class="rule"></div>
<div class="mid"><div class="vbar"></div><div class="txt"><div class="stamp">ПОЗИЦИЯ</div>__FLY__<div class="hl" style="font-size:__SIZE__px">__HL__</div></div></div>
<div class="foot"><span>Мнение редакции</span>__MARK__</div>
</div></body></html>"""


def _e(s: str) -> str:
    return html.escape(plain(s or ""), quote=False)


def _fill(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace(f"__{k.upper()}__", str(v))
    return tpl


def _fly(fly: str) -> str:
    f = plain(fly or "").strip()
    return f'<div class="fly">{html.escape(f[:1].upper() + f[1:])}</div>' if f else ""


def place_line(place: dict | None, fallback: str = "") -> str:
    if not place:
        return html.escape(fallback)
    head = B.coords(place["lat"], place["lon"])
    return html.escape(f"{head}  ·  {place['name']}" if place.get("name") else head)


def hl_size(hl: str, big: tuple = (88, 80, 72, 64)) -> int:
    n = len(hl)
    return big[0] if n <= 38 else (big[1] if n <= 52 else (big[2] if n <= 68 else big[3]))


def render_cover(note: dict, out_path: Path, site: str = "") -> Path:
    rubric_name = note.get("_label") or RUBRICS[note["rubric"]][0]
    svg = SVG.get(note.get("_mark") or note["rubric"], SVG["world"])
    date = datetime.now().strftime("%d.%m.%Y")
    hl = plain(note["headline"])
    cover = note.get("cover") or {}
    place = B.place_ok(note.get("place"))
    common = dict(css=B.BASE_CSS, rubric=html.escape(rubric_name.upper()), date=date, fly=_fly(note.get("fly")),
                  hl=html.escape(hl, quote=False))

    if cover.get("type") == "position":
        page = _fill(POSITION, **common, size=hl_size(hl, (90, 82, 74, 66)), mark=B.mark(svg, "#0B0D10"))
    elif cover.get("type") == "data" and cover.get("number"):
        num = str(cover["number"])
        cap = plain(cover.get("number_caption") or "")
        page = _fill(
            DATA, **common, size=hl_size(hl, (64, 58, 52, 48)), mark=B.mark(svg),
            map=B.map_layer(place, fx=.82, opacity=.35) if place else B.map_layer(None),
            num=html.escape(num), numsize=230 if len(num) <= 3 else (190 if len(num) <= 4 else (150 if len(num) <= 6 else 116)),
            cap=html.escape(cap[:1].upper() + cap[1:]), place=place_line(place, note.get("_foot") or site),
        )
    else:
        page = _fill(
            NOTE, **common, size=hl_size(hl), mark=B.mark(svg), w=740 if place else 1000,
            map=B.map_layer(place, fx=.76, fy=.5), place=place_line(place, note.get("_foot") or site),
        )
    return B.shoot(page, out_path)
