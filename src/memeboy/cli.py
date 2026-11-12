#!/usr/bin/env python
"""memeboy — mission control for your paper-trading agent.

Commands:
  memeboy init          create MEMEBOY_HOME/state, seed config
  memeboy tick <name>   run one watchdog once (eyes/* or book/*)
  memeboy run-all       one full sweep: eyes then book
  memeboy cockpit       rebuild state/cockpit.html (single self-contained page)
  memeboy edge          print the edge-check verdict table
  memeboy status        one-screen health: freshness, book, verdict
"""
import argparse
import json
import os
import runpy
import sys
import time

PKG = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(PKG))  # allow `memeboy` import from src layout

MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
STATE = os.path.join(MEMEBOY_HOME, "state")

WATCHDOGS = {
    "eyes": ["pulse", "news_eye", "discovery_eye", "birth_eye", "funding_eye",
             "physics_eye", "narrative_eye", "wallet_eye", "brain_gate"],
    "book": ["paper_trader", "birth_lottery", "decision_score", "exit_engine",
             "edge_check", "maintenance_guard", "alerter", "report_bot"],
}


def load_json(name, default=None):
    try:
        with open(os.path.join(STATE, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def run_watchdog(group, name):
    path = os.path.join(PKG, group, "%s.py" % name)
    if not os.path.exists(path):
        print("missing: %s/%s" % (group, name))
        return 1
    os.environ["MEMEBOY_HOME"] = MEMEBOY_HOME
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        print("ERROR %s/%s: %s: %s" % (group, name, type(e).__name__, e))
        return 1


def cmd_init(_args):
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(os.path.join(STATE, "decisions"), exist_ok=True)
    print("MEMEBOY_HOME =", MEMEBOY_HOME)
    print("state ready. run: memeboy run-all   then   memeboy status")
    return 0


def cmd_tick(args):
    for g, names in WATCHDOGS.items():
        if args.name in names:
            return run_watchdog(g, args.name)
    print("unknown watchdog:", args.name)
    print("known:", ", ".join(n for v in WATCHDOGS.values() for n in v))
    return 1


def cmd_run_all(_args):
    rc = 0
    for g in ("eyes", "book"):
        for name in WATCHDOGS[g]:
            r = run_watchdog(g, name)
            rc = rc or r
            print("%-5s %-18s %s" % (g, name, "ok" if r == 0 else "FAIL"))
    return rc


def cmd_status(_args):
    now = time.time() * 1000
    perf = load_json("performance.json") or {}
    edge = load_json("edge_check.json") or {}
    pulse = load_json("pulse.json") or {}
    print("=== MEMEBOY STATUS ===")
    age = (now - pulse.get("ts", 0)) / 60000 if pulse else None
    print("pulse:", "fresh (%.0f min)" % age if age is not None and age < 20 else "STALE",
          "| BTC:", (pulse.get("feeds") or {}).get("binance_BTCUSDT", {}).get("price"))
    print("book :", perf.get("n_open"), "open |", perf.get("n_closed"), "closed |",
          "realized", perf.get("realized_pnl_usdt"), "USDT |",
          "wins", perf.get("wins"), "losses", perf.get("losses"))
    for s, r in (edge.get("sleeves") or {}).items():
        print("edge :", s, "->", r.get("verdict"), "| n", r.get("n"),
              "| avg", r.get("avg_usdt"), "| w/o best3", r.get("without_best3_usdt"))
    if not perf:
        print("(no book yet — run: memeboy run-all)")
    return 0


def cmd_cockpit(_args):
    from memeboy.report import cockpit
    return cockpit.build(STATE)


def cmd_edge(_args):
    from memeboy.report import cockpit
    return cockpit.edge_table(STATE)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="memeboy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    t = sub.add_parser("tick"); t.add_argument("name"); t.set_defaults(fn=cmd_tick)
    sub.add_parser("run-all").set_defaults(fn=cmd_run_all)
    sub.add_parser("cockpit").set_defaults(fn=cmd_cockpit)
    sub.add_parser("edge").set_defaults(fn=cmd_edge)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
