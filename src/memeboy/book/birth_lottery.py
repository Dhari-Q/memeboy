#!/usr/bin/env python
"""Quant machine: birth lottery sleeve (paper only).

Buys a FIXED small ticket on every newborn that passes the hard veto, so the
machine accumulates a REAL track record instead of waiting forever. Rules come
from the 2026-11 edge research (references/edge-research-2026-11.md):

  HARD VETO (any one = skip, never buy):
    - rugcheck missing            -> unknown is not a pass
    - mint authority set          -> creator can print infinite supply
    - freeze authority set        -> creator can freeze your tokens
    - rugged == true
    - any red risk: creator rugged before, single holder, high concentration,
      mint still enabled, LP unlocked
    - age under 2 minutes         -> pure sniper block, we lose that race
  TICKET: 25 USDT paper, priced live via Jupiter, one per mint ever.
  EXIT: owned by quant_exit_engine (stop -20%, scale-out +50%, runner rides).

This writes files. It never touches a wallet or a key. Silent watchdog.
"""
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


BIRTHS = os.path.join(STATE, "births.json")
ORDERS = os.path.join(STATE, "paper_orders.json")
TICKET_USDT = 25.0
MIN_AGE_H = 0.5           # REAL age now that the +3h timezone bug is fixed
MIN_FDV_USD = 50_000.0    # the only lottery winner had FDV $103k; every loser was < $46k
MIN_RESERVE_USD = 50_000.0
MAX_TOP10 = 0.50
MIN_TRADES = 10           # arxiv 2602.14860: graduation odds rise with trade count;
                          # sub-10-trade launches are mostly instant and manipulated
MIN_UNIQUE_BUYERS = 5     # distinct humans, not one wallet looping
MAX_WASH = 0.6            # 1 - buyers/buys above this = wash trading (arxiv 2609.10246)
TIMEOUT = 12

RED = ("creator history of rugged", "single holder ownership",
       "high holder concentration", "mint authority still enabled",
       "large amount of lp unlocked", "freeze authority")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def jup_price(mint):
    try:
        return float((get(f"https://api.jup.ag/price/v3?ids={mint}").get(mint) or {}).get("usdPrice") or 0)
    except Exception:
        return 0.0


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def veto(b):
    rc = b.get("rugcheck")
    if not rc:
        return "no_rugcheck"
    if rc.get("mint_authority"):
        return "mint_authority_live"
    if rc.get("freeze_authority"):
        return "freeze_authority_live"
    if rc.get("rugged"):
        return "rugged"
    risks = " | ".join(r.lower() for r in (rc.get("risk_names") or []))
    for w in RED:
        if w in risks:
            return f"red:{w}"
    age = b.get("age_h")
    if age is None or age < MIN_AGE_H:
        return "too_young"
    if not b.get("token_address") or len(b["token_address"]) < 32:
        return "bad_mint"
    try:
        fdv = float(b.get("fdv_usd") or 0)
    except (TypeError, ValueError):
        fdv = 0.0
    if fdv < MIN_FDV_USD:
        return f"small_fdv:{fdv:.0f}"
    try:
        reserve = float(b.get("reserve_usd") or 0)
    except (TypeError, ValueError):
        reserve = 0.0
    if reserve < MIN_RESERVE_USD:
        return f"thin_reserve:{reserve:.0f}"
    top10 = sum((h.get("pct") or 0) for h in (rc.get("top_holders") or []))
    if top10 > MAX_TOP10:
        return f"top10:{top10:.2f}"
    tx = b.get("txns_24h") or {}
    buys, buyers = tx.get("buys") or 0, tx.get("buyers") or 0
    sells = tx.get("sells") or 0
    if buys + sells < MIN_TRADES:
        return f"thin:{buys + sells}_trades"
    if buyers < MIN_UNIQUE_BUYERS:
        return f"few_buyers:{buyers}"
    if buys and 1 - buyers / buys > MAX_WASH:
        return "wash"
    return None


def main():
    births = load(BIRTHS, {}).get("births") or []
    book = load(ORDERS, {"orders": []})
    held = {o.get("mint") for o in book["orders"]}
    placed = 0
    for b in births:
        why = veto(b)
        if why or b["token_address"] in held:
            continue
        price = jup_price(b["token_address"])
        qty = (TICKET_USDT / price) if price > 0 else None
        book["orders"].append({
            "order_id": f"PO-{int(time.time()*1000)}",
            "decision_key": f"birth-lottery::{b['token_address']}",
            "sleeve": "birth-lottery",
            "symbol": b.get("symbol"),
            "mint": b["token_address"],
            "dex": b.get("dex"),
            "side": "buy",
            "notional_usdt": TICKET_USDT,
            "entry_price_usd": price,
            "qty": round(qty, 4) if qty else None,
            "status": "filled" if price > 0 else "pending_price",
            "entry_fdv_usd": b.get("fdv_usd"),
            "entry_age_h": b.get("age_h"),
            "invalidates_if": ["rugcheck turns red", "price -20%"],
            "placed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        held.add(b["token_address"])
        placed += 1
    if placed:
        book["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = ORDERS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(book, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, ORDERS)


if __name__ == "__main__":
    main()
