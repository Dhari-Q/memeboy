#!/usr/bin/env python
"""Quant machine: brain gate. Computes a cheap deterministic digest of the
market state; the cron engine compares it to the previous tick and wakes the
LLM brain ONLY when something actually changed (smart routing: no change, no
tokens spent). No timestamps in the output — output must be deterministic."""
import json
import os
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


STATE = os.path.join(REPO, "state")


def load(name):
    try:
        with open(os.path.join(STATE, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def bucket(x, step=0.5):
    try:
        return int(float(x) // step)
    except Exception:
        return None


def main():
    parts = []
    p = load("pulse.json").get("feeds") or {}
    for k in ("binance_BTCUSDT", "binance_SOLUSDT", "binance_PEPEUSDT"):
        v = p.get(k) or {}
        parts.append(f"{k}={bucket(v.get('price'), 50)}")   # $50 buckets
    b = (p.get("dexscreener_boosts") or {})
    parts.append(f"boosts={b.get('count')}")
    c = load("candidates.json")
    tops = sorted((c.get("candidates") or []), key=lambda x: x.get("score", 0), reverse=True)[:3]
    parts.append("cands=" + "|".join(f"{t.get('symbol')}:{bucket(t.get('score'), 1) or 0}"
                                     f":{bucket(t.get('liquidity_usd'), 10000) or 0}" for t in tops))
    births = load("births.json").get("births") or []
    parts.append("births=" + "|".join(f"{b2.get('symbol')}:{b2.get('dex')}"
                                      f":{bucket((b2.get('rugcheck') or {}).get('creator_balance'), 1e6) or 0}"
                                      for b2 in births[:4]))
    n = load("news.json")
    parts.append(f"news={n.get('count')}")
    print(";".join(parts))


if __name__ == "__main__":
    main()
