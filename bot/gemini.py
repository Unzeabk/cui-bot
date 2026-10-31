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


# Модели, у которых в этом запуске кончилась дневная квота: до конца запуска их не дёргаем,
# иначе каждый запрос сначала тратит время на заведомый отказ.
EXHAUSTED: set[str] = set()


def _call(model: str, system: str, user: str, temperature: float) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(API.format(model=model.strip()), json=body, headers=headers, timeout=180)
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(15)
                continue
            raise QuotaExhausted(f"{model}: нет связи ({e})")
        if r.status_code == 429:
            # Минутный лимит — подождать; дневной — отдать следующей модели.
            msg = r.text.lower()
            if "per day" in msg or "perday" in msg or "daily" in msg or attempt == 2:
                EXHAUSTED.add(model.strip())
                raise QuotaExhausted(f"{model}: квота исчерпана")
            time.sleep(35)
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
    live = [m for m in chain if m.strip() not in EXHAUSTED] or chain
    for model in live:
        try:
            return _call(model, system, user, temperature), model.strip()
        except (QuotaExhausted, RuntimeError) as e:
            print("[cui]", e, "— пробую следующую модель", flush=True)
            last = e
    raise RuntimeError(f"Все модели исчерпали квоту: {last}")
