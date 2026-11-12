#!/usr/bin/env python
"""Quant machine: report bot. Composes a short Arabic status report from the
machine's own state files and prints it (cron delivers to Dhari)."""
import json
import os
import time
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)




def load(name, default):
    try:
        with open(os.path.join(STATE, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def fmt_usd(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "?"


def main():
    perf = load("performance.json", {})
    mind = load("mind.json", {})
    pulse = load("pulse.json", {})
    orders = load("paper_orders.json", {"orders": []}).get("orders", [])

    lines = ["📊 تقرير الآلة — " + time.strftime("%Y-%m-%d %H:%M")]
    feeds = (pulse.get("feeds") or {})
    btc = feeds.get("binance_BTCUSDT", {}).get("price") if isinstance(feeds.get("binance_BTCUSDT"), dict) else None
    sol = feeds.get("binance_SOLUSDT", {}).get("price") if isinstance(feeds.get("binance_SOLUSDT"), dict) else None
    if btc or sol:
        lines.append(f"السوق: بيتكوين {fmt_usd(btc)}$ · سولانا {fmt_usd(sol)}$")
    m = (mind.get("mind") or {})
    if m.get("regime"):
        lines.append(f"الريجيم: {m.get('regime')} · {m.get('aggression', '')}")
    c = (mind.get("counters") or {})
    lines.append(f"جولات العقل: {c.get('brain_turns', 0)} · قرارات: {c.get('decisions_logged', 0)}")
    if perf:
        lines.append(f"مراكز مفتوحة: {perf.get('n_open', 0)} {perf.get('open_positions') or ''}")
        lines.append(f"صفقات مغلقة: {perf.get('n_closed', 0)} · ربح/خسارة محقق: {fmt_usd(perf.get('realized_pnl_usdt', 0))}$")
        wr = perf.get("win_rate")
        if wr is not None:
            lines.append(f"نسبة الرابحة: {int(wr * 100)}٪؜")
        hist = (perf.get("history") or [])[-5:]
        for h in hist:
            sign = "🟢" if (h.get("pnl") or 0) > 0 else "🔴"
            lines.append(f"{sign} {h.get('symbol')} · {h.get('reason')} · {fmt_usd(h.get('pnl'))}$")
    else:
        lines.append("لا صفقات بعد — الآلة تراقب وتتعلم.")
    pend = [o for o in orders if o.get("status") == "pending_price"]
    if pend:
        lines.append(f"⏳ أوامر بانتظار سعر: {len(pend)}")
    lines.append("كل الأرقام ورقية (تجريبية) — لا فلوس حقيقية.")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
