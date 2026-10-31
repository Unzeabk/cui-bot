"""Фирменный стиль Cui Bono? для обложек: цвета, шрифты, карта места события и съёмка страницы.

Набор из брендбука:
  тёмный #0B0D10 (основной) · слоновая кость #EDE9E2 · графит #6B6F72 · глубоководный синий #123E5C (акцент) · холодный серый #C9CFD6
  Literata — словесный знак «Cui Bono?» (как в логотипе), Commissioner — заголовки и акценты (вместо Commissioner и бордо — решение владельца 31 октября), Onest — служебные надписи, IBM Plex Mono — данные, координаты, маркировка.
Графические элементы: сетка и фокус (перекрестие), география и контекст (карта), связи, маркеры (точечная сетка).

Шрифты лежат в assets/fonts (OFL), контуры стран — в assets/geo (Natural Earth, общественное достояние),
так что обложка не зависит от Google Fonts. Ночной снимок Земли берётся у NASA GIBS (VIIRS Black Marble,
общественное достояние); если сервис не ответил, остаётся линейная карта.
"""

import base64
import json
import math
import tempfile
from functools import lru_cache
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "assets" / "fonts"
GEO_DIR = ROOT / "assets" / "geo"

DARK = "#0B0D10"
IVORY = "#EDE9E2"
GRAPHITE = "#6B6F72"
ACCENT = "#123E5C"  # глубоководный синий, акцент; заменил бордо, которое резало глаз (решение владельца 31 октября)
BORDO = ACCENT  # старое имя, чтобы не ломать вызовы
COLD = "#C9CFD6"
LINE = "#2A2F35"  # тонкие линии на тёмном

FONT_CSS = f"""
@font-face{{font-family:'Logo';src:url('{(FONTS_DIR / "Literata.ttf").as_uri()}') format('truetype');font-weight:200 900}}
@font-face{{font-family:'Head';src:url('{(FONTS_DIR / "Commissioner.ttf").as_uri()}') format('truetype');font-weight:100 900}}
@font-face{{font-family:'Onest';src:url('{(FONTS_DIR / "Onest.ttf").as_uri()}') format('truetype');font-weight:100 900}}
@font-face{{font-family:'Plex';src:url('{(FONTS_DIR / "IBMPlexMono-Regular.ttf").as_uri()}') format('truetype');font-weight:400}}
@font-face{{font-family:'Plex';src:url('{(FONTS_DIR / "IBMPlexMono-Medium.ttf").as_uri()}') format('truetype');font-weight:500}}
@font-face{{font-family:'Plex';src:url('{(FONTS_DIR / "IBMPlexMono-SemiBold.ttf").as_uri()}') format('truetype');font-weight:600}}
"""

# Общая сетка всех обложек. В ленте Telegram картинка ужимается до ~360 px, поэтому
# заголовок крупный, служебные строки не мельче 21 px, линии не тоньше 1,5 px.
BASE_CSS = FONT_CSS + f"""
*{{box-sizing:border-box}} body{{margin:0;background:{DARK}}}
.root{{position:relative;width:1280px;height:720px;overflow:hidden;background:{DARK};color:{IVORY};
  font-family:'Onest',sans-serif;padding:40px 64px 36px;display:flex;flex-direction:column}}
.layer{{position:absolute;inset:0;z-index:0}}
.root>*:not(.layer){{position:relative;z-index:1}}
.top{{display:flex;justify-content:space-between;align-items:baseline}}
.logo{{font-family:'Logo',serif;font-weight:500;font-size:34px;letter-spacing:-.005em;font-variation-settings:'opsz' 48}}
.tag{{font-family:'Plex',monospace;font-weight:500;font-size:21px;letter-spacing:.16em;color:{COLD};text-transform:uppercase}}
.tag b{{display:inline-block;width:12px;height:12px;background:{BORDO};margin-right:14px;vertical-align:1px}}
.rule{{height:1.5px;background:{LINE};margin-top:16px}}
.fly{{font-family:'Head',sans-serif;font-weight:400;font-size:35px;line-height:1.2;color:{COLD};font-variation-settings:'slnt' -10}}
.hl{{font-family:'Head',sans-serif;font-weight:620;line-height:1.1;letter-spacing:-.02em;font-variation-settings:'FLAR' 100,'VOLM' 100;text-wrap:balance}}
.foot{{display:flex;justify-content:space-between;align-items:center;border-top:1.5px solid {LINE};padding-top:14px;
  font-family:'Plex',monospace;font-weight:500;font-size:21px;letter-spacing:.12em;color:{COLD};text-transform:uppercase}}
.foot svg{{width:46px;height:46px}}
"""


def mark(svg: str, color: str = IVORY) -> str:
    """Знак рубрики в нужном цвете (в rubrics.py он нарисован чёрным)."""
    return svg.replace("#141414", color)


def shoot(page: str, path) -> Path:
    """Снимает HTML 1280×720 в PNG. Страница открывается как файл, чтобы подтянулись локальные шрифты."""
    from playwright.sync_api import sync_playwright

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page)
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5)
            pg.goto(tmp.as_uri(), wait_until="load")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(250)
            pg.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": 1280, "height": 720})
            b.close()
    finally:
        tmp.unlink(missing_ok=True)
    return path


# --- карта -------------------------------------------------------------------

R = 6378137.0
SCALES = {"city": 700, "region": 1800, "country": 3200, "macro": 7000}  # ширина кадра в км


def _merc(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-85.0, min(85.0, lat))
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


@lru_cache(maxsize=2)
def _countries(res: str) -> list:
    return json.loads((GEO_DIR / f"countries-{res}.json").read_text(encoding="utf-8"))


class Frame:
    """Кадр в проекции Меркатора: место события стоит в точке (fx, fy) картинки w×h."""

    def __init__(self, lat: float, lon: float, width_km: float, w: int, h: int, fx: float = .5, fy: float = .5):
        self.w, self.h = w, h
        self.mpp = width_km * 1000 / max(math.cos(math.radians(lat)), 0.2) / w  # метров Меркатора на пиксель
        cx, cy = _merc(lon, lat)
        self.x0 = cx - fx * w * self.mpp
        self.y1 = cy + fy * h * self.mpp

    def px(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = _merc(lon, lat)
        return (x - self.x0) / self.mpp, (self.y1 - y) / self.mpp

    def bbox(self) -> tuple[float, float, float, float]:
        return self.x0, self.y1 - self.h * self.mpp, self.x0 + self.w * self.mpp, self.y1


def outlines(fr: Frame, res: str = "50m", stroke: str = COLD, opacity: float = .55, fill: str = "#12161B") -> str:
    """Контуры стран, попадающих в кадр, одной SVG-группой."""
    paths = []
    pad = 60
    for c in _countries(res):
        for ring in c["p"]:
            pts = [fr.px(x, y) for x, y in ring]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if max(xs) < -pad or min(xs) > fr.w + pad or max(ys) < -pad or min(ys) > fr.h + pad:
                continue
            if max(xs) - min(xs) > fr.w * 40:  # кольцо через линию перемены дат
                continue
            paths.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return (f'<g fill="{fill}" stroke="{stroke}" stroke-opacity="{opacity}" stroke-width="1.3" stroke-linejoin="round">'
            f'<path d="{" ".join(paths)}"/></g>')


def dot_grid(w: int, h: int, step: int = 28, color: str = GRAPHITE, opacity: float = .45) -> str:
    """Точечная сетка — «маркеры и индексы» из брендбука."""
    return (f'<defs><pattern id="dots" width="{step}" height="{step}" patternUnits="userSpaceOnUse">'
            f'<circle cx="{step / 2}" cy="{step / 2}" r="1.6" fill="{color}" fill-opacity="{opacity}"/></pattern></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#dots)"/>')


def crosshair(x: float, y: float, arm: int = 64) -> str:
    """«Сетка и фокус»: перекрестие на месте события, в центре бордовая точка."""
    return (f'<g stroke="{IVORY}" stroke-width="1.5" fill="none">'
            f'<line x1="{x - arm}" y1="{y}" x2="{x - 12}" y2="{y}"/><line x1="{x + 12}" y1="{y}" x2="{x + arm}" y2="{y}"/>'
            f'<line x1="{x}" y1="{y - arm}" x2="{x}" y2="{y - 12}"/><line x1="{x}" y1="{y + 12}" x2="{x}" y2="{y + arm}"/>'
            f'<circle cx="{x}" cy="{y}" r="22" stroke-opacity=".6"/></g>'
            f'<circle cx="{x}" cy="{y}" r="7" fill="{BORDO}" stroke="{IVORY}" stroke-width="2"/>')


GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi"
NIGHT_LAYERS = ("VIIRS_Black_Marble", "VIIRS_CityLights_2012")


def night_image(fr: Frame) -> str | None:
    """Ночной снимок Земли из космоса для кадра: data URI или None, если NASA не ответило."""
    x0, y0, x1, y1 = fr.bbox()
    for layer in NIGHT_LAYERS:
        try:
            r = requests.get(GIBS, timeout=25, params={
                "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": layer, "STYLES": "",
                "SRS": "EPSG:3857", "BBOX": f"{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}",
                "WIDTH": str(min(fr.w, 1600)), "HEIGHT": str(min(fr.h, 900)), "FORMAT": "image/jpeg"})
            if r.ok and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 8000:
                return "data:image/jpeg;base64," + base64.b64encode(r.content).decode()
        except requests.RequestException:
            continue
    return None


def coords(lat: float, lon: float) -> str:
    return f"{abs(lat):.2f}° {'N' if lat >= 0 else 'S'}  {abs(lon):.2f}° {'E' if lon >= 0 else 'W'}"


def place_ok(p) -> dict | None:
    """Проверяет место события из ответа модели: {"name", "lat", "lon", "scale"}."""
    if not isinstance(p, dict):
        return None
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-75 <= lat <= 80 and -180 <= lon <= 180) or (abs(lat) < .01 and abs(lon) < .01):
        return None
    name = str(p.get("name") or "").strip().upper()[:22]
    scale = p.get("scale") if p.get("scale") in SCALES else "country"
    return {"name": name, "lat": lat, "lon": lon, "scale": scale}


def map_layer(place: dict | None, w: int = 1280, h: int = 720, fx: float = .72, fy: float = .5,
              fade: str = "to right", night: bool = True, opacity: float = 1.0) -> str:
    """Фон обложки: ночной снимок и контуры вокруг места события с перекрестием.
    Без места — точечная сетка: координат нет, и это тоже сообщение."""
    if not place:
        return f'<svg class="layer" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{dot_grid(w, h)}</svg>'
    fr = Frame(place["lat"], place["lon"], SCALES[place["scale"]], w, h, fx, fy)
    res = "50m" if place["scale"] in ("city", "region", "country") else "110m"
    img = night_image(fr) if night else None
    x, y = fr.px(place["lon"], place["lat"])
    sat = (f'<image href="{img}" width="{w}" height="{h}" preserveAspectRatio="none" '
           f'style="filter:grayscale(1) contrast(1.25) brightness(.95)" opacity=".9"/>') if img else ""
    fill = "none" if img else "#12161B"
    mask = (f'<defs><linearGradient id="fade" gradientTransform="{"rotate(0)" if fade == "to right" else "rotate(90)"}">'
            f'<stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".42" stop-color="#fff" stop-opacity="1"/></linearGradient>'
            f'<mask id="m"><rect width="{w}" height="{h}" fill="url(#fade)"/></mask></defs>') if fade else ""
    body = f'{sat}{outlines(fr, res, fill=fill, opacity=.42 if img else .55)}'
    masked = 'mask="url(#m)"' if fade else ""
    return (f'<svg class="layer" viewBox="0 0 {w} {h}" width="{w}" height="{h}" style="opacity:{opacity}">{mask}'
            f'<g {masked}>{body}</g>{crosshair(x, y)}</svg>')


def world_layer(points: list[dict], w: int = 1280, h: int = 720, area: tuple = (64, 110, 760, 400)) -> str:
    """Карта для «Недели»: кадр подгоняется под события, они — пронумерованные маркеры в области area
    (x0, y0, x1, y1), подальше от оглавления справа. Близкие точки получают общую подпись «02·03»."""
    pts = [(k, p) for k, p in enumerate(points) if p]
    if not pts:
        return f'<svg class="layer" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{dot_grid(w, h)}</svg>'
    ms = [_merc(p["lon"], p["lat"]) for _, p in pts]
    xs, ys = [m[0] for m in ms], [m[1] for m in ms]
    aw, ah = area[2] - area[0], area[3] - area[1]
    mpp = max((max(xs) - min(xs)) / aw, (max(ys) - min(ys)) / ah, 4_500_000 / aw) * 1.3
    fr = Frame.__new__(Frame)
    fr.w, fr.h, fr.mpp = w, h, mpp
    fr.x0 = (max(xs) + min(xs)) / 2 - (area[0] + aw / 2) * mpp
    fr.y1 = (max(ys) + min(ys)) / 2 + (area[1] + ah / 2) * mpp
    out = [f'<svg class="layer" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
           outlines(fr, "50m" if mpp < 9000 else "110m", opacity=.5, fill="#13171C")]
    groups: list[list] = []
    for k, p in pts:
        x, y = fr.px(p["lon"], p["lat"])
        g = next((g for g in groups if abs(g[0] - x) < 34 and abs(g[1] - y) < 34), None)
        if g:
            g[2].append(k)
        else:
            groups.append([x, y, [k]])
    for x, y, ks in groups:
        label = "·".join(f"{k + 1:02d}" for k in ks)
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="7" fill="{BORDO}" stroke="{IVORY}" stroke-width="2"/>'
                   f'<text x="{x + 14:.0f}" y="{y - 12:.0f}" font-family="Plex" font-weight="500" font-size="21" fill="{IVORY}">{label}</text>')
    out.append("</svg>")
    return "".join(out)
