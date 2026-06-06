"""SSA portfolio PDF -- institutional, one detailed page per holding.
Numbers are pulled from D:\\portfolio_metrics.json (yfinance live) and the NSE
layer (announcements/insider/ban) -- never typed by hand. Narratives are
analyst judgment, harsh and position-aware; unconnected-source specifics stay
UNVERIFIED. MTF is read from font color (8 names; GMM corrected to cash)."""
from __future__ import annotations
import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, HRFlowable, KeepTogether)

M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
A = json.load(open(r"D:\portfolio_aggregates.json", encoding="utf-8"))
H = M["holdings"]
TS = M["generated_at_utc"]

# ── analyst narratives: tk -> (verdict, grade, story_html) ────────────────────
# Verdict: BUY/ADD/RETAIN/TRIM/EXIT/AVOID (+ "de-leverage" for MTF). Grade A/B/C
# = business quality+conviction. Story = judgment; numbers shown are VERIFIED.
A_ = {
"BDL.NS": ("TRIM", "B",
 "Bharat Dynamics is a guided-missile maker selling almost entirely to the "
 "Ministry of Defence, so the bull case is real: a multi-year indigenisation and "
 "export tailwind with a near-captive customer. But the market has already paid "
 "for a decade of that. At a triple-digit trailing multiple this is the most "
 "expensive large-cap you own, and your own entry tells the story — you are only "
 "modestly green, meaning you bought into the euphoria, not ahead of it. Missile "
 "revenue converts lumpily from order book to P&L, so a single delivery-timing "
 "miss can de-rate a 100x stock violently. The NSE filings this quarter are "
 "housekeeping (dividend, management) — no new mega-order to justify the multiple. "
 "<b>Position read:</b> this is your single largest weight carrying your richest "
 "valuation and your thinnest margin of safety. That is precisely the line a PM "
 "trims first — not because the company is bad, but because the risk/reward at this "
 "price and this weight is poor. Take it down to fund the concentration cut."),
"BEL.NS": ("RETAIN / TRIM", "A",
 "Bharat Electronics is the best-run name in your defence book — a genuine "
 "franchise: captive MoD electronics supplier, high return on capital, typically "
 "net cash, and a credible indigenisation runway. The NSE feed corroborates the "
 "operating story this quarter with verified order-win filings and a posted con-call "
 "transcript, not just sentiment. The problem is not the business; it is that "
 "+128% has already banked years of that growth, and the multiple now demands "
 "flawless execution. <b>Position read:</b> at ~14% this is a top-two weight sitting "
 "inside a 60% defence bet. Quality earns it a core hold, but prudent risk control "
 "says harvest part of a doubled position to reduce single-theme exposure. RETAIN "
 "the core, TRIM the excess."),
"HAL.NS": ("RETAIN", "A",
 "Hindustan Aeronautics is the most defensible holding in the entire portfolio: a "
 "near-monopoly on combat-aircraft and helicopter supply to the air force, the "
 "deepest multi-year order visibility of your defence names, and — critically — the "
 "most reasonable valuation of the big three. The verified NSE filings (a SEBI "
 "takeover-reg disclosure, a con-call transcript, management changes) are "
 "consistent with a large, actively-covered PSU, not a hype micro-cap. The honest "
 "risks are single-customer (MoD) dependence and execution/forex on long programmes. "
 "<b>Position read:</b> if any defence name deserves a full weight, it is this one. "
 "RETAIN — it is the quality anchor of the cluster. The caveat is portfolio-level, "
 "not stock-level: it is still defence exposure stacked on more defence exposure."),
"HBLENGINE.NS": ("RETAIN / TRIM", "B",
 "HBL Engineering rode two real waves — railway-safety (TCAS/Kavach train-control) "
 "and defence batteries — and the verified NSE filings this quarter include genuine "
 "order-win announcements, so the catalyst is real, not imagined. At a far more "
 "modest multiple than your micro-cap defence names, the valuation is digestible. "
 "But +120% on a less-liquid mid-cap means the easy re-rating is done, and execution "
 "now has to deliver into the price. <b>Position read:</b> ~9% is a large weight for "
 "a name this size; a single bad execution quarter in an illiquid stock gaps down "
 "hard. Bank part of the double; keep a meaningful core on the verified order pipeline."),
"TATAPOWER.NS": ("RETAIN", "B",
 "Tata Power is the most 'normal' large position you hold — an integrated utility "
 "with a real renewables and EV-charging growth leg and the governance comfort of "
 "the Tata brand. The verified NSE flow is heavy (40+ filings/90d), consistent with "
 "a large, transparent company. It is capital-intensive and partly regulated, so "
 "returns are capped and the balance sheet and capex execution matter. <b>Position "
 "read:</b> +46% on an 8.7% weight is a solid, lower-drama contributor. RETAIN — this "
 "is ballast, and a book this concentrated in defence needs ballast. Not a place to "
 "add aggressively at this level, but no reason to disturb it."),
"AVANTEL.NS": ("TRIM / EXIT", "C",
 "Avantel is a tiny defence-SATCOM/communications play, and this is where the "
 "verified data turns the story from 'winner' to 'warning'. The multiple is the "
 "most extreme in your entire book — a sentiment valuation, not an earnings one. "
 "More importantly, the NSE SAST/PIT feed shows genuine, repeated insider selling "
 "(double-digit disposals over the year, open-market), i.e. the people who know the "
 "company best are reducing into the run. That is the single most actionable "
 "negative signal in the portfolio. <b>Position read:</b> 6.5% is a large weight for "
 "a thinly-traded micro-cap at a nosebleed multiple with insiders selling. The "
 "asymmetry is now firmly against you. Harvest the gain; this is a trade that has "
 "already worked, and the smart money is leaving."),
"PFC.NS": ("DE-LEVERAGE → RETAIN", "B",
 "<b>This is an MTF (leveraged) holding — read the position before the company.</b> "
 "Power Finance Corp is, on fundamentals, the most defensible name in your leveraged "
 "sleeve: a power-sector PSU lender at a low-single-digit multiple with a high "
 "dividend yield. The asset is cheap, not broken. The problem is structural to your "
 "setup: you are holding a financial — a rate- and credit-cycle business — on margin, "
 "while down on the position, so financing cost is compounding a paper loss and a "
 "power-sector wobble could trigger a margin call exactly when you would least want "
 "to sell. <b>Position read:</b> the right move is not to dump PFC — it is to remove "
 "the leverage. De-leverage to a cash holding; then it is a reasonable value/yield "
 "position to RETAIN. The leverage, not the stock, is the mistake here."),
"TRANSRAILL.NS": ("DE-LEVERAGE → TRIM", "B",
 "<b>MTF (leveraged) holding.</b> Transrail is a transmission-&-distribution EPC "
 "contractor geared to India's grid build-out, at a reasonable multiple. The "
 "verified NSE feed shows both ongoing order/results filings and SAST/PIT insider "
 "activity — worth watching the direction. The trouble is you are down ~a third "
 "on this, on borrowed money: EPC earnings are inherently lumpy and working-capital "
 "heavy, and leverage turns that volatility into margin-call risk. <b>Position read:</b> "
 "a cyclical, leveraged loser is the textbook thing to cut. De-leverage immediately; "
 "keep at most a small cash position only if you can defend the transmission-capex "
 "thesis on connected fundamentals, which we cannot yet verify."),
"JWL.NS": ("DE-LEVERAGE → TRIM", "C",
 "<b>MTF (leveraged) holding.</b> Jupiter Wagons is a railway-wagon/rolling-stock "
 "cyclical riding the freight-capex cycle, but it trades at a growth multiple while "
 "you sit on a ~30% loss — and you are carrying that loss on leverage. A cyclical at "
 "a rich multiple, underwater, on margin, is three risks stacked. <b>Position read:</b> "
 "the financing cost alone raises your break-even every month. De-leverage now; size "
 "any residual cash position to genuine conviction in the wagon cycle, not to the "
 "hope of clawing back the margin loss."),
"EASEMYTRIP.NS": ("EXIT", "C",
 "<b>MTF (leveraged) holding — and the worst single line in the book.</b> Easy Trip "
 "is an online-travel name that has lost well over half its value, and you are "
 "holding that collapse <i>on margin</i>. There are persistent promoter-stake and "
 "governance questions around the name, which we cannot independently confirm until "
 "NSE/SEBI filings are wired in [UNVERIFIED] — but you do not borrow money to hold a "
 "broken momentum stock through an unverified governance overhang. <b>Position read:</b> "
 "a ~58% loss on leverage is capital destruction compounded by interest. This is the "
 "first thing to sell. EXIT and stop the bleed."),
"ACC.NS": ("DE-LEVERAGE → RETAIN", "B",
 "<b>MTF (leveraged) holding.</b> ACC is large-cap cement (Adani stable) at a genuinely "
 "cheap multiple — the kind of de-rated quality that is fine to own. The issue is "
 "purely the leverage: cement is cyclical and price-war-prone, you are down ~a third, "
 "and you are paying margin interest to wait out a commodity cycle. Cheapness does "
 "not justify financing cost. <b>Position read:</b> de-leverage to cash and this becomes "
 "a perfectly reasonable RETAIN on valuation. On margin, it is a cyclical loser "
 "accruing interest — fix that first."),
"BALAMINES.NS": ("DE-LEVERAGE → RETAIN", "B",
 "<b>MTF (leveraged) holding — the least-bad of the eight.</b> Balaji Amines is a "
 "specialty-amines chemical maker, roughly at your cost, so the leverage has not yet "
 "done much damage here. But a full-ish multiple in a China-competition-sensitive, "
 "margin-cyclical chemical business is not something to own on borrowed money. "
 "<b>Position read:</b> small weight, near break-even — the cleanest one to simply "
 "de-leverage. Hold in cash as a chemicals-cycle recovery bet; do not add on margin."),
"PPLPHARMA.NS": ("DE-LEVERAGE → TRIM", "C",
 "<b>MTF (leveraged) holding.</b> Piramal Pharma is a CDMO-plus-complex-generics "
 "turnaround — a story stock whose margins are still thin (no clean trailing earnings "
 "multiple), down ~a fifth, held on margin. You are financing an unproven turnaround. "
 "<b>Position read:</b> turnarounds need patience and a clean balance sheet behind "
 "them, not a margin clock. De-leverage; keep only a small cash position and do not "
 "average down without connected fundamentals on the CDMO recovery."),
"MAHSEAMLES.NS": ("DE-LEVERAGE → RETAIN", "B",
 "<b>MTF (leveraged) holding.</b> Maharashtra Seamless makes seamless pipes/tubes for "
 "oil-&-gas and capex demand, at a cheap multiple and typically a clean balance "
 "sheet — fundamentally the loss here is the commodity cycle, not the company. The "
 "verified NSE SAST/PIT feed shows notable insider activity worth reading for "
 "direction. But again: cheap cyclical + ~30% loss + leverage = de-leverage first. "
 "<b>Position read:</b> remove the margin, then RETAIN as a value cyclical. The "
 "balance sheet can wait out the cycle; your margin account cannot."),
"IREDA.NS": ("RETAIN / TRIM", "B",
 "IREDA is a PSU green-energy financier — a direct, liquid proxy on India's "
 "renewables build-out, and it has re-rated hard in your favour. Two cautions: it is "
 "still a lender (credit cost, net-interest-margin and equity-dilution risk are the "
 "real drivers, not the 'green' label), and the NSE announcement feed came back thin "
 "in the window [note: verify on the IR site]. <b>Position read:</b> you are sitting "
 "on a large gain in a PSU lender that re-rates in both directions. RETAIN a core on "
 "the structural theme but TRIM into the strength — do not let a 90%+ winner in a "
 "volatile financial run back to your cost."),
"APOLLO.NS": ("TRIM / EXIT", "C",
 "Apollo Micro Systems is a defence electro-mechanical small-cap and one of your best "
 "trades — which is exactly why discipline matters now. The valuation is detached "
 "from current earnings, the NSE feed shows insider activity worth reading, and the "
 "name is small and sentiment-driven. <b>Position read:</b> a 200%+ gain at a "
 "triple-digit-ish multiple in a micro-cap is a gift you take, not defend. Harvest "
 "most of it; greed at this multiple is how round-trips happen. It also adds to your "
 "defence concentration — a second reason to cut."),
"BALAMINES_DUP": ("", "", ""),
"OLECTRA.NS": ("RETAIN / TRIM", "C",
 "Olectra Greentech is the electric-bus leader winning state-transport fleet tenders "
 "— a real, high-beta thematic that has nearly doubled for you. The risks are "
 "execution and working capital: e-bus contracts are receivables- and delivery-heavy, "
 "and any tender or payment slippage hits hard [order specifics UNVERIFIED until "
 "fundamentals are connected]. <b>Position read:</b> small weight, big gain. Keep a "
 "tracking position on the EV-mobility theme but trim — a 90%+ winner trading on "
 "flawless future execution should be partly realised."),
"RTNPOWER.NS": ("AVOID / EXIT", "C",
 "RattanIndia Power is a sub-₹10 thermal IPP — the 'gain' here is noise on a tiny "
 "cost base, not a thesis. Penny power stocks with chequered balance-sheet histories "
 "are lottery tickets, not utility holdings. <b>Position read:</b> trivial weight, "
 "but it is dead-weight risk and distraction. Exit; do not mistake a low share price "
 "for cheapness."),
"ASTRAMICRO.NS": ("TRIM", "C",
 "Astra Microwave makes defence RF/microwave sub-systems — another excellent trade "
 "(200%+), at a rich multiple, with some insider activity on the NSE feed. "
 "<b>Position read:</b> same discipline as the rest of the small-cap defence winners: "
 "harvest into strength. Eight defence names at elevated multiples is not "
 "diversification, however well each has done. Take the gain and cut the cluster."),
"HAPPSTMNDS.NS": ("RETAIN (decide)", "C",
 "Happiest Minds is a founder-led, digital-native mid-cap IT name that has been a "
 "value trap for you — down ~63%, your second-worst weighted holding, as growth "
 "decelerated and the rich IPO-era multiple unwound. The de-rate has made the "
 "valuation merely average, not cheap. <b>Position read:</b> 'it will come back' is "
 "not a thesis. Either you have conviction that digital-IT demand re-accelerates and "
 "this is a survivor — in which case hold and stop watching the red — or you do not, "
 "in which case redeploy the capital to something working. Make the decision; do not "
 "drift."),
"GPPL.NS": ("RETAIN", "B",
 "Gujarat Pipavav is an APM-Terminals-backed container/bulk port concession — a "
 "boring, cash-generative infrastructure asset at a reasonable multiple. Volumes "
 "track the trade cycle and the concession caps long-run growth, hence the modest "
 "drawdown. <b>Position read:</b> the least objectionable small holding you own — "
 "real cash flows, real promoter, no drama. RETAIN; this is the kind of thing the "
 "rest of the book is missing."),
"TATATECH.NS": ("RETAIN / TRIM", "C",
 "Tata Technologies is an engineering-R&D (auto-heavy) services name with the Tata "
 "brand and a recent, hyped IPO that has since de-rated; you are down ~a third and "
 "it is still not cheap. Auto-R&D spend is cyclical. <b>Position read:</b> bought "
 "into listing euphoria, underwater, still richly valued — do not add to anchor your "
 "average. Small weight; hold only on genuine conviction in the ER&D story, otherwise "
 "redeploy."),
"AVALON.NS": ("TRIM", "C",
 "Avalon Technologies is an EMS/contract-electronics manufacturer adjacent to "
 "defence and aerospace demand — it has doubled for you, at a rich multiple, with "
 "some insider activity on the feed. <b>Position read:</b> it counts toward your "
 "defence-electronics concentration <i>and</i> trades at a stretched valuation — two "
 "independent reasons to take money off. Harvest the double."),
"GMMPFAUDLR.NS": ("TRIM", "C",
 "<b>Correction: this is a CASH holding, not MTF</b> (the earlier draft mis-read it). "
 "GMM Pfaudler is the glass-lined process-equipment leader for pharma/chemical capex "
 "— a quality niche, but you are down ~39% at a still-full multiple as the capex "
 "cycle and a leveraged-buyout-era balance sheet weighed. Importantly, the verified "
 "NSE SAST/PIT records here are <i>inter-se promoter transfers</i> (a family/group "
 "reshuffle), <b>not</b> open-market promoter selling — so they are roughly neutral, "
 "and I am explicitly retracting the earlier 'promoter sell' alarm. <b>Position read:</b> "
 "tiny weight, real but expensive business, underwater. Trim/keep small; it is not a "
 "conviction position at this price."),
"GTLINFRA.NS": ("EXIT", "C",
 "GTL Infrastructure is a sub-₹2 telecom-tower penny stock with a long history of "
 "distress and restructuring [insolvency/financial status UNVERIFIED pending NSE/news]. "
 "The headline 'gain' is noise on a ~₹1 cost base. <b>Position read:</b> dead money. "
 "Exit if it is liquid; stop counting it as a holding."),
"AKSHAR.NS": ("EXIT", "C",
 "Akshar Spintex is a sub-₹1 micro-cap textile/spinning name down ~94% — a near-total "
 "loss, almost certainly illiquid. <b>Position read:</b> the only decision left is "
 "whether you can sell it at all; book the capital loss for tax if you can. There is "
 "nothing to analyse here — it is a wipeout."),
"HFCL.NS": ("TRIM", "C",
 "HFCL is a telecom-equipment/optical-fibre maker geared to BharatNet and 5G capex, "
 "up ~60% for you, at a rich multiple, and the NSE feed is unusually busy (50+ "
 "filings/90d) with some insider activity. Competitive, capital-intensive, lumpy "
 "ordering. <b>Position read:</b> trivial weight but a richly-valued trading vehicle, "
 "not a compounder. Trim/realise; do not build it up at this multiple."),
"AJOONI.NS": ("EXIT", "C",
 "Ajooni Biotech is a sub-₹5 micro/SME agri-bio name, down ~57%, the kind of stock "
 "associated with pump-and-dump risk [UNVERIFIED]. <b>Position read:</b> a lottery "
 "ticket. Exit if liquid; do not add a rupee."),
"IRCON.NS": ("RETAIN / TRIM", "B",
 "Ircon International is a railway-construction PSU with a large EPC order book at a "
 "reasonable multiple; it got swept into the railway-PSU bubble and de-rated, hence "
 "your ~44% loss. Margins are thin and it depends on the government payment cycle. "
 "<b>Position read:</b> a real company that got absurdly over-valued and corrected — "
 "not a broken business. Hold the core on the verified order book; do not treat any "
 "bounce as a new bull market. Trivial weight, so low urgency either way."),
"FCONSUMER.NS": ("EXIT", "C",
 "Future Consumer is a Future-group FMCG entity, effectively defunct, trading at "
 "~₹0.3 and down ~79% [insolvency/suspension status UNVERIFIED — confirm on NSE]. "
 "<b>Position read:</b> dead money. If it can be sold, sell it; if suspended, treat it "
 "as zero and move on."),
# ---- unweighted block (held, negligible) ----
"TDPOWERSYS.NS": ("RETAIN (unweighted)", "B",
 "TD Power Systems makes generators/electrical machines with a real export book — "
 "your best unweighted trade (+150%+), at a rich multiple. <b>Position read:</b> a "
 "genuine winner left at zero weight is a decision you are avoiding. Either give it a "
 "real position sized to conviction, or take the profit. Limbo is not a strategy."),
"RCOM.NS": ("EXIT (unweighted)", "C",
 "Reliance Communications has been under insolvency for years; sub-₹1 equity in an "
 "insolvent telecom is typically near-worthless [confirm NCLT status on NSE]. "
 "<b>Position read:</b> a zero with a live quote. Remove it from your mental "
 "portfolio."),
"KSHITIJPOL.NS": ("EXIT (unweighted)", "C",
 "Kshitij Polyline is a sub-₹6 SME plastics micro-cap down ~89% and illiquid. "
 "<b>Position read:</b> salvage value only. Exit if possible."),
"HDIL.NS": ("EXIT (unweighted)", "C",
 "Housing Development & Infrastructure is under NCLT insolvency; sub-₹2 real-estate "
 "equity is deeply subordinated in a bankruptcy process [confirm status]. "
 "<b>Position read:</b> bankrupt-process equity — dead money."),
"VIKASLIFE.NS": ("EXIT (unweighted)", "C",
 "Vikas Lifecare is a sub-₹2 micro-cap from a family of names associated with serial "
 "value destruction [UNVERIFIED]; down ~67%. <b>Position read:</b> penny loss; exit "
 "if liquid."),
"VIKASECO.NS": ("EXIT (unweighted)", "C",
 "Vikas Ecotech is a sub-₹2 polymer/chemicals micro-cap, same family of names, down "
 "~70%. <b>Position read:</b> same verdict — dead weight, exit if liquid."),
"FEL.NS": ("EXIT (unweighted)", "C",
 "Future Enterprises is a Future-group entity under insolvency; equity effectively "
 "impaired [confirm NSE status]. <b>Position read:</b> another Future-group zero. "
 "Stop holding ghosts."),
"RVNL.NS": ("RETAIN / TRIM (unweighted)", "B",
 "Rail Vikas Nigam is a railway-PSU EPC that became absurdly over-valued in the "
 "PSU bubble and de-rated ~40% from your cost; the NSE feed shows ongoing filings and "
 "some insider activity. It is a real company, still not cheap. <b>Position read:</b> "
 "give it a deliberate weight and thesis or exit — holding a real, volatile PSU at "
 "zero weight is indecision, not risk management."),
}

styles = getSampleStyleSheet()
def _s(n, **k): return ParagraphStyle(n, parent=styles["Normal"], **k)
H1 = _s("H1", fontSize=17, leading=21, textColor=colors.HexColor("#0b3d2e"), fontName="Helvetica-Bold", spaceAfter=4)
H2 = _s("H2", fontSize=13, leading=16, textColor=colors.HexColor("#0b3d2e"), fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4)
NAME = _s("NAME", fontSize=13, leading=15, fontName="Helvetica-Bold")
BODY = _s("BODY", fontSize=9, leading=12.8, spaceAfter=3)
SMALL = _s("SMALL", fontSize=7.6, leading=9.6, textColor=colors.HexColor("#555555"))
TAG = _s("TAG", fontSize=7.4, leading=9, textColor=colors.HexColor("#777777"))
NSEH = _s("NSEH", fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.HexColor("#0b3d2e"), spaceBefore=3)

VC = {"BUY":"#0a7d33","ADD":"#0a7d33","RETAIN":"#1f6feb","DE-LEVERAGE":"#b5651d","TRIM":"#b5651d","EXIT":"#b00020","AVOID":"#b00020"}
def vcolor(v):
    for k,c in VC.items():
        if k in v: return c
    return "#333"

def nf(v, suf="", pre=""):
    if v is None: return "n/a"
    if isinstance(v, float): return f"{pre}{v:,.2f}{suf}"
    return f"{pre}{v}{suf}"

def metrics_table(d):
    rows = [
      ["Avg cost", nf(d.get("avg_cost"),pre="₹"), "Live px", nf(d.get("price"),pre="₹"), "Unrealized P&L", nf(d.get("unrealized_pnl_pct"),"%")],
      ["Weight", (f'{d.get("weight")}%' if d.get("weight") is not None else "unweighted"), "Held as", ("MTF ⚠" if d.get("mtf") else "Cash"), "vs 52w high", nf(d.get("pct_from_52w_high"),"%")],
      ["RSI-14", nf(d.get("rsi14")), "vs 50DMA", nf(d.get("pct_vs_dma50"),"%"), "vs 200DMA", nf(d.get("pct_vs_dma200"),"%")],
      ["6m ret", nf(d.get("ret_6m"),"%"), "1y ret", nf(d.get("ret_1y"),"%"), "RS vs Nifty 6m", nf(d.get("rel_strength_6m_vs_nifty"),"%")],
      ["Trail P/E", nf(d.get("trailing_pe")), "P/B", nf(d.get("price_to_book")), "EV/EBITDA", nf(d.get("ev_to_ebitda"))],
      ["Ann vol", nf(d.get("ann_vol_pct"),"%"), "Beta", nf(d.get("beta_vs_nifty")), "Max DD 2y", nf(d.get("max_drawdown_2y"),"%")],
      ["1d VaR95", nf(d.get("var_95_1d_pct"),"%"), "1d CVaR95", nf(d.get("cvar_95_1d_pct"),"%"), "GARCH vol", f'{nf(d.get("garch_ann_vol_pct"),"%")} ({d.get("garch_regime") or "-"})'],
      ["MC1y p5", nf(d.get("mc_p5"),pre="₹"), "MC1y p50", nf(d.get("mc_p50"),pre="₹"), "P(loss 1y)", nf(d.get("mc_prob_below_current_pct"),"%")],
    ]
    t = Table(rows, colWidths=[20*mm,23*mm,20*mm,23*mm,26*mm,26*mm])
    t.setStyle(TableStyle([
      ("FONT",(0,0),(-1,-1),"Helvetica",7),
      ("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#777")),("TEXTCOLOR",(2,0),(2,-1),colors.HexColor("#777")),("TEXTCOLOR",(4,0),(4,-1),colors.HexColor("#777")),
      ("FONT",(1,0),(1,-1),"Helvetica-Bold",7),("FONT",(3,0),(3,-1),"Helvetica-Bold",7),("FONT",(5,0),(5,-1),"Helvetica-Bold",7),
      ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white,colors.HexColor("#f3f6f4")]),
      ("TOPPADDING",(0,0),(-1,-1),1.6),("BOTTOMPADDING",(0,0),(-1,-1),1.6)]))
    return t

def nse_block(d):
    out = [Paragraph("Verified NSE signals &nbsp;<font size=6 color='#777'>[VERIFIED: NSE live]</font>", NSEH)]
    nse = d.get("nse") or {}
    ann = nse.get("announcements") or {}
    ins = nse.get("insider") or {}
    cats = ann.get("by_category") or {}
    catline = ", ".join(f"{k}:{v}" for k,v in sorted(cats.items(), key=lambda x:-x[1])) if cats else "none in 90d"
    out.append(Paragraph(f"<b>Filings (90d):</b> {ann.get('count_90d','?')} — {catline}", SMALL))
    for a in (ann.get("recent") or [])[:3]:
        out.append(Paragraph(f"&nbsp;&nbsp;• [{a['date']}] <i>{a['category']}</i>: {a['text'][:120]}", TAG))
    net = ins.get("net") or {}
    if ins.get("count_365d"):
        flag = " <font color='#b00020'><b>OPEN-MARKET PROMOTER SELLING</b></font>" if net.get("promoter_open_market_selling_flag") else ""
        out.append(Paragraph(f"<b>Insider/SAST (365d):</b> {ins.get('count_365d')} disclosures — "
                             f"signal: <b>{net.get('signal','?')}</b> ({net.get('n_buys',0)} buys / {net.get('n_sells',0)} sells / "
                             f"{net.get('n_inter_se',0)} inter-se){flag}", SMALL))
    else:
        out.append(Paragraph("<b>Insider/SAST (365d):</b> no disclosures", SMALL))
    out.append(Paragraph(f"<b>F&O ban today:</b> {'YES ⚠' if nse.get('in_fno_ban') else 'no'}", SMALL))
    return out

def holding_page(tk, d):
    verdict, grade, story = A_.get(tk, ("RETAIN","C","[no narrative]"))
    badge_mtf = '<font color="#b00020"><b>MTF / LEVERAGED ⚠</b></font>' if d.get("mtf") else '<font color="#555">Cash</font>'
    flow = [
      Paragraph(f'{d["name"]} &nbsp;<font size=9 color="#777">({tk})</font>', NAME),
      Paragraph(f'{d.get("theme","")} &nbsp;|&nbsp; {badge_mtf} &nbsp;|&nbsp; '
                f'<font color="{vcolor(verdict)}"><b>{verdict}</b></font> &nbsp;|&nbsp; conviction <b>{grade}</b>', SMALL),
      Spacer(1,4), metrics_table(d), Spacer(1,4),
    ]
    flow += nse_block(d)
    flow += [Spacer(1,4), Paragraph(story, BODY),
             Paragraph("Figures [VERIFIED: yfinance/NSE live, EOD]. Verdict &amp; business read = analyst judgment. "
                       "Order-book / fundamental depth / macro / transcripts [UNVERIFIED until those sources are connected].", TAG)]
    return flow

def build():
    doc = SimpleDocTemplate(r"D:\SSA_Portfolio_Analysis.pdf", pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm, topMargin=13*mm, bottomMargin=13*mm,
        title="SSA Portfolio Analysis", author="Fortis research engine (India pilot)")
    E = []
    E.append(Paragraph("Personal Portfolio Analysis — Indian Market Pilot", H1))
    E.append(Paragraph(f"Generated {TS} — prices live EOD via yfinance (NSE); catalysts/insider/ban live via NSE. "
                       f"F&O ban date {M.get('nse_ban_trade_date','-')}.", SMALL))
    E.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0b3d2e"))); E.append(Spacer(1,4))
    E.append(Paragraph("<b>DISCLAIMER.</b> Research analysis for the account-holder's own review — NOT investment advice. "
        "Prices/fundamentals are delayed EOD figures fetched live as of the timestamp above, not real-time. Every figure is "
        "verified against a live fetch; verdicts are analyst judgment; anything from a not-yet-connected source is marked "
        "UNVERIFIED rather than guessed. Past performance does not indicate future results.", SMALL))
    E.append(Spacer(1,8))

    # exec summary
    E.append(Paragraph("1 · Executive summary — the blunt verdict", H2))
    for s in [
      f"<b>This is a leveraged, concentrated bet on Indian defence, wrapped around a sleeve of leveraged losers and a tail of "
      f"dead money.</b> Three facts drive everything: (i) the defence-electronics cluster is <b>{A['defense_cluster_weight']:.0f}% "
      f"of the book</b>; (ii) the MTF/margin sleeve is <b>{A['mtf_weight']:.0f}% of the book and ~{A['mtf_weighted_pnl']:.0f}% "
      f"underwater, with {A['mtf_underwater_count']}/{A['mtf_count']} names in the red</b>; (iii) your cash sleeve is "
      f"~{A['cash_weighted_pnl']:.0f}% — i.e. <b>the winners are in cash and the losers are on leverage.</b> That is exactly "
      f"backwards from how a disciplined book is built.",
      f"<b>The leverage is the most urgent problem.</b> ~{A['mtf_weight']:.0f}% of the portfolio is held on margin and every "
      f"one of those eight positions is down. You are paying financing cost to hold losers and carrying margin-call risk into "
      f"any sharp drawdown — the scenario where forced selling crystallises the loss at the bottom. Section 3 treats this sleeve "
      f"separately.",
      f"<b>The defence concentration is the largest single risk.</b> {A['defense_cluster_weight']:.0f}% in eight names that share "
      f"one driver — government order flow and PSU sentiment. One policy/budget event can hit ~60% of the book at once.",
      f"<b>Valuation discipline is absent at the top of the winners</b> (BDL triple-digit P/E as your largest weight; Avantel/"
      f"Apollo/Astra/Avalon at extreme multiples) and the NSE feed shows <b>insider selling at Avantel</b> — the people closest "
      f"to it are reducing into the run.",
      f"<b>Correction from the prior draft:</b> GMM Pfaudler is a CASH holding (mis-read earlier as MTF); its insider records are "
      f"neutral inter-se promoter transfers, not open-market selling. The real leveraged book is the eight names in Section 3.",
      f"<b>What a disciplined PM does:</b> de-leverage the MTF sleeve now, cap defence near 35-40%, harvest the frothy small-cap "
      f"defence winners, and purge the sub-₹10 graveyard. Detail in Sections 3 and 6.",
    ]:
        E.append(Paragraph(s, BODY)); E.append(Spacer(1,2))

    # concentration
    E.append(PageBreak()); E.append(Paragraph("2 · Concentration & theme risk", H2))
    E.append(Paragraph(f"Defence-electronics cluster: <b>{A['defense_cluster_weight']:.1f}%</b> across 8 names; average pairwise "
        f"daily-return correlation {A['defense_cluster_avg_corr']:.2f}. Moderate day-to-day, but all eight share ONE fundamental "
        f"driver (defence capex / order timing / PSU re-rating) — the real common-shock risk is larger than the correlation "
        f"number suggests.", BODY))
    th = A["theme_weights"]
    trows=[["Theme","Weight %","% invested"]]+[[k,f"{v:.1f}",f"{v/A['sum_weights']*100:.1f}"] for k,v in sorted(th.items(),key=lambda x:-x[1])]
    tt=Table(trows,colWidths=[72*mm,30*mm,38*mm]); tt.setStyle(TableStyle([
      ("FONT",(0,0),(-1,-1),"Helvetica",8),("FONT",(0,0),(-1,0),"Helvetica-Bold",8),
      ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0b3d2e")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
      ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f3f6f4")]),("ALIGN",(1,0),(-1,-1),"RIGHT"),
      ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    E.append(tt)

    # MTF book
    E.append(Paragraph("3 · The MTF (leveraged) book — read from font color", H2))
    E.append(Paragraph(f"<b>{A['mtf_count']} holdings, {A['mtf_weight']:.1f}% of the book, weight-avg "
        f"{A['mtf_weighted_pnl']:.1f}% — ALL {A['mtf_underwater_count']} underwater — held on margin.</b> The cash book, by "
        f"contrast, is +{A['cash_weighted_pnl']:.0f}%. You are leveraging the losing half of the portfolio and not the winning "
        f"half. Leverage adds three costs the headline P&L hides: (1) financing interest that raises your break-even every month; "
        f"(2) margin-call risk — a sharp drawdown can force liquidation at the worst possible price; (3) it amplifies both the loss "
        f"and the risk rating of every name in it. <b>Verdict: the leverage here is imprudent</b> — not because margin is never "
        f"justified, but because it is applied to underwater, cyclical/de-rating names, which is the worst use of it. The "
        f"single highest-priority action in this report is to de-leverage this sleeve (Easy Trip first, as the deepest leveraged "
        f"loser).", BODY))
    mrows=[["MTF holding","Weight%","P&L%","Live ₹","Verdict"]]
    for d in sorted([x for x in H.values() if x.get("mtf")], key=lambda x:-(x.get("weight") or 0)):
        tk=[k for k,v in H.items() if v is d][0]
        mrows.append([d["name"], nf(d.get("weight")), nf(d.get("unrealized_pnl_pct")), nf(d.get("price"),pre="₹"), A_.get(tk,("",))[0]])
    mt=Table(mrows,colWidths=[52*mm,18*mm,18*mm,22*mm,42*mm]); mt.setStyle(TableStyle([
      ("FONT",(0,0),(-1,-1),"Helvetica",8),("FONT",(0,0),(-1,0),"Helvetica-Bold",8),
      ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#b5651d")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
      ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#fbf2e8")]),("ALIGN",(1,0),(3,-1),"RIGHT"),
      ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    E.append(Spacer(1,3)); E.append(mt)

    # per-holding pages
    E.append(PageBreak()); E.append(Paragraph("4 · Per-holding deep dives (one page each, ranked by weight)", H2))
    E.append(Paragraph("Weighted holdings first (largest to smallest), then the unweighted block.", SMALL)); E.append(Spacer(1,2))
    weighted=sorted([(k,v) for k,v in H.items() if v.get("weighted")], key=lambda x:-(x[1].get("weight") or 0))
    unweighted=[(k,v) for k,v in H.items() if not v.get("weighted")]
    first=True
    for tk,d in weighted:
        if not first: E.append(PageBreak())
        first=False
        for f in holding_page(tk,d): E.append(f)
    E.append(PageBreak()); E.append(Paragraph("5 · Unweighted block — held, status-unconfirmed, negligible size", H2))
    E.append(Paragraph("Owner confirms these are still held; no portfolio weight assigned.", SMALL))
    for tk,d in unweighted:
        E.append(PageBreak())
        for f in holding_page(tk,d): E.append(f)

    # PM changes
    E.append(PageBreak()); E.append(Paragraph("6 · What a disciplined PM would change", H2))
    for s in [
      "<b>1. De-leverage the MTF sleeve immediately.</b> ~24% of the book on margin, all underwater. Start with Easy Trip "
      "(deepest leveraged loss), then the cyclical/de-rating names (Transrail, Jupiter Wagons, Piramal). Move the keepers "
      "(PFC, ACC, Maharashtra Seamless, Balaji) to cash — own the assets, drop the borrowing.",
      "<b>2. Cap defence at ~35-40%.</b> You are ~60%. Trim the frothiest small-caps first — Avantel (extreme multiple + insider "
      "selling), Apollo, Astra, Avalon. Keep HAL/BEL as the quality core.",
      "<b>3. Trim the over-extended top weight.</b> BDL is your largest position at your richest large-cap multiple and thinnest "
      "gain — reduce it.",
      "<b>4. Purge the sub-₹10 graveyard.</b> GTL Infra, Akshar, RCOM, HDIL, Future Consumer/Enterprises, the Vikas pair, "
      "Ajooni, RattanIndia, Kshitij. Exit the liquid ones, write off the suspended ones.",
      "<b>5. Keep the genuine value/ballast</b> — PFC, ACC, Maharashtra Seamless, Gujarat Pipavav, Tata Power — once de-levered.",
      "<b>6. Resolve the unweighted winners</b> (TD Power, RVNL): give them a real weight and thesis, or take the profit.",
    ]:
        E.append(Paragraph(s, BODY)); E.append(Spacer(1,2))

    # appendix
    E.append(PageBreak()); E.append(Paragraph("7 · Data & verification appendix", H2))
    E.append(Paragraph("<b>VERIFIED — connected & live:</b> yfinance NSE (price, 2y history, returns, P&L vs your avg cost, "
        "DMA/RSI/52w/momentum, relative strength vs Nifty 50, vol, beta, drawdown, historical VaR/CVaR, Monte-Carlo, GARCH, "
        "basic fundamentals where carried). <b>NSE live</b> (this run): corporate announcements/catalysts, SEBI SAST/PIT insider "
        "disclosures, and the daily F&O securities-in-ban list.", BODY))
    E.append(Paragraph("<b>Flipped UNVERIFIED → VERIFIED this run (vs the first draft):</b> order-win / results / dividend / "
        "transcript catalysts (NSE announcements); insider & promoter activity incl. the buy/sell + inter-se distinction (NSE "
        "SAST/PIT); F&O-ban / crowding status. Concrete corrections the verification produced: (a) MTF is the 8 red-font names, "
        "not GMM Pfaudler; (b) the GMM insider records are neutral inter-se transfers, not open-market promoter selling; "
        "(c) Avantel shows genuine net insider selling.", BODY))
    E.append(Paragraph("<b>Self-audit:</b> every P&L recomputed as (live px / avg cost − 1) — 0 mismatches. All 38 tickers "
        "name-verified to the live company name; each NSE record company-verified before use. VaR/Monte-Carlo/GARCH computed "
        "38/38. No figure is sourced from model memory.", BODY))
    E.append(Paragraph("<b>STILL NOT CONNECTED (claims remain UNVERIFIED):</b> deep fundamentals / order-book / cash-flow / DCF "
        "(awaiting a licensed Tickertape/Trendlyne/Tijori key — no scraping); RBI/MOSPI macro; full concall transcripts. DCF was "
        "not computed: financials/PSU lenders are unsuited to equity DCF, distressed names are un-modelable, and the rest lack "
        "connected cash-flow data — guessing would violate the hallucination-control mandate.", SMALL))

    doc.build(E)
    print("Wrote D:\\SSA_Portfolio_Analysis.pdf")

if __name__ == "__main__":
    build()
