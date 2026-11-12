#!/usr/bin/env python
"""Quant machine: edge check (stdlib only, no LLM).

Judges every sleeve by the skeptical standard instead of the highlight reel:
  - per-ticket return and win rate
  - the 90% range of per-ticket returns (bootstrap-free percentile)
  - return WITHOUT its best 3 trades (a real edge survives removing luck)
  - sample size (n < 30 is not evidence)

Writes state/edge_check.json. Silent unless QM_EDGE_ALERT=1, in which case it
prints one Arabic line per sleeve whose verdict changed to a bad one.
"""
import json
import os
import time
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


ORDERS = os.path.join(STATE, "paper_orders.json")
OUT = os.path.join(STATE, "edge_check.json")
ALERT = True   # the cron engine delivers stdout; a bad verdict change is the alert

VERDICT_AR = {
    "no_trades": "لا صفقات",
    "negative": "خاسرة صافية",
    "insufficient_sample": "عينة صغيرة — ليست دليلاً",
    "range_includes_zero": "المدى يشمل الصفر — لا حافة",
    "fragile_without_best3": "هشة — تنقلب سالبة بدون أفضل 3 صفقات",
    "positive_edge": "حافة موجبة",
}


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def pct(vals, q):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * q
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def judge(pnls):
    n = len(pnls)
    if n == 0:
        return {"n": 0, "verdict": "no_trades"}
    total = sum(pnls)
    avg = total / n
    wins = sum(1 for x in pnls if x > 0)
    lo, hi = pct(pnls, 0.05), pct(pnls, 0.95)
    without_best3 = sum(sorted(pnls)[:-3]) if n > 3 else 0.0
    if avg <= 0 and n >= 5:
        verdict = "negative"
    elif n < 30:
        verdict = "insufficient_sample"
    elif lo is not None and hi is not None and lo <= 0 <= hi:
        verdict = "range_includes_zero"
    elif without_best3 <= 0:
        verdict = "fragile_without_best3"
    else:
        verdict = "positive_edge"
    return {
        "n": n,
        "total_usdt": round(total, 4),
        "avg_usdt": round(avg, 4),
        "win_rate": round(wins / n, 3),
        "p05_usdt": round(lo, 4) if lo is not None else None,
        "p95_usdt": round(hi, 4) if hi is not None else None,
        "without_best3_usdt": round(without_best3, 4),
        "verdict": verdict,
    }


def main():
    book = load(ORDERS, {"orders": []})
    orders = book.get("orders") or []
    by = {}
    for o in orders:
        if "realized_pnl_usdt" not in o:
            continue
        sleeve = o.get("sleeve") or "brain"
        by.setdefault(sleeve, []).append(float(o["realized_pnl_usdt"]))
    prev = load(OUT, {}).get("sleeves") or {}
    out = {
        "ts": int(time.time() * 1000),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sleeves": {s: judge(v) for s, v in sorted(by.items())},
    }
    out["all"] = judge([x for v in by.values() for x in v])
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    if ALERT:
        lines = []
        for s, r in out["sleeves"].items():
            bad = r.get("verdict") in ("negative", "fragile_without_best3", "range_includes_zero")
            changed = (prev.get(s) or {}).get("verdict") != r.get("verdict")
            if bad and changed:
                lines.append("⚠️ حكم الحافة — %s: %s (n=%s، متوسط %.2f$، بدون أفضل 3: %.2f$)"
                             % (s, VERDICT_AR.get(r["verdict"], r["verdict"]), r["n"],
                                r.get("avg_usdt") or 0, r.get("without_best3_usdt") or 0))
        if lines:
            print("\n".join(lines))


if __name__ == "__main__":
    main()
