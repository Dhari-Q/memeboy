#!/usr/bin/env python
"""Quant machine: discovery eye (stdlib only). Scores freshly boosted tokens
from DexScreener by hard metrics (age, liquidity, volume) and writes
state/candidates.json. Silent watchdog."""
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


CAND = os.path.join(STATE, "candidates.json")
TIMEOUT = 15
TOP_N = 10


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def score(pair, boosted=False):
    now = int(time.time() * 1000)
    age_ms = now - int(pair.get("pairCreatedAt") or 0)
    age_h = age_ms / 3.6e6
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    vol24 = float((pair.get("volume") or {}).get("h24") or 0)
    price = float(pair.get("priceUsd") or 0)
    txns24 = int((pair.get("txns") or {}).get("h24", {}).get("buys") or 0) + \
             int((pair.get("txns") or {}).get("h24", {}).get("sells") or 0)
    pc24 = float((pair.get("priceChange") or {}).get("h24") or 0)
    vol_liq = (vol24 / liq) if liq > 0 else 0
    s = 0.0
    flags = []
    if age_h < 0.2:
        flags.append("born<12min")
    if liq < 5_000:
        flags.append("liq<5k")
    elif liq < 50_000:
        s += 1
    elif liq < 500_000:
        s += 2
    else:
        s += 3
    if vol_liq > 20:
        s += 2
    elif vol_liq > 5:
        s += 1
    if txns24 > 500:
        s += 2
    elif txns24 > 100:
        s += 1
    if 0 < pc24 < 40:
        s += 1
    if boosted:
        s += 1
    return {"score": s, "age_h": round(age_h, 2), "liquidity_usd": liq,
            "volume_24h_usd": vol24, "vol_liq_ratio": round(vol_liq, 1),
            "txns_24h": txns24, "price_change_24h_pct": pc24,
            "price_usd": price, "flags": flags}


def main():
    out = []
    errors = []
    try:
        boosts = get("https://api.dexscreener.com/token-boosts/latest/v1")
        boosts = boosts[:TOP_N] if isinstance(boosts, list) else []
    except Exception as exc:
        boosts, errors = [], [f"boosts: {exc}"]
    for b in boosts:
        try:
            tok = b.get("tokenAddress") or ""
            if not tok:
                continue
            pairs = get(f"https://api.dexscreener.com/latest/dex/tokens/{tok}")
            chain_pairs = pairs.get("pairs") or []
            # pick the most liquid pair for the token
            best = None
            for p in chain_pairs:
                if not best or float((p.get("liquidity") or {}).get("usd") or 0) > \
                        float((best.get("liquidity") or {}).get("usd") or 0):
                    best = p
            if not best:
                continue
            base = best.get("baseToken") or {}
            sc = score(best, boosted=True)
            out.append({"chain": best.get("chainId"), "token_address": tok,
                        "symbol": base.get("symbol"), "name": base.get("name"),
                        "pair_address": best.get("pairAddress"),
                        "dex": best.get("dexId"), "url": best.get("url"),
                        "boost_desc": (b.get("description") or "")[:160],
                        **sc})
        except Exception as exc:
            errors.append(f"{tok}: {type(exc).__name__}")
    out.sort(key=lambda x: x["score"], reverse=True)
    payload = {"ts": int(time.time() * 1000), "count": len(out), "errors": errors,
               "candidates": out}
    os.makedirs(STATE, exist_ok=True)
    tmp = CAND + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CAND)
    # silent


if __name__ == "__main__":
    main()
