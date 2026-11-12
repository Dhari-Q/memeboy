#!/usr/bin/env python
"""Quant machine: paper trader. Turns the brain's ENTER verdicts into real
paper orders (recorded, priced live via Jupiter), so the loop runs hands-off.
Paper only — this writes files, never touches a wallet. Silent watchdog."""
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


DECISIONS = os.path.join(STATE, "decisions")
ORDERS = os.path.join(STATE, "paper_orders.json")
EQUITY_USDT = 10_000.0
BASE_NOTIONAL = 25.0      # paper ticket, fixed — matches the birth-lottery sleeve
MAX_NOTIONAL = 25.0       # confidence never buys size; one ticket, one bet
TIMEOUT = 12


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def jup_price(mint):
    try:
        d = get(f"https://api.jup.ag/price/v3?ids={mint}")
        info = (d.get(mint) or {})
        return float(info.get("usdPrice") or 0)
    except Exception:
        return 0.0


def load_orders():
    try:
        with open(ORDERS, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"orders": []}


def main():
    book = load_orders()
    done_ids = {o.get("decision_key") for o in book["orders"]}
    placed = 0
    if not os.path.isdir(DECISIONS):
        return
    for fname in sorted(os.listdir(DECISIONS)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(DECISIONS, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                dec = json.load(fh)
        except Exception:
            continue
        for d in dec.get("decisions", []):
            if d.get("verdict") != "ENTER":
                continue
            mint = d.get("token_address")
            if not mint or not mint.startswith(("So", "A", "B", "C", "D", "E", "F", "G", "H", "1", "2", "3", "4", "5", "6", "7", "8", "9")) or len(mint) < 32:
                continue  # paper trades Solana mints only for now
            key = f"{dec.get('decision_id')}::{d.get('symbol')}"
            if key in done_ids:
                continue
            price = jup_price(mint)
            sf = 0.0  # size is fixed; the model's confidence must not scale exposure
            notional = BASE_NOTIONAL
            qty = (notional / price) if price > 0 else None
            order = {
                "order_id": f"PO-{int(time.time()*1000)}",
                "decision_key": key,
                "symbol": d.get("symbol"),
                "mint": mint,
                "side": "buy",
                "notional_usdt": round(notional, 2),
                "entry_price_usd": price,
                "qty": round(qty, 6) if qty else None,
                "status": "filled" if price > 0 else "pending_price",
                "invalidates_if": d.get("invalidates_if") or [],
                "placed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            book["orders"].append(order)
            done_ids.add(key)
            placed += 1
    if placed:
        book["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = ORDERS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(book, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, ORDERS)
    # silent


if __name__ == "__main__":
    main()
