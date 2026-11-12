# 🎰 MEMEBOY

**The memecoin research agent that tells you when it's losing.**

Most "AI trading bots" sell you a dream. MEMEBOY runs 17 always-on watchdogs that
watch Solana memecoin births, rugs, funding, wash-trading and narrative — and an
**edge-check that audits its own strategy and calls it `negative` when it is.**
Paper money only. No wallet. No keys. No live order path — structurally: none exists.

```
pip install memeboy && memeboy init && memeboy run-all && memeboy status
```

![cockpit](docs/cockpit.gif)

## What it actually is

```
        ┌───────────────────── eyes (stdlib, silent, free APIs) ─────────────────────┐
        │ pulse   news   discovery   births   funding   physics   narrative   wallets│
        └───────────────┬───────────────────────────────────────────────────────────┘
                        ▼  state/*.json
        ┌───────────────────────── the book ─────────────────────────┐
        │ paper_trader → birth_lottery → exit_engine → edge_check    │
        │ (25$ tickets)  (gated)         (rug guard,  stop, trail,    │
        │                                 scale-out)  write-downs)   │
        └───────────────┬────────────────────────────────────────────┘
                        ▼
              cockpit.html  ·  status  ·  Arabic daily report
```

- **Honest by construction**: missing data shows as `gap`, never as zero. Unknown
  never passes a safety gate. The only winner in the first live sample was the
  $103k-FDV entry; the tool says so in the verdict table.
- **Real costs**: ~4% round trip (fees + slippage + priority) is deducted from
  every paper fill. Strategies that survive the cost table are worth attention.
- **Self-audit**: `memeboy edge` prints per-sleeve win rate, avg, and PnL
  **without the best 3 trades** — the classic luck filter.
- **26.1% of published agent skills contain vulnerabilities** (arXiv, 31k-skill
  study, 2026). MEMEBOY ships as plain stdlib Python you can read in one sitting,
  signed releases, and zero network egress beyond 5 public read-only APIs.

## Pro (coming) — $19/mo

Same brain, pointed at your watchlist: Telegram alerts on your positions,
multi-chain, smart-money watch, replay of decisions, hosted cockpit.
Free tier stays free forever, stays stdlib, and stays the audit surface.

## Why paper-first is the feature

The measured verdict of our own 4-day live paper run: the newborn "lottery" sleeve
**lost 23.6% per closed ticket** (1 win / 8) and got switched off by the tool
itself. That's the product: an agent you can *believe* because it can embarrass
itself.

MIT licensed. Not financial advice. Real money in memecoins mostly becomes
someone else's money — the machine that shows you this for free is the machine to trust.
