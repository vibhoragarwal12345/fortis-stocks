# How the CEFA Emerging-Opportunities Engine Works — From ~4,000 Small-Caps to a Hand of Asymmetric Bets

*A plain-English walkthrough of the multibagger discovery engine, written for Christopher Edwards Financial Associates.*

---

## The one-paragraph version

Once a week we run a **completely separate engine** from the daily conviction scan — one built to do the opposite job. Instead of finding the strongest, most-confirmed stocks in the large-cap market, it hunts the **small and micro-cap names ($50M–$10B) that almost nobody is looking at yet**, and asks a single question of each: *does this company carry the structural DNA of a historical 100-bagger — a small base, durable growth, improving economics, owner-operators with skin in the game, and almost no analyst coverage?* A few thousand names are scored on six of those traits by pure math; the best ~30 get a full, balanced AI thesis in which **the "what kills it" case is written as rigorously as the "10x path"**; every number is mechanically traced back to real data, and any name whose core rationale can't be verified is **dropped, not dressed up**. The survivors join a tiered watchlist that we then track honestly forever — including the ones that fail. **This is structured speculation, not prediction. Most of these names will not become multibaggers, and the engine is built to say so out loud.**

---

## The single most important thing to understand

This engine is the **deliberate inverse** of the daily stock scan. They are not the same machine pointed at different stocks — they are built on opposite philosophies, on purpose:

| | **Daily conviction scan** | **Emerging-opportunities engine** |
|---|---|---|
| **Universe** | ~3,300 liquid, mostly large/mid-cap US stocks | ~4,000 small & micro-caps, $50M–$10B |
| **What it rewards** | **Confirmation** — many independent sources agreeing *right now* | **Obscurity** — being structurally promising but *undiscovered* |
| **Horizon** | Days to weeks (a catalyst that's moving today) | Years (a base that can compound) |
| **Analyst coverage** | Helps — it's part of the confirmation | **The less, the better** — heavy coverage *lowers* the score |
| **The output** | "These are moving and the move is real" | "These are cheap because no one's found them yet — here's the asymmetric bet, and here's what kills it" |

The reason for the split is simple: **a stock that lights up every confirmation signal is, by definition, already discovered — and no longer cheap.** The multibagger math lives where the daily scan refuses to fish. That is why it has its own screener, its own scoring, its own research desk, and its own dashboard — and why it never borrows the daily engine's conviction grader.

---

## The funnel at a glance

```
   ~4,000 small & micro-cap US stocks     ← $50M–$10B, the cheap end of the market
            │
   ┌────────▼────────┐  STAGE 1   build the universe                  weekly, no AI
   │    UNIVERSE     │            NASDAQ screener + recent IPOs,
   └────────┬────────┘            filtered to $50M–$10B
            │
   ┌────────▼────────┐  STAGE 2   six "multibagger DNA" traits         pure math, no AI
   │   DNA SCREEN    │            each scored 0–100; pass at 60
   └────────┬────────┘            5/6 pass → advance · 4 → watch
            │
   ┌────────▼────────┐  STAGE 3   weighted score + hard caps           rules, no AI
   │  SCORE & CAP    │            caps can only SUBTRACT, never add
   └────────┬────────┘            ── the top 30 advance ──
            │  30
   ┌────────▼────────┐  STAGE 4   one balanced AI thesis per name       the AI work
   │  DEEP THESIS    │            9 sections incl. a co-equal
   │  (verify-or-    │            "what kills it"; rationale must
   │     drop)       │            verify or the name is dropped
   └────────┬────────┘
            │
   ┌────────▼────────┐  STAGE 5   tiered watchlist                      rules, no AI
   │   WATCHLIST     │            graduate at $10B · archive broken
   └────────┬────────┘
            │
   ┌────────▼────────┐  STAGE 6   honest outcome tracking               pure math, no AI
   │     TRACK       │            2x / 5x / 10x — and the failures
   └────────┬────────┘
            │
      a tiered, tracked watchlist of asymmetric bets
```

**The headline:** ~4,000 → six trait scores → top 30 researched → a tiered, honestly-tracked watchlist. The math finds the *shape* of a multibagger cheaply; the AI writes the *theory* and, just as hard, the *threats*; the tracker keeps everyone honest about how it actually plays out.

---

## Stage 1 — Build the universe (the cheap end of the market)

**A few thousand names → kept. No AI. Rebuilt periodically.**

The daily scan's universe is the liquid large/mid-cap market. This engine needs the *opposite* — the small and obscure — so it builds its own list from scratch:

- **The NASDAQ screener** — every US-listed common stock across NYSE / NASDAQ / AMEX (~7,000 names) in one authoritative call, with market cap, sector, and IPO year.
- **The recent-IPO calendar** — the last three years of new listings, to catch fresh names the screener hasn't fully tagged yet.

We then keep only names with a market value between **$50M and $10B**. Below $50M is too illiquid and fragile to research responsibly; above $10B the "small base" that makes multibagger math possible is already gone. *(We first tried the iShares small-cap index files, but the provider gates direct access; the NASDAQ screener is more authoritative and reaches further down into the micro-caps the strategy actually wants.)*

---

## Stage 2 — The DNA screen: six traits of a historical multibagger

**Every name → six scores. Pure math. No AI.**

This is the heart of the cheap, objective filter. Decades of 100-baggers share a recognisable shape, and we score every candidate on six traits of that shape — each as its own **0–100 sub-score**, each "passing" at **60**:

| # | Trait | What earns a high score |
|---|---|---|
| 1 | **Small base** | A market cap in the **$200M–$2B sweet spot** scores 100 (there's room to grow 10×). $100M–$200M scores 80; $2B–$5B tapers to 60; $5B–$10B to 30. |
| 2 | **Durable revenue growth** | A **3-year revenue growth rate (CAGR) ≥ 40%** scores 95; ≥ 25% → 80; ≥ 15% → 50. Growth that's **decelerating** quarter-on-quarter is penalised — fast *and slowing* is a warning, not a win. |
| 3 | **Improving unit economics** | High and/or *rising* gross and operating margins. Crucially, a margin that's still negative but **clearly improving** scores well — that's the signature of a business approaching escape velocity. |
| 4 | **Large addressable market** | A structural prior from sector and size (a tiny company in a big, growing sector has more runway). This one is deliberately coarse — the AI analyst refines the real TAM later. |
| 5 | **Aligned owner-operators** | **Real insider ownership** (from live ownership data): ≥ 20% of the company held by insiders scores 95; ≥ 10% → 80. Recent insider *buying* (SEC Form 4) tilts it up; heavy insider *selling* tilts it down. Skin in the game. |
| 6 | **Under-discovered** | **The inverted signal.** Fewer analysts covering the stock = a *higher* score: 0–2 analysts → 95, and coverage above ~7 → 20. This is the trait that makes the engine the mirror image of the daily scan — it rewards exactly the obscurity the daily scan would treat as a missing confirmation. |

A name that passes **5 or 6** of the six advances toward research; one that passes **4** lands in a "watch" bucket. The screen is transparent and identical for every name, and it degrades gracefully — a field we genuinely can't source is marked unknown and scored neutrally rather than guessed.

> **Why trait 5 was worth rebuilding.** Insider-ownership data isn't on the data vendor's free tier, so for a long time this trait silently sat at its neutral default for *every* company — alignment was effectively switched off. It's now sourced from live ownership data, so a founder-led company with 50% insider ownership finally scores like one, and a company insiders are quietly exiting is finally marked down.

---

## Stage 3 — The multibagger score (and caps that can only subtract)

**The screened names → one 0–100 score → the top 30 advance. Rules + math. No AI.**

The six traits roll up into a single **multibagger score** with fixed weights that encode what actually compounds:

| Component | Weight | What it captures |
|---|---:|---|
| **Growth quality** | **30%** | Real, durable revenue growth — the engine of the whole thesis |
| **Reinvestment runway** | **25%** | Room and economics to redeploy capital at high returns |
| **Unit economics** | **20%** | Margins that work, or are clearly on their way to working |
| **Alignment** | **15%** | Owner-operators betting alongside you |
| **Discovery gap** | **10%** | How undiscovered it still is |

Then — exactly as in the daily grader — **the risk checks can only ever move a score *down*, never up:**

- **Burning cash with under a year of runway, and not yet profitable** → capped at 50 (dilution risk: the people who win are usually not today's shareholders).
- **Debt-to-equity above 2.0** → capped at 40 (an over-levered balance sheet can't survive the long, bumpy road a multibagger requires).
- **Revenue growth decelerating** → capped at 50.

**Accounting red flags** — restatements, auditor changes, going-concern language — are treated as disqualifiers and surfaced in the deep-research stage's *"what kills it"* analysis, where they belong.

The **top 30** by capped score advance to deep research. This asymmetry is the same credibility guarantee the daily scan uses: nothing in the system can *talk a score up* — only the evidence of quality can lift it, and known risks can only hold it back.

---

## Stage 4 — The deep thesis: balanced, closed-context, verify-or-drop

**The top 30 → one full thesis each. This is the AI work.**

For each of the 30, an AI analyst writes a structured, deliberately **balanced** dossier — and it runs under the same anti-hallucination discipline as the daily desk. The model is given a **closed data section** and may cite *nothing else*; every specific number must be tagged to the exact data point it came from. Each dossier has nine parts:

| Section | What it must do |
|---|---|
| **Selection rationale** | *The spine.* In 2–4 sentences, why the screen surfaced this name — which DNA traits scored highest and the theory for how that profile compounds. |
| **Business model** | Plainly: what they do and how they make money. |
| **The 10x path** | The concrete route to a 10× outcome — TAM capture, margin expansion, re-rating — grounded in the metrics. |
| **Moat assessment** | Network effects / switching costs / brand / IP — or honestly, *none verifiable yet*. |
| **Founder assessment** | Who runs it and how aligned they are. |
| **What has to go right** | The 3–4 load-bearing assumptions. |
| **What kills it** | *Co-equal with the 10x path* — 3–4 specific, rigorous failure modes. This is treated as a first-class deliverable, never an asterisk. |
| **Key metrics to track** | Four specific, human-readable things to watch for *this* company. |
| **Risk rating + conviction tier** | An honest label: speculative / high-risk / moderate, and tier 1 / 2 / 3. |

Two rules make this trustworthy rather than promotional:

**① The rationale must verify, or the name is dropped.** A deterministic fact-checker reads the selection rationale and confirms every number traces to real data. If it can't be verified, **the name is removed from the run entirely** — we never show a name we can't justify. Every *other* section that fails verification isn't faked or guessed: it's replaced with an honest line naming exactly what couldn't be confirmed (e.g. *"No analyst coverage and few filings — moat not verifiable from connected sources."*). For these under-covered names, thin data is the *norm*, and the engine is built to say "we don't know" instead of inventing.

**② Hype is mechanically banned.** Phrases like *"to the moon," "guaranteed," "next Nvidia," "no-brainer,"* and *"easy 10x"* cause the entire thesis to be **discarded and rewritten.** The voice is plain, balanced, and risk-forward — the opposite of a stock-promotion newsletter.

---

## Stage 5 — The watchlist (tiers, graduation, and honest retirement)

**Verified theses → a persistent, tiered watchlist. Rules. No AI.**

A name earns a place on the watchlist only with a **complete, fact-checked dossier** (when several theses exist for one name, the engine keeps the *best-verified* one, never the most recent if that's weaker; an unverifiable best thesis means the name simply waits). The watchlist is organised by conviction:

- **Tier 1 — higher conviction** and **Tier 2 — promising** always join.
- **Tier 3 — speculative** joins only if its score clears 60 (a quality floor on the riskiest bucket).

From there it's a living list: prices, returns, peak gains, and drawdowns refresh weekly. Two lifecycle rules keep it clean — a name whose market cap **crosses $10B "graduates"** out of the emerging engine and into the daily pipeline's universe (it's no longer small), and a name whose **thesis breaks is archived**, not quietly deleted.

---

## Stage 6 — Honest outcome tracking (including the failures)

**Every name ever added → a permanent return record. Pure math.**

This is where the engine earns trust. For *every* name that has ever touched the watchlist — winners, losers, and the dead — we keep a long-tail outcome record and report engine-level statistics plainly:

- the share that hit **2×, 5×, and 10×** (with the dates they crossed each milestone),
- the share **archived as broken theses**,
- the **average current return**, and
- the **median time to double** for the names that doubled.

Broken theses don't vanish from the numbers; they're counted. A strategy that expects most of its names to fail has no credibility unless it shows you the failures — so it does.

---

## The risk framing isn't a footnote — it's the product

Every emerging report opens and closes with the same banner, in plain language:

> *Emerging candidates are speculative, long-horizon, high-risk positions. Most will not become multibaggers. Position sizing should reflect the asymmetric risk: small bets, long horizons, expect many failures and a few large winners. For licensed-advisor review — not investment advice.*

The dashboard (`/dashboard/emerging`) carries it too, and the daily dashboard links here only through a clearly-labelled door. The winners can return many multiples; the losers can only ever return −100%. The whole engine is sized around that asymmetry, and it never pretends otherwise.

---

## Why this is the moat (what's actually hard to copy)

1. **It fishes where confirmation-based screeners can't.** By *rewarding* obscurity, the engine systematically surfaces names before the crowd — the only place asymmetric returns actually live. A tool built to demand confirmation is structurally blind to these.

2. **The same anti-hallucination spine as the daily desk.** Closed context, every number tagged and checked, the core rationale verified-or-dropped. For thinly-covered names — where fabrication is *easiest* — it refuses to guess.

3. **"What kills it" is a deliverable, not a disclaimer.** The failure case is written as rigorously as the upside, by design. That's what separates research from promotion.

4. **Risk can only subtract.** Cash-burn, leverage, deceleration and accounting flags can only cap or exclude a name — nothing inflates a score.

5. **It keeps score honestly, forever.** Most strategies bury their losers. This one tracks every name it ever touched and publishes the hit-rate, the failures, and the time-to-double.

> **One-line version for the firm:**
> *"Once a week we screen ~4,000 small and micro-cap US stocks for the six structural traits of a historical multibagger — deliberately rewarding the obscure names a confirmation-based scan would ignore — score them with checks that can only ever lower a score, then write a balanced AI thesis on the top 30 in which the 'what kills it' case is as rigorous as the '10x path.' Every number is fact-checked against real data, any name we can't justify is dropped, and every name we ever add is tracked honestly forever — winners and losers. It's structured speculation: small bets, long horizons, most will fail, a few can return many multiples — and we never pretend otherwise."*
