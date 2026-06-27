# How a Fortis Scan Works — From 3,300 Stocks to 15 Conviction Picks

*A plain-English walkthrough of the engine, written for The Fortis Agency.*

---

## The one-paragraph version

Twice every trading day we put the **entire liquid US stock market — roughly 3,300 companies — through a single funnel** that ends in a short list of fully-researched, fact-checked conviction picks. The funnel is deliberately **two-speed**: cheap, objective math touches *every* stock to find the ~35 that are genuinely moving; then expensive AI research — an institutional-grade analyst desk — is spent **only** on those 35. Each pick that survives carries a complete dossier: the one catalyst driving it, an institutional bull case, a stress-tested bear case written by a *different* AI, smart-money positioning, honest price levels, and a conviction grade. **Every number in every dossier is mechanically traced back to real data we collected that morning — anything we can't verify is deleted, not shown.** A name only appears on the dashboard if its dossier is complete and verified. If a scan can't produce at least 15 clean dossiers, we keep the last good board up rather than show you anything half-built.

---

## The funnel at a glance

```
   ~3,300 liquid US stocks        ← the whole investable market
            │
   ┌────────▼────────┐  STAGE 1   pure math on every stock        ~3 min, no AI
   │   FAST SCAN     │            measure: momentum, volume, RSI,
   └────────┬────────┘            52-week position, breakouts
            │
   ┌────────▼────────┐  STAGE 2   one composite score (0–100)     ~10 sec, no AI
   │   RANK & CUT    │            keep the top 35
   └────────┬────────┘            ── this is the first big cut ──
            │  35
   ┌────────▼────────┐  STAGE 3   zero-drift price cones          deterministic, no AI
   │  PRICE LEVELS   │            every name gets honest levels
   └────────┬────────┘
            │  35
   ┌────────▼────────┐  STAGE 4   the AI research desk            ~20 min, the expensive part
   │  DEEP RESEARCH  │            6 specialists, each a different
   │   (assembly     │            job, each building on the last
   │     line)       │
   └────────┬────────┘
            │  35
   ┌────────▼────────┐  STAGE 5   conviction grade A/B/C/D         rules + quant, no AI
   │     GRADE       │            risk can only lower a grade
   └────────┬────────┘
            │
   ┌────────▼────────┐  GATES     fact-check + completeness        anything unverified
   │  QUALITY GATES  │            + "fill to a full board"         is hidden, not shown
   └────────┬────────┘            + promotion safety switch
            │
        15–25 picks                ← what you see on the dashboard
```

**The headline:** ~3,300 → 35 → a displayed board of 15–25. The math is what lets us afford to look at *every* stock every day; the AI is what turns the survivors into research. Neither half works without the other.

---

## Step 0 — We gather fresh intelligence *before* the scan starts

About **90 minutes before** the pre-market scan, a separate "data harvest" runs and refreshes the real-world facts the analysis will stand on. It is kept deliberately separate from the scan itself, so if a news source is flaky it degrades *freshness* but can never break the scan:

| What we pull | Source | Why it matters |
|---|---|---|
| **News** — every headline in the last 24h | Yahoo / Google RSS + Finnhub | The "why now" behind a move |
| **Headline verification** | our tracked ticker master list | Confirms each story is about the *right* company — a mis-tagged headline never reaches a dossier |
| **SEC filings** — 8-Ks, Form 4 insider trades, 13F/13D | SEC EDGAR | Material events and what insiders are actually doing |
| **Options activity** | option chains on the most liquid names | Where large, informed money is positioning |
| **Crowd & social signals** | free social sources + FinBERT sentiment model | Attention, consensus shifts, divergence |
| **Sentiment scoring** | FinBERT on the *verified* news + filings + crowd | A real, model-scored read — not a guess |

**The principle that runs through everything:** if a piece of data isn't there, the system records *"no data"* — it never invents one. This is the foundation the rest of the engine depends on.

---

## Stage 1 — The fast scan: pure math on the whole market

**~3,300 stocks → all measured. No AI. ~3 minutes.**

This stage is intentionally cheap and completely objective. For every stock in our liquid universe (rebuilt weekly; a stock must trade at least **$1M average daily volume** to even be eligible), we pull one year of daily prices and compute a fixed set of measurements:

- **Price action** — today's move, and the overnight **gap** (today's open vs. yesterday's close)
- **Momentum** — 5-day and **20-day return**
- **Relative volume** — today's volume vs. its own 20-day average (is it trading *unusually* heavily?)
- **RSI (14-day)** — the classic Wilder momentum oscillator
- **52-week position** — how far the stock is below (or above) its 52-week high and low
- **Breakout / breakdown flags** — did it just close above its prior 20-day high, or below its prior 20-day low?

That's it for Stage 1. It is **measuring, not judging** — fast, transparent, identical for every stock. The output is one row of clean numbers per company. Crucially, it is robust to thin data: a recent IPO with only a few months of history returns *"not enough data"* on the fields it can't support, rather than producing a misleading number.

---

## Stage 2 — The composite score: how we rank the whole market into a top 35

**~3,300 scored → top 35 kept. No AI. ~10 seconds.** *This is the first big cut, and it deserves a real explanation because it's the gate to everything expensive that follows.*

Every stock from Stage 1 is collapsed into **one number from 0 to 100** — the **composite score** — built from five factors. Each factor is first converted to its own 0–100 sub-score, then blended with fixed weights. The weights encode our philosophy of what a high-quality momentum setup looks like:

| Factor | Weight | What it rewards |
|---|---:|---|
| **Momentum** (20-day return) | **25%** | Stocks that are actually trending up |
| **Relative volume** | **25%** | Conviction *behind* the move — volume confirms price |
| **Breakout** | **20%** | A clean technical event, not just drift |
| **52-week proximity** | **15%** | Strength — nearness to new highs |
| **RSI** | **15%** | Healthy momentum, not exhaustion |

The detail that matters — **each sub-score is shaped, not linear:**

- **Momentum** maps through a `tanh` curve: 0% → 50 points, +25% → ~85, −25% → ~15. The curve **saturates**, so a stock up 200% on a fluke can't simply buy its way to the top of the board on one number. Big moves are rewarded, but with diminishing returns.
- **Relative volume** is also `tanh`-shaped: 1.0× (average) → 50, 2.0× → ~75, 5.0× → 95+. Heavy volume is a strong signal, but capped so a single freak volume day doesn't dominate.
- **Breakout** is binary and decisive: a fresh breakout = 100, a breakdown = 20, neither = 50.
- **52-week proximity** maps linearly: at the 52-week high → 80, a new high → 100, down 50% from the high → 0. Strength is rewarded; broken-down stocks are pushed to the bottom.
- **RSI uses a sweet spot, not "higher is better."** RSI rising through 50→70 scores best; **oversold (<30) gets a deliberate bounce bonus**; **overbought (>70) decays** because exhaustion is a *risk*, not a virtue. This single design choice is the difference between a momentum screen and a momentum-*quality* screen.

The five sub-scores are blended, the whole market is sorted, and **the top 35 advance.** Everything below them is scored and stored (so we have a complete record), but nothing expensive is ever spent on them.

> **Why 35?** It's tuned, not arbitrary. The deep-research desk (Stage 4) is the costly part. We size the pool so that after the fact-check gate trims the names whose theses can't be fully verified (~30–35% on a strict bar), we still land on a **displayed board of 20–25 fully-verified dossiers** — comfortably above our floor of 15.

---

## Stage 3 — Honest price levels (before any AI touches the name)

**All 35 → a price cone each. Deterministic statistics. No AI.**

For each of the 35 we run a **20,000-path Monte Carlo simulation** over a ~one-month horizon, calibrated to the stock's own recent return behaviour, and read off three reference levels: a **downside (5th percentile)**, a **midpoint**, and an **upside (95th percentile)**.

The discipline here is the whole point: **we zero out the drift.** A naïve simulation would bake a stock's recent rally into the forecast and hand you a "median +18%" number that is just momentum wearing a lab coat. By zeroing drift, the cone describes **only volatility — how widely this stock realistically swings** — and we let *direction* live where it belongs: in the bull/bear thesis. These are honest reference levels, not price targets, and because they're pure math they exist for **every** name even on a day the AI budget is tight.

---

## Stage 4 — The deep-research desk: six specialists, an assembly line (NOT repetition)

**The 35 → each gets a full institutional dossier. This is the expensive AI work (~20 min).**

This is the part that's easy to misread as "doing the same research six times." It isn't. It's a **production line**: each specialist does *one distinct job*, consumes the output of the ones before it, and adds something none of the others produce. Here's the line, in order, with the input → unique job → output for each:

| # | Specialist | Reads (input) | Its one job | Produces |
|---|---|---|---|---|
| 1 | **Anomaly Detector** *(pure stats, no AI)* | the harvested tables | Flag **everything unusual today** — and grade each anomaly 0–100 | A list of flags: gaps, volume spikes, IV spikes, **insider clusters** (3+ directional Form 4s in 5 days), 8-Ks, 13D activist stakes, 13F pivots, attention spikes |
| 2 | **Cross-Validator** *(the triangulation engine)* | the anomaly flags + all six source categories | Decide which flags are **real vs. noise** by demanding *independent confirmation* | A `validated_strength` per name — signals that agree across sources score higher; contradictory signals get penalised |
| 3 | **Catalyst Agent** | validated signals, filings, news, technicals | Pin down **the single specific reason** this stock is moving *right now* | One catalyst with evidence: an earnings beat, an 8-K, a 52-week breakout, an options sweep, a news cluster |
| 4 | **Smart-Money Intel** *(the institutional desk)* | filings, transcripts, FINRA, analyst data | Read what **informed money** is doing | A composite of 4 signals → one of 10 named patterns (e.g. *Full Conviction Long*, *Short Squeeze Setup*, *Insider Smoke Signal*) |
| 5 | **Debate Synthesizer** *(the portfolio manager)* | **everything above, assembled** | Write the **institutional thesis** | THESIS / BULL CASE / BEAR CASE / what-to-watch — every number cited back to real data |
| 6 | **Critic Agent** *(the devil's advocate)* | the bull case + smart-money signals | **Attack** the thesis and rewrite a sharper bear case | Weaknesses, disconfirming data, precedent failures, and an **objection level** that can only *lower* the grade |

Three of these deserve a closer look, because they are the moat:

**① The Cross-Validator — why a "boring" stock can outrank a spectacular one.**
A single dramatic signal — one giant volume day, one wild headline — is exactly what fools retail screeners. We treat a signal as *validated* only when **independent source categories confirm it**: price action, volume, news sentiment, SEC/insider activity, options, and crowd. The score rises with each *confirming* category and with directional agreement, and is **cut by 30% when sources contradict each other** (bullish options but insiders dumping, say). There's also a hard **freshness floor**: any source older than two days is treated as *absent* — an honest "no signal" — never as a stale confirmation. The result is deliberate: **a stock with several modest, mutually-confirming signals beats one with a single spectacular but unconfirmed spike.** That is the line between institutional research and a stock screener.

**④ Smart-Money Intel — four reads, one verdict.** It triangulates (a) **earnings-call tone** — shift versus the prior four quarters, guidance changes, CEO/CFO divergence, how management handled Q&A pushback; (b) **insider activity** from raw SEC Form 4 filings, where **scheduled 10b5-1 trades are down-weighted 5×** because they aren't a real decision, and post-earnings buys count most; (c) **short interest & squeeze potential** from FINRA data; and (d) **analyst revision velocity** — direction over magnitude. It even cross-references **Congressional trading**. Every intel note is scanned for advice-language ("buy", "sell", "you must") and rejected if it crosses the line — this is *research*, not a recommendation.

**⑥ The Critic uses a *different* AI than the thesis writer — on purpose.** The bull case is written by one model (Groq Llama 3.3 70B); the critique is written by a *different* model (Google Gemini). **A model is never allowed to mark its own homework.** The critic's objections are recorded as a formal objection level that the grader is *forced* to honour — it can pull a grade down but can never push one up.

---

## Stage 5 — The conviction grade (A / B / C / D)

**The 35 → each graded. Rules + quant models. No AI, no discretion.**

The grade starts from the composite score and earns **bonuses** only from corroborating quantitative models — Monte Carlo probability-up, Value-at-Risk that isn't in the worst quartile, a statistically significant positive **alpha** (Fama-French), a DCF showing sensible 20–50% upside. Notably, a DCF showing **>50% upside earns nothing** — it's treated as *suspicious*, not exciting.

Then names are graded on a curve (top 20% → A, next 30% → B, and so on) — and **hard caps bite, in one direction only:**

- Critic raised a **STRONG** objection → **capped at B**
- Volatility model says **crisis regime** → **capped at C**; **elevated** → capped at B
- Price simulation **poorly calibrated** → can't be an A
- Cross-validator found a **contradiction** → capped at B

**A grade can only ever move *down*. Nothing in the system can inflate one.** That asymmetry is intentional — it's a credibility guarantee.

---

## The quality gates — why you never see a half-built pick

A graded name still has to clear three gates before it's allowed onto the dashboard:

1. **Fact-check (mechanical, not "AI judgment").** The thesis-writing models are forced to tag every specific number with a reference to the exact data point it came from. A deterministic checker then verifies that **every** numeric claim (a) has a tag, (b) points to a key that actually exists in the data we collected, and (c) matches that value within 2%. An invented number with no source, or a tag pointing at data that doesn't exist, is caught automatically. The dossier earns a grade of **Verified**, **Partially Verified**, or **Unverified** — and only the first two are allowed through. **This is our hallucination spine: the AI can only ever cite facts we actually have.**

2. **Completeness.** The dossier must be *whole* — catalyst + thesis + bull + bear + critic verdict + grade + price levels — and the bull and bear cases must read as *finished*: long enough to be real, ending on a proper sentence, with no leftover formatting artifacts. This is the specific guard against a truncated, cut-off case ever reaching you.

3. **Verification grade.** The fact-check verdict must come back Verified or Partially Verified.

Fail any gate and the name is **hidden** — not shown half-built. A shorter all-clean board is correct; a padded one is not.

**Fill-to-a-full-board.** Because one deep-research pass under AI rate limits only completes part of the pool, the engine then **re-runs the research on just the names that didn't finish** — each pass skips the already-complete dossiers and heals only the rest — until we reach a full board or run out of time. It can *only ever add* complete dossiers, so it can never blank or corrupt the board.

**The promotion safety switch.** A fresh scan is only allowed to *replace* what's on the dashboard if it carries **at least 15 complete, fact-checked dossiers**. If a run gets truncated and produces fewer, **the last good board stays up.** The dashboard is never empty, never thin, never broken.

---

## What the client sees

The dashboard shows the surviving board — typically **15 to 25 names** — each numbered and each backed by a complete, fact-checked dossier: the catalyst, the bull and bear cases, the smart-money read, honest price levels, and a conviction grade. Click any name for the full research page. A separate track-record page reports **honest forward returns** on past picks — winners and losers — because the same discipline that governs the research governs how we grade ourselves.

---

## Why this is the moat (what's actually hard to copy)

1. **We can afford to look at the whole market, every day.** The two-speed funnel — cheap math on 3,300, expensive AI on 35 — is what makes full-market coverage economically possible. Most tools either screen shallowly on everything *or* research deeply on a hand-picked few. We do both, every scan.

2. **Triangulation, not single-signal screening.** A pick has to be confirmed by *independent* sources, with stale data treated as no data and contradictions penalised. This is the institutional discipline retail screeners skip.

3. **A mechanical anti-hallucination spine.** The AI is run in *closed context* — it may only cite data we actually harvested — and every number is verified against its source automatically. Unverifiable claims are deleted, not displayed. This is the single biggest reason the research can be trusted.

4. **Separation of powers.** The thesis and its critique are written by *different* AI models, and the verdict, completeness, and grade are enforced by *deterministic code* the models don't control.

5. **Risk can only subtract.** Every cap moves a grade down; nothing in the system can talk a grade up.

6. **Honest by construction.** Zero-drift price cones instead of momentum dressed as forecasts; "no data" instead of a guess; the last good board instead of a broken one; a track record that shows the misses.

> **One-line version for the company:**
> *"We score all ~3,300 liquid US stocks with pure math, rank them to the top 35, then run a six-specialist AI research desk on those 35 — anomaly detection, cross-source triangulation, the catalyst, smart-money positioning, a full bull/bear thesis, and a skeptical critic running on a different AI. Every number is mechanically fact-checked against data we collected that morning, every dossier must be complete, risk can only lower a grade, and only a genuinely-verified board is ever shown. If a scan can't produce at least 15 clean dossiers, we keep the last good board up rather than show you anything half-built."*
