#!/usr/bin/env python
"""Quant machine: wallet eye (stdlib only, no LLM).

Builds a small, honest "smart money" signal from free data:
  1. A wallet pool seeded by the top holders of the tokens this machine already
     won on (Robinhood) plus its current conviction holds (jeff, WASTED, CBUCKS).
  2. For every Solana candidate / newborn, the overlap between its top holders
     and that pool (>= 2 independent wallets = convergence signal).
  3. A developer record proxy from the public Solana RPC: wallet age (oldest
     recent signature), recent tx count, SOL balance.

Writes state/wallets.json. Unknown is written as known=false, never guessed.
Silent watchdog.
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


ORDERS = os.path.join(STATE, "paper_orders.json")
CANDS = os.path.join(STATE, "candidates.json")
BIRTHS = os.path.join(STATE, "births.json")
OUT = os.path.join(STATE, "wallets.json")
RPC = "https://api.mainnet-beta.solana.com"
TIMEOUT = 15
MAX_RC_CALLS = 12          # RugCheck budget per run
MAX_DEV_CHECKS = 5         # RPC dev checks per run
OVERLAP_SIGNAL = 2         # independent pool wallets = convergence


def get(url, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace")), None
        except Exception as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(1.0)
    return None, last


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode("utf-8")
    req = urllib.request.Request(RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace")).get("result")


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def rc_report(mint):
    d, _ = get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
    return d


def holders_of(report):
    """(owner, pct, insider) for the report's top holders; excludes creator and AMM/locker accounts."""
    if not report:
        return []
    creator = report.get("creator")
    known = report.get("knownAccounts") or {}
    out = []
    for h in (report.get("topHolders") or []):
        owner = h.get("owner")
        if not owner or owner == creator:
            continue
        label = (known.get(owner) or {}).get("type") if isinstance(known.get(owner), dict) else None
        if label in ("AMM", "LOCKER"):
            continue
        out.append((owner, h.get("pct") or 0.0, bool(h.get("insider"))))
    return out


def dev_record(creator):
    if not creator:
        return {"status": "unknown", "reason": "no_creator"}
    try:
        bal = rpc("getBalance", [creator])
        sigs = rpc("getSignaturesForAddress", [creator, {"limit": 100}])
    except Exception as exc:
        return {"status": "unknown", "reason": "%s: %s" % (type(exc).__name__, exc)}
    if bal is None or sigs is None:
        return {"status": "unknown", "reason": "rpc_null"}
    lamports = bal.get("value", 0) if isinstance(bal, dict) else 0
    sol = round(lamports / 1e9, 6)
    n = len(sigs)
    times = [s.get("blockTime") for s in sigs if s.get("blockTime")]
    first = min(times) if times else None
    age_days = round((time.time() - first) / 86400.0, 1) if first else None
    if n == 0:
        status = "fresh"
    elif age_days is not None and age_days < 7:
        status = "fresh"
    elif n >= 100 and sol < 0.05:
        status = "busy_low_balance"
    elif sol >= 0.5 and 10 <= n < 100:
        status = "clean"
    else:
        status = "neutral"
    return {"status": status, "sol_balance": sol, "recent_tx": n,
            "oldest_tx_age_days": age_days}


def main():
    book = load(ORDERS, {"orders": []})
    orders = book.get("orders") or []
    cands = load(CANDS, {}).get("candidates") or []
    births = load(BIRTHS, {}).get("births") or []
    prev = load(OUT, {})
    pool = dict(prev.get("pool") or {})
    rc_calls = 0

    # 1) seed the pool from the machine's own winners + conviction holds
    seeds = []
    for o in orders:
        if o.get("status") == "closed" and float(o.get("realized_pnl_usdt") or 0) > 0:
            seeds.append(o)
        elif o.get("sleeve") is None and o.get("status") == "filled":
            seeds.append(o)
    seen = set()
    for o in seeds[:6]:
        mint = o.get("mint")
        if not mint or mint in seen:
            continue
        seen.add(mint)
        rep = rc_report(mint)
        rc_calls += 1
        for owner, pct, insider in holders_of(rep):
            if insider:
                continue
            rec = pool.setdefault(owner, {"sources": [], "first_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            if o.get("symbol") not in rec["sources"]:
                rec["sources"].append(o.get("symbol"))
            rec["wins"] = sum(1 for s in rec["sources"] if s in {x.get("symbol") for x in orders if float(x.get("realized_pnl_usdt") or 0) > 0})
        time.sleep(1.2)
        if rc_calls >= MAX_RC_CALLS:
            break

    # 2) score candidates and births
    mints_out = {}
    targets = []
    for c in cands:
        if (c.get("chain") or "").lower() != "solana":
            continue
        targets.append({"mint": c.get("token_address"), "symbol": c.get("symbol"),
                        "kind": "candidate", "report": None})
        if len(targets) >= 6:
            break
    for b in births:
        targets.append({"mint": b.get("token_address"), "symbol": b.get("symbol"),
                        "kind": "birth", "report": None})
        if len(targets) >= 10:
            break

    dev_checks = 0
    for t in targets:
        mint = t["mint"]
        if not mint:
            continue
        rep = None
        # reuse the birth-eye's stored report shape when it has owner fields
        if t["kind"] == "birth":
            for b in births:
                if b.get("token_address") == mint and b.get("rugcheck"):
                    rc = b["rugcheck"]
                    if (rc.get("top_holders") or [{}])[0].get("owner") is not None:
                        rep = {"creator": rc.get("creator"), "topHolders": rc.get("top_holders"),
                               "totalHolders": None, "risks": [{"name": n} for n in (rc.get("risk_names") or [])],
                               "creatorBalance": rc.get("creator_balance")}
                    break
        if rep is None and rc_calls < MAX_RC_CALLS:
            rep = rc_report(mint)
            rc_calls += 1
            time.sleep(1.2)
        if not rep:
            mints_out[mint] = {"symbol": t["symbol"], "known": False, "reason": "report_missing"}
            continue
        holders = holders_of(rep)
        overlap = [w for w, _, _ in holders if w in pool]
        creators = rep.get("creator")
        dev = {"status": "unknown"}
        if creators and dev_checks < MAX_DEV_CHECKS:
            dev = dev_record(creators)
            dev_checks += 1
        mints_out[mint] = {
            "symbol": t["symbol"],
            "kind": t["kind"],
            "known": True,
            "overlap": len(overlap),
            "overlap_wallets": overlap[:5],
            "convergence": len(overlap) >= OVERLAP_SIGNAL,
            "creator": creators,
            "creator_balance": rep.get("creatorBalance"),
            "holders": rep.get("totalHolders"),
            "top10_pct": round(sum(p for _, p, _ in holders[:10]), 2),
            "insider_holders": sum(1 for _, _, ins in holders if ins),
            "risk_names": [r.get("name") for r in (rep.get("risks") or [])],
            "dev": dev,
        }

    out = {
        "ts": int(time.time() * 1000),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pool_size": len(pool),
        "overlap_signal": OVERLAP_SIGNAL,
        "pool": pool,
        "mints": mints_out,
        "calls": {"rugcheck": rc_calls, "dev_rpc": dev_checks},
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    # silent


if __name__ == "__main__":
    main()
