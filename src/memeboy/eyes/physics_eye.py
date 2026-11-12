#!/usr/bin/env python
"""Quant machine: physics eye (stdlib only).

Reads the market's own microstructure and writes state/physics.json. Two
measured effects from the 2026 literature, computed on data we already pull:

  1. Wash-trading ratio. A wash trader buys from himself, so buys >> unique
     buyers. Carnegie Mellon (arxiv 2609.10246, Sep 2026) found 4M+ wash trades
     were 17% of all pump.fun transactions. We score it per pool:
     wash = 1 - unique_buyers / buys, over the 5-minute and 1-hour windows.
  2. Hour-of-day penalty. A 15-day autonomous deployment (arxiv 2606.08232)
     found UTC hours 2, 13 and 23 were the worst-performing. Directional, not
     confirmed (p=0.22), so it is a penalty the brain can read, not a veto.

Silent watchdog. Prints nothing.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


BIRTHS = os.path.join(STATE, "births.json")
OUT = os.path.join(STATE, "physics.json")
TIMEOUT = 15

WEAK_HOURS_UTC = (2, 13, 23)
WASH_FLAG = 0.6     # 60% of buys are not unique buyers
PACE_SECONDS = 1.5  # GeckoTerminal free tier 429s on a burst
RETRY_WAIT = 4.0


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def gecko(url):
    """Paced fetch: sleep between calls, and retry once when the tier says 429."""
    for attempt in (1, 2):
        try:
            return get(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 2:
                raise
            time.sleep(RETRY_WAIT)
        finally:
            time.sleep(PACE_SECONDS)


def wash(window):
    """1 - unique buyers / buys. None when there is nothing to measure."""
    if not isinstance(window, dict):
        return None
    buys = window.get("buys") or 0
    buyers = window.get("buyers")
    if not buys or buyers is None:
        return None
    return round(max(0.0, 1.0 - buyers / buys), 3)


def main():
    births = {}
    try:
        births = json.load(open(BIRTHS, encoding="utf-8"))
    except Exception:
        pass

    scored = []
    errors = []
    batch = (births.get("births") or [])[:8]
    pools = [(b.get("pool_id") or "").split("_", 1)[-1] for b in batch]
    wanted = [p for p in pools if p]
    by_pool = {}
    # one batched request instead of eight: the free tier 429s half a burst
    if wanted:
        try:
            data = gecko("https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/"
                         + ",".join(wanted))
            for item in data.get("data") or []:
                key = (item.get("id") or "").split("_", 1)[-1]
                by_pool[key] = (item.get("attributes") or {}).get("transactions") or {}
        except Exception as exc:
            errors.append(f"gecko_multi: {type(exc).__name__}")
    for b, pool in zip(batch, pools):
        if not pool:
            continue
        tx = by_pool.get(pool)
        if tx is None:
            errors.append(f"{b.get('symbol')}: not_indexed")
            continue
        w5, w1 = wash(tx.get("m5")), wash(tx.get("h1"))
        worst = max(v for v in (w5, w1) if v is not None) if any(v is not None for v in (w5, w1)) else None
        scored.append({
            "symbol": b.get("symbol"),
            "mint": b.get("token_address"),
            "dex": b.get("dex"),
            "wash_m5": w5,
            "wash_h1": w1,
            "buys_h1": (tx.get("h1") or {}).get("buys"),
            "buyers_h1": (tx.get("h1") or {}).get("buyers"),
            "flag": "wash" if worst is not None and worst >= WASH_FLAG else "",
        })

    hour = datetime.now(timezone.utc).hour
    payload = {
        "ts": int(time.time() * 1000),
        "hour_utc": hour,
        "hour_penalty": hour in WEAK_HOURS_UTC,
        "weak_hours_utc": list(WEAK_HOURS_UTC),
        "scored": len(scored),
        "n_wash": sum(1 for s in scored if s["flag"] == "wash"),
        "pools": scored,
        "errors": errors,
        "method": "wash = 1 - unique_buyers/buys (arxiv 2609.10246); hour penalty (arxiv 2606.08232)",
    }
    os.makedirs(STATE, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
