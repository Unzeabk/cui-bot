"""Brevo: разовая настройка рассылки без ручной работы.

Запускается в workflow «сайт», пока state/brevo.json не готов: находит или создаёт список «Cui Bono»,
подключает домен cuibono.press (DNS-записи кладёт в Cloudflare через CLOUDFLARE_API_TOKEN),
создаёт отправителя editors@cuibono.press. Итог пишет в state/brevo.json — его читают сайт и рассылка.
Нужен только secrets.BREVO_API_KEY.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

from .main import log

API = "https://api.brevo.com/v3"
DOMAIN = "cuibono.press"
SENDER = f"editors@{DOMAIN}"
STATE = Path("state") / "brevo.json"


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _h() -> dict:
    return {"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json", "accept": "application/json"}


def _list_id() -> int:
    r = requests.get(f"{API}/contacts/lists", headers=_h(), params={"limit": 50}, timeout=30).json()
    for l in r.get("lists") or []:
        if l["name"].lower().startswith("cui bono"):
            return l["id"]
    folders = requests.get(f"{API}/contacts/folders", headers=_h(), params={"limit": 50}, timeout=30).json().get("folders") or []
    fid = folders[0]["id"] if folders else requests.post(f"{API}/contacts/folders", headers=_h(), json={"name": "Cui Bono"}, timeout=30).json()["id"]
    return requests.post(f"{API}/contacts/lists", headers=_h(), json={"name": "Cui Bono", "folderId": fid}, timeout=30).json()["id"]


def _cloudflare_dns(records: list[dict]) -> None:
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not tok:
        log("brevo: нет CLOUDFLARE_API_TOKEN, DNS не трогаю")
        return
    ch = {"Authorization": f"Bearer {tok}"}
    z = requests.get("https://api.cloudflare.com/client/v4/zones", headers=ch, params={"name": DOMAIN}, timeout=30).json()
    if not z.get("result"):
        log(f"brevo: зона {DOMAIN} в Cloudflare не найдена или нет прав ({z.get('errors')})")
        return
    zid = z["result"][0]["id"]
    have = requests.get(f"https://api.cloudflare.com/client/v4/zones/{zid}/dns_records", headers=ch,
                        params={"per_page": 500}, timeout=30).json().get("result") or []
    for rec in records:
        typ, host, val = rec["type"].upper(), rec["host"], rec["value"]
        name = DOMAIN if host in ("", "@") else (host if host.endswith(DOMAIN) else f"{host}.{DOMAIN}")
        if any(x["type"] == typ and x["name"] == name and x["content"].strip('"') == val.strip('"') for x in have):
            continue
        if typ == "TXT" and name.startswith("_dmarc") and any(x["type"] == "TXT" and x["name"] == name for x in have):
            continue  # свой DMARC не перетираем
        r = requests.post(f"https://api.cloudflare.com/client/v4/zones/{zid}/dns_records", headers=ch, timeout=30,
                          json={"type": typ, "name": name, "content": val, "ttl": 1, "proxied": False})
        log(f"brevo: DNS {typ} {name} — {'добавлено' if r.ok else r.text[:200]}")


def _domain() -> bool:
    doms = requests.get(f"{API}/senders/domains", headers=_h(), timeout=30).json().get("domains") or []
    d = next((x for x in doms if x.get("domain_name") == DOMAIN), None)
    if not d:
        r = requests.post(f"{API}/senders/domains", headers=_h(), json={"name": DOMAIN}, timeout=30)
        log(f"brevo: домен добавлен {r.status_code}")
    elif d.get("authenticated"):
        return True
    info = requests.get(f"{API}/senders/domains/{DOMAIN}", headers=_h(), timeout=30).json()
    recs = []
    for k, v in (info.get("dns_records") or {}).items():
        if isinstance(v, dict) and v.get("value"):
            recs.append({"type": v.get("type", "TXT"), "host": v.get("host_name", ""), "value": v["value"]})
    _cloudflare_dns(recs)
    r = requests.put(f"{API}/senders/domains/{DOMAIN}/authenticate", headers=_h(), timeout=30)
    log(f"brevo: проверка домена {r.status_code} {r.text[:200]}")
    return r.ok


def _sender() -> str:
    ss = requests.get(f"{API}/senders", headers=_h(), timeout=30).json().get("senders") or []
    if not any(s["email"] == SENDER for s in ss):
        r = requests.post(f"{API}/senders", headers=_h(), json={"name": "Cui Bono?", "email": SENDER}, timeout=30)
        log(f"brevo: отправитель {SENDER} {r.status_code} {r.text[:200]}")
    return SENDER


def _check(st: dict) -> bool:
    """Проверка рассылки без отправки: собрать черновик кампании на список и удалить его."""
    r = requests.post(f"{API}/emailCampaigns", headers=_h(), timeout=30, json={
        "name": "проверка", "subject": "проверка", "sender": {"name": "Cui Bono?", "email": st["sender"]},
        "htmlContent": "<p>проверка</p><a href='{{ unsubscribe }}'>x</a>", "recipients": {"listIds": [int(st["list_id"])]}})
    log(f"brevo: проверочная кампания {r.status_code} {r.text[:300]}")
    if r.ok:
        requests.delete(f"{API}/emailCampaigns/{r.json()['id']}", headers=_h(), timeout=30)
    senders = requests.get(f"{API}/senders", headers=_h(), timeout=30).json().get("senders") or []
    me = next((x for x in senders if x["email"] == st["sender"]), {})
    log(f"brevo: отправитель {me}")
    return bool(r.ok and me.get("active"))


def setup() -> int:
    if not os.environ.get("BREVO_API_KEY"):
        return 0
    st = load()
    if st.get("list_id") and st.get("domain_ok") and st.get("sender") == SENDER and st.get("checked"):
        return 0
    try:
        acc = requests.get(f"{API}/account", headers=_h(), timeout=30)
        if not acc.ok:
            log(f"brevo: ключ не принят {acc.status_code} {acc.text[:200]}")
            return 0
        st["list_id"] = st.get("list_id") or _list_id()
        st["domain_ok"] = _domain()
        st["sender"] = _sender()
    except Exception as e:  # noqa: BLE001
        log(f"brevo: настройка не закончена ({e})")
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"brevo: {st}")
    return 0


def selftest() -> int:
    """После выкладки: подписать тестовый адрес через форму сайта, собрать черновик письма, всё удалить."""
    if not os.environ.get("BREVO_API_KEY"):
        return 0
    st = load()
    if not st.get("list_id") or st.get("checked"):
        return 0
    import time
    site = (os.environ.get("SITE_URL") or f"https://{DOMAIN}").rstrip("/")
    test = "selftest@cuibono.press"
    r = requests.post(f"{site}/api/subscribe", data={"email": test}, allow_redirects=False, timeout=30)
    log(f"brevo: форма подписки {r.status_code} → {r.headers.get('location')}")
    form_ok = "ok=1" in (r.headers.get("location") or "")
    time.sleep(5)
    c = requests.get(f"{API}/contacts/{test}", headers=_h(), timeout=30)
    in_list = c.ok and st["list_id"] in (c.json().get("listIds") or [])
    log(f"brevo: тестовый контакт в списке — {in_list}")
    st["checked"] = bool(form_ok and in_list and _check(st))
    requests.delete(f"{API}/contacts/{test}", headers=_h(), timeout=30)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"brevo: проверка {'пройдена' if st['checked'] else 'НЕ пройдена'}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    sys.exit(setup() if cmd == ["setup"] else selftest() if cmd == ["selftest"] else 0)
