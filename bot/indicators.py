"""«Показатели» — табло цифр недели.

Устроено иначе, чем заметки:
- главные цифры берутся напрямую у первоисточников (ЦБ, Мосбиржа), а не из пересказа новостей;
- модель добавляет только то, чего нет в открытых API (инфляция, нефть), со ссылкой на новость,
  и выбирает «цифру недели» с одной-двумя фразами пояснения;
- в посте нет флая и заголовка: дата недели, «цифра недели» и таблица моноширинным шрифтом;
- обложка — табло с цифрами, «цифра недели» выделена бордовым.
"""

import html
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

from .compose import BANNED, banned_hits, LINK_RE, _inline, _norm, extract_json, plain, visible_length
from .main import TZ, log

UA = {"User-Agent": "Mozilla/5.0 (compatible; CuiBonoBot/1.0)"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

LABEL_W, VALUE_W, CHANGE_W = 14, 9, 8  # ширина колонок таблицы: строка ≤ 33 знаков, влезает в экран телефона


def _num(x: float, digits: int = 2) -> str:
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def _pct(now: float, before: float) -> tuple[str, int]:
    if not before:
        return "", 0
    ch = (now / before - 1) * 100
    if abs(ch) < 0.05:
        return "=", 0
    return ("+" if ch > 0 else "−") + _num(abs(ch), 1) + "%", (1 if ch > 0 else -1)


def week_range() -> str:
    end = datetime.now(TZ)
    start = end - timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} — {end.day} {MONTHS[end.month - 1]}"


# --- первоисточники ------------------------------------------------------------

def cbr_currencies() -> list[dict]:
    def day(d: datetime) -> dict:
        r = requests.get("https://www.cbr.ru/scripts/XML_daily.asp", params={"date_req": d.strftime("%d/%m/%Y")}, headers=UA, timeout=20)
        out = {}
        for v in ET.fromstring(r.content).findall("Valute"):
            code = v.findtext("CharCode")
            if code in ("USD", "EUR", "CNY"):
                out[code] = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
        return out

    now = datetime.now(TZ)
    a, b = day(now), day(now - timedelta(days=7))
    names = {"USD": "Доллар ЦБ", "CNY": "Юань ЦБ", "EUR": "Евро ЦБ"}
    rows = []
    for c in ("USD", "CNY", "EUR"):
        if c in a and c in b:
            ch, d = _pct(a[c], b[c])
            rows.append({"label": names[c], "value": f"{_num(a[c])} ₽", "change": ch, "dir": d,
                         "fact": f"{names[c]}: {_num(a[c])} ₽, неделю назад {_num(b[c])} ₽ ({ch})",
                         "source": "ЦБ", "url": "https://www.cbr.ru/currency_base/daily/"})
    return rows


def _key_rate_points(now: datetime) -> list[tuple[str, float]]:
    """Ключевая ставка за 60 дней: сначала веб-сервис ЦБ, если он молчит — таблица на сайте ЦБ."""
    pts = []
    try:
        r = requests.get(
            "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx/KeyRateXML",
            params={"fromDate": (now - timedelta(days=60)).strftime("%Y-%m-%d"), "ToDate": now.strftime("%Y-%m-%d")},
            headers=UA, timeout=20,
        )
        for kr in ET.fromstring(r.content).iter():
            if kr.tag.endswith("KR"):
                dt = next((c.text for c in kr if c.tag.endswith("DT")), None)
                rate = next((c.text for c in kr if c.tag.endswith("Rate")), None)
                if dt and rate:
                    pts.append((dt[:10], float(rate)))
    except Exception as e:  # noqa: BLE001
        log(f"веб-сервис ставки ЦБ не ответил: {e}")
    if pts:
        return pts
    r = requests.get("https://www.cbr.ru/hd_base/KeyRate/", headers=UA, timeout=20, params={
        "UniDbQuery.Posted": "True", "UniDbQuery.From": (now - timedelta(days=60)).strftime("%d.%m.%Y"),
        "UniDbQuery.To": now.strftime("%d.%m.%Y")})
    for d, v in re.findall(r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>\s*([\d,]+)\s*</td>", r.text):
        pts.append((datetime.strptime(d, "%d.%m.%Y").strftime("%Y-%m-%d"), float(v.replace(",", "."))))
    return pts


def cbr_key_rate() -> list[dict]:
    now = datetime.now(TZ)
    pts = _key_rate_points(now)
    if not pts:
        return []
    pts.sort()
    cur = pts[-1][1]
    week_ago = next((v for d, v in reversed(pts) if d <= (now - timedelta(days=7)).strftime("%Y-%m-%d")), cur)
    diff = cur - week_ago
    ch = "=" if abs(diff) < 0.01 else ("+" if diff > 0 else "−") + _num(abs(diff)) + " пп"
    return [{"label": "Ставка ЦБ", "value": f"{_num(cur)}%", "change": ch, "dir": (diff > 0) - (diff < 0),
             "fact": f"Ключевая ставка ЦБ: {_num(cur)}% (неделю назад {_num(week_ago)}%)",
             "source": "ЦБ", "url": "https://www.cbr.ru/hd_base/KeyRate/"}]


def moex_index() -> list[dict]:
    now = datetime.now(TZ)
    r = requests.get(
        "https://iss.moex.com/iss/history/engines/stock/markets/index/securities/IMOEX.json",
        params={"from": (now - timedelta(days=14)).strftime("%Y-%m-%d"), "iss.meta": "off", "history.columns": "TRADEDATE,CLOSE"},
        headers=UA, timeout=20,
    ).json()
    rows = [(d, c) for d, c in r["history"]["data"] if c]
    if len(rows) < 2:
        return []
    last_d, last = rows[-1]
    target = (datetime.fromisoformat(last_d) - timedelta(days=7)).strftime("%Y-%m-%d")
    before = next((c for d, c in reversed(rows) if d <= target), rows[0][1])
    ch, d = _pct(last, before)
    return [{"label": "Индекс IMOEX", "value": _num(last, 0), "change": ch, "dir": d,
             "fact": f"Индекс МосБиржи: {_num(last, 0)} пунктов, неделю назад {_num(before, 0)} ({ch})",
             "source": "Мосбиржа", "url": "https://www.moex.com/ru/index/IMOEX"}]


def cbr_gold() -> list[dict]:
    now = datetime.now(TZ)
    r = requests.get("https://www.cbr.ru/scripts/xml_metall.asp", headers=UA, timeout=20, params={
        "date_req1": (now - timedelta(days=10)).strftime("%d/%m/%Y"), "date_req2": now.strftime("%d/%m/%Y")})
    recs = [(rec.get("Date"), float(rec.findtext("Buy").replace(",", ".")))
            for rec in ET.fromstring(r.content).findall("Record") if rec.get("Code") == "1"]
    if len(recs) < 2:
        return []
    recs.sort(key=lambda x: datetime.strptime(x[0], "%d.%m.%Y"))
    last = recs[-1][1]
    target = datetime.strptime(recs[-1][0], "%d.%m.%Y") - timedelta(days=7)
    before = next((v for d, v in reversed(recs) if datetime.strptime(d, "%d.%m.%Y") <= target), recs[0][1])
    ch, d = _pct(last, before)
    return [{"label": "Золото, грамм", "value": f"{_num(last, 0)} ₽", "change": ch, "dir": d,
             "fact": f"Учётная цена золота ЦБ: {_num(last, 0)} ₽ за грамм, неделю назад {_num(before, 0)} ₽ ({ch})",
             "source": "ЦБ", "url": "https://www.cbr.ru/hd_base/metall/metall_base_new/"}]


def official_rows() -> list[dict]:
    rows = []
    for f in (cbr_currencies, cbr_key_rate, moex_index, cbr_gold):
        try:
            got = f()
            rows += got
            log(f"{f.__name__}: {len(got)} строк")
        except Exception as e:  # noqa: BLE001
            log(f"{f.__name__} недоступен: {e}")
    return rows


# --- модель: инфляция, нефть и «цифра недели» -----------------------------------

PICK = ("Выбери до 6 новостей недели, где есть свежие цифры: недельная или годовая инфляция в России (Росстат), "
        "цена нефти Brent или Urals и скидка на Urals, нефтегазовые доходы бюджета, неплатежи компаний, крупные движения рынка. "
        "Без экзотики, не связанной с деньгами нашего читателя.")

SYSTEM = """Ты — редакция Cui Bono?, аналитического издания об экономике и власти. Ведёшь рубрику «Показатели» — табло цифр недели для инвесторов, релокантов, топ-менеджеров.

Тебе дают:
1) официальные строки табло (ЦБ, Мосбиржа) — они уже точные, их не меняй;
2) материалы новостей.

Сделай две вещи:
А. "rows" — от 0 до 3 дополнительных строк из материалов, только если цифра есть в материале прямо: инфляция (годовая или за неделю), нефть Brent или Urals, нефтегазовые доходы. Поля: "label" — до 14 знаков, с заглавной («Инфляция, год», «Brent», «Urals»); "value" — до 9 знаков («7,1%», «$63,40»); "change" — до 8 знаков, изменение к прошлой неделе («+0,2 пп», «−1,4%», «=»), если его нет в материале — пустая строка; "url" — адрес материала, где эта цифра; "source" — короткое имя источника («Росстат», «Интерфакс»).
Б. "week_figure" — «цифра недели»: одна строка табло (официальная или твоя), которая сильнее всего бьёт по деньгам и планам читателя. "label" — точно как в табло. "text" — 1–2 фразы, до 240 знаков, начинается с названия показателя («Годовая инфляция ускорилась…»): что изменилось и почему это касается денег читателя — вторая фраза только с новым фактом или цифрой, без пустых оценок («рынок перешёл к умеренному восстановлению», «ситуация стабилизировалась») и без прогнозов, одна ссылка [текст](url) на источник цифры. Пустые движения («+0,02%») цифрой недели не делай.

ЯЗЫК: по-русски, как умный живой человек. Без канцелярита, англицизмов, «важно понимать», качелей «не X, а Y».
ОТВЕТ — только JSON: {"rows": [{"label": "...", "value": "...", "change": "...", "url": "...", "source": "..."}], "week_figure": {"label": "...", "text": "..."}}"""


def materials(recent: list[dict]) -> list[dict]:
    from .formats import PICK_SYSTEM
    from .gemini import LIGHT_CHAIN, ask
    from .news import collect, fetch_text
    from .prompts import topic_user_message

    items = collect(hours=168, limit=320)
    if not items:
        return []
    text, _ = ask(LIGHT_CHAIN, PICK_SYSTEM.format(task=PICK), topic_user_message(items, recent), temperature=0.3)
    pick = extract_json(text)
    idx = [pick.get("main")] + list(pick.get("extra") or [])
    idx = [i for i in dict.fromkeys(idx) if isinstance(i, int) and 0 <= i < len(items)][:6]
    return [{**items[i], "text": fetch_text(items[i]["link"], fallback=items[i]["summary"], max_chars=3500)} for i in idx]


def _validate(d: dict, official: list[dict], allowed: set[str]) -> list[str]:
    probs = []
    labels = {r["label"] for r in official}
    for r in d.get("rows", []):
        if not r.get("label") or not r.get("value") or not r.get("url"):
            probs.append(f"неполная строка: {r}")
            continue
        if len(r["label"]) > LABEL_W or len(r["value"]) > VALUE_W or len(r.get("change", "")) > CHANGE_W:
            probs.append(f"строка «{r['label']}» не влезает: метка до {LABEL_W}, значение до {VALUE_W}, изменение до {CHANGE_W} знаков")
        if _norm(r["url"]) not in {_norm(u) for u in allowed}:
            probs.append(f"ссылка не из материалов: {r['url']}")
        labels.add(r["label"])
    wf = d.get("week_figure") or {}
    if wf.get("label") not in labels:
        probs.append("цифра недели должна быть одной из строк табло")
    t = wf.get("text", "")
    if not LINK_RE.search(t):
        probs.append("в тексте цифры недели нет ссылки на источник")
    if len(plain(t)) > 260:
        probs.append("текст цифры недели длиннее 240 знаков")
    probs += banned_hits(plain(t))
    for _, u in LINK_RE.findall(t):
        if _norm(u) not in {_norm(x) for x in allowed}:
            probs.append(f"ссылка не из материалов: {u}")
    return probs


def build(recent: list[dict]) -> dict | None:
    from .gemini import CHECK_CHAIN, WRITER_CHAIN, ask

    official = official_rows()
    mats = materials(recent)
    if len(official) < 3:
        log("из первоисточников пришло меньше трёх цифр — табло не собираем")
        return None
    off_txt = "\n".join(f"- {r['label']} | {r['value']} | {r['change']} | {r['fact']} | {r['url']}" for r in official)
    mat_txt = "\n\n".join(f"=== {m['source']} | {m['link']} ===\n{m['title']}\n{m['text']}" for m in mats)
    allowed = {m["link"] for m in mats} | {r["url"] for r in official}
    user = f"Официальные строки табло:\n{off_txt}\n\nМатериалы:\n{mat_txt}"
    feedback = ""
    for _ in range(3):
        try:
            text, model = ask(WRITER_CHAIN, SYSTEM, user + (f"\n\nПрошлая версия не прошла проверку:\n{feedback}" if feedback else ""), 0.4)
            d = extract_json(text)
        except Exception as e:  # noqa: BLE001
            feedback = str(e)
            continue
        probs = _validate(d, official, allowed)
        if probs:
            feedback = "\n".join(f"- {p}" for p in probs)
            log("валидация не прошла:\n" + feedback)
            continue
        # фактчек — только того, что добавила модель
        chk = ("Ты — фактчекер. Проверь, что каждая цифра в строках и в тексте есть в материалах или в официальных строках, "
               "и что текст не утверждает больше, чем там сказано. Ответ — только JSON: "
               '{"verdict": "ok" | "reject", "problems": ["..."]}')
        try:
            v = extract_json(ask(CHECK_CHAIN, chk, f"Черновик:\n{json.dumps(d, ensure_ascii=False)}\n\n{user}", 0.1)[0])
        except Exception as e:  # noqa: BLE001
            log(f"фактчек недоступен ({e}) — без проверки не публикуем")
            return None
        log("вердикт:", v.get("verdict"), "|", "; ".join(v.get("problems") or []))
        if v.get("verdict") == "ok":
            extra = [{**r, "dir": 1 if r.get("change", "").startswith("+") else (-1 if r.get("change", "").startswith("−") else 0)} for r in d.get("rows", [])]
            return {"rows": official + extra, "week_figure": d["week_figure"], "_sources": sorted(allowed)}
        feedback = "Фактчек отклонил:\n" + "\n".join(f"- {p}" for p in (v.get("problems") or []))
    return None


# --- вёрстка -------------------------------------------------------------------

ORDER = ["Доллар ЦБ", "Юань ЦБ", "Евро ЦБ", "Ставка ЦБ", "Инфляция, год", "Инфляция, неделя", "Brent", "Urals", "Индекс IMOEX", "Золото, грамм"]


def _sorted_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: ORDER.index(r["label"]) if r["label"] in ORDER else 50)[:8]


def table(rows: list[dict]) -> str:
    lines = []
    for r in _sorted_rows(rows):
        lines.append(f"{r['label'][:LABEL_W]:<{LABEL_W}} {r['value'][:VALUE_W]:>{VALUE_W}} {r.get('change', '')[:CHANGE_W]:>{CHANGE_W}}")
    return "\n".join(lines)


def _lower_first(t: str) -> str:
    """«Доллар ЦБ» → «доллар ЦБ», но «Brent» и «ЦБ» не трогаем."""
    if len(t) > 1 and t[0].isupper() and t[1].islower() and t[0].isascii() is False:
        return t[0].lower() + t[1:]
    return t


CAPTION_LABEL = {"Золото, грамм": "Золото за грамм", "Инфляция, год": "Инфляция за год", "Инфляция, неделя": "Инфляция за неделю"}


def _change_text(ch: str) -> str:
    ch = (ch or "").strip()
    return "без изменений" if ch in ("", "=", "0", "0%") else ch


def caption(d: dict) -> str:
    """Подпись обычным текстом: блок кода <pre> на телефоне ломает строки и показывает плашку «copy»."""
    wf = d["week_figure"]
    sources = {}
    for r in d["rows"]:
        sources.setdefault(r.get("source") or "Источник", r["url"])
    src = ", ".join(f'<a href="{html.escape(u, quote=True)}">{html.escape(n)}</a>' for n, u in sources.items())
    lines = "\n".join(
        f"{html.escape(CAPTION_LABEL.get(r['label'], r['label']))} — <b>{html.escape(r['value'])}</b>, {html.escape(_change_text(r.get('change')))}"
        for r in _sorted_rows(d["rows"])
    )
    return (
        f"<code>ПОКАЗАТЕЛИ</code> · <i>{week_range()}</i>\n\n"
        f"<b>Цифра недели.</b> {_inline(wf['text'])}\n\n"
        f"{lines}\n\n"
        f"Источники: {src}"
    )


BOARD = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>__CSS__
.rows{flex:1;display:flex;flex-direction:column;justify-content:center}
.row{display:grid;grid-template-columns:22px 1fr 340px 190px;align-items:baseline;padding:10px 0;border-bottom:1.5px solid #2A2F35}
.row:last-child{border-bottom:none}
.k{width:12px;height:12px;align-self:center}
.l{font-family:'Onest',sans-serif;font-weight:400;font-size:31px;color:#C9CFD6}
.v{font-family:'Head',sans-serif;font-weight:600;font-size:52px;text-align:right;line-height:1;font-variation-settings:'FLAR' 100,'VOLM' 100}
.c{font-family:'Plex',monospace;font-weight:500;font-size:28px;text-align:right;color:#C9CFD6}
.star .k{background:linear-gradient(90deg,#262B31 0%,#2F343C 100%);} .star .l{color:#EDE9E2;font-weight:500}
</style></head><body><div class="root">__GRID__
<div class="top"><span class="logo">Cui Bono?</span><span class="tag"><b></b>ПОКАЗАТЕЛИ · __WEEK__</span></div>
<div class="rule"></div>
<div class="rows">__ROWS__</div>
<div class="foot"><span>Цифры недели</span><span>__SOURCES__</span></div>
</div></body></html>"""


def render_board(d: dict, path) -> None:
    from . import brand as B

    rows = _sorted_rows(d["rows"])[:6]
    star = d["week_figure"]["label"]
    if star not in [r["label"] for r in rows]:  # цифра недели всегда на табло
        rows = rows[:5] + [r for r in d["rows"] if r["label"] == star]
    arrow = {1: "▲ ", -1: "▼ ", 0: ""}
    h = "".join(
        f'<div class="row{" star" if r["label"] == star else ""}"><span class="k"></span><span class="l">{html.escape(r["label"])}</span>'
        f'<span class="v">{html.escape(r["value"])}</span><span class="c">{arrow.get(r.get("dir", 0), "")}{html.escape(r.get("change", "").lstrip("+−"))}</span></div>'
        for r in rows
    )
    srcs = " · ".join(dict.fromkeys(r.get("source", "") for r in rows if r.get("source")))
    page = (BOARD.replace("__CSS__", B.BASE_CSS).replace("__GRID__", B.map_layer(None))
            .replace("__WEEK__", html.escape(week_range())).replace("__ROWS__", h).replace("__SOURCES__", html.escape(srcs)))
    B.shoot(page, path)


def check_length(d: dict) -> int:
    return visible_length(caption(d))
