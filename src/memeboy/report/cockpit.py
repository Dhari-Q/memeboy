#!/usr/bin/env python
"""memeboy cockpit — one self-contained HTML page over state/*.json.

Rules (inherited from production): missing or corrupt state is shown as a GAP,
never as a zero; unrealized PnL is only computed from live marks when present;
stored counts that disagree with the book are surfaced, not reconciled.
"""
import json
import os
import time


def load(state_dir, name, default=None):
    try:
        with open(os.path.join(state_dir, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def fmt(x, nd=2):
    if x is None:
        return '<span class="gap">gap</span>'
    return ("%." + str(nd) + "f") % x


def edge_table(state_dir):
    e = load(state_dir, "edge_check.json") or {}
    rows = []
    for s, r in (e.get("sleeves") or {}).items():
        rows.append((s, r.get("verdict"), r.get("n"), r.get("avg_usdt"), r.get("without_best3_usdt")))
    print("generated:", e.get("generated_at", "—"))
    for s, v, n, a, w3 in rows:
        print("%-16s %-24s n=%-4s avg=%-8s w/o best3=%s" % (s, v, n, fmt(a), fmt(w3)))
    if not rows:
        print("(no edge check yet — run: memeboy tick edge_check)")
    return 0


def build(state_dir):
    perf = load(state_dir, "performance.json") or {}
    edge = load(state_dir, "edge_check.json") or {}
    pulse = load(state_dir, "pulse.json") or {}
    narr = load(state_dir, "narrative.json") or {}
    book = (load(state_dir, "paper_orders.json") or {}).get("orders") or []
    open_o = [o for o in book if o.get("status") == "filled"]

    feeds = pulse.get("feeds") or {}
    btc = (feeds.get("binance_BTCUSDT") or {}).get("price")
    sol = (feeds.get("binance_SOLUSDT") or {}).get("price")
    now = time.time() * 1000
    fresh = lambda ts: "ok" if ts and (now - ts) / 60000 < 45 else "stale"

    rows_html = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
        (s, v or "—", n if n is not None else "—",
         ("<span class='neg'>%s</span>" % w3) if (w3 or 0) < 0 else fmt(w3))
        for s, v, n, w3 in ((s, r.get("verdict"), r.get("n"), r.get("without_best3_usdt"))
                            for s, r in (edge.get("sleeves") or {}).items()))

    pos_html = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
        (o.get("symbol") or "?", o.get("sleeve") or "brain",
         fmt(o.get("entry_price_usd"), 6), o.get("placed_at") or "—")
        for o in open_o[:100])

    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>MEMEBOY cockpit</title>
<style>
body{font:14px/1.5 ui-monospace,monospace;background:#0a0d12;color:#d6e2f0;padding:24px}
h1{color:#ffd54a;font-size:18px} h2{font-size:13px;color:#7fa6c9;margin-top:24px;text-transform:uppercase}
table{border-collapse:collapse;width:100%%} td,th{padding:4px 10px;border-bottom:1px solid #1c2733;text-align:left}
.pos{color:#4ade80}.neg{color:#f87171}.gap{color:#64748b;font-style:italic}
.kpi{display:inline-block;margin:0 18px 8px 0;padding:8px 14px;background:#111823;border:1px solid #1c2733;border-radius:8px}
.kpi b{display:block;font-size:18px;color:#ffd54a}
.ok{color:#4ade80}.stale{color:#f87171}
.disclaimer{margin-top:28px;color:#64748b;font-size:12px}
</style></head><body dir="ltr">
<h1>🎰 MEMEBOY — paper cockpit</h1>
<div>
<span class="kpi"><b>%s</b>BTC <span class="%s">(%s)</span></span>
<span class="kpi"><b>%s</b>SOL <span class="%s">(%s)</span></span>
<span class="kpi"><b>%s</b>open</span>
<span class="kpi"><b>%s</b>closed</span>
<span class="kpi"><b>%s</b>realized USDT</span>
<span class="kpi"><b>%s / %s</b>wins / losses</span>
</div>
<h2>Edge verdict</h2>
<table><tr><th>sleeve</th><th>verdict</th><th>n</th><th>w/o best 3</th></tr>%s</table>
<h2>Open paper book (last 100)</h2>
<table><tr><th>symbol</th><th>sleeve</th><th>entry</th><th>placed</th></tr>%s</table>
<div class="disclaimer">Paper trading only. No wallet, no keys, no live orders — ever.
Simulated fills, simulated money. Not financial advice. Generated %s.
Regime: %s</div>
</body></html>""" % (
        btc or "gap", fresh((pulse.get("ts") or 0)), fresh((pulse.get("ts") or 0)),
        sol or "gap", fresh((pulse.get("ts") or 0)), fresh((pulse.get("ts") or 0)),
        perf.get("n_open", "gap"), perf.get("n_closed", "gap"),
        fmt(perf.get("realized_pnl_usdt")), perf.get("wins", "gap"), perf.get("losses", "gap"),
        rows_html or "<tr><td colspan=4 class='gap'>no edge data yet</td></tr>",
        pos_html or "<tr><td colspan=4 class='gap'>book empty</td></tr>",
        time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        narr.get("risk_regime") or "gap")

    out = os.path.join(state_dir, "cockpit.html")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, out)
    print("cockpit:", out, os.path.getsize(out), "bytes")
    return 0
