#!/usr/bin/env python
"""Quant machine: funding eye (stdlib only). Reads Binance USDT-M perp funding
for the meme universe and writes state/funding.json. No key, no orders.
Silent watchdog — prints nothing."""
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


OUT = os.path.join(STATE, "funding.json")
TIMEOUT = 20

# meme perp universe, verified live 2026-11-09 (46 contracts tagged Meme).
# Refresh from fapi/v1/exchangeInfo (underlyingSubType contains "Meme") if a
# name here 404s — a missing symbol is skipped, never fatal.
UNIVERSE = [
    "DOGEUSDT", "1000SHIBUSDT", "1000PEPEUSDT", "1000FLOKIUSDT", "1000BONKUSDT",
    "WIFUSDT", "BOMEUSDT", "MEMEUSDT", "PEOPLEUSDT", "1000RATSUSDT", "MEWUSDT",
    "BRETTUSDT", "POPCATUSDT", "NEIROUSDT", "TURBOUSDT", "PNUTUSDT", "ACTUSDT",
    "1000CATUSDT", "1MBABYDOGEUSDT", "DOGSUSDT", "NOTUSDT", "HMSTRUSDT",
    "BANANAUSDT", "1000SATSUSDT", "ORDIUSDT", "TRUMPUSDT", "PENGUUSDT",
    "FARTCOINUSDT", "MOODENGUSDT", "GOATUSDT", "PONKEUSDT", "SLERFUSDT",
    "MYROUSDT", "BODENUSDT", "MICHIUSDT", "GIGGLEUSDT", "PIPPINUSDT", "MUSDT",
    "BROCCOLI714USDT", "BROCCOLIF3BUSDT", "BANANAS31USDT", "TSTUSDT", "TUTUSDT",
    "MUBARAKUSDT", "1000CHEEMSUSDT", "NEWTUSDT",
]

HOT = 50.0     # |APR| above this = the brain should notice
WILD = 150.0   # |APR| above this = regime flag


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main():
    errors = []
    try:
        prem = get("https://fapi.binance.com/fapi/v1/premiumIndex")
        by_rate = {p["symbol"]: float(p.get("lastFundingRate") or 0) for p in prem}
        by_mark = {p["symbol"]: float(p.get("markPrice") or 0) for p in prem}
    except Exception as exc:
        by_rate, by_mark = {}, {}
        errors.append(f"premiumIndex: {type(exc).__name__}")
    try:
        tick = get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        by_chg = {t["symbol"]: float(t.get("priceChangePercent") or 0) for t in tick}
        by_vol = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in tick}
    except Exception as exc:
        by_chg, by_vol = {}, {}
        errors.append(f"ticker: {type(exc).__name__}")

    rows = []
    for sym in UNIVERSE:
        if sym not in by_rate:
            continue
        rate = by_rate[sym]
        apr = rate * 3 * 365 * 100
        rows.append({
            "symbol": sym,
            "funding_pct_8h": round(rate * 100, 4),
            "apr_pct": round(apr, 1),
            "mark": by_mark.get(sym),
            "chg_24h_pct": round(by_chg.get(sym, 0.0), 2),
            "vol_24h_usdt": round(by_vol.get(sym, 0.0)),
            "flag": "wild" if abs(apr) >= WILD else ("hot" if abs(apr) >= HOT else ""),
        })
    rows.sort(key=lambda r: r["apr_pct"], reverse=True)
    flagged = [r for r in rows if r["flag"]]
    aprs = [r["apr_pct"] for r in rows]
    payload = {
        "ts": int(time.time() * 1000),
        "source": "binance-fapi",
        "universe": len(UNIVERSE),
        "covered": len(rows),
        "median_apr_pct": round(sorted(aprs)[len(aprs) // 2], 1) if aprs else None,
        "n_hot": sum(1 for r in rows if r["flag"] == "hot"),
        "n_wild": sum(1 for r in rows if r["flag"] == "wild"),
        "hottest": rows[0]["symbol"] if rows else None,
        "coldest": rows[-1]["symbol"] if rows else None,
        "flagged": flagged,
        "rows": rows,
        "errors": errors,
    }
    os.makedirs(STATE, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
