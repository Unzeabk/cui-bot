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

from .compose import build_caption, extract_json, validate, visible_length

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


def write_note(recent: list[dict], materials: list[dict], feedback: str = "") -> dict:
    from .gemini import WRITER_CHAIN, ask
    from .prompts import WRITER_SYSTEM, writer_user_message

    now = datetime.now(TZ)
    user = writer_user_message(now.strftime("%d.%m.%Y, %H:%M"), recent, materials)
    if feedback:
        user += f"\n\nПрошлая версия не прошла проверку. Исправь:\n{feedback}"
    text, model = ask(WRITER_CHAIN, WRITER_SYSTEM, user, temperature=0.8)
    log(f"текст написала {model}")
    return extract_json(text)


def check_note(note: dict, materials: list[dict]) -> dict:
    from .gemini import LIGHT_CHAIN, ask
    from .prompts import CHECKER_SYSTEM, checker_user_message

    text, model = ask(LIGHT_CHAIN, CHECKER_SYSTEM,
                      checker_user_message(json.dumps(note, ensure_ascii=False, indent=1), materials), temperature=0.1)
    log(f"фактчек: {model}")
    return extract_json(text)


def produce(recent: list[dict]) -> dict | None:
    from .news import collect

    items = collect()
    if not items:
        log("ни одна лента не ответила")
        return None
    topics = pick_topics(items, recent)
    for n, topic in enumerate(topics[:3], 1):
        materials = materials_for(topic, items)
        allowed = {m["link"] for m in materials}
        log(f"тема {n}: {materials[0]['title']}")
        feedback = ""
        for attempt in (1, 2):
            try:
                note = write_note(recent, materials, feedback)
            except Exception as e:  # noqa: BLE001
                feedback = f"Ответ не разобрался как JSON: {e}"
                log(feedback)
                continue
            problems = validate(note, allowed)
            if problems:
                feedback = "\n".join(f"- {p}" for p in problems)
                log("валидация не прошла:\n" + feedback)
                continue
            v = check_note(note, materials)
            log("вердикт:", v.get("verdict"), "|", "; ".join(v.get("problems") or []))
            if v.get("verdict") == "ok":
                return note
            if v.get("verdict") == "fix" and v.get("fixed_note"):
                fixed = v["fixed_note"]
                if not validate(fixed, allowed):
                    return fixed
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
    return send_photo(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], cover, caption)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--from-json":
        note = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        problems = validate(note, None)
        if problems:
            log("проблемы:", *problems, sep="\n- ")
        publish(note, dry=True)
        return 0

    dry = bool(os.environ.get("DRY_RUN"))
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
            "rubric": note["rubric"], "form": note["form"], "optics": note.get("optics", []),
            "topic_key": note.get("topic_key", ""), "headline": note["headline"],
            "message_id": result.get("message_id"),
        })
        save_state(state)
    log("готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
