"""Gemini API (Google AI Studio), бесплатный тариф.

Модели перебираются по цепочке: если у одной кончилась дневная квота (429),
запрос уходит следующей. Так бот дотягивает до сброса квот (полночь по тихоокеанскому времени).
"""

import os
import time

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Лучшая бесплатная модель первой; у Flash-Lite дневная квота в разы больше.
WRITER_CHAIN = (os.environ.get("WRITER_MODELS") or "gemini-3.8-flash,gemini-3.5-flash-lite").split(",")
# Фактчек — вторая сильная модель со своей отдельной квотой, потом лёгкие
CHECK_CHAIN = (os.environ.get("CHECK_MODELS") or "gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",")
# Для «Разбора»: только сильные модели. Облегчённая пишет по 50–150 слов на часть — такой разбор не выпускаем.
STRONG_CHAIN = (os.environ.get("STRONG_MODELS") or "gemini-3.8-flash,gemini-3.6-flash").split(",")
LIGHT_CHAIN = (os.environ.get("LIGHT_MODELS") or "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash").split(",")


class QuotaExhausted(RuntimeError):
    pass


# Ключи из разных проектов Google: у каждого проекта своя дневная квота на каждую модель.
# GEMINI_API_KEYS — запасные через запятую; основной GEMINI_API_KEY идёт первым.
def _keys() -> list[str]:
    raw = [os.environ.get("GEMINI_API_KEY", "")] + (os.environ.get("GEMINI_API_KEYS") or "").replace("\n", ",").split(",")
    out = []
    for k in (x.strip() for x in raw):
        if k and k not in out:
            out.append(k)
    return out


# (номер ключа, модель), у которых в этом запуске кончилась дневная квота: до конца запуска их не дёргаем.
EXHAUSTED: set[tuple[int, str]] = set()
USAGE: dict[str, int] = {}


def _report() -> None:
    if USAGE:
        print("[cui] запросы к Gemini за запуск:", ", ".join(f"{k} — {v}" for k, v in sorted(USAGE.items())), flush=True)


import atexit  # noqa: E402
atexit.register(_report)


def _per_day(r) -> tuple[bool, float]:
    """Дневная квота или минутная, и сколько ждать по подсказке Google."""
    wait = 35.0
    try:
        details = r.json().get("error", {}).get("details", [])
    except ValueError:
        details = []
    daily = False
    for d in details:
        for v in d.get("violations", []) or []:
            if "perday" in (v.get("quotaId", "") + v.get("quotaMetric", "")).lower().replace("_", ""):
                daily = True
        if d.get("retryDelay"):
            try:
                wait = float(str(d["retryDelay"]).rstrip("s"))
            except ValueError:
                pass
    if not details:
        msg = r.text.lower()
        daily = "per day" in msg or "perday" in msg or "daily" in msg
    return daily, min(max(wait, 5.0), 65.0)


def _call(model: str, system: str, user: str, temperature: float, key: str = "", ki: int = 0) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    headers = {"x-goog-api-key": key or _keys()[0], "content-type": "application/json"}
    for attempt in range(4):
        try:
            r = requests.post(API.format(model=model.strip()), json=body, headers=headers, timeout=180)
            tag = f"{model.strip()} #{ki + 1}"
            USAGE[tag] = USAGE.get(tag, 0) + 1
        except requests.RequestException as e:
            if attempt < 1:
                time.sleep(15)
                continue
            raise QuotaExhausted(f"{model}: нет связи ({e})")
        if r.status_code == 429:
            # Дневной лимит — отдать следующему ключу или модели; минутный — подождать, сколько просит Google.
            daily, wait = _per_day(r)
            if daily:
                EXHAUSTED.add((ki, model.strip()))
                raise QuotaExhausted(f"{model} ключ #{ki + 1}: дневная квота исчерпана")
            if attempt == 3:
                raise QuotaExhausted(f"{model} ключ #{ki + 1}: минутный лимит не отпустил")
            print(f"[cui] {model} ключ #{ki + 1}: минутный лимит, жду {wait:.0f} с", flush=True)
            time.sleep(wait)
            continue
        if r.status_code in (500, 502, 503, 504):
            if attempt < 2:
                time.sleep(15 * (attempt + 1))
                continue
            raise QuotaExhausted(f"{model}: сервер перегружен ({r.status_code})")
        if r.status_code == 404:
            raise QuotaExhausted(f"{model}: модель недоступна")
        r.raise_for_status()
        data = r.json()
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise RuntimeError(f"{model}: пустой ответ ({data.get('promptFeedback') or data.get('candidates')})")
        return text
    raise QuotaExhausted(f"{model}: не ответила")


def ask(chain: list[str], system: str, user: str, temperature: float = 0.7) -> tuple[str, str]:
    """Возвращает (текст ответа, модель, которая ответила)."""
    last = None
    keys = _keys()
    # сначала сильная модель на всех ключах, только потом модель слабее
    for model in chain:
        for ki, key in enumerate(keys):
            if (ki, model.strip()) in EXHAUSTED:
                continue
            try:
                return _call(model, system, user, temperature, key, ki), model.strip()
            except (QuotaExhausted, RuntimeError) as e:
                print("[cui]", e, "— пробую дальше", flush=True)
                last = e
    raise RuntimeError(f"Ни одна модель не ответила, последняя ошибка: {last}")
