"""Per-holding analyst narratives -- harsh, position-aware, judgment only.
All NUMBERS are rendered separately from the verified data; this is the read.
Verdicts live in verdicts.py; endings here are kept consistent with them
(Part-D decisions baked in)."""

NARR = {
"BDL.NS":
 "Bharat Dynamics is a guided-missile maker selling almost entirely to the Ministry of Defence, so the bull "
 "case is real: a multi-year indigenisation and export tailwind with a near-captive customer. But the market "
 "has already paid for a decade of that. At a triple-digit trailing multiple this is the most expensive "
 "large-cap you own, and your own entry tells the story — you are only modestly green, meaning you bought into "
 "the euphoria, not ahead of it. Missile revenue converts lumpily from order book to P&L, so a single "
 "delivery-timing miss can de-rate a 100x stock violently. <b>Position read:</b> your single largest weight "
 "carrying your richest valuation and thinnest margin of safety — the line a PM trims first. Take it down to "
 "fund the concentration cut.",
"BEL.NS":
 "Bharat Electronics is the best-run name in your defence book — a genuine franchise: captive MoD electronics "
 "supplier, high return on capital, typically net cash, a credible indigenisation runway. The NSE feed "
 "corroborates the operating story with verified order-win filings, not just sentiment. The problem is not the "
 "business; it is that +128% has already banked years of growth and the multiple now demands flawless "
 "execution. <b>Position read:</b> a top-two weight inside a 60% defence bet. Quality earns it a core hold, but "
 "prudent risk control says harvest part of a doubled position. RETAIN the core, TRIM the excess.",
"HAL.NS":
 "Hindustan Aeronautics is the most defensible holding in the entire portfolio: a near-monopoly on "
 "combat-aircraft and helicopter supply to the air force, the deepest order visibility of your defence names, "
 "and — critically — the most reasonable valuation of the big three. Verified NSE filings (SEBI takeover-reg "
 "disclosure, transcript, management changes) are consistent with a large, actively-covered PSU, not a hype "
 "micro-cap. Honest risks: single-customer (MoD) dependence and execution/forex on long programmes. "
 "<b>Position read:</b> if any defence name deserves full weight it is this one. RETAIN — the quality anchor of "
 "the cluster; the only caveat is portfolio-level (defence stacked on defence).",
"HBLENGINE.NS":
 "HBL Engineering rode two real waves — railway-safety (Kavach train-control) and defence batteries — and the "
 "verified NSE filings this quarter include genuine, quantified order-win announcements, so the catalyst is "
 "real. At a far more modest multiple than your micro-cap defence names, the valuation is digestible. But "
 "+114% on a less-liquid mid-cap means the easy re-rating is done and execution now has to deliver into the "
 "price. <b>Position read:</b> ~9% is a large weight for a name this size; a bad execution quarter in an "
 "illiquid stock gaps down hard. Bank part of the double; keep a meaningful core on the verified order pipeline.",
"TATAPOWER.NS":
 "Tata Power is the most 'normal' large position you hold — an integrated utility with a real renewables and "
 "EV-charging growth leg and the governance comfort of the Tata brand. Verified NSE flow is heavy, consistent "
 "with a large, transparent company. Capital-intensive and partly regulated, so returns are capped and the "
 "balance sheet and capex execution matter. <b>Position read:</b> +43% on a large weight is a solid, "
 "lower-drama contributor. RETAIN — this is ballast, and a book this concentrated in defence needs ballast.",
"AVANTEL.NS":
 "Avantel is a tiny defence-SATCOM/communications play, and this is where the verified data turns the story "
 "from 'winner' to 'warning'. The multiple is the most extreme in your entire book — a sentiment valuation, not "
 "an earnings one. More importantly, the live NSE PIT feed shows genuine NET INSIDER SELLING (5 sells, 0 buys "
 "over the year) — the people who know the company best are reducing into the run. That is the single most "
 "actionable negative signal in the portfolio. <b>Position read:</b> a large weight for a thinly-traded "
 "micro-cap at a nosebleed multiple with insiders selling. Harvest the gain; the smart money is leaving.",
"PFC.NS":
 "<b>This is an MTF (leveraged) holding — read the position before the company.</b> Power Finance Corp is, on "
 "fundamentals, the most defensible name in your leveraged sleeve: a power-sector PSU lender at a "
 "low-single-digit multiple with a high dividend yield. The asset is cheap, not broken. The problem is "
 "structural: you hold a rate- and credit-cycle business on margin, while down on the position, so financing "
 "cost compounds a paper loss and a power-sector wobble could trigger a margin call. <b>Position read — your "
 "plan (sell half, convert half):</b> sound. Sell the first half into any strength toward resistance to cut the "
 "leverage and bank liquidity; convert the retained half to a fully-paid CASH holding — a reasonable value/yield "
 "position to keep. The leverage, not the stock, is the mistake here.",
"TRANSRAILL.NS":
 "<b>MTF (leveraged) holding.</b> Transrail is a transmission-&-distribution EPC contractor geared to India's "
 "grid build-out, at a reasonable multiple. The verified NSE feed shows ongoing order/results filings BUT also "
 "NET INSIDER SELLING (12 sells, 0 buys) — a real cautionary tell. You are down ~a third on borrowed money; EPC "
 "earnings are lumpy and working-capital heavy, and leverage turns that into margin-call risk. <b>Position "
 "read:</b> a cyclical, leveraged loser with insiders selling is the textbook thing to cut. EXIT the leveraged "
 "position; do not finance an EPC cycle on margin against the people selling it.",
"JWL.NS":
 "<b>MTF (leveraged) holding.</b> Jupiter Wagons is a railway-wagon/rolling-stock cyclical riding the "
 "freight-capex cycle, but it trades at a growth multiple while you sit on a ~31% loss — carried on leverage. A "
 "cyclical at a rich multiple, underwater, on margin, is three risks stacked, and the financing cost alone "
 "raises your break-even every month. <b>Position read:</b> EXIT the leveraged position. If you retain genuine "
 "conviction in the wagon cycle, re-enter later as a small CASH position — never on margin against a loss.",
"EASEMYTRIP.NS":
 "<b>MTF (leveraged) holding — and the worst single line in the book.</b> Easy Trip is an online-travel name "
 "that has lost well over half its value, and you are holding that collapse <i>on margin</i>. There are "
 "persistent promoter-stake and governance questions around the name, which we cannot independently confirm "
 "until SEBI filings are parsed [UNVERIFIED] — but you do not borrow money to hold a broken momentum stock "
 "through an unverified governance overhang. <b>Position read:</b> a ~58% loss on leverage is capital "
 "destruction compounded by interest. This is the first thing to sell. EXIT and stop the bleed.",
"ACC.NS":
 "<b>MTF (leveraged) holding.</b> ACC is large-cap cement (Adani stable) at a genuinely cheap multiple — the "
 "kind of de-rated quality that is fine to own. The issue is purely the leverage: cement is cyclical and "
 "price-war-prone, you are down ~a third, and you pay margin interest to wait out a commodity cycle. "
 "<b>Position read — your plan (sell half, convert half):</b> reasonable. Sell half into strength to cut the "
 "margin and book liquidity; convert the retained half to a CASH holding and own the cheap cement cycle "
 "without the financing clock. Cheapness does not justify paying interest to hold it.",
"BALAMINES.NS":
 "<b>MTF (leveraged) holding — the least-bad of the eight.</b> Balaji Amines is a specialty-amines chemical "
 "maker, roughly at your cost, so the leverage has not yet done much damage. But a full-ish multiple in a "
 "China-competition-sensitive, margin-cyclical chemical business is not something to own on borrowed money. "
 "<b>Position read — your plan (convert fully to cash, keep):</b> the cleanest of all eight. Pay off the margin, "
 "hold it as a fully-paid CASH chemicals-cycle recovery bet, and do not add on leverage. The level that would "
 "change this: a clean break below structural support on weak amines pricing — then it is a hold-no-more.",
"PPLPHARMA.NS":
 "<b>MTF (leveraged) holding.</b> Piramal Pharma is a CDMO-plus-complex-generics turnaround — a story stock "
 "whose margins are still thin (no clean trailing earnings multiple), down ~a fifth, held on margin. You are "
 "financing an unproven turnaround with a margin clock running. <b>Position read — your plan (EXIT near avg "
 "cost ₹211):</b> the EXIT is right; the price target is not realistic. At ~₹168 you are ~25% below cost and "
 "the near-term technical ceiling sits far under ₹211 — waiting for cost means holding a leveraged loser "
 "indefinitely. Sell into strength in the realistic exit zone; do not hostage the de-leverage to a round-trip "
 "that the data does not support.",
"MAHSEAMLES.NS":
 "<b>MTF (leveraged) holding.</b> Maharashtra Seamless makes seamless pipes/tubes for oil-&-gas and capex "
 "demand, at a cheap multiple and typically a clean balance sheet — the loss here is the commodity cycle, not "
 "the company, and the live NSE PIT feed actually shows NET INSIDER BUYING (13 buys vs 3 sells), a constructive "
 "tell. <b>Position read — your plan (EXIT near avg cost ₹890):</b> the de-leverage is right but ₹890 is "
 "~46% above the current ~₹608 — not reachable on any near-term technical. The honest split: the leverage must "
 "go regardless; the asset is the most defensible of your MTF losers. Sell the leveraged position in the "
 "realistic exit zone, and if you still believe the cycle, re-own it later in CASH — insiders are buying it, "
 "not selling.",
"IREDA.NS":
 "IREDA is a PSU green-energy financier — a direct, liquid proxy on India's renewables build-out, and it has "
 "re-rated hard in your favour. Two cautions: it is still a lender (credit cost, NIM and equity-dilution risk "
 "are the real drivers, not the 'green' label), and the announcement feed was thin in the window. "
 "<b>Position read:</b> a large gain in a PSU lender that re-rates in both directions. RETAIN a core on the "
 "structural theme but TRIM into strength — do not let a 90%+ winner in a volatile financial run back to cost.",
"APOLLO.NS":
 "Apollo Micro Systems is a defence electro-mechanical small-cap and one of your best trades — which is exactly "
 "why discipline matters now. The valuation is detached from current earnings and the name is small and "
 "sentiment-driven; the live PIT feed actually shows some insider BUYING, but that does not offset a "
 "triple-digit multiple. <b>Position read:</b> a 220%+ gain at this multiple in a micro-cap is a gift you take, "
 "not defend. Harvest most of it; greed at this multiple is how round-trips happen, and it adds to your defence "
 "concentration — a second reason to cut.",
"OLECTRA.NS":
 "Olectra Greentech is the electric-bus leader winning state-transport fleet tenders — a real, high-beta "
 "thematic that has nearly doubled for you. The risks are execution and working capital: e-bus contracts are "
 "receivables- and delivery-heavy, and any tender or payment slippage hits hard [order specifics UNVERIFIED "
 "until a fundamentals feed is connected]. <b>Position read:</b> small weight, big gain. Keep a tracking "
 "position on the EV-mobility theme but TRIM — a 90%+ winner trading on flawless future execution should be "
 "partly realised.",
"RTNPOWER.NS":
 "RattanIndia Power is a sub-₹10 thermal IPP — the 'gain' here is noise on a tiny cost base, not a thesis. The "
 "live PIT feed shows balanced insider activity (1 buy / 1 sell — NOT the 'promoter selling' the earlier draft "
 "wrongly tagged). Penny power stocks with chequered balance-sheet histories are lottery tickets, not utility "
 "holdings. <b>Position read — your plan (EXIT at best price):</b> correct. Sell into any liquidity; do not "
 "mistake a low share price for cheapness. Trivial weight, but dead-weight risk and distraction.",
"ASTRAMICRO.NS":
 "Astra Microwave makes defence RF/microwave sub-systems — another excellent trade (220%+), at a rich multiple, "
 "with a touch of insider BUYING on the live feed. <b>Position read:</b> same discipline as the rest of the "
 "small-cap defence winners — harvest into strength. Eight defence names at elevated multiples is not "
 "diversification, however well each has done. Take the gain and cut the cluster.",
"HAPPSTMNDS.NS":
 "Happiest Minds is a founder-led, digital-native mid-cap IT name that has been a value trap for you — down "
 "~64%, your worst weighted holding, as growth decelerated and the rich IPO-era multiple unwound. But the "
 "de-rate has done its work: at ~₹350 the stock trades around the mid-20s P/E — no longer expensive, and this "
 "is a real, profitable, cash-generative IT business, NOT a distressed name. <b>Position read — on your "
 "reconsideration:</b> do NOT book this loss here. Selling a sound business at -64% near multi-year lows, on "
 "the ₹986 anchor, crystallises maximum pain at the wrong point. HOLD; the invalidation is a clean break of "
 "the recent swing low (then the thesis is genuinely broken and you cut). 'It will recover' is not the thesis — "
 "'a profitable IT compounder de-rated to ~25x' is.",
"GPPL.NS":
 "Gujarat Pipavav is an APM-Terminals-backed container/bulk port concession — a boring, cash-generative "
 "infrastructure asset at a reasonable multiple. Volumes track the trade cycle and the concession caps long-run "
 "growth, hence the modest drawdown. <b>Position read:</b> the least objectionable small holding you own — real "
 "cash flows, real promoter, no drama. RETAIN; this is the kind of thing the rest of the book is missing.",
"TATATECH.NS":
 "Tata Technologies is an engineering-R&D (auto-heavy) services name with the Tata brand and a hyped IPO that "
 "has since de-rated; you are down ~37% AND it is still expensive at ~55x earnings. Auto-R&D spend is "
 "cyclical. <b>Position read — on your reconsideration:</b> unlike Happiest Minds, the de-rate has NOT made "
 "this cheap — 55x after a 37% fall is still a growth multiple with broken momentum and no catalyst. EXIT and "
 "book the loss; the capital (tiny weight) is better deployed than anchored to a ₹1,177 entry. If you want "
 "ER&D exposure, there are cheaper ways than holding a still-rich laggard.",
"AVALON.NS":
 "Avalon Technologies is an EMS/contract-electronics manufacturer adjacent to defence and aerospace demand. "
 "<b>One honest correction to your framing:</b> you listed this for loss-booking, but it is NOT a loss — it is "
 "a +205% WINNER. The live PIT feed also shows NET INSIDER SELLING (3 sells, 0 buys). <b>Position read:</b> "
 "this is a frothy winner at a rich multiple with insiders distributing AND it counts toward your "
 "defence-electronics concentration — two independent reasons to take money off. TRIM/realise the gain; there "
 "is no loss to book here, only profit to harvest.",
"GMMPFAUDLR.NS":
 "<b>This is a CASH holding</b> (the first draft mis-read it as MTF — corrected). GMM Pfaudler is the "
 "glass-lined process-equipment leader for pharma/chemical capex — a quality niche, but you are down ~40% at a "
 "still-full multiple as the capex cycle and an LBO-era balance sheet weighed. Importantly, the verified NSE "
 "PIT records here are <i>inter-se promoter transfers</i> (a family/group reshuffle), <b>not</b> open-market "
 "selling — explicitly retracting the earlier 'promoter sell' alarm. <b>Position read:</b> tiny weight, real "
 "but expensive business, underwater. TRIM / hold small; not a conviction position at this price.",
"GTLINFRA.NS":
 "GTL Infrastructure is a sub-₹2 telecom-tower penny stock with a long history of distress and restructuring "
 "[insolvency status UNVERIFIED pending filings]. The headline 'gain' is noise on a ~₹1 cost base. "
 "<b>Position read:</b> dead money. EXIT if it is liquid; stop counting it as a holding.",
"AKSHAR.NS":
 "Akshar Spintex is a sub-₹1 micro-cap textile/spinning name down ~94% — a near-total loss, almost certainly "
 "illiquid. <b>Position read — on your reconsideration:</b> there is nothing left to reconsider. The only "
 "decision is whether you can sell it at all. EXIT to whatever bid exists and reclaim the screen space; it is a "
 "wipeout, not a recovery candidate.",
"HFCL.NS":
 "HFCL is a telecom-equipment/optical-fibre maker geared to BharatNet and 5G capex, up ~54% for you, at a rich "
 "multiple, and the NSE feed is busy with genuine, quantified order-win filings — but the same feed shows NET "
 "INSIDER SELLING (2 sells, 0 buys). Competitive, capital-intensive, lumpy ordering. <b>Position read:</b> a "
 "trivial-weight, richly-valued trading vehicle, not a compounder, with insiders trimming into the orders. "
 "TRIM/realise; do not build it up at this multiple.",
"AJOONI.NS":
 "Ajooni Biotech is a sub-₹5 micro/SME agri-bio name, down ~57%. The live PIT feed shows NET INSIDER BUYING "
 "(4 buys / 2 sells — NOT the 'promoter selling' the earlier draft wrongly tagged), but insider buying does not "
 "redeem a sub-₹5 SME lottery ticket with pump-and-dump risk [UNVERIFIED]. <b>Position read:</b> EXIT if "
 "liquid; do not add a rupee.",
"IRCON.NS":
 "Ircon International is a railway-construction PSU with a large EPC order book at a reasonable multiple; it got "
 "swept into the railway-PSU bubble and de-rated, hence your ~45% loss. Margins are thin and it depends on the "
 "government payment cycle. <b>Position read:</b> a real company that got absurdly over-valued and corrected — "
 "not a broken business. Hold the core on the verified order book; do not treat a bounce as a new bull market. "
 "Trivial weight, low urgency.",
"FCONSUMER.NS":
 "Future Consumer is a Future-group FMCG entity, effectively defunct, trading at ~₹0.3 and down ~79% (the "
 "holdings file even carries its valuation as ₹0). [Insolvency/suspension status UNVERIFIED — confirm on NSE.] "
 "<b>Position read:</b> dead money. If it can be sold, sell it; otherwise treat it as zero and move on.",
"TDPOWERSYS.NS":
 "TD Power Systems makes generators/electrical machines with a real export book — your best unweighted trade "
 "(+137%), at a rich multiple. <b>Position read:</b> a genuine winner left at zero weight is a decision you are "
 "avoiding. Either give it a real position sized to conviction, or BOOK THE PROFIT. Limbo is not a strategy.",
"RCOM.NS":
 "Reliance Communications has been under insolvency for years; sub-₹1 equity in an insolvent telecom is "
 "typically near-worthless [confirm NCLT status]. <b>Position read:</b> a zero with a live quote. EXIT / remove "
 "it from your mental portfolio.",
"KSHITIJPOL.NS":
 "Kshitij Polyline is a sub-₹7 SME plastics micro-cap down ~88% and illiquid. <b>Position read — on your "
 "reconsideration:</b> salvage value only. EXIT to whatever bid exists; there is no thesis to hold, only a tax "
 "loss to harvest and screen space to reclaim.",
"HDIL.NS":
 "Housing Development & Infrastructure is under NCLT insolvency; sub-₹2 real-estate equity is deeply "
 "subordinated in a bankruptcy process [confirm status]. <b>Position read:</b> bankrupt-process equity — dead "
 "money. EXIT if any bid exists.",
"VIKASLIFE.NS":
 "Vikas Lifecare is a sub-₹2 micro-cap from a family of names associated with serial value destruction "
 "[UNVERIFIED]; down ~67%. <b>Position read:</b> penny loss; EXIT if liquid.",
"VIKASECO.NS":
 "Vikas Ecotech is a sub-₹2 polymer/chemicals micro-cap, same family of names, down ~70%. <b>Position read:</b> "
 "same verdict — dead weight, EXIT if liquid.",
"FEL.NS":
 "Future Enterprises is a Future-group entity under insolvency; equity effectively impaired [confirm status]. "
 "<b>Position read:</b> another Future-group zero. EXIT; stop holding ghosts.",
"RVNL.NS":
 "Rail Vikas Nigam is a railway-PSU EPC that became absurdly over-valued in the PSU bubble and de-rated ~40% "
 "from your cost; the feed shows ongoing filings. It is a real company, still not cheap, and your position is a "
 "token single share. <b>Position read:</b> either give it a deliberate weight and thesis or EXIT — a token "
 "holding in a volatile PSU is indecision, not risk management.",
}
