# How the CEFA Commodities Engine Works — Ten Markets, Two Clocks

*A plain-English walkthrough of the commodities research engine, written for Christopher Edwards Financial Associates.*

---

## The one-paragraph version

Alongside the stock engines we run a **separate research engine for ten of the world's most-watched commodities** — crude, natural gas, the precious and industrial metals, and the major grains and coffee. Its single organising idea is that a commodity has to be read on **two completely different clocks at once**: a **short-term / tactical** clock (about a week, for traders) and a **structural / long-term** clock (multiple quarters, for investors). The engine keeps these two reads *architecturally* apart — they are built from disjoint data, written by separate AI passes, and labelled so a long-horizon reader is never handed a one-week trading signal by accident. Every figure comes from a **live public source and is stamped with where it came from**; anything we can't verify is labelled *unverified* or left out — never invented. And because what happens in copper, gold and oil tells you something about the *whole economy*, the engine feeds three clean cross-asset signals back into the stock side. **This is research intelligence, not advice — and commodities carry more risk than equities, so every read says so.**

---

## The single most important idea: two clocks, never crossed

The classic way to get commodities wrong is to **mix the horizons** — to let a one-week trading observation ("it's overbought, positioning is crowded") bleed into a multi-year investment view ("the supply deficit is structural"), or vice versa. They are different questions for different readers, and blending them is how investors get talked into overtrading and traders get anchored to themes that don't matter this week.

So the engine refuses to blend them. For every commodity it produces **two separate reads**, and the separation is enforced by the architecture, not by good intentions:

| | **Tactical read** | **Structural read** |
|---|---|---|
| **Horizon** | ~1 week | Multiple quarters to years |
| **Written for** | Traders | Investors |
| **Built from** | Technicals, positioning, curve, the latest inventory *surprise*, seasonality | Supply/demand *balance*, macro drivers, long trends, inventory *levels* vs the 5-year norm |
| **Carries** | Timing | No timing information at all |
| **The rule it enforces** | "Don't act on this with long-horizon capital" | "Don't trade off this read" |

The two reads are assembled from **two disjoint sets of data** and written by **two independent AI passes** — the tactical writer never even sees the structural framing, and vice versa. A horizon *can't* leak into the other by construction, and a separate AI auditor checks for leakage anyway.

---

## The flow at a glance

```
   10 commodities          ← energy · metals · agriculture
        │
   ┌────▼─────────────────────────┐
   │  GATHER — live public data    │   fetched once per run, each figure
   │  prices · EIA · USDA · COT ·  │   tagged VERIFIED / ESTIMATED /
   │  macro · news                 │   UNVERIFIED — gaps labeled, never filled
   └────┬─────────────────────────┘
        │
   ┌────▼─────────────────────────┐
   │  SIX ANALYSIS LAYERS (no AI)  │   technicals · supply/demand · curve ·
   │  pure, deterministic math     │   positioning · seasonality · macro
   └────┬─────────────────────────┘
        │
        ├──────────────► split into two DISJOINT data sets ◄──────────────┤
        │                                                                  │
   ┌────▼─────────┐                                              ┌─────────▼────┐
   │  TACTICAL    │  one-week clock                              │  STRUCTURAL  │  multi-quarter clock
   │  narrative   │  separate AI pass, 3 gates                   │  narrative   │  separate AI pass, 3 gates
   └────┬─────────┘                                              └─────────┬────┘
        │                                                                  │
        └───────────────► one published read per commodity ◄──────────────┘
        │
   ┌────▼─────────────────────────┐
   │  CROSS-LINK to the stock side │   copper → growth · gold → risk ·
   │  three structural signals     │   oil → inflation  (deterministic, no AI)
   └──────────────────────────────┘
```

**The headline:** ten markets, six deterministic analysis layers each, two architecturally-separated narratives, and three cross-asset signals — every number traced to a live source, every read labelled by horizon and risk.

---

## What we cover — ten commodities, three groups

Everything keys off a single registry, so a commodity is defined (and its every data source mapped) in exactly one place:

| Group | Commodities |
|---|---|
| **Energy** | Crude Oil (WTI), Crude Oil (Brent), Natural Gas (Henry Hub) |
| **Metals** | Gold, Silver, Copper |
| **Agriculture** | Soybeans, Corn, Wheat (Chicago SRW), Coffee (Arabica) |

*(Iron ore was covered briefly and then deliberately dropped: on free data it had no reliable daily price, no positioning data, and no futures curve, so five of its six analysis layers came back empty. Rather than ship a hollow tile next to the others, we removed it — and we'll only re-add a commodity when we can actually research it honestly. That decision is the whole philosophy in miniature.)*

---

## Step 1 — Gather live data (and tag every number with where it came from)

**Each source is fetched once per run. No AI.**

The engine stands on independent, authoritative public sources, each chosen for stability:

| Source | What it provides |
|---|---|
| **Market prices** (yfinance front-month futures) | The live price for each commodity, sanity-checked against an independent **FRED** spot series wherever one exists |
| **EIA** (US Energy Information Administration) | Energy fundamentals — crude and product inventories, gas storage, refinery utilisation, the strategic reserve |
| **USDA** (WASDE + NASS) | Agricultural fundamentals — stocks, production, the monthly world supply/demand estimates |
| **CFTC Commitments of Traders** | How speculative money is *positioned* — net stance, how extreme it is vs history, weekly flow |
| **FRED macro** | The dollar, real interest rates, industrial production, global growth, inflation |
| **News (Google News RSS)** | The headlines that give the numbers context, per commodity |

**The principle that runs through everything:** every figure carries a **provenance tag** — *verified* from a named live source, *estimated* (e.g. a statistical simulation), or *unverified*. Where a source is silent or paid-gated (some metals data genuinely is), the figure is marked **unverified or simply left out — it is never guessed and never filled in.** A read built on a gap says so, plainly. This "label the gaps, never fill them" discipline is the same honesty spine the stock engine uses, applied to a messier data world.

---

## Step 2 — Six analysis layers (pure math, no AI)

**Every commodity → six deterministic reads.**

Before any language is written, six independent layers turn the raw data into structured signals. None of them use AI — they are transparent, repeatable math:

| # | Layer | What it determines |
|---|---|---|
| 1 | **Price technicals** | Trend vs the 50- and 200-day averages, RSI, ATR, realised volatility, position in the 52-week range, support/resistance — **and the zero-drift reference bands** (below) |
| 2 | **Supply & demand** | A balance classification — *tightening / loosening / balanced* — with a confidence level and an honest caveat when the data is thin |
| 3 | **Curve structure** | Whether the futures curve is in **contango or backwardation**, and how steeply — a market's own read on scarcity vs glut |
| 4 | **Positioning** | From CFTC data: how speculators are leaning, how extreme that is vs the last three years, and whether the trade is **crowded** |
| 5 | **Seasonality** | This commodity's tendency in *this* calendar month — average move and how often it actually happens |
| 6 | **Macro drivers** | The dollar, real rates, industrial production, growth and inflation — and a single net read of whether the backdrop is a tailwind or headwind |

---

## The zero-drift reference bands (honest levels, not forecasts)

Layer 1 produces price **reference bands** — but with a discipline that's worth spelling out, because it's where most "price target" math quietly lies.

For each commodity we run a **10,000-path Monte Carlo simulation**, with the return distribution **fitted to the commodity's own recent behaviour** (including fat tails). It yields three levels on each clock — a **lower band (5th percentile)**, a **midpoint (50th)**, and an **upper band (95th)** — over a **one-week** horizon (tactical) and a **one-year** horizon (structural).

The crucial move: **we zero out the drift.** A naïve simulation extrapolates the recent trend forward — which, over a year, turned a two-year gold rally into a "+69% median" band: *momentum dressed up as a statistical forecast.* By zeroing the drift, the bands describe **only volatility — the range where the price could plausibly sit given how it actually moves — with no directional view baked in.** Direction belongs in the supply/demand and macro reads, not smuggled into a number. The bands are explicitly labelled *"statistical reference levels — not forecasts, not targets,"* and because they're pure math they exist even on a day a data source is down.

---

## Step 3 — The narrative: the one AI layer, on a short leash

**Two reads per commodity. The only place AI is used — and it's fenced in.**

Finally, an AI writer turns the six layers into two plain-English narratives (the tactical and the structural). It runs under exactly the same closed-context discipline as the stock desk, and then some:

- **It sees only a data section of named keys** computed this run — no outside knowledge, no remembered prices, no events not in the data. Every specific number must be tagged to the exact data point it came from.
- **Every draft must clear three gates, in order:**
  1. **Not truncated** — the text must end on a finished sentence (a cut-off read that happens to be cited still fails).
  2. **Fact-check ≥ 0.85** — a deterministic checker confirms every tagged number traces to a real data key and matches it.
  3. **Critic audit** — a *second* AI model checks for three things specifically: any claim the data doesn't support, **any advice language** ("buy / sell / hold / should"), and **horizon leakage** (a tactical point in the structural read or vice versa).
- **Each failure feeds its specific findings back into the next attempt.** After three tries the read is **discarded and reported as absent — never backfilled** from the model's memory. An honest blank beats a confident guess.

Because the tactical and structural narratives are **two separate calls on two disjoint data sets**, a horizon can fail its check on its own without dragging the other down — and one read being absent never fabricates the other.

---

## Step 4 — The cross-link: what commodities tell the stock side

**Three structural signals. Deterministic, no AI, no new data.**

Copper, gold and oil aren't just markets — they're read-outs on the whole economy. So the engine distils three clean signals and hands them to the stock side's macro analysis:

| Signal | From | What it reads |
|---|---|---|
| **Industrial / growth** | Copper trend | Copper demand *is* industrial production — its trend is a global-growth proxy (tailwind / headwind) |
| **Risk appetite** | Gold trend | A sustained gold bid is hedge / haven demand — a risk-off tilt |
| **Inflation impulse** | Oil (WTI) trend | Crude feeds headline inflation with a short lag |

Three rules keep this a clean interface rather than a tangle: the signals are **structural by construction** (they read the 50/200-day trend state, *not* day moves, because their job is regime context, not trade timing); they are **purely advisory** — the stock engine's macro agent lets them *colour* its reasoning but never lets them override its own thresholds; and a **missing or stale scan degrades to "no signal," never to a neutral guess** (anything older than five days is flagged stale). The commodity side never imports stock code and the stock side never imports commodity code — they meet only through this one snapshot.

---

## Cadence — a daily tactical pulse, a weekly structural refresh

The two clocks run on two schedules, for a reason. **Each weekday** a lightweight *tactical* run refreshes the one-week read (technicals and positioning move daily). **Once a week (Monday)** a full run regenerates everything, including the expensive *structural* read — which is then **carried forward** through the daily runs until the next Monday. The fundamentals that drive the structural view (EIA weekly, CFTC weekly, USDA/IMF monthly) simply don't change fast enough to re-compute intraday, so we don't — that would only republish identical fundamentals under fresher timestamps and waste compute.

---

## What the client sees

The commodities dashboard groups the ten by energy / metals / agriculture, and each commodity opens to **two clearly-separated cards** — a tactical card and a structural card — each with its own plain-English read, its reference bands, and its own audience warning. Every figure is traceable; the disclaimer is unmissable: *research and intelligence only, not investment advice, and commodities carry higher risk than equities.*

---

## Why this is the moat (what's actually hard to copy)

1. **Two clocks, never crossed.** Most commodity commentary blends the trade and the thesis into one muddled paragraph. Separating them *architecturally* — disjoint data, separate AI passes, leakage audited — is what stops a one-week signal from masquerading as a multi-year view.

2. **Verify-or-label, never fabricate.** Every number is tagged to a live source; gaps are labelled, not filled; the one AI layer can only cite data we actually have, and a read that can't clear three gates is dropped, not published.

3. **Zero-drift honesty on the levels.** Reference bands describe volatility, not a disguised momentum forecast — direction lives in the fundamentals, where it can be argued, not in a number that looks objective but isn't.

4. **A real cross-asset signal.** Copper, gold and oil feed the stock engine's macro read through one clean, structural, deterministic interface — the kind of whole-economy context most equity tools simply don't have.

5. **Risk-first by construction.** Every read carries its horizon warning and the standing reminder that commodities are riskier than equities, and that a qualified human must review anything acted on downstream.

> **One-line version for the firm:**
> *"We research ten major commodities on two separate clocks at once — a one-week tactical read for traders and a multi-quarter structural read for investors — and we keep them architecturally apart so neither contaminates the other. Six deterministic analysis layers feed two independent AI narratives that may only cite live, tagged data and must pass a fact-check and a critic that bans advice language and horizon leakage; reference levels are zero-drift volatility cones, not forecasts; gaps are labelled, never filled; and three clean signals — copper for growth, gold for risk, oil for inflation — feed the stock side. Research intelligence, not advice, and every read says commodities carry more risk than equities."*
