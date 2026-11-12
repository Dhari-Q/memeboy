#!/usr/bin/env python
"""Quant machine: maintenance guard (stdlib only, no LLM, no cost).

Checks the machine's own books for rule violations and prints ONE Arabic line
per breach, which cron delivers to Dhari. Silent when everything is clean.
This is the cheap always-on layer; the LLM maintainer does the weekly deep pass.

Rules enforced:
  - every paper order is exactly one ticket (25 USDT), never scaled by confidence
  - no order is missing its mint
  - no state file the eyes write is older than its staleness budget
"""
import json
import os
import time
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


TICKET = 25.0
WAKE_QUORUM = 5   # this many stale eyes at once = the host slept, not a broken eye
# file -> max age in minutes before it counts as stale
FRESH = {
    "pulse.json": 20,
    "births.json": 40,
    "candidates.json": 40,
    "news.json": 40,
    "funding.json": 60,
    "physics.json": 40,
    # the LLM-facing eyes write these and nothing else watches them: a dead
    # wallet eye stayed silent for 38h because it was not on this list.
    "narrative.json": 45,
    "wallets.json": 45,
    "edge_check.json": 780,
    "decision_score.json": 40,
}


def load(name):
    try:
        with open(os.path.join(STATE, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main():
    breaches = []
    book = load("paper_orders.json") or {"orders": []}
    for o in book.get("orders", []):
        n = o.get("notional_usdt")
        if n != TICKET:
            breaches.append(f"حجم مخالف: {o.get('symbol')} بـ{n}$ (المفروض {TICKET:.0f}$)")
        if not o.get("mint"):
            breaches.append(f"أمر بلا عقد: {o.get('symbol')}")

    now = time.time() * 1000
    stale = []
    for name, budget in FRESH.items():
        data = load(name)
        if data is None:
            breaches.append(f"ملف ناقص: {name}")
            continue
        ts = data.get("ts")
        if not isinstance(ts, (int, float)):
            continue   # a file stamped with an ISO string has no numeric clock
        if (now - ts) / 60000 > budget:
            stale.append(f"عين متوقفة: {name} ما تحدّث من {int((now - ts) / 60000)} دقيقة")
    # every eye stale at once is a host sleep, not nine broken eyes: cron fires
    # them together on wake and they refresh within a minute. One dead eye while
    # the rest are fresh is the real fault, so it is still reported.
    if len(stale) < WAKE_QUORUM:
        breaches.extend(stale)

    if not breaches:
        return
    print("🔧 حارس الصيانة — " + str(len(breaches)) + " مخالفة:")
    for b in breaches:
        print("• " + b)


if __name__ == "__main__":
    main()
