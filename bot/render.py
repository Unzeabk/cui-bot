"""Обложки 1280×720 в стиле холста «Cui Bono? — оформление в Telegram»."""

import html
from datetime import datetime
from pathlib import Path
from string import Template

from playwright.sync_api import sync_playwright

from .compose import plain
from .rubrics import RUBRICS, SVG

FONTS = (
    '<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700'
    '&family=PT+Serif:ital,wght@0,400;1,400;1,700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">'
)

# В ленте Telegram картинка ужимается примерно до 360 px по ширине, то есть в 3,5 раза.
# Поэтому крупный плотный заголовок, служебные строки не тоньше 24 px и минимум пустоты сверху.
BASE_CSS = """
*{box-sizing:border-box} body{margin:0}
.root{width:1280px;height:720px;background:#F2EDE3;color:#141414;padding:48px 72px 44px;font-family:'PT Serif',serif;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:center;font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:24px;letter-spacing:.12em;color:#141414}
.rule{height:2px;background:#141414;margin-top:16px}
.fly{font-style:italic;font-weight:700;font-size:40px;color:#7A1F2B;line-height:1.1}
.hl{font-family:'Playfair Display',serif;font-weight:700;line-height:1.04;letter-spacing:-.01em}
.foot{display:flex;justify-content:space-between;align-items:center;border-top:2px solid #141414;padding-top:14px;font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:22px;letter-spacing:.1em}
.foot svg{width:56px;height:56px}
"""

IVORY = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8">$fonts<style>$css
.mid{flex:1;display:flex;flex-direction:column;justify-content:center;gap:20px;padding:22px 0}
</style></head><body><div class="root">
<div class="top"><span>CUI BONO?</span><span>$rubric · $date</span></div>
<div class="rule"></div>
<div class="mid"><div class="fly">$fly</div><div class="hl" style="font-size:${size}px">$headline</div></div>
<div class="foot"><span>$site</span>$svg</div>
</div></body></html>""")

DATA = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8">$fonts<style>$css
.mid{flex:1;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);column-gap:48px;align-items:center;padding:16px 0}
.num{font-family:'Playfair Display',serif;font-weight:700;line-height:.9;letter-spacing:-.03em;color:#7A1F2B;white-space:nowrap}
.numbox{display:flex;flex-direction:column;align-items:flex-start;gap:22px}
.cap{font-family:'IBM Plex Mono',monospace;font-weight:400;font-size:29px;letter-spacing:0;color:#55524A;line-height:1.3;max-width:540px;text-wrap:balance}
.txt{display:flex;flex-direction:column;gap:16px}
.fly{font-size:34px}
</style></head><body><div class="root">
<div class="top"><span>CUI BONO?</span><span>$rubric · $date</span></div>
<div class="rule"></div>
<div class="mid"><div class="numbox"><div class="num" style="font-size:${numsize}px">$number</div><div class="cap">$caption</div></div>
<div class="txt"><div class="fly">$fly</div><div class="hl" style="font-size:${size}px">$headline</div></div></div>
<div class="foot"><span>$site</span>$svg</div>
</div></body></html>""")


def _e(s: str) -> str:
    return html.escape(plain(s or ""), quote=False)


def render_cover(note: dict, out_path: Path, site: str = "") -> Path:
    rubric_name = RUBRICS[note["rubric"]][0]
    date = datetime.now().strftime("%d.%m.%Y")
    hl = plain(note["headline"])
    cover = note.get("cover") or {}
    common = dict(
        fonts=FONTS, css=BASE_CSS, rubric=html.escape(rubric_name), date=date,
        fly=_e(note["fly"]), headline=html.escape(hl, quote=False), site=html.escape(site or ""),
    )
    if cover.get("type") == "data" and cover.get("number"):
        num = str(cover["number"])
        page = DATA.substitute(
            **common,
            size=64 if len(hl) <= 45 else (56 if len(hl) <= 65 else 48),
            svg=SVG.get(note["rubric"], SVG["world"]),
            number=html.escape(num, quote=False),
            numsize=250 if len(num) <= 3 else (200 if len(num) <= 4 else (150 if len(num) <= 6 else 115)),
            caption=_e((lambda c: c[:1].upper() + c[1:])(cover.get("number_caption") or "")),
        )
    else:
        size = 112 if len(hl) <= 40 else (100 if len(hl) <= 55 else (88 if len(hl) <= 70 else 76))
        page = IVORY.substitute(**common, size=size, svg=SVG.get(note["rubric"], SVG["world"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5)
        pg.set_content(page, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": 1280, "height": 720})
        browser.close()
    return out_path
