"""Обложки 1280×720 в стиле холста «Cui Bono? — оформление в Telegram»."""

import html
from datetime import datetime
from pathlib import Path
from string import Template

from playwright.sync_api import sync_playwright

from .compose import plain
from .rubrics import RUBRICS, SVG

FONTS = (
    '<link href="https://fonts.googleapis.com/css2?family=Prata&family=PT+Serif:ital,wght@0,400;1,400'
    '&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

BASE_CSS = """
*{box-sizing:border-box} body{margin:0}
.root{width:1280px;height:720px;background:#F2EDE3;color:#141414;padding:56px 72px;font-family:'PT Serif',serif;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:center;font-family:'IBM Plex Mono',monospace;font-size:18px;letter-spacing:.14em;color:#2B2B2B}
.rule{height:1px;background:#2B2B2B;margin-top:20px}
.fly{font-style:italic;font-size:30px;color:#7A1F2B}
.hl{font-family:Prata,serif;line-height:1.08}
.foot{display:flex;justify-content:space-between;border-top:1px solid #C9C2B4;padding-top:16px;margin-top:28px;font-family:'IBM Plex Mono',monospace;font-size:15px;letter-spacing:.08em;color:#5E5A52}
"""

IVORY = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8">$fonts<style>$css
.mid{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 240px;column-gap:64px;align-items:end}
.txt{display:flex;flex-direction:column;gap:22px;padding-bottom:8px}
.mark{display:flex;justify-content:flex-end} .mark svg{width:200px;height:200px}
</style></head><body><div class="root">
<div class="top"><span>CUI BONO?</span><span>$rubric · $date</span></div>
<div class="rule"></div>
<div class="mid"><div class="txt"><div class="fly">$fly</div><div class="hl" style="font-size:${size}px">$headline</div></div>
<div class="mark">$svg</div></div>
<div class="foot"><span>ЗАМЕТКА</span><span>$site</span></div>
</div></body></html>""")

DATA = Template("""<!doctype html><html lang="ru"><head><meta charset="utf-8">$fonts<style>$css
.fly{color:#2B2B2B;font-size:26px}
.mid{flex:1;display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);column-gap:56px;align-items:end}
.num{font-family:Prata,serif;line-height:.9;letter-spacing:-.03em;color:#7A1F2B;white-space:nowrap}
.cap{font-family:'IBM Plex Mono',monospace;font-size:17px;letter-spacing:.06em;color:#4A4740;padding-left:6px;text-transform:uppercase;margin-top:8px}
.txt{display:flex;flex-direction:column;gap:18px;padding-bottom:30px}
</style></head><body><div class="root">
<div class="top"><span>CUI BONO?</span><span>$rubric · $date</span></div>
<div class="rule"></div>
<div class="mid"><div><div class="num" style="font-size:${numsize}px">$number</div><div class="cap">$caption</div></div>
<div class="txt"><div class="fly">$fly</div><div class="hl" style="font-size:${size}px">$headline</div></div></div>
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
            size=56 if len(hl) <= 60 else 46,
            number=html.escape(num, quote=False),
            numsize=280 if len(num) <= 3 else (220 if len(num) <= 4 else (165 if len(num) <= 6 else 125)),
            caption=_e(cover.get("number_caption", "")),
        )
    else:
        size = 76 if len(hl) <= 45 else (68 if len(hl) <= 65 else 58)
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
