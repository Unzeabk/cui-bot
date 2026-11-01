"""Один запуск = одна заметка: тема → текст → фактчек → обложка → пост → запись в журнал ленты.

Запуск:
  python -m bot.main                      # полный цикл, публикует в канал
  DRY_RUN=1 python -m bot.main            # всё, кроме отправки; результат в out/
  python -m bot.main --from-json note.json  # без API: только обложка и подпись из готовой заметки
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .rubrics import RUBRICS
from .compose import copied_examples, fix_links, build_caption, dedupe_links, extract_json, validate, visible_length

# Один секрет CUI_CONFIG вместо трёх: строки вида ИМЯ=значение
for _line in (os.environ.get("CUI_CONFIG") or "").splitlines():
    if "=" in _line:
        _k, _v = _line.split("=", 1)
        if _k.strip() and _v.strip():
            if not os.environ.get(_k.strip()):
                os.environ[_k.strip()] = _v.strip()

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "posted.json"
OUT = ROOT / "out"
TZ = ZoneInfo(os.environ.get("EDITION_TZ") or "Europe/Moscow")

SITE = os.environ.get("SITE_LABEL") or ""
CUSTOM_EMOJI = json.loads(os.environ.get("CUSTOM_EMOJI_JSON") or "{}")


def log(*a):
    print("[cui]", *a, flush=True)


def load_state() -> list[dict]:
    if not STATE.exists():
        return []
    items = json.loads(STATE.read_text(encoding="utf-8"))
    cutoff = datetime.now(TZ) - timedelta(days=7)
    return [i for i in items if datetime.fromisoformat(i["time_iso"]) >= cutoff]


def save_state(items: list[dict]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


import re as _re


def _words(t: str) -> set[str]:
    return {w[:6] for w in _re.findall(r"[а-яёa-z0-9]{4,}", (t or "").lower())}


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.4


# Уголовная хроника: такие новости до отбора тем не доходят (свод, «Тема» → «Не берём»).
# Только явные слова насилия: «задержан», «обыск», «изъяли» не трогаем — это бывает темой «Собственности».
_CRIME = _re.compile(
    r"похищ|похити|паяльник|рэкет|вымогател|убийств|убил[аи]?\b|застрелил|зарезал|изнасил|ограбил|грабител|разбой"
    r"|kidnap|abduct|murder|torture|ransom", _re.I)
# Спорт: в «Не берём» свода. Призовые и трансферы тоже не берём.
_SPORT = _re.compile(
    r"теннис|\bWTA\b|\bATP\b|футбол|хокке|\bКХЛ\b|\bНХЛ\b|\bНБА\b|\bРПЛ\b|баскетбол|волейбол|олимпи|\bUFC\b|боксёр|боксер"
    r"|чемпионат|турнир|\bматч(а|е|и|ей|ах)?\b|сборн(ая|ой) (России|по)|Формул[аые] ?1|гран-при|Уимблдон|теннисист|футболист|хоккеист"
    r"|\btennis\b|football|soccer|Premier League|Champions League|\bNBA\b|\bNFL\b|\bNHL\b|Grand Slam|Olympic|Wimbledon", _re.I)


def fresh_items(items: list[dict], recent: list[dict]) -> list[dict]:
    """Убирает новости, уже использованные, похожие на темы недавних заметок, и уголовную хронику и спорт."""
    used = {u for r in recent for u in r.get("sources", [])}
    past = [r.get("main_title") or r.get("headline", "") for r in recent] + [r.get("headline", "") for r in recent]
    out = [it for it in items if it["link"] not in used and not any(_similar(it["title"], p) for p in past if p)
           and not _CRIME.search(it["title"] + " " + it.get("summary", "")[:200])
           and not _SPORT.search(it["title"] + " " + it.get("summary", "")[:200])]
    log(f"после отсева повторов: {len(out)} из {len(items)}")
    return out


def pick_topics(items: list[dict], recent: list[dict]) -> list[dict]:
    from .gemini import LIGHT_CHAIN, ask
    from .prompts import TOPIC_SYSTEM, topic_user_message

    text, model = ask(LIGHT_CHAIN, TOPIC_SYSTEM, topic_user_message(items, recent), temperature=0.4)
    topics = extract_json(text).get("topics", [])
    log(f"темы ({model}):", "; ".join(t.get("why", "") for t in topics))
    return [t for t in topics if isinstance(t.get("main"), int) and 0 <= t["main"] < len(items)]


def materials_for(topic: dict, items: list[dict]) -> list[dict]:
    from .news import fetch_text

    idx = [topic["main"]] + [i for i in topic.get("extra", []) if isinstance(i, int) and 0 <= i < len(items)][:3]
    mats = []
    for i in dict.fromkeys(idx):
        it = items[i]
        mats.append({**it, "text": fetch_text(it["link"], fallback=it["summary"])})
    return mats


def _snap(value: str, options) -> str:
    """Опечатку в названии оптики или формы («спекталь») чиним, а не тратим на неё попытку."""
    import difflib
    v = str(value or "").strip().lower()
    hit = difflib.get_close_matches(v, list(options), n=1, cutoff=0.8)
    return hit[0] if hit else v


def write_note(recent: list[dict], materials: list[dict], feedback: str = "", rubric: str = "", slot: dict | None = None) -> dict:
    from .gemini import WRITER_CHAIN, ask
    from .prompts import WRITER_SYSTEM, writer_user_message

    now = datetime.now(TZ)
    user = writer_user_message(now.strftime("%d.%m.%Y, %H:%M"), recent, materials, rubric, slot)
    if feedback:
        user += f"\n\nПрошлая версия не прошла проверку. Исправь:\n{feedback}"
    text, model = ask(WRITER_CHAIN, WRITER_SYSTEM, user, temperature=0.8)
    log(f"текст написала {model}")
    return dedupe_links(fix_links(extract_json(text), [m["link"] for m in materials]))


def check_note(note: dict, materials: list[dict]) -> dict:
    from .gemini import CHECK_CHAIN, ask
    from .prompts import CHECKER_SYSTEM, checker_user_message

    text, model = ask(CHECK_CHAIN, CHECKER_SYSTEM,
                      checker_user_message(json.dumps(note, ensure_ascii=False, indent=1), materials), temperature=0.1)
    log(f"фактчек: {model}")
    return extract_json(text)


def rubric_problems(note: dict, recent: list[dict]) -> list[str]:
    """Разнообразие ленты: не та же рубрика, что в двух последних, и не больше двух за сутки."""
    rub = note.get("rubric")
    last2 = [r.get("rubric") for r in recent[-2:]]
    cutoff = datetime.now(TZ) - timedelta(hours=24)
    day = sum(1 for r in recent if r.get("rubric") == rub and datetime.fromisoformat(r["time_iso"]) >= cutoff)
    out = []
    if rub in last2:
        out.append(f"рубрика {rub} была в одной из двух последних заметок — нужна другая")
    if day >= 2:
        out.append(f"рубрики {rub} за сутки уже {day} — нужна другая")
    return out


def feed_problems(note: dict, recent: list[dict]) -> list[str]:
    """Правила ленты по форме, раскладке и оптике (свод: «Формы заголовка», «Оптики»; telegram.md: раскладки)."""
    from .rubrics import FORMS, OPTIC_FAMILY

    notes = [r for r in recent if r.get("form") in FORMS]
    out = []
    if notes:
        last = notes[-1]
        if note.get("form") == last.get("form"):
            out.append(f"форма «{note.get('form')}» была в прошлой заметке — нужна другая")
        if note.get("layout") and note.get("layout") == last.get("layout"):
            out.append(f"раскладка «{note.get('layout')}» была в прошлой заметке — нужна другая")
        main_o = (note.get("optics") or [""])[0]
        if main_o and main_o == (last.get("optics") or [""])[0]:
            out.append(f"главная оптика «{main_o}» была в прошлой заметке — нужна другая")
    cutoff = datetime.now(TZ) - timedelta(hours=24)

    def _fresh(r: dict, since: datetime) -> bool:
        try:
            return datetime.fromisoformat(r["time_iso"]) >= since
        except (KeyError, ValueError):
            return False

    if note.get("form") == "невозможный факт" and any(r.get("form") == "невозможный факт" and _fresh(r, cutoff) for r in notes):
        out.append("«невозможный факт» уже был за сутки — нужна другая форма")
    week = [r for r in notes if _fresh(r, datetime.now(TZ) - timedelta(days=7))]
    fams = {OPTIC_FAMILY.get((r.get("optics") or [""])[0]) for r in week} - {None}
    fam = OPTIC_FAMILY.get((note.get("optics") or [""])[0])
    if len(week) >= 6 and len(fams) < 4 and fam in fams:
        out.append(f"за неделю главные оптики только из семейств {sorted(fams)}: возьми главную оптику из другого семейства")
    lw = (note.get("headline") or "").lower()
    if any(lw and lw == (r.get("headline") or "").lower() for r in recent):
        out.append("такой заголовок уже был")
    return out


def material_problems(materials: list[dict]) -> str:
    """Статьи не скачались (пейволл, капча) — фактчеку нечего проверять, тему не тратим."""
    total = sum(len((m.get("text") or "").strip()) for m in materials)
    if total < 500:
        return f"текст статей не скачался ({total} знаков) — фактчек не сможет проверить"
    return ""


def topic_problems(topic: dict, recent: list[dict]) -> list[str]:
    """Жёсткие проверки темы до написания: география рубрики и повтор участника за трое суток."""
    from .rubrics import RU_LINKED, RU_ONLY

    rub, geo = topic.get("rubric"), str(topic.get("russia", "")).strip().lower()
    out = []
    if rub in RU_ONLY and geo != "в россии":
        out.append(f"рубрика {rub} только про Россию, а событие не в России ({geo or 'не указано'})")
    if rub in RU_LINKED and geo not in ("в россии", "связь"):
        out.append(f"рубрика {rub} требует участия России, а его нет ({geo or 'не указано'})")
    out += rubric_problems({"rubric": rub}, recent)
    actor = str(topic.get("actor", "")).strip().lower()
    if actor:
        cutoff = datetime.now(TZ) - timedelta(hours=72)
        for r in recent:
            try:
                fresh = datetime.fromisoformat(r["time_iso"]) >= cutoff
            except (KeyError, ValueError):
                continue
            seen = str(r.get("actor", "")).strip().lower()
            key = str(r.get("topic_key", "")).lower()
            if fresh and (seen == actor or (len(actor) >= 4 and actor.replace(" ", "-") in key)):
                out.append(f"участник «{actor}» уже был за трое суток: «{r.get('headline', '')}»")
                break
    return out


def _mark(note: dict, materials: list[dict], topic: dict) -> dict:
    note["_actor"] = str(topic.get("actor", "")).strip().lower()
    note["_main_title"] = materials[0]["title"]
    note["_sources"] = [m["link"] for m in materials]
    return note


def produce(recent: list[dict]) -> dict | None:
    from .news import collect

    items = fresh_items(collect(), recent)
    if not items:
        log("ни одна лента не ответила")
        return None
    topics = pick_topics(items, recent)
    for n, topic in enumerate(topics[:5], 1):
        bad = topic_problems(topic, recent)
        if bad:
            log(f"тема {n} отклонена: " + "; ".join(bad))
            continue
        materials = materials_for(topic, items)
        bad = material_problems(materials)
        if bad:
            log(f"тема {n} отклонена: {bad}")
            continue
        allowed = {m["link"] for m in materials}
        log(f"тема {n}: {materials[0]['title']}")
        feedback = ""
        from .prompts import plan_slot
        from .rubrics import FORMS, OPTIC_FAMILY
        slot = plan_slot(recent)
        for attempt in (1, 2, 3):
            try:
                note = write_note(recent, materials, feedback, topic.get("rubric", ""), slot)
            except Exception as e:  # noqa: BLE001
                feedback = f"Ответ не разобрался как JSON: {e}"
                log(feedback)
                continue
            note["optics"] = [_snap(o, OPTIC_FAMILY) for o in note.get("optics") or []]
            note["form"] = _snap(note.get("form"), FORMS)
            note["layout"] = slot["layout"]  # раскладку назначил код
            if topic.get("rubric") in RUBRICS:
                note["rubric"] = topic["rubric"]  # рубрику выбрал отбор тем и проверил код; писатель её не меняет
            problems = validate(note, allowed) + rubric_problems(note, recent) + feed_problems(note, recent) + copied_examples(note, recent)
            if any(p.startswith("запрещённый оборот") or p.startswith("в заголовке") for p in problems):
                from .formats import fix_headline, repair_banned  # точечная правка вместо полного переписывания
                note = fix_headline(repair_banned("note", note))
                problems = validate(note, allowed) + rubric_problems(note, recent) + feed_problems(note, recent) + copied_examples(note, recent)
            if problems:
                feedback = "\n".join(f"- {p}" for p in problems)
                log("валидация не прошла:\n" + feedback)
                continue
            try:
                v = check_note(note, materials)
            except Exception as e:  # noqa: BLE001
                log(f"фактчек недоступен ({e}) — без проверки не публикуем, следующая тема")
                break
            log("вердикт:", v.get("verdict"), "|", "; ".join(v.get("problems") or []))
            if v.get("verdict") == "ok":
                return _mark(note, materials, topic)
            if v.get("verdict") == "fix" and v.get("fixed_note"):
                fixed = dedupe_links(v["fixed_note"])
                for k in ("place", "cover"):  # фактчек правит текст, оформление сохраняем
                    fixed.setdefault(k, note.get(k))
                if not validate(fixed, allowed):
                    fixed["rubric"] = note["rubric"]
                    return _mark(fixed, materials, topic)
            if v.get("verdict") == "reject":
                break  # тема держится на ошибке — следующая тема
            feedback = "Фактчек нашёл ошибки:\n" + "\n".join(f"- {p}" for p in (v.get("problems") or []))
    return None


def publish(note: dict, dry: bool) -> dict:
    from .render import render_cover

    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    OUT.mkdir(exist_ok=True)
    cover = render_cover(note, OUT / f"cover-{stamp}.png", site=SITE)
    caption = build_caption(note, CUSTOM_EMOJI)
    (OUT / f"note-{stamp}.json").write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"caption-{stamp}.html").write_text(caption, encoding="utf-8")
    log(f"подпись: {visible_length(caption)} знаков, обложка: {cover.name}")
    if dry:
        log("DRY_RUN: в канал не отправляю")
        return {}
    from .telegram import send_photo
    from .site import write_entry
    res = send_photo(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], cover, caption)
    write_entry("note", note, res, cover)
    return res


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--from-json":
        note = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        problems = validate(note, None)
        if problems:
            log("проблемы:", *problems, sep="\n- ")
        publish(note, dry=True)
        return 0

    dry = bool(os.environ.get("DRY_RUN"))
    count = max(1, min(8, int(os.environ.get("POST_COUNT") or 1)))
    failed = 0
    for i in range(count):
        if i:
            import time
            time.sleep(90)  # пауза между постами при стартовом наполнении ленты
        log(f"заметка {i + 1} из {count}")
        try:
            failed += run_once(dry)
        except Exception as e:  # noqa: BLE001
            log(f"заметка {i + 1} упала: {e}")
            failed += 1
    return 1 if failed == count else 0


def run_once(dry: bool) -> int:
    state = load_state()
    note = produce(state)
    if not note:
        log("ни одна тема не прошла проверку — в этот слот ничего не публикуем")
        return 1
    result = publish(note, dry)
    if not dry:
        now = datetime.now(TZ)
        state.append({
            "time_iso": now.isoformat(), "time": now.strftime("%d.%m %H:%M"),
            "rubric": note["rubric"], "form": note["form"], "layout": note.get("layout", ""), "optics": note.get("optics", []),
            "topic_key": note.get("topic_key", ""), "headline": note["headline"],
            "fly": note.get("fly", ""), "subhead": note.get("subhead", ""), "final": note.get("final", ""),
            "actor": note.get("_actor", ""), "main_title": note.get("_main_title", ""), "sources": note.get("_sources", []),
            "message_id": result.get("message_id"),
        })
        save_state(state)
    log("готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
