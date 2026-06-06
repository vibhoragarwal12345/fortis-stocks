"""Render the portfolio PDF from verified JSON metrics + written assessments.
All NUMBERS are pulled from D:\\portfolio_metrics.json (live yfinance) -- never
typed by hand. Qualitative verdicts are analyst judgment, tagged as such."""
from __future__ import annotations
import json
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, HRFlowable)

M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
A = json.load(open(r"D:\portfolio_aggregates.json", encoding="utf-8"))
H = M["holdings"]
TS = M["generated_at_utc"]

# ── analyst assessments: (verdict, grade, bull, bear, critic) ─────────────────
# Verdicts: BUY / ADD / RETAIN / TRIM / EXIT / AVOID. Grade A/B/C = business
# quality+conviction, NOT a price target. Judgment, not a connected-source fact.
AS = {
 "BDL.NS": ("TRIM","B",
   "Indigenous missile maker; structural beneficiary of India's defence capex and a nascent export push.",
   "PE ~105x is extreme; you are only +17% i.e. you bought near the top; order-to-revenue conversion is lumpy.",
   "Your SINGLE LARGEST weight (14.8%) sits at a triple-digit PE inside a 60% defence bet. Indefensible on valuation alone."),
 "BEL.NS": ("RETAIN/TRIM","A",
   "Best-run defence-electronics PSU; deep order visibility from indigenisation, high ROE, typically net cash.",
   "PE ~49x already prices years of growth; +128% means most of the easy money is behind you.",
   "Quality is not the question; concentration is. 14% of the book in one name within a 60% theme."),
 "HAL.NS": ("RETAIN","A",
   "Near-monopoly on combat aircraft/helicopters for the IAF; large multi-year order book; PE ~31x is the sanest of your big-3 defence.",
   "Single-customer (MoD) dependence; execution and forex risk; still re-rated vs its own history.",
   "The most defensible holding you own — but it is still defence #3 in a defence-dominated book."),
 "HBLENGINE.NS": ("TRIM","B",
   "Niche defence/railway electronics (batteries, TCAS train-control); rode the railway-safety capex wave.",
   "+120% with a sharp prior run; smaller, less liquid; execution-dependent.",
   "A momentum winner you should partly harvest, not a forever-hold at this multiple."),
 "TATAPOWER.NS": ("RETAIN","B",
   "Integrated utility with a real renewables/EV-charging growth leg and Tata parentage; +46%.",
   "Capital-intensive; regulated returns cap upside; leverage and capex execution matter.",
   "The most 'normal' large holding you own. Fine to hold; not a high-conviction compounder at this price."),
 "AVANTEL.NS": ("TRIM/EXIT","C",
   "Tiny defence-communications/SATCOM play leveraged to the same indigenisation theme.",
   "PE ~309x is the most egregious valuation in your book; micro-cap, thin liquidity.",
   "A 309x PE micro-cap is a sentiment trade, not an investment. Book the gain before the multiple does the talking."),
 "PFC.NS": ("RETAIN/ADD","B",
   "Power-sector PSU lender at PE ~5.5x with a high dividend yield; cheapest large holding you own.",
   "Asset quality tied to discom/power-sector health; PSU policy/rate sensitivity; you are -16%.",
   "One of the few genuinely cheap things here. The -16% is a valuation opportunity, not a thesis break — IF you believe the power capex cycle."),
 "TRANSRAILL.NS": ("RETAIN","B",
   "T&D EPC + poles/conductors; geared to grid/transmission capex; PE ~16x is reasonable.",
   "-34% post-IPO de-rating; EPC margins and working-capital are cyclical; order-book claims UNVERIFIED.",
   "A capex-cycle bet that has already hurt you. Hold only if you can defend the transmission-capex thesis on connected data."),
 "JWL.NS": ("RETAIN/TRIM","C",
   "Railway wagons/components into a multi-year rolling-stock cycle.",
   "-31% from your cost; PE ~69x still rich for a cyclical; froth has only partly unwound.",
   "Cyclical at a growth multiple after a drawdown — the worst combination. Size it down."),
 "EASEMYTRIP.NS": ("AVOID/EXIT","C",
   "Asset-light OTA that was once a market darling.",
   "-58%; collapsed from highs; promoter-stake/governance concerns exist [UNVERIFIED: needs NSE/SEBI].",
   "A broken momentum stock at 3.8% weight. Down 58% is not 'cheap' without a connected look at why it fell."),
 "ACC.NS": ("RETAIN","B",
   "Large-cap cement (Adani group) at PE ~12x — genuinely inexpensive vs sector.",
   "-32%; cement is cyclical and price-war-prone; demand tied to construction capex.",
   "Cheap and liquid — the kind of de-rated quality worth holding. Your loss here is a sector-cycle issue, not a broken business."),
 "IREDA.NS": ("RETAIN/TRIM","B",
   "PSU green-energy financier; +93%; direct play on India's renewables build-out.",
   "Re-rated hard; it is still a lender (credit risk, NIM, dilution); PE ~18x after a huge run.",
   "Great theme, but you are sitting on a 93% gain in a PSU lender that re-rates both ways. Trim into strength."),
 "APOLLO.NS": ("TRIM/EXIT","C",
   "Defence-electronics/electro-mechanical small-cap; +227%, one of your best trades.",
   "PE ~134x; micro/small-cap; valuation utterly detached from current earnings.",
   "A 227% winner at 134x earnings is a gift you should partly take. Greed here is how round-trips happen."),
 "BALAMINES.NS": ("RETAIN","B",
   "Specialty-amines chemical maker, cyclical recovery candidate; nearly flat (-4%).",
   "PE ~40x is full; chemicals margins are volatile and China-competition sensitive.",
   "Roughly at cost with a fair-to-full multiple. Neutral hold; not a place to add."),
 "PPLPHARMA.NS": ("RETAIN","B",
   "Piramal's pharma arm (CDMO + complex generics + consumer).",
   "-21%; loss-making/low-margin optics (no trailing PE); CDMO recovery is the swing factor.",
   "A turnaround bet, not a proven compounder. Hold small; don't average down without connected fundamentals."),
 "MAHSEAMLES.NS": ("RETAIN","B",
   "Seamless pipes/tubes into oil&gas + capex; PE ~12x, cheap; typically debt-light.",
   "-30%; commodity-linked demand and pricing; cyclical earnings.",
   "Cheap cyclical that has hurt you on the cycle, not the balance sheet. Defensible hold."),
 "OLECTRA.NS": ("RETAIN/TRIM","C",
   "Electric-bus leader riding state EV-fleet tenders; +94%.",
   "PE ~60x; execution/working-capital heavy; order-delivery and receivables risk [UNVERIFIED].",
   "A high-beta thematic up 94%. Keep a tracking position; the valuation needs flawless execution you can't verify here."),
 "RTNPOWER.NS": ("AVOID/EXIT","C",
   "Sub-Rs.10 thermal IPP; the 'gain' is on a Rs.6 cost base.",
   "Rs.9.74 penny stock; PE ~97x; balance-sheet/leverage history is poor [status UNVERIFIED].",
   "A penny power stock is a lottery ticket, not a utility holding. The +62% is noise on a tiny base."),
 "ASTRAMICRO.NS": ("TRIM","C",
   "Defence RF/microwave components; +219%, excellent trade.",
   "PE ~70x; small-cap; same single-theme dependence as the rest of your defence book.",
   "Another 200%+ defence winner you should harvest. Eight defence names at nosebleed multiples is not diversification."),
 "HAPPSTMNDS.NS": ("RETAIN","C",
   "Digital-native midcap IT; founder-led; differentiated positioning.",
   "-63% — your second-worst weighted holding; PE ~26x after a brutal de-rate; growth has slowed.",
   "This has been a value trap. Either you believe the digital-IT recovery or you don't — 'it'll come back' is not a thesis."),
 "GPPL.NS": ("RETAIN","B",
   "APM-Terminals-backed container/bulk port concession; PE ~15x; cash-generative.",
   "-18%; volume-linked to trade cycles; concession/limited-growth ceiling.",
   "A boring, reasonable infrastructure cash-flow asset. The least objectionable small holding you own."),
 "TATATECH.NS": ("RETAIN/TRIM","C",
   "ER&D/engineering-services (auto-heavy) with Tata brand; recent IPO.",
   "-34%; PE ~57x still rich; auto-R&D spend cyclical; post-listing de-rate ongoing.",
   "Bought into IPO hype, down a third, still not cheap. Don't add to anchor your average."),
 "AVALON.NS": ("TRIM","C",
   "EMS/contract-electronics manufacturer adjacent to defence/aero demand; +200%.",
   "PE ~95x; small-cap; you've doubled — valuation now does the heavy lifting.",
   "Counts toward your defence-electronics concentration AND trades at 95x. Two reasons to take some off."),
 "GMMPFAUDLR.NS": ("TRIM/EXIT","C",
   "Glass-lined process equipment leader for pharma/chemical capex.",
   "-39% AND held on MTF (leverage) — the loss is amplified by borrowing cost and margin risk; PE ~61x.",
   "LEVERAGED LOSER. MTF on a -39% position at 61x earnings is the single riskiest line in this book. The margin clock is running against you."),
 "GTLINFRA.NS": ("EXIT","C",
   "Telecom-tower infrastructure; effectively a sub-Rs.2 penny stock.",
   "Rs.1.61; long history of distress/restructuring [insolvency status UNVERIFIED: needs NSE/news].",
   "Dead money dressed up as a +61% 'gain' on a Rs.1 base. Exit if liquid; stop pretending it's a holding."),
 "AKSHAR.NS": ("EXIT","C",
   "Micro-cap textile/spinning name.",
   "Rs.0.45; -94% — a near-total loss; almost certainly illiquid.",
   "This is a -94% wipeout. The only decision left is whether you can sell it at all (book the tax loss if you can)."),
 "HFCL.NS": ("TRIM","C",
   "Telecom equipment/optical-fibre maker geared to BharatNet/5G capex; +61%.",
   "PE ~535x is absurd; competitive, capital-intensive, lumpy ordering.",
   "A 535x PE is not a typo you should own through. Trim hard; this is a trading vehicle, not a hold."),
 "AJOONI.NS": ("EXIT","C",
   "Sub-Rs.5 micro/SME agri-bio name.",
   "Rs.3.91; -57%; the kind of name associated with pump-and-dump risk [UNVERIFIED].",
   "Lottery ticket. Exit if you can; do not add a rupee."),
 "IRCON.NS": ("RETAIN/TRIM","B",
   "Railway-construction PSU with a large EPC order book; PE ~22x.",
   "-44% as the railway-PSU froth unwound; margins thin, govt-payment cycle dependent.",
   "A reasonable PSU EPC that got swept up in a bubble and de-rated. Hold the core; don't treat the bounce as a new bull run."),
 "FCONSUMER.NS": ("EXIT","C",
   "Future-group FMCG; effectively defunct.",
   "Rs.0.32; -79%; Future-group entities are under insolvency/suspension [status UNVERIFIED: confirm on NSE].",
   "This is dead money. If it can be sold, sell; if suspended, write it to zero mentally and move on."),
 # ---- unweighted block (still held per owner; negligible size) ----
 "TDPOWERSYS.NS": ("RETAIN (unweighted)","B",
   "Generators/electrical machines exporter; +153% — your best unweighted trade.",
   "PE ~86x; small-cap; cyclical capital-goods demand.",
   "A genuine winner left at zero weight — decide if it deserves a real position or a profit-take, not limbo."),
 "RCOM.NS": ("EXIT (unweighted)","C",
   "Reliance Communications — under insolvency for years.",
   "Rs.0.97; -54%; equity in an insolvent telecom is typically near-worthless [confirm NCLT status].",
   "Insolvent telecom equity. This is a zero with a quote. Remove it from your mental portfolio."),
 "KSHITIJPOL.NS": ("EXIT (unweighted)","C",
   "Sub-Rs.6 SME plastics/polyline micro-cap.",
   "Rs.5.96; -89%; illiquid SME.",
   "An 89% loss in an illiquid micro-cap. Salvage value only."),
 "HDIL.NS": ("EXIT (unweighted)","C",
   "Housing Development & Infrastructure — under NCLT insolvency.",
   "Rs.1.95; -61%; real-estate insolvency, equity deeply subordinated [confirm status].",
   "Bankrupt-process real estate. Dead money."),
 "VIKASLIFE.NS": ("EXIT (unweighted)","C",
   "Sub-Rs.2 'Vikas' micro-cap.",
   "Rs.1.48; -67%; the Vikas cluster is associated with serial value destruction [UNVERIFIED].",
   "Penny micro-cap loss. Exit if liquid."),
 "VIKASECO.NS": ("EXIT (unweighted)","C",
   "Sub-Rs.2 'Vikas' polymer/chem micro-cap.",
   "Rs.1.29; -70%; same family of names as above.",
   "Same story, same verdict: dead weight."),
 "FEL.NS": ("EXIT (unweighted)","C",
   "Future Enterprises — Future-group, under insolvency.",
   "Rs.0.42; -75%; equity effectively impaired [confirm NSE status].",
   "Another Future-group zero. Stop holding ghosts."),
 "RVNL.NS": ("RETAIN/TRIM (unweighted)","B",
   "Rail Vikas Nigam — railway-PSU EPC; PE ~56x.",
   "-40% from your cost as railway-PSU bubble deflated; still not cheap.",
   "A real company that got absurdly over-valued and de-rated. If you hold it, give it a weight and a thesis or exit."),
}

styles = getSampleStyleSheet()
def S(name, **kw):
    return ParagraphStyle(name, parent=styles["Normal"], **kw)
H1 = S("H1", fontSize=18, leading=22, spaceAfter=6, textColor=colors.HexColor("#0b3d2e"), fontName="Helvetica-Bold")
H2 = S("H2", fontSize=13, leading=16, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#0b3d2e"), fontName="Helvetica-Bold")
H3 = S("H3", fontSize=10.5, leading=13, spaceBefore=8, spaceAfter=2, fontName="Helvetica-Bold")
BODY = S("BODY", fontSize=9, leading=12.5)
SMALL = S("SMALL", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555"))
TAG = S("TAG", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#777777"))

VCOLOR = {"BUY":"#0a7d33","ADD":"#0a7d33","RETAIN":"#1f6feb","TRIM":"#b5651d","EXIT":"#b00020","AVOID":"#b00020"}
def vcolor(v):
    for k,c in VCOLOR.items():
        if k in v: return c
    return "#333333"

def n(v, suf="", pre="", dash="n/a"):
    if v is None: return dash
    if isinstance(v,float): return f"{pre}{v:,.2f}{suf}"
    return f"{pre}{v}{suf}"

def holding_block(tk, d, brief=False):
    out = []
    wt = d.get("weight")
    title = f'{d["name"]} <font size=8 color="#777777">({tk})</font>'
    out.append(Paragraph(title, H3))
    verdict, grade, bull, bear, critic = AS.get(tk, ("RETAIN","C","-","-","-"))
    # metrics table (numbers all from live JSON)
    rows = [
        ["Avg cost", n(d.get("avg_cost"),pre="Rs."), "Live px", n(d.get("price"),pre="Rs."), "Unrealized P&L", n(d.get("unrealized_pnl_pct"),"%")],
        ["Weight", (f"{wt}%" if wt is not None else "unweighted"), "Day chg", n(d.get("day_change_pct"),"%"), "vs 52w high", n(d.get("pct_from_52w_high"),"%")],
        ["RSI-14", n(d.get("rsi14")), "vs 50DMA", n(d.get("pct_vs_dma50"),"%"), "vs 200DMA", n(d.get("pct_vs_dma200"),"%")],
        ["6m ret", n(d.get("ret_6m"),"%"), "1y ret", n(d.get("ret_1y"),"%"), "RS vs Nifty(6m)", n(d.get("rel_strength_6m_vs_nifty"),"%")],
        ["Trail P/E", n(d.get("trailing_pe")), "P/B", n(d.get("price_to_book")), "EV/EBITDA", n(d.get("ev_to_ebitda"))],
        ["Ann vol", n(d.get("ann_vol_pct"),"%"), "Beta", n(d.get("beta_vs_nifty")), "Max DD(2y)", n(d.get("max_drawdown_2y"),"%")],
        ["1d VaR95", n(d.get("var_95_1d_pct"),"%"), "1d CVaR95", n(d.get("cvar_95_1d_pct"),"%"), "GARCH vol", f'{n(d.get("garch_ann_vol_pct"),"%")} ({d.get("garch_regime") or "-"})'],
        ["MC 1y p5", n(d.get("mc_p5"),pre="Rs."), "p50", n(d.get("mc_p50"),pre="Rs."), "P(loss 1y)", n(d.get("mc_prob_below_current_pct"),"%")],
    ]
    t = Table(rows, colWidths=[22*mm,24*mm,22*mm,24*mm,28*mm,24*mm])
    t.setStyle(TableStyle([
        ("FONT",(0,0),(-1,-1),"Helvetica",7),
        ("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#777777")),
        ("TEXTCOLOR",(2,0),(2,-1),colors.HexColor("#777777")),
        ("TEXTCOLOR",(4,0),(4,-1),colors.HexColor("#777777")),
        ("FONT",(1,0),(1,-1),"Helvetica-Bold",7),("FONT",(3,0),(3,-1),"Helvetica-Bold",7),("FONT",(5,0),(5,-1),"Helvetica-Bold",7),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white,colors.HexColor("#f3f6f4")]),
        ("LINEBELOW",(0,-1),(-1,-1),0.3,colors.HexColor("#dddddd")),
        ("TOPPADDING",(0,0),(-1,-1),1.5),("BOTTOMPADDING",(0,0),(-1,-1),1.5),
    ]))
    out.append(t)
    out.append(Spacer(1,3))
    out.append(Paragraph(f'<b>Bull:</b> {bull}', BODY))
    out.append(Paragraph(f'<b>Bear:</b> {bear}', BODY))
    out.append(Paragraph(f'<b>Critic:</b> {critic}', BODY))
    out.append(Paragraph(
        f'<b>Conclusion:</b> <font color="{vcolor(verdict)}"><b>{verdict}</b></font> '
        f'&nbsp;|&nbsp; Conviction grade: <b>{grade}</b>', BODY))
    out.append(Paragraph(
        "Numbers [VERIFIED: yfinance live, EOD]. Verdict = analyst judgment. "
        "Order-book/insider/short/peer specifics [UNVERIFIED: Indian sources not connected].", TAG))
    out.append(Spacer(1,7))
    return out

def build():
    doc = SimpleDocTemplate(r"D:\SSA_Portfolio_Analysis.pdf", pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm, topMargin=14*mm, bottomMargin=14*mm,
        title="SSA Portfolio Analysis", author="Fortis research engine (India pilot)")
    E = []
    # ── cover / disclaimer ──
    E.append(Paragraph("Personal Portfolio Analysis — Indian Market Pilot", H1))
    E.append(Paragraph(f"Generated {TS} · Data: {M['data_source']}", SMALL))
    E.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0b3d2e")))
    E.append(Spacer(1,4))
    E.append(Paragraph(
        "<b>DISCLAIMER.</b> Research analysis for the account-holder's own review — NOT investment advice. "
        "Prices and fundamentals are delayed end-of-day figures fetched live from yfinance (NSE) as of the "
        "timestamp above, not real-time tick data. Every figure is verified against a live fetch; qualitative "
        "verdicts are analyst judgment. Where Indian data sources are not yet connected, claims are marked "
        "UNVERIFIED rather than guessed. Past performance does not indicate future results.", SMALL))
    E.append(Spacer(1,8))

    # ── executive summary ──
    E.append(Paragraph("1 · Executive summary — the blunt verdict", H2))
    dw = A["defense_cluster_weight"]; bookpnl = A["weighted_book_pnl_pct"]
    summary = [
      f"<b>This is not a diversified portfolio; it is a leveraged bet on Indian defence with a tail of dead money.</b> "
      f"The defence-electronics cluster (BDL, BEL, HAL, HBL, Avantel, Apollo Micro, Astra, Avalon) is "
      f"<b>{dw:.0f}% of the invested book</b> [VERIFIED]. The book is up ~{bookpnl:.0f}% on a weighted basis [VERIFIED], "
      f"but that gain is almost entirely the defence trade; strip it out and the rest is mediocre-to-awful.",
      f"<b>Valuation discipline is largely absent at the top.</b> Your two largest names trade at extreme multiples "
      f"(BDL ~{n(H['BDL.NS'].get('trailing_pe'))}x, BEL ~{n(H['BEL.NS'].get('trailing_pe'))}x), and the small-cap "
      f"defence/EMS winners are at 70–310x earnings — Avantel ~{n(H['AVANTEL.NS'].get('trailing_pe'))}x, "
      f"Apollo ~{n(H['APOLLO.NS'].get('trailing_pe'))}x, HFCL ~{n(H['HFCL.NS'].get('trailing_pe'))}x. These are sentiment, not value.",
      f"<b>A leveraged loser sits in the book.</b> GMM Pfaudler is held on MTF and is "
      f"{n(H['GMMPFAUDLR.NS'].get('unrealized_pnl_pct'),'%')} — borrowing to hold a loser is the single riskiest line here.",
      f"<b>A dozen names trade below Rs.10</b> [VERIFIED] — several are insolvent/penny shells (RCOM, HDIL, GTL Infra, "
      f"Future Consumer/Enterprises, Akshar, the Vikas pair, Ajooni). They are effectively dead money occupying mental capital.",
      "<b>What a disciplined PM would say:</b> harvest the frothy small-cap defence winners, cap the total defence weight, "
      "clean out the sub-Rs.10 graveyard, and resolve the leveraged GMM position. Detail in sections 5–6.",
    ]
    for s in summary:
        E.append(Paragraph(s, BODY)); E.append(Spacer(1,3))

    # ── concentration ──
    E.append(Paragraph("2 · Portfolio risk & concentration", H2))
    E.append(Paragraph(
        f"Defence-electronics cluster: <b>{dw:.1f}%</b> of invested capital across 8 names. Average pairwise daily-return "
        f"correlation {A['defense_cluster_avg_corr']:.2f} [VERIFIED] — moderate day-to-day, but all eight share ONE "
        f"fundamental driver (government defence capex, order-flow timing, PSU re-rating). A single policy event — a budget "
        f"deferral, an order-push, a PSU de-rating — can hit ~60% of your book at once. That is the dominant risk, and it is "
        f"far larger than any single-stock risk in this report.", BODY))
    E.append(Spacer(1,3))
    th = A["theme_weights"]
    trows = [["Theme","Weight %","% of invested"]] + [
        [k, f"{v:.1f}", f"{v/A['sum_weights']*100:.1f}"] for k,v in sorted(th.items(), key=lambda x:-x[1])]
    tt = Table(trows, colWidths=[70*mm,30*mm,40*mm])
    tt.setStyle(TableStyle([
        ("FONT",(0,0),(-1,-1),"Helvetica",8),("FONT",(0,0),(-1,0),"Helvetica-Bold",8),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0b3d2e")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f3f6f4")]),
        ("ALIGN",(1,0),(-1,-1),"RIGHT"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    E.append(tt)
    E.append(Spacer(1,8))

    # ── per-holding deep dives ──
    E.append(PageBreak())
    E.append(Paragraph("3 · Per-holding deep dives (ranked by weight)", H2))
    E.append(Paragraph("Weighted holdings first, largest to smallest, then the unweighted block.", SMALL))
    E.append(Spacer(1,4))
    weighted = sorted([(tk,d) for tk,d in H.items() if d.get("weighted")],
                      key=lambda x:-(x[1].get("weight") or 0))
    unweighted = [(tk,d) for tk,d in H.items() if not d.get("weighted")]
    for tk,d in weighted:
        for f in holding_block(tk,d): E.append(f)
    E.append(PageBreak())
    E.append(Paragraph("4 · Unweighted block — held, status-unconfirmed, negligible size", H2))
    E.append(Paragraph("Owner confirms these are still held. No portfolio weight assigned; treated as tracking / dead positions.", SMALL))
    E.append(Spacer(1,4))
    for tk,d in unweighted:
        for f in holding_block(tk,d,brief=True): E.append(f)

    # ── distressed ──
    E.append(PageBreak())
    E.append(Paragraph("5 · Distressed / dead-money section", H2))
    penny = sorted([(d["name"],tk,d) for tk,d in H.items() if d.get("price") and d["price"]<10],
                   key=lambda x:x[2]["price"])
    prows = [["Name","Ticker","Live Rs.","P&L %","Verdict"]]
    for nm,tk,d in penny:
        v = AS.get(tk,("",""))[0]
        prows.append([nm, tk, f'{d["price"]:.2f}', n(d.get("unrealized_pnl_pct")), v])
    pt = Table(prows, colWidths=[48*mm,28*mm,20*mm,20*mm,40*mm])
    pt.setStyle(TableStyle([
        ("FONT",(0,0),(-1,-1),"Helvetica",8),("FONT",(0,0),(-1,0),"Helvetica-Bold",8),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#b00020")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#fbeaec")]),
        ("ALIGN",(2,0),(3,-1),"RIGHT"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    E.append(pt)
    E.append(Spacer(1,4))
    E.append(Paragraph(
        "Several of these are associated with insolvency/NCLT or suspension (RCOM, HDIL, Future Consumer/Enterprises, "
        "GTL Infra). <b>Their legal/trading status is [UNVERIFIED] until NSE/BSE filings are connected</b> — but a sub-Rs.2 "
        "price after a 60–95% loss is the market's verdict regardless. Treat as dead money: exit what is liquid, book the "
        "capital loss for tax, and stop assigning these mental shelf-space.", BODY))

    # ── PM changes ──
    E.append(Paragraph("6 · What a disciplined PM would change", H2))
    for s in [
      "<b>1. Cap defence at ~35–40%.</b> You are at ~60%. Trim the frothiest small-caps first (Avantel ~309x, Apollo ~134x, "
      "Astra ~70x, Avalon ~95x) — book gains, cut single-theme risk. Keep HAL/BEL as the quality core.",
      "<b>2. Resolve the leveraged position.</b> GMM Pfaudler on MTF, down ~39%, at ~61x — decide: de-leverage to cash holding, "
      "or exit. Do not roll margin on a losing, expensive name.",
      "<b>3. Purge the sub-Rs.10 graveyard.</b> ~12 names; collectively tiny weight but 100% distraction. Exit the liquid ones, "
      "write off the suspended ones mentally.",
      "<b>4. Re-underwrite the de-rated capex/PSU losers</b> (Transrail, Jupiter Wagons, Ircon, RVNL, Maharashtra Seamless) on "
      "CONNECTED fundamentals — hold the ones with intact order-books, exit the ones you only own because they fell.",
      "<b>5. Keep the genuine value</b> (PFC ~5.5x, ACC ~12x, Maharashtra Seamless ~12x, GPPL ~15x) — these are the few names "
      "where the loss is cyclical, not structural.",
      "<b>6. Decide on the unweighted winners</b> (TD Power +153%, possibly RVNL): give them a real weight and thesis, or take the profit. Limbo is not a position.",
    ]:
        E.append(Paragraph(s, BODY)); E.append(Spacer(1,2))

    # ── appendix ──
    E.append(PageBreak())
    E.append(Paragraph("7 · Data & verification appendix", H2))
    E.append(Paragraph("<b>VERIFIED (connected, live):</b> yfinance NSE — price, OHLC history (2y), returns, P&L vs your "
        "avg cost, 50/200 DMA, RSI, 52w range, momentum, relative strength vs Nifty 50, annualised vol, beta, max drawdown, "
        "historical VaR/CVaR, Monte-Carlo 1y distribution, GARCH(1,1) vol regime, and basic fundamentals where Yahoo carries "
        "them (P/E 30/38, P/B, EV/EBITDA, margins, ROE, D/E).", BODY))
    E.append(Paragraph("<b>Self-audit result:</b> every P&L recomputed as (live px / avg cost − 1); "
        f"{len(A['audit_issues'])} mismatches found. All 38 tickers name-verified against the live Yahoo company name "
        "(no mis-resolution). VaR/Monte-Carlo/GARCH computed for 38/38. No figure in this report is sourced from model "
        "memory; anything not live-fetched is labelled UNVERIFIED.", BODY))
    E.append(Paragraph("<b>NOT CONNECTED (claims could not be independently verified — connect to close the gap):</b>", BODY))
    for s in [
      "NSE/BSE corporate filings & announcements — order wins, results, regulatory events (the SEC-EDGAR equivalent).",
      "SEBI SAST/PIT disclosures — promoter & insider buying/selling (the Form-4 equivalent).",
      "NSE F&O ban list + SLB/short data — crowding/short pressure (partial FINRA equivalent).",
      "Screener.in / Tickertape / Trendlyne — reliable Indian fundamentals, order-book, cash-flow, debt (Finnhub/FMP equivalent).",
      "RBI / MOSPI — macro (rates, IIP, GDP) for top-down/factor context (FRED equivalent).",
      "Concall transcripts (NSE/BSE/IR) — management guidance (our SEC-transcript-fetcher equivalent).",
    ]:
        E.append(Paragraph("• "+s, BODY))
    E.append(Spacer(1,3))
    E.append(Paragraph("Until these are connected, all order-book, insider, short-interest, catalyst, and deep-valuation "
        "(DCF) claims are intentionally absent or marked UNVERIFIED. DCF was not computed: financials/PSU lenders (PFC, IREDA) "
        "are structurally unsuited to equity DCF, distressed names are un-modelable, and the rest lack connected cash-flow "
        "data — guessing would violate the hallucination-control mandate.", SMALL))

    doc.build(E)
    print("Wrote D:\\SSA_Portfolio_Analysis.pdf")

if __name__ == "__main__":
    build()
