#!/usr/bin/env python
"""Quant machine: birth eye (stdlib only). Watches tokens born on Solana via
GeckoTerminal new_pools, enriches the hottest newborns with RugCheck reports
(mint/freeze authority, top holders, creator balance) and writes
state/births.json. Silent watchdog."""
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


BIRTHS = os.path.join(STATE, "births.json")
TIMEOUT = 15
TOP_N = 8          # newborns to inspect
ENRICH_N = 5       # rugcheck reports per tick (be gentle with the free API)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def safe(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def main():
    out = []
    errors = []
    now = int(time.time() * 1000)
    try:
        pools = safe(get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"),
                     "data") or []
    except Exception as exc:
        pools, errors = [], [f"geckoterminal: {exc}"]
    for p in pools[:TOP_N]:
        try:
            a = p.get("attributes") or {}
            rel = p.get("relationships") or {}
            base = safe(rel, "base_token", "data") or {}
            dex = safe(rel, "dex", "data") or {}
            base_id = base.get("id") or ""
            mint = base_id.split("_", 1)[1] if "_" in base_id else None
            name = a.get("name") or ""
            symbol = name.split(" / ")[0] if " / " in name else name
            created = a.get("pool_created_at") or ""
            age_h = (now / 1000 - calendar.timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))) / 3600 if created else None
            txns = a.get("transactions", {}).get("h24") or {}
            out.append({
                "pool_id": p.get("id"),
                "token_address": mint,
                "name": name,
                "symbol": symbol,
                "dex": dex.get("id"),
                "age_h": round(age_h, 2) if age_h is not None else None,
                "price_usd": a.get("base_token_price_usd"),
                "price_change": (a.get("price_change_percentage") or {}),
                "volume_24h_usd": safe(a, "volume_usd", "h24"),
                "reserve_usd": a.get("reserve_in_usd"),
                "fdv_usd": a.get("fdv_usd"),
                "txns_24h": {"buys": txns.get("buys"), "sells": txns.get("sells"),
                             "buyers": txns.get("buyers"), "sellers": txns.get("sellers")},
            })
        except Exception as exc:
            errors.append(f"pool: {type(exc).__name__}")
    # rugcheck enrichment (malice fingerprints)
    for cand in out[:ENRICH_N]:
        tok = cand.get("token_address")
        if not tok:
            continue
        try:
            rep = get(f"https://api.rugcheck.xyz/v1/tokens/{tok}/report")
            cand["rugcheck"] = {
                "mint_authority": safe(rep, "token", "mintAuthority"),
                "freeze_authority": safe(rep, "token", "freezeAuthority"),
                "rugged": rep.get("rugged"),
                "creator": rep.get("creator"),
                "creator_balance": rep.get("creatorBalance"),
                "top_holders": [
                    {"address": h.get("address"), "owner": h.get("owner"),
                     "pct": h.get("pct"), "insider": h.get("insider")}
                    for h in (rep.get("topHolders") or [])[:10]
                ],
                "risk_names": [r.get("name") for r in (rep.get("risks") or [])],
            }
        except Exception as exc:
            cand["rugcheck"] = None
            code = getattr(exc, "code", "")
            errors.append(f"rugcheck {tok[:8]}: {type(exc).__name__}{' ' + str(code) if code else ''}")
    payload = {"ts": now, "count": len(out), "errors": errors, "births": out}
    os.makedirs(STATE, exist_ok=True)
    tmp = BIRTHS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, BIRTHS)
    # silent


if __name__ == "__main__":
    main()
