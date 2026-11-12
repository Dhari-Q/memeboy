#!/usr/bin/env python
"""Quant machine: pulse supervisor (stdlib only, no LLM cost).

Polls live market feeds every tick and writes state/pulse.json in the repo.
Prints nothing (silent watchdog); cron delivers nothing when stdout is empty.
"""
import hashlib
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


PULSE = os.path.join(STATE, "pulse.json")
TIMEOUT = 12

FEEDS = [
    ("binance_BTCUSDT", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ("binance_SOLUSDT", "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"),
    ("binance_PEPEUSDT", "https://api.binance.com/api/v3/ticker/price?symbol=PEPEUSDT"),
    ("dexscreener_boosts", "https://api.dexscreener.com/token-boosts/latest/v1"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def main():
    now = int(time.time() * 1000)
    pulse = {"ts": now, "feeds": {}, "ok": True, "errors": []}
    for name, url in FEEDS:
        try:
            raw = fetch(url)
            data = json.loads(raw)
            if name == "dexscreener_boosts":
                n = len(data) if isinstance(data, list) else 0
                first = {}
                if n:
                    f = data[0]
                    first = {"chainId": f.get("chainId"), "tokenAddress": f.get("tokenAddress")}
                pulse["feeds"][name] = {"count": n, "first": first,
                                        "sha": hashlib.sha256(raw.encode()).hexdigest()[:16]}
            else:
                pulse["feeds"][name] = {"price": data.get("price"),
                                        "sha": hashlib.sha256(raw.encode()).hexdigest()[:16]}
        except Exception as exc:  # keep the supervisor alive on any feed failure
            pulse["feeds"][name] = None
            pulse["errors"].append(f"{name}: {type(exc).__name__} {exc}")
    if pulse["errors"]:
        pulse["ok"] = False

    os.makedirs(STATE, exist_ok=True)
    tmp = PULSE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pulse, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, PULSE)
    # silent: empty stdout means no delivery, no cost


if __name__ == "__main__":
    main()
