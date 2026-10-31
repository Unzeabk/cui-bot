"""Логотип Cui Bono? в стиле «Разделённый контур» (концепт 04 брендбука):
литера C из Literata, разрезанная на горизонтальные полосы со сдвигами — форма теряется и проявляется.

Запуск: python tools/logo.py  →  assets/logo/avatar.png, logo.png, mark.png
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "fonts" / "Literata.ttf"
OUT = ROOT / "assets" / "logo"
DARK = (11, 13, 16)
IVORY = (237, 233, 226)

# Полосы по высоте самой буквы: (верх, высота, сдвиг) в долях высоты и ширины буквы.
# Верх и низ целые, чтобы C читалась; посередине полосы разной толщины
# разъезжаются в обе стороны — контур «теряется и проявляется».
BANDS = [
    (.340, .030, -.05), (.370, .050, .03), (.420, .018, -.22), (.438, .055, -.09),
    (.493, .024, .14), (.517, .062, -.03), (.579, .016, -.30), (.595, .048, -.12),
    (.643, .022, .09), (.665, .036, -.05),
]


def font(size: int, wght: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_axes([72, wght])  # оптический размер, насыщенность
    return f


def glyph(size: int) -> Image.Image:
    """Буква C, обрезанная по контуру, слоновой костью на прозрачном."""
    f = font(size, 800)
    l, t, r, b = f.getbbox("C")
    im = Image.new("RGBA", (r - l + 4, b - t + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2 - l, 2 - t), "C", font=f, fill=IVORY + (255,))
    return im


def split(c: Image.Image) -> Image.Image:
    """Режет букву на полосы и сдвигает их; холст шире, чтобы сдвинутые куски не обрезались."""
    w, h = c.size
    pad = int(w * .35)
    src = np.array(c)
    out = np.zeros((h, w + 2 * pad, 4), dtype=np.uint8)
    out[:, pad:pad + w] = src
    for top, hh, shift in BANDS:
        y0, y1 = int(top * h), int((top + hh) * h)
        out[y0:y1] = 0
        dx = pad + int(shift * w)
        out[y0:y1, dx:dx + w] = src[y0:y1]
    im = Image.fromarray(out)
    return im.crop(im.getbbox())


def mark(height: int) -> Image.Image:
    m = split(glyph(int(height * 1.6)))
    return m.resize((int(m.width * height / m.height), height), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark(520).save(OUT / "mark.png")

    # аватар канала: знак по центру, с запасом под круглую обрезку Telegram
    av = Image.new("RGB", (1024, 1024), DARK)
    m = mark(480)
    av.paste(m, ((1024 - m.width) // 2 + 30, (1024 - m.height) // 2), m)  # сдвиг: полосы слева утяжеляют знак
    av.save(OUT / "avatar.png")

    # знак со словесной частью — для сайта, шапок, презентаций
    lg = Image.new("RGB", (1400, 1000), DARK)
    m = mark(360)
    lg.paste(m, ((1400 - m.width) // 2, 170), m)
    wf = font(150, 400)
    d = ImageDraw.Draw(lg)
    d.text(((1400 - d.textlength("Cui Bono?", font=wf)) / 2, 610), "Cui Bono?", font=wf, fill=IVORY)
    lg.save(OUT / "logo.png")
    print("готово:", *sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
