#!/usr/bin/env python
"""Quant machine: score the brain's decisions against later prices (stdlib only, no LLM).

The brain already records a verdict per candidate in state/decisions/*.json, but
nothing measured whether those verdicts were right. This job builds the missing
evidence:

1. It captures a price/liquidity TAPE for every mint the brain decided on
   (state/decision_tape.jsonl, append-only, DexScreener best pair, cache-busted),
   one line per 10-minute bucket — so forward returns can be computed from marks
   that were actually observed, never from a re-fetched "current" value.
2. It scores each decision at +15m/+1h/+6h/+24h from the tape, plus the coarse
   return from entry to the newest mark, plus survival (is there still a book
   that can absorb an exit).
3. Everything lands in state/decision_score.json for the dashboard/edge review.

Honesty rules: a missing price stays "pending"/"missing"/"no_entry_mark"; it is
never filled with 0. Writes are atomic; the script prints NOTHING on success
(the cron engine delivers stdout, and this eye must stay silent).
"""
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


from memeboy.lib.decision_score_core import (  # noqa: E402
    HORIZONS_H,
    Mark,
    default_decision_id,
    extract_decisions,
    horizon_keys,
    iso_now,
    latest_marks,
    marks_by_mint,
    parse_tape,
    score_decision,
    since_decision,
    summarize,
    survival,
    window_bounds,
)
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


DEC_DIR = os.path.join(STATE, "decisions")
TAPE = os.path.join(STATE, "decision_tape.jsonl")
OUT = os.path.join(STATE, "decision_score.json")

BUCKET_MS = 10 * 60 * 1000        # one tape line per 10-minute bucket
LOOKBACK_MS = 96 * 3_600_000      # mark mints decided on within 4 days
TAPE_KEEP_MS = 4 * 24 * 3_600_000  # a pruned tape still covers the 24h horizon + slack
TAPE_PRUNE_OVER = 700             # ~4 days of 10-minute lines before rewriting the tape
MAX_MINTS = 450                   # hard cap: bounded run time, newest decisions first
BATCH = 30                        # DexScreener tokens endpoint limit
SPACING_S = 1.0
BUDGET_S = 75.0                   # stop fetching; the rest is honestly "missing"
UA = "quant-research-decision-score/0.1 (paper only)"


def atomic_write(path, obj):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_decisions(errors):
    decisions = []
    docs = 0
    for path in sorted(glob.glob(os.path.join(DEC_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:  # a corrupt decision file must not kill the scoreboard
            errors.append("%s: unreadable decision file (%s)" % (os.path.basename(path), exc))
            continue
        if not isinstance(doc, dict):
            errors.append("%s: decision file is not an object" % os.path.basename(path))
            continue
        docs += 1
        fallback = os.path.splitext(os.path.basename(path))[0]
        try:
            decisions.extend(extract_decisions(doc, default_decision_id(doc, fallback)))
        except Exception as exc:
            errors.append("%s: extract failed (%s)" % (os.path.basename(path), exc))
    return docs, decisions


def read_tape():
    try:
        with open(TAPE, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return [], 0
    return lines, len(lines)


def tape_last_bucket(lines):
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        bucket = rec.get("bucket")
        if isinstance(bucket, int):
            return bucket
        ts = rec.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            return int(ts) // BUCKET_MS
    return None


def prune_tape(lines, now_ms):
    """Drop lines older than TAPE_KEEP_MS; returns (kept_lines, dropped)."""
    kept, dropped = [], 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
            ts = int(rec.get("ts") or 0)
        except (ValueError, TypeError):
            dropped += 1
            continue
        if ts and ts < now_ms - TAPE_KEEP_MS:
            dropped += 1
            continue
        kept.append(stripped)
    return kept, dropped


def mint_priority(decisions):
    """Unique mints with their newest decision time, newest first."""
    seen = {}
    for d in decisions:
        if not d.mint or not d.decision_ts_ms:
            continue
        prev = seen.get(d.mint)
        if prev is None or d.decision_ts_ms > prev:
            seen[d.mint] = d.decision_ts_ms
    return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)


def fetch_batch(mints):
    url = ("https://api.dexscreener.com/latest/dex/tokens/"
           + ",".join(mints) + "?cb=" + str(int(time.time() * 1000)))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def marks_from_pairs(payload, wanted):
    """Best (highest-liquidity) pair per mint. Only pairs where the mint is the base
    token are accepted: DexScreener's priceUsd is the base token's price, so a quote-
    side match would invert the price."""
    best = {}
    for pair in payload.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        base = (pair.get("baseToken") or {}).get("address")
        if not base or base not in wanted:
            continue
        liq = (pair.get("liquidity") or {}).get("usd")
        try:
            liq = float(liq) if liq is not None else None
        except (TypeError, ValueError):
            liq = None
        price = pair.get("priceUsd")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        cur = best.get(base)
        cur_liq = (cur or {}).get("liq") or 0.0
        if cur is None or (liq or 0.0) > cur_liq:
            best[base] = {"px": price, "liq": liq, "chain": pair.get("chainId"),
                          "pair": pair.get("pairAddress")}
    return best


def capture_tape(mints, now_ms, errors):
    """Fetch marks for the given mints and append one line. Returns (line|None, info)."""
    info = {"mints_requested": len(mints), "calls": 0, "calls_failed": 0, "priced": 0}
    if not mints:
        return None, info
    started = time.time()
    marks = {}
    for i in range(0, len(mints), BATCH):
        if time.time() - started > BUDGET_S:
            info["aborted_on_budget"] = True
            errors.append("fetch budget hit after %d of %d mints"
                          % (i, len(mints)))
            break
        chunk = mints[i:i + BATCH]
        info["calls"] += 1
        try:
            payload = fetch_batch(chunk)
        except Exception as exc:
            info["calls_failed"] += 1
            errors.append("dexscreener batch %d failed: %s" % (i // BATCH + 1, exc))
            time.sleep(SPACING_S)
            continue
        marks.update(marks_from_pairs(payload, set(chunk)))
        time.sleep(SPACING_S)
    if not marks:
        return None, info
    info["priced"] = sum(1 for m in marks.values() if m.get("px"))
    line = json.dumps({
        "bucket": now_ms // BUCKET_MS,
        "ts": now_ms,
        "source": "dexscreener_best_pair",
        "marks": marks,
    }, ensure_ascii=False, sort_keys=True)
    with open(TAPE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line, info


def main():
    errors = []
    now_ms = int(time.time() * 1000)
    docs, decisions = load_decisions(errors)

    lines, _ = read_tape()
    if len(lines) > TAPE_PRUNE_OVER:
        kept, dropped = prune_tape(lines, now_ms)
        if dropped:
            tmp = "%s.%d.rewrite" % (TAPE, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + ("\n" if kept else ""))
            os.replace(tmp, TAPE)
            lines = [ln + "\n" for ln in kept]

    window_mints = [mint for mint, ts in mint_priority(decisions)
                    if ts >= now_ms - LOOKBACK_MS][:MAX_MINTS]

    bucket = now_ms // BUCKET_MS
    fetch_info = {"skipped": "no mints in window"}
    if window_mints:
        if tape_last_bucket(lines) == bucket:
            fetch_info = {"skipped": "tick bucket already captured", "bucket": bucket}
        else:
            _, fetch_info = capture_tape(window_mints, now_ms, errors)
            fetch_info["bucket"] = bucket

    # re-read (the append above is the newest evidence) and score offline
    lines, _ = read_tape()
    marks = parse_tape(lines)
    grouped = marks_by_mint(marks)
    rows = [score_decision(d, grouped.get(d.mint, []), now_ms) for d in decisions]
    latest = latest_marks(marks)

    scored = sum(1 for r in rows if r["entry_price"])
    out = {
        "ts": now_ms,
        "generated_at": iso_now(now_ms),
        "window": {
            "decision_files": docs,
            "decisions": len(decisions),
            "unique_mints": len({d.mint for d in decisions}),
            "mints_in_lookback": len(window_mints),
            **window_bounds([d.decision_ts_ms for d in decisions]),
        },
        "tape": {
            "lines": len(lines),
            "marks": len(marks),
            "mints": len(latest),
            "last_ts": iso_now(max((m.ts_ms for m in marks), default=0)) or None,
        },
        "fetch": fetch_info,
        "scoring": {
            "decisions_with_entry": scored,
            "decisions_without_entry": len(rows) - scored,
            "horizons": [k for k, _ in HORIZONS_H],
        },
        "verdicts": summarize(rows, horizon_keys()),
        "since_decision": since_decision(rows, latest, now_ms),
        "survival": survival(rows, latest),
        "rows": rows,
        "method": (
            "entry = price stored on the decision row, else nearest tape mark within 20m "
            "(labelled entry_source); forward = first tape mark at/after decision+horizon "
            "inside a grace of max(20m, 25% of horizon); survival: dead = best-pair "
            "liquidity < 1000 USD, unknown = no readable liquidity (mint unpriced by the "
            "venue or not fetched); unknown is never scored as a number"
        ),
        "errors": errors,
    }
    atomic_write(OUT, out)


if __name__ == "__main__":
    main()
