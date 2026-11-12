"""Score stored brain decisions against later prices (pure, no network, no clock).

The brain writes ``state/decisions/*.json`` with a verdict per candidate
(``ENTER | WATCH | AVOID | HOLD``). Until now nothing measured whether those
verdicts were right: ``edge_check`` only judges *closed paper trades*, so an
AVOID that saved a -90% rug — or an AVOID that refused a 5x — was invisible.

This module turns decisions into measurements:

- **entry price** = the price stored on the decision row when the brain recorded
  one, otherwise the tape mark nearest the decision time inside ``ENTRY_TOL_MS``.
  A mark borrowed from the tape is labelled as such; without either the decision
  is ``no_entry_mark`` — never scored on a guessed price.
- **forward return** = first tape mark at or after ``decision_time + horizon``
  (within a grace window), as a percentage change from the entry price.
- **status vocabulary**: ``scored`` | ``pending`` (horizon not elapsed yet) |
  ``missing`` (elapsed but no mark inside the grace) | ``no_entry_mark`` |
  ``no_decision_time``. Unknown stays unknown: nothing is filled with 0.
- **survival** = current liquidity of every decided mint, classified
  ``dead`` (< ``DEAD_LIQ_USD``), ``alive``, or ``unknown`` (no readable book).

No result in here can be produced without a stored or captured price, so the
module cannot fabricate a win rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

HORIZONS_H: tuple[tuple[str, float], ...] = (
    ("15m", 0.25),
    ("1h", 1.0),
    ("6h", 6.0),
    ("24h", 24.0),
)

ENTRY_TOL_MS = 20 * 60 * 1000          # a tape mark further away than this is not an entry
DEAD_LIQ_USD = 1_000.0                 # below this a book cannot absorb a 25 USDT exit
MIN_GRACE_MS = ENTRY_TOL_MS

STATUS_SCORED = "scored"
STATUS_PENDING = "pending"
STATUS_MISSING = "missing"
STATUS_NO_ENTRY = "no_entry_mark"
STATUS_NO_TIME = "no_decision_time"
STATUS_NO_HORIZON = "no_horizon"

# decision-row price fields, in priority order (brain turns used several names)
PRICE_FIELDS = ("price_usd_at_decision", "price_at_decision", "live_price_usd", "price_usd")


def to_float(value: Any) -> Optional[float]:
    """Strict positive float from number or numeric string; anything else is None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v > 0 else None
    if isinstance(value, str):
        try:
            v = float(value.strip().replace(",", ""))
        except ValueError:
            return None
        return v if v > 0 else None
    return None


def to_float_nonneg(value: Any) -> Optional[float]:
    """Non-negative float (0.0 is a real reading: an empty book), else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v >= 0 else None
    if isinstance(value, str):
        try:
            v = float(value.strip().replace(",", ""))
        except ValueError:
            return None
        return v if v >= 0 else None
    return None


def iso_to_ms(value: Any) -> Optional[int]:
    """Epoch ms from ISO-8601 ('Z' allowed) or an epoch number (s or ms)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        return int(v * 1000) if v < 1e11 else int(v)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        pass
    else:
        return int(v * 1000) if v < 1e11 else int(v)
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def ms_to_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Decision:
    decision_id: str
    symbol: str
    mint: str
    verdict: str
    decision_ts_ms: Optional[int]
    price_at_decision: Optional[float]
    price_source: Optional[str]


@dataclass(frozen=True)
class Mark:
    mint: str
    ts_ms: int
    price: Optional[float]
    liquidity_usd: Optional[float]
    chain: Optional[str] = None


def decision_time_ms(doc: dict) -> Optional[int]:
    """When the decision was made: probe time first (that is when prices were read).

    Older brain turns used several field names (``created_utc``, ``generated_at_utc``,
    ``at``); they are read only when they parse, so a weird value cannot fake a time.
    """
    for key in ("probed_at", "written_at", "created_at", "generated_at_utc",
                "created_utc", "ts", "generated_at", "at"):
        ms = iso_to_ms(doc.get(key))
        if ms:
            return ms
    return None


def default_decision_id(doc: dict, fallback: str = "") -> str:
    did = doc.get("decision_id") or doc.get("turn") or fallback
    return str(did)


def extract_decisions(doc: dict, fallback_id: str = "") -> list[Decision]:
    """All decision rows of one decision document; malformed rows are skipped."""
    decision_id = default_decision_id(doc, fallback_id)
    ts = decision_time_ms(doc)
    out: list[Decision] = []
    rows = doc.get("decisions")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        mint = row.get("token_address") or row.get("mint") or ""
        verdict = str(row.get("verdict") or "").strip().upper()
        if not mint or not verdict:
            continue
        price = None
        source = None
        for field in PRICE_FIELDS:
            price = to_float(row.get(field))
            if price:
                source = field
                break
        if price is None:
            live = row.get("live")
            if isinstance(live, dict):
                price = to_float(live.get("price"))
                source = "live.price" if price else None
            elif isinstance(live, (int, float)):
                price = to_float(live)
                source = "live"
        metrics = row.get("metrics")
        if price is None and isinstance(metrics, dict):
            price = to_float(metrics.get("price_usd"))
            source = "metrics.price_usd" if price else None
        out.append(Decision(
            decision_id=decision_id,
            symbol=str(row.get("symbol") or ""),
            mint=str(mint),
            verdict=verdict,
            decision_ts_ms=ts,
            price_at_decision=price,
            price_source=source,
        ))
    return out


def marks_by_mint(marks: Iterable[Mark]) -> dict[str, list[Mark]]:
    """Group marks per mint, sorted by time, keeping only rows that carry a price."""
    grouped: dict[str, list[Mark]] = {}
    for m in marks:
        if m.price and m.price > 0 and m.ts_ms > 0:
            grouped.setdefault(m.mint, []).append(m)
    for lst in grouped.values():
        lst.sort(key=lambda m: m.ts_ms)
    return grouped


def entry_for(dec: Decision, marks: Sequence[Mark], tol_ms: int = ENTRY_TOL_MS) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """(price, source, delta_ms). Stored decision price wins; else nearest tape mark."""
    if dec.price_at_decision:
        return dec.price_at_decision, dec.price_source or "decision_row", 0
    if dec.decision_ts_ms is None:
        return None, None, None
    best: Optional[Mark] = None
    for m in marks:
        delta = abs(m.ts_ms - dec.decision_ts_ms)
        if delta > tol_ms:
            continue
        if best is None or delta < abs(best.ts_ms - dec.decision_ts_ms):
            best = m
    if best is None:
        return None, None, None
    return best.price, "tape_nearest_mark", best.ts_ms - dec.decision_ts_ms


def grace_ms_for(horizon_h: float) -> int:
    return int(max(MIN_GRACE_MS, horizon_h * 3_600_000 * 0.25))


def forward_for(
    dec: Decision, marks: Sequence[Mark], horizon_h: float, now_ms: int
) -> tuple[Optional[Mark], str]:
    """First mark at/after decision+horizon, else pending/missing."""
    if dec.decision_ts_ms is None:
        return None, STATUS_NO_TIME
    target = dec.decision_ts_ms + int(horizon_h * 3_600_000)
    grace = grace_ms_for(horizon_h)
    for m in marks:
        if m.ts_ms >= target:
            if m.ts_ms <= target + grace:
                return m, STATUS_SCORED
            break
    if now_ms < target + grace:
        return None, STATUS_PENDING
    return None, STATUS_MISSING


def score_decision(dec: Decision, marks: Sequence[Mark], now_ms: int,
                   horizons: Sequence[tuple[str, float]] = HORIZONS_H) -> dict:
    """One decision row: entry, per-horizon status and return percentages."""
    entry, entry_source, entry_delta = entry_for(dec, marks)
    row: dict[str, Any] = {
        "decision_id": dec.decision_id,
        "symbol": dec.symbol,
        "mint": dec.mint,
        "verdict": dec.verdict,
        "decision_ts": ms_to_iso(dec.decision_ts_ms),
        "decision_ts_ms": dec.decision_ts_ms,
        "entry_price": entry,
        "entry_source": entry_source,
        "entry_delta_s": round(entry_delta / 1000.0, 1) if entry_delta is not None else None,
        "horizons": {},
    }
    for key, hours in horizons:
        mark, status = forward_for(dec, marks, hours, now_ms)
        if status != STATUS_SCORED or mark is None:
            row["horizons"][key] = {"status": status}
            continue
        ret = None
        if entry and mark.price:
            ret = (mark.price / entry - 1.0) * 100.0
        row["horizons"][key] = {
            "status": STATUS_SCORED if ret is not None else STATUS_NO_ENTRY,
            "ret_pct": round(ret, 3) if ret is not None else None,
            "mark_ts": ms_to_iso(mark.ts_ms),
            "mark_elapsed_h": round((mark.ts_ms - dec.decision_ts_ms) / 3_600_000, 2) if dec.decision_ts_ms else None,
            "mark_liquidity_usd": mark.liquidity_usd,
        }
    return row


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    vs = sorted(values)
    k = (len(vs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(vs) - 1)
    if lo == hi:
        return vs[lo]
    return vs[lo] + (vs[hi] - vs[lo]) * (k - lo)


def _round(value: Optional[float], ndigits: int = 3) -> Optional[float]:
    return None if value is None else round(value, ndigits)


def _stats(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median_pct": _round(quantile(ordered, 0.5)),
        "mean_pct": _round(sum(ordered) / len(ordered)),
        "p25_pct": _round(quantile(ordered, 0.25)),
        "p75_pct": _round(quantile(ordered, 0.75)),
        "worst_pct": _round(min(ordered)),
        "best_pct": _round(max(ordered)),
    }


def summarize(rows: Sequence[dict], horizons: Sequence[str] = tuple(k for k, _ in HORIZONS_H)) -> dict:
    """Per verdict and horizon: how many scored, pending, missing, and the return spread."""
    out: dict[str, Any] = {}
    verdicts = sorted({r["verdict"] for r in rows})
    for verdict in verdicts:
        subset = [r for r in rows if r["verdict"] == verdict]
        block: dict[str, Any] = {"decisions": len(subset), "horizons": {}}
        for key in horizons:
            statuses: dict[str, int] = {}
            rets: list[float] = []
            for r in subset:
                h = (r.get("horizons") or {}).get(key) or {}
                st = h.get("status") or STATUS_MISSING
                statuses[st] = statuses.get(st, 0) + 1
                if st == STATUS_SCORED and h.get("ret_pct") is not None:
                    rets.append(float(h["ret_pct"]))
            block["horizons"][key] = {"statuses": statuses, **_stats(rets)}
        out[verdict] = block
    return out


def since_decision(rows: Sequence[dict], latest: dict[str, Mark], now_ms: int) -> dict:
    """Return from entry to the newest mark, per verdict — the coarse 'what happened'.

    Only decisions with an entry price and a newer mark are included; the elapsed
    span is reported so a 40h view is never mistaken for a 1h one.
    """
    out: dict[str, Any] = {}
    for verdict in sorted({r["verdict"] for r in rows}):
        rets: list[float] = []
        elapsed: list[float] = []
        down20 = 0
        for r in rows:
            if r["verdict"] != verdict or not r.get("entry_price") or not r.get("decision_ts_ms"):
                continue
            mark = latest.get(r["mint"])
            if mark is None or not mark.price or mark.ts_ms <= r["decision_ts_ms"]:
                continue
            ret = (mark.price / r["entry_price"] - 1.0) * 100.0
            rets.append(ret)
            elapsed.append((mark.ts_ms - r["decision_ts_ms"]) / 3_600_000)
            if ret <= -20.0:
                down20 += 1
        block = _stats(rets)
        block["below_minus20_pct"] = round(down20 / len(rets), 3) if rets else None
        block["median_elapsed_h"] = _round(quantile(elapsed, 0.5), 2)
        out[verdict] = block
    return out


def classify_survival(liquidity_usd: Optional[float]) -> str:
    if liquidity_usd is None:
        return "unknown"
    return "dead" if liquidity_usd < DEAD_LIQ_USD else "alive"


def survival(rows: Sequence[dict], latest: dict[str, Mark]) -> dict:
    """Per verdict: how many decided mints still have a book that can be exited."""
    out: dict[str, Any] = {}
    for verdict in sorted({r["verdict"] for r in rows}):
        counts = {"dead": 0, "alive": 0, "unknown": 0}
        for r in rows:
            if r["verdict"] != verdict:
                continue
            mark = latest.get(r["mint"])
            counts[classify_survival(mark.liquidity_usd if mark else None)] += 1
        n_known = counts["dead"] + counts["alive"]
        out[verdict] = {
            **counts,
            "n": sum(counts.values()),
            "dead_share_of_known": round(counts["dead"] / n_known, 3) if n_known else None,
        }
    return out


def latest_marks(marks: Iterable[Mark]) -> dict[str, Mark]:
    """Newest mark per mint (a mark with no price still records liquidity)."""
    newest: dict[str, Mark] = {}
    for m in marks:
        cur = newest.get(m.mint)
        if cur is None or m.ts_ms >= cur.ts_ms:
            newest[m.mint] = m
    return newest


def parse_tape(lines: Iterable[str]) -> list[Mark]:
    """Read tape lines ('{json}' per line). Corrupt lines are skipped, not fatal."""
    import json

    marks: list[Mark] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = iso_to_ms(rec.get("ts"))
        payload = rec.get("marks")
        if ts is None or not isinstance(payload, dict):
            continue
        for mint, mark in payload.items():
            if not isinstance(mark, dict):
                continue
            marks.append(Mark(
                mint=str(mint),
                ts_ms=ts,
                price=to_float(mark.get("px")),
                liquidity_usd=to_float_nonneg(mark.get("liq")),
                chain=mark.get("chain"),
            ))
    return marks


def horizon_keys() -> tuple[str, ...]:
    return tuple(k for k, _ in HORIZONS_H)


def window_bounds(values: Sequence[Optional[int]]) -> dict:
    known = [v for v in values if v]
    if not known:
        return {"oldest": None, "newest": None}
    return {"oldest": ms_to_iso(min(known)), "newest": ms_to_iso(max(known))}


def iso_now(now_ms: int) -> str:
    return ms_to_iso(now_ms) or ""


def horizon_target_iso(decision_ts_ms: int, hours: float) -> str:
    return (datetime.fromtimestamp(decision_ts_ms / 1000, tz=timezone.utc)
            + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
