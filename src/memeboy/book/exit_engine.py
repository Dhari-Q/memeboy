#!/usr/bin/env python
"""Quant machine: exit engine — the 'auto-dump' agent (paper only).

Watches every open paper position each tick and sells by hard rules, BEFORE
the loss grows. Never buys. Never touches a wallet. Silent watchdog.

Rules (declared, deterministic — asymmetric by design: loss is capped, profit has NO ceiling):
  - fill_pending : a buy left without price gets filled at current price
  - rug_guard    : rugcheck shows rugged / mint authority turned on / new red
                   risk => dump ALL immediately ("sell before the rug")
  - stop_loss    : price <= entry * 0.80        => dump ALL (hard loss cap)
  - crash_guard  : trailing stop from peak. Tightens as profit grows:
                   -30% from peak normally, -20% once price >= 3x entry,
                   -15% once >= 5x. The runner is NEVER sold at a fixed target.
  - scale_out    : price >= entry * 1.50        => sell 50% once (capital out,
                   the remaining half rides with the trailing stop — no ceiling)
"""
import calendar
import json
import os
import time
import urllib.request
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


ORDERS = os.path.join(STATE, "paper_orders.json")
PERF = os.path.join(STATE, "performance.json")
TIMEOUT = 12
PENDING_BUDGET_MIN = 60   # a buy with no live price is cancelled, never left hanging
ILLIQUID_BUDGET_MIN = 24 * 60   # no live price on any venue for 24h => write down to 0
MIN_MARK_LIQ_USD = 1_000.0      # a DexScreener pair below this depth is not a real mark
ROUND_TRIP_COST = 0.04          # 2% per side: fees + slippage + priority, the measured round trip
FAST_EXIT_MIN = 60              # first hour: exit 15% off peak, not 30%
RUG_CACHE = os.path.join(STATE, "rug_cache.json")
RUG_TTL_MIN = 15                # a cached rug verdict is good for 15 minutes
MAX_RUG_CALLS = 15              # rugcheck budget per 3-minute tick (77 positions, round-robin)

RED_RISK_WORDS = ("rugged", "mint authority still enabled", "freeze authority",
                  "creator history of rugged", "single holder ownership",
                  "high holder concentration")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def jup_price(mint):
    try:
        info = (get(f"https://api.jup.ag/price/v3?ids={mint}").get(mint) or {})
        return float(info.get("usdPrice") or 0)
    except Exception:
        return 0.0


def batch_jup_prices(mints):
    """One batch call instead of 70 single calls: the per-token loop was hitting
    Jupiter's rate limit on every tick."""
    out = {}
    uniq = list(dict.fromkeys([m for m in mints if m]))
    for i in range(0, len(uniq), 50):
        chunk = uniq[i:i + 50]
        try:
            d = get("https://api.jup.ag/price/v3?ids=" + ",".join(chunk))
            for m in chunk:
                px = float((d.get(m) or {}).get("usdPrice") or 0)
                if px > 0:
                    out[m] = px
        except Exception:
            pass
        time.sleep(0.3)
    return out


def dex_price(mint):
    """Fallback mark for tokens Jupiter won't price. Returns (price, liquidity);
    a pair below MIN_MARK_LIQ_USD is not a real mark (a stale $0.02 pool is not a price)."""
    try:
        d = get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
        best_px, best_liq = 0.0, -1.0
        for p in (d.get("pairs") or []):
            if (p.get("baseToken") or {}).get("address") != mint:
                continue
            px = float(p.get("priceUsd") or 0)
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            if px > 0 and liq > best_liq:
                best_px, best_liq = px, liq
        if best_liq < MIN_MARK_LIQ_USD:
            return 0.0, max(best_liq, 0.0)
        return best_px, best_liq
    except Exception:
        return 0.0, 0.0


def rugcheck_flags(mint, cache=None, budget=None):
    """Rug verdict with a 15-minute cache and a per-tick call budget. Without
    this, a 3-minute exit tick on 77 positions would make 1,500 RugCheck calls
    an hour and get the eye rate-limited."""
    if cache is not None:
        rec = cache.get(mint)
        if rec and (time.time() - rec.get("ts", 0)) < RUG_TTL_MIN * 60:
            return rec.get("red", False), rec.get("detail", {})
        if budget is not None and budget[0] <= 0:
            if rec:
                return rec.get("red", False), rec.get("detail", {})
            return False, {"unknown": True}
        if budget is not None:
            budget[0] -= 1
    try:
        rep = get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
        risks = " | ".join((r.get("name") or "").lower() for r in (rep.get("risks") or []))
        ma = rep.get("token", {}).get("mintAuthority")
        rugged = bool(rep.get("rugged"))
        red = rugged or bool(ma) or any(w in risks for w in RED_RISK_WORDS)
        detail = {"rugged": rugged, "mint_authority": ma, "risks": risks[:200]}
        if cache is not None:
            cache[mint] = {"ts": time.time(), "red": red, "detail": detail}
        return red, detail
    except Exception:
        return False, {}   # unknown is NOT a pass for buying, but not a sell trigger either


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def age_min(stamp):
    """Minutes since an ISO 'Z' stamp; 0 when unparsable (never expire on bad data)."""
    try:
        return (time.time() - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))) / 60.0
    except Exception:
        return 0.0


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    book = load(ORDERS, {"orders": []})
    changed = False
    events = []
    open_mints = [o.get("mint") for o in book["orders"]
                  if o.get("side") == "buy" and o.get("status") not in ("closed", "expired")
                  and o.get("mint")]
    prices = batch_jup_prices(open_mints)
    rug_cache = load(RUG_CACHE, {})
    rug_budget = [MAX_RUG_CALLS]
    for o in book["orders"]:
        if o.get("side") != "buy" or o.get("status") in ("closed", "expired"):
            continue
        mint = o.get("mint")
        if not mint:
            continue
        price = prices.get(mint) or 0.0
        venue_liq = None
        if price <= 0:
            price = jup_price(mint)
        if price <= 0:
            price, venue_liq = dex_price(mint)
        # reconcile a buy that never got a price: cancel it after the 60-min budget
        if o.get("status") == "pending_price" and not o.get("entry_price_usd") and price <= 0:
            if age_min(o.get("placed_at")) > PENDING_BUDGET_MIN:
                o["status"] = "expired"
                o["close_reason"] = "never_priced_60m"
                o["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                events.append((o.get("symbol"), "expired_never_priced", 0))
                changed = True
            continue
        if price <= 0:
            # Durable illiquidity: one failed tick is noise; 24h of continuous
            # no-price on every venue is a dead position. Never write down on a
            # single failed call - that turns live positions into fake losses.
            if not o.get("unpriced_since"):
                o["unpriced_since"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                changed = True
            elif age_min(o.get("unpriced_since")) > ILLIQUID_BUDGET_MIN and float(o.get("entry_price_usd") or 0) > 0 and float(o.get("qty") or 0) > 0:
                # only a real filled position can be written down; an order that
                # never got a price (entry 0 / no qty) is not a 25$ loss
                notional = float(o.get("notional_usdt") or 0)
                o["status"] = "closed"
                o["close_reason"] = "illiquid_write_down_24h"
                o["exit_price_usd"] = 0.0
                o["realized_pnl_usdt"] = round(float(o.get("realized_pnl_usdt") or 0) - notional, 4)
                o["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                events.append((o.get("symbol"), "illiquid_write_down_24h", -notional))
                changed = True
            continue
        if o.pop("unpriced_since", None) is not None:
            changed = True
        # fill a pending buy
        if o.get("status") == "pending_price" and not o.get("entry_price_usd"):
            o["entry_price_usd"] = price
            o["qty"] = round(o.get("notional_usdt", 0) / price, 6)
            o["status"] = "filled"
            changed = True
        entry = float(o.get("entry_price_usd") or 0)
        qty = float(o.get("qty") or 0)
        if entry <= 0 or qty <= 0:
            continue
        peak = max(float(o.get("peak_price_usd") or entry), price)
        if peak != o.get("peak_price_usd"):
            o["peak_price_usd"] = peak
            changed = True

        def dump(reason, frac=1.0):
            sold = qty * frac
            # the measured round trip is ~4%: 2% on the way in, 2% on the way out.
            # PnL is net of both, so a flat trade still loses the cost.
            eff_entry = entry * (1 + ROUND_TRIP_COST / 2)
            eff_exit = price * (1 - ROUND_TRIP_COST / 2)
            pnl = (eff_exit - eff_entry) * sold
            o["realized_pnl_usdt"] = round(float(o.get("realized_pnl_usdt") or 0) + pnl, 4)
            o["qty"] = round(qty - sold, 6)
            o["exit_price_usd"] = price
            o["close_reason"] = reason
            o["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            o["status"] = "closed" if o["qty"] <= 0 else "partial"
            events.append((o.get("symbol"), reason, round(pnl, 2)))
            return True

        # 1) rug guard — sell before the rug
        red, detail = rugcheck_flags(mint, rug_cache, rug_budget)
        if red:
            changed |= dump("rug_guard")
            continue
        # 2) stop loss (hard loss cap)
        if price <= entry * 0.80:
            changed |= dump("stop_loss_20pct")
            continue
        # 3) trailing crash guard — tightens as profit grows, never a fixed ceiling.
        # In the first hour a memecoin pump is minutes, not hours: exit 15% off
        # peak there, and only widen to 30% once the position has survived an hour.
        mult = price / entry
        if age_min(o.get("placed_at")) <= FAST_EXIT_MIN:
            trail = 0.85
        else:
            trail = 0.85 if mult >= 5 else (0.80 if mult >= 3 else 0.70)
        if peak > entry and price <= peak * trail:
            changed |= dump(f"crash_guard_{int((1 - trail) * 100)}pct_from_peak")
            continue
        # 4) scale out once: capital out at +50%, runner rides with no ceiling
        if price >= entry * 1.5 and not o.get("partial_taken"):
            o["partial_taken"] = True
            changed |= dump("scale_out_50pct_capital_out", frac=0.5)

    if changed:
        book["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save(ORDERS, book)
    save(RUG_CACHE, rug_cache)

    # performance.json rebuilt from the book every run (idempotent, no drift)
    closed = [o for o in book["orders"] if "realized_pnl_usdt" in o]
    wins = [o for o in closed if o["realized_pnl_usdt"] > 0]
    losses = [o for o in closed if o["realized_pnl_usdt"] <= 0]
    perf = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "open_positions": [o.get("symbol") for o in book["orders"]
                           if o.get("side") == "buy" and o.get("status") not in ("closed", "expired")],
        "n_open": sum(1 for o in book["orders"] if o.get("side") == "buy"
                      and o.get("status") not in ("closed", "expired")),
        "n_closed": len(closed),
        "realized_pnl_usdt": round(sum(o["realized_pnl_usdt"] for o in closed), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "best_trade_usdt": round(max((o["realized_pnl_usdt"] for o in closed), default=0), 2),
        "worst_trade_usdt": round(min((o["realized_pnl_usdt"] for o in closed), default=0), 2),
        "history": [{"symbol": o.get("symbol"), "reason": o.get("close_reason"),
                     "pnl": o.get("realized_pnl_usdt"), "at": o.get("closed_at")}
                    for o in closed][-50:],
    }
    save(PERF, perf)
    # silent


if __name__ == "__main__":
    main()
