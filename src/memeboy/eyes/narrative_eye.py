#!/usr/bin/env python
"""Quant machine: narrative / ecosystem eye (stdlib only, no LLM).

Reads the feeds the machine already pays for (news, candidates, births, pulse)
and writes state/narrative.json:
  - hot_keywords: what the tape is talking about (counted, not guessed)
  - hot_dex: which launchpad / DEX the current batch is being born on
  - risk_regime: the same BTC 75k band the brain already uses
  - summary_ar: one short Arabic line a human can read

Silent watchdog: prints nothing. The brain reads the file.
"""
import json
import os
import re
import time
from collections import Counter
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


OUT = os.path.join(STATE, "narrative.json")

# keyword -> regex (word-ish, case-insensitive)
TAXONOMY = {
    "ai": r"\b(ai|agent|agents|gpt|llm|neural|bot|bots)\b",
    "dog": r"\b(dog|dogs|doge|shiba|inu|pup|puppy|bonk)\b",
    "cat": r"\b(cat|cats|kitty|kitten|meow|feline)\b",
    "frog": r"\b(frog|pepe|kek)\b",
    "political": r"\b(trump|maga|biden|elon|musk|kennedy|obama)\b",
    "solana": r"\b(solana|sol|jupiter|raydium|pumpswap)\b",
    "base": r"\b(base|coinbase)\b",
    "bnb": r"\b(bnb|binance|four\.meme)\b",
    "robinhood": r"\b(robinhood)\b",
    "etf": r"\b(etf|blackrock|vanguard)\b",
    "fed": r"\b(fed|rate|rates|inflation|cpi)\b",
    "launchpad": r"\b(pump\.fun|letsbonk|believe|moonshot|bags|launchlab)\b",
    "stablecoin": r"\b(stablecoin|usdt|usdc|treasury)\b",
}


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def count_keywords(texts):
    out = Counter()
    for t in texts:
        low = (t or "").lower()
        for name, pat in TAXONOMY.items():
            if re.search(pat, low):
                out[name] += 1
    return out


def main():
    news = load(os.path.join(STATE, "news.json"), {})
    cands = load(os.path.join(STATE, "candidates.json"), {})
    births = load(os.path.join(STATE, "births.json"), {})
    pulse = load(os.path.join(STATE, "pulse.json"), {})

    items = news.get("items") or []
    texts = []
    for it in items[:120]:
        texts.append(it.get("title") or "")
        texts.append((it.get("desc") or "")[:200])
    for c in (cands.get("candidates") or []):
        texts.append(c.get("symbol") or "")
        texts.append(c.get("name") or "")
    for b in (births.get("births") or []):
        texts.append(b.get("symbol") or "")
    kw = count_keywords(texts)

    dex = Counter()
    for b in (births.get("births") or []):
        dex[b.get("dex") or "unknown"] += 1
    for c in (cands.get("candidates") or []):
        dex[c.get("dex") or "unknown"] += 1

    feeds = pulse.get("feeds") or {}
    btc = float((feeds.get("binance_BTCUSDT") or {}).get("price") or 0)
    sol = float((feeds.get("binance_SOLUSDT") or {}).get("price") or 0)
    pepe = float((feeds.get("binance_PEPEUSDT") or {}).get("price") or 0)
    # same band the brain uses: 75k +/- 0.15%
    low, high = 75000 * 0.9985, 75000 * 1.0015
    if btc <= 0:
        regime = "unknown"
    elif btc < low:
        regime = "risk-off"
    elif btc > high:
        regime = "calm"
    else:
        regime = "chop"

    top_kw = [{"name": k, "hits": v} for k, v in kw.most_common(8)]
    top_dex = [{"dex": d, "count": n} for d, n in dex.most_common(3)]
    summary = "ريجيم %s | BTC %.0f | أعلى سرد: %s | أنشط منصة: %s" % (
        regime, btc,
        "، ".join(x["name"] for x in top_kw[:3]) or "لا شيء",
        ", ".join("%s(%d)" % (x["dex"], x["count"]) for x in top_dex[:2]) or "لا شيء",
    )
    out = {
        "ts": int(time.time() * 1000),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "risk_regime": regime,
        "prices": {"btc": btc, "sol": sol, "pepe": pepe},
        "hot_keywords": top_kw,
        "hot_dex": top_dex,
        "summary_ar": summary,
        "news_items": len(items),
        "candidates": len(cands.get("candidates") or []),
        "births": len(births.get("births") or []),
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    # silent


if __name__ == "__main__":
    main()
