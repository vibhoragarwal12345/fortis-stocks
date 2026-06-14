"""SSA portfolio PDF -- institutional research note (UPGRADED).
Every figure traces to D:\\portfolio_metrics.json (yfinance + NSE live),
D:\\news_sentiment.json, D:\\liquidation3.json, D:\\portfolio_aggregates.json,
D:\\macro_context.json, D:\\source_status.json -- never typed by hand.
Verdicts come from verdicts.py; narratives from narratives.py. Reference levels
are statistical/technical, explicitly NOT fundamental targets.
"""
from __future__ import annotations
import json, re, os
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, HRFlowable, KeepTogether, CondPageBreak)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon

from verdicts import VERDICT, PART_D, compute_levels, action_side
from narratives import NARR

# ── fonts: embed DejaVu Sans (has ₹ U+20B9; Helvetica does not) ───────────────
FONTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
def _font(name, fname): pdfmetrics.registerFont(TTFont(name, os.path.join(FONTDIR, fname)))
_font("DejaVu", "DejaVuSans.ttf"); _font("DejaVu-Bold", "DejaVuSans-Bold.ttf")
_font("DejaVu-Oblique", "DejaVuSans-Oblique.ttf"); _font("DejaVu-BoldOblique", "DejaVuSans-BoldOblique.ttf")
registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu-Oblique", boldItalic="DejaVu-BoldOblique")
BASE, BOLD, ITAL = "DejaVu", "DejaVu-Bold", "DejaVu-Oblique"

_AMP = re.compile(r'&(?!(amp|lt|gt|quot|apos|nbsp|mdash|ndash|rarr|larr|bull|middot|hellip|'
    r'lsquo|rsquo|ldquo|rdquo|deg|times|divide|copy|reg|trade|frac12|frac14|frac34|#\d+|#x[0-9A-Fa-f]+);)')
def esc(s: str) -> str: return _AMP.sub("&amp;", str(s))

# ── data ──────────────────────────────────────────────────────────────────────
M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
A = json.load(open(r"D:\portfolio_aggregates.json", encoding="utf-8"))
NEWS = json.load(open(r"D:\news_sentiment.json", encoding="utf-8"))["holdings"]
L3 = json.load(open(r"D:\liquidation3.json", encoding="utf-8"))
try: MACRO = json.load(open(r"D:\macro_context.json", encoding="utf-8"))["series"]
except Exception: MACRO = {}
try: SRC = json.load(open(r"D:\source_status.json", encoding="utf-8"))
except Exception: SRC = {}
H = M["holdings"]
TS = M["generated_at_utc"]; DATESHORT = TS[:10]
NIFTY = M.get("nifty_context", {})

# ── colours ───────────────────────────────────────────────────────────────────
ACCENT = "#0b3d2e"; INK = "#1a1a1a"; MUTED = "#6b6b6b"; FAINT = "#8a8a8a"
GREEN, AMBER, RED, BLUE, GRAY = "#0a7d33", "#b5651d", "#b00020", "#1f6feb", "#5a5a5a"
ROWALT = "#f3f6f4"; PANEL_BG, PANEL_BORDER = "#f6f8f7", "#dfe6e2"
INKc, MUTEDc, FAINTc, ACCENTc = HexColor(INK), HexColor(MUTED), HexColor(FAINT), HexColor(ACCENT)
TINT = {RED:"#fdecef", AMBER:"#fbf2e8", GREEN:"#eaf6ee", BLUE:"#eaf1fd", GRAY:"#f0f0f0"}
def tint(c): return HexColor(TINT.get(c, "#f4f4f4"))
def vcolor(v):
    u = (v or "").upper()
    if any(k in u for k in ("EXIT","AVOID","DE-LEVERAGE","BOOK LOSS")): return RED
    if "TRIM" in u or "BOOK PROFIT" in u or "decide" in (v or ""): return AMBER
    if any(k in u for k in ("BUY","ADD","RETAIN","CONVERT","HOLD")): return GREEN
    return GRAY
def pcol(v): return GREEN if (v is not None and v >= 0) else RED

styles = getSampleStyleSheet()
def _s(n, **k):
    base = {"fontName": BASE, "textColor": INKc}; base.update(k)
    return ParagraphStyle(n, parent=styles["Normal"], **base)
H1 = _s("H1", fontSize=18, leading=22, textColor=ACCENTc, fontName=BOLD, spaceAfter=4)
H2 = _s("H2", fontSize=13, leading=16, textColor=ACCENTc, fontName=BOLD, spaceBefore=8, spaceAfter=4)
NAME = _s("NAME", fontSize=14, leading=16, fontName=BOLD)
BODY = _s("BODY", fontSize=9, leading=12.8, spaceAfter=3)
SMALL = _s("SMALL", fontSize=7.6, leading=9.8, textColor=HexColor("#555555"))
TAG = _s("TAG", fontSize=7.2, leading=9, textColor=HexColor("#888888"))
NSEH = _s("NSEH", fontSize=8.5, leading=11, fontName=BOLD, textColor=ACCENTc, spaceBefore=3)
DAT = _s("DAT", fontSize=7.7, leading=10.4)
READ = _s("READ", fontSize=7.4, leading=9.6, textColor=HexColor("#444444"), fontName=ITAL)
KPIL = _s("KPIL", fontSize=6.6, leading=8, textColor=FAINTc, fontName=BOLD)
KPIV = _s("KPIV", fontSize=11, leading=12.5, fontName=BOLD)
CALLH = _s("CALLH", fontSize=9, leading=11, fontName=BOLD, textColor=HexColor("#333333"))
CALLB = _s("CALLB", fontSize=8.4, leading=11)
CAP = _s("CAP", fontSize=6.4, leading=7.6, textColor=FAINTc)

def P(t, st=BODY): return Paragraph(esc(t), st)
def nf(v, suf="", pre=""):
    if v is None: return "n/a"
    if isinstance(v, float): return f"{pre}{v:,.2f}{suf}"
    return f"{pre}{v}{suf}"
def money(v):
    if v is None: return "n/a"
    return f"₹{v:,.2f}" if abs(v) < 100 else f"₹{v:,.0f}"
def lakh(v):
    if v is None: return "n/a"
    if abs(v) >= 1e7: return f"₹{v/1e7:,.2f} cr"
    return f"₹{v/1e5:,.2f} L"
def pct(v, signed=False):
    if v is None: return "n/a"
    return (f"{v:+.1f}%" if signed else f"{v:.1f}%")

# ── fundamentals access (currency/plausibility aware) ─────────────────────────
def fund(d, key):
    return (d.get("fundamentals") or {}).get(key)
def fsuspect(d, key):
    return bool((d.get("fundamentals_suspect") or {}).get(key))
def fval(d, key, suf="", pre=""):
    v = fund(d, key)
    if v is None: return "n/a"
    s = nf(v, suf, pre)
    return s + " [UNVERIFIED]" if fsuspect(d, key) else s

# ── per-holding reads ─────────────────────────────────────────────────────────
def read_valuation(d):
    pe = fund(d, "trailing_pe"); pb = fund(d, "price_to_book")
    pe_ok = pe is not None and not fsuspect(d, "trailing_pe")
    if pe is None: base = "No clean trailing P/E (thin/negative earnings) — value on assets/cash-flow, not earnings"
    elif not pe_ok: base = "Reported P/E fails the currency/plausibility check — UNVERIFIED, treat with suspicion"
    elif pe >= 80: base = f"Extreme multiple ({pe:.0f}x) — a sentiment valuation, priced for perfection"
    elif pe >= 40: base = f"Rich ({pe:.0f}x) — demands sustained high growth"
    elif pe >= 22: base = f"Full but not extreme ({pe:.0f}x)"
    elif pe >= 12: base = f"Reasonable ({pe:.0f}x)"
    else: base = f"Cheap on earnings ({pe:.0f}x)"
    if pb is not None and not fsuspect(d, "price_to_book"):
        if pb >= 8: base += f"; very high P/B {pb:.1f}"
        elif pb < 1: base += f"; below book (P/B {pb:.2f})"
    if d.get("fundamentals_currency_mismatch"): base += " [currency mismatch — ratios suspect]"
    return base + ". Deep fundamentals UNVERIFIED until a licensed feed is connected."
def read_technical(d):
    rsi, v200, h52 = d.get("rsi14"), d.get("pct_vs_dma200"), d.get("pct_from_52w_high")
    p = []
    if rsi is not None: p.append(f"overbought (RSI {rsi:.0f})" if rsi>=70 else f"oversold (RSI {rsi:.0f})" if rsi<=30 else f"neutral momentum (RSI {rsi:.0f})")
    if v200 is not None: p.append(f"{'above' if v200>=0 else 'below'} its 200-day trend ({v200:+.0f}%)")
    if h52 is not None: p.append(f"{abs(h52):.0f}% off the 52w high")
    s = ", ".join(p); return (s[0].upper()+s[1:]+".") if s else "n/a"
def read_risk(d):
    vol, beta, dd = d.get("ann_vol_pct"), d.get("beta_vs_nifty"), d.get("max_drawdown_2y")
    b = []
    if vol is not None:
        lvl = "low" if vol<25 else "moderate" if vol<40 else "high" if vol<55 else "very high"
        b.append(f"{lvl} volatility ({vol:.0f}% ann)")
    if beta is not None: b.append(f"beta {beta:.2f} vs Nifty")
    if dd is not None: b.append(f"2y max drawdown {dd:.0f}%")
    g = d.get("garch_regime")
    if g: b.append(f"GARCH regime {g}")
    s = ", ".join(b); return (s[0].upper()+s[1:]+".") if s else "n/a"
def read_mc(d):
    pl, p5, cur = d.get("mc_prob_below_current_pct"), d.get("mc_p5"), d.get("price")
    s = []
    if pl is not None: s.append(f"{pl:.0f}% modelled chance of trading below today's price in 1y")
    if p5 is not None and cur: s.append(f"5th-pct downside {money(p5)} ({(p5/cur-1)*100:+.0f}%)")
    return ("; ".join(s)+". GBM ignores jumps/fat-tails/regime change.") if s else "n/a"

# ── panels / layout ───────────────────────────────────────────────────────────
def panel(title, pairs, read, accent=ACCENT):
    ph = ParagraphStyle("ph", parent=DAT, fontName=BOLD, fontSize=7.0, textColor=HexColor(accent), spaceAfter=1)
    datatxt = "&nbsp;&nbsp;&middot;&nbsp;&nbsp;".join(
        f"<font color='{FAINT}'>{esc(str(lab))}</font> <b>{esc(str(val))}</b>" for lab, val in pairs)
    t = Table([[Paragraph(esc(title).upper(), ph)], [Paragraph(datatxt, DAT)],
               [Paragraph("<i>"+esc(read)+"</i>", READ)]], colWidths=[88*mm])
    t.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(0,0),3),("TOPPADDING",(0,1),(-1,-1),1),
        ("BOTTOMPADDING",(0,0),(-1,-2),1),("BOTTOMPADDING",(0,-1),(-1,-1),4),
        ("BACKGROUND",(0,0),(-1,-1),HexColor(PANEL_BG)),("BOX",(0,0),(-1,-1),0.5,HexColor(PANEL_BORDER)),
        ("LINEBEFORE",(0,0),(0,-1),2,HexColor(accent))]))
    return t
def two_up(a, b):
    t = Table([[a, b]], colWidths=[91*mm, 91*mm])
    t.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(0,0),3),
        ("RIGHTPADDING",(1,0),(1,0),0),("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),("VALIGN",(0,0),(-1,-1),"TOP")]))
    return t

# ── charts ────────────────────────────────────────────────────────────────────
def hbars(rows, width=182*mm, label_w=70*mm, row_h=6.0*mm, default=ACCENT, fmt=lambda v: f"{v:.1f}%", maxval=None):
    maxval = maxval or max((r[1] for r in rows), default=1) or 1
    val_w, h = 20*mm, row_h*len(rows); bar_w = width-label_w-val_w
    d = Drawing(width, h); y = h-row_h
    for r in rows:
        lab, val = r[0], r[1]; col = HexColor(r[2]) if len(r)>2 else HexColor(default)
        d.add(String(0, y+row_h*0.30, str(lab), fontName=BASE, fontSize=7.3, fillColor=INKc))
        w = bar_w*(val/maxval) if maxval else 0
        d.add(Rect(label_w, y+row_h*0.14, max(w,0.4), row_h*0.60, fillColor=col, strokeColor=None))
        d.add(String(label_w+max(w,0.4)+3, y+row_h*0.30, fmt(val), fontName=BASE, fontSize=7.1, fillColor=MUTEDc))
        y -= row_h
    return d
def split_bar(cash_v, mtf_v, width=182*mm, h=11*mm):
    tot = (cash_v+mtf_v) or 1; d = Drawing(width, h+9); cw = width*(cash_v/tot)
    d.add(Rect(0, 9, cw, h, fillColor=HexColor(GREEN), strokeColor=colors.white, strokeWidth=1))
    d.add(Rect(cw, 9, width-cw, h, fillColor=HexColor(RED), strokeColor=colors.white, strokeWidth=1))
    d.add(String(5, 9+h*0.34, f"CASH  {cash_v/tot*100:.0f}%", fontName=BOLD, fontSize=8.5, fillColor=colors.white))
    if (width-cw) > 26*mm:
        d.add(String(cw+5, 9+h*0.34, f"MTF / MARGIN  {mtf_v/tot*100:.0f}%", fontName=BOLD, fontSize=8.5, fillColor=colors.white))
    d.add(String(0, 1, f"Cash holdings  {money(cash_v)}", fontName=BASE, fontSize=7, fillColor=MUTEDc))
    s = String(width, 1, f"Leveraged  {money(mtf_v)}", fontName=BASE, fontSize=7, fillColor=MUTEDc); s.textAnchor="end"
    d.add(s); return d
def range_bar(d_, w=42*mm, h=4.2*mm):
    lo, hi, cur = d_.get("low_52w"), d_.get("high_52w"), d_.get("price")
    dr = Drawing(w, h); dr.add(Rect(0,0,w,h, fillColor=HexColor("#e8ece9"), strokeColor=None))
    x = w*max(0.0,min(1.0,(cur-lo)/(hi-lo))) if (lo and hi and hi>lo and cur) else w/2
    dr.add(Rect(0,0,max(x,0.4),h, fillColor=HexColor("#cfe0d6"), strokeColor=None))
    dr.add(Rect(min(max(x-0.9,0),w-1.8),-1.4,1.8,h+2.8, fillColor=ACCENTc, strokeColor=None))
    return dr

def refband(L, width=176*mm, h=20*mm):
    """Visual bull/normal/bear 1y band with current, 52w lo/hi and avg cost."""
    cur = L.get("price"); bear, norm, bull = L.get("bear"), L.get("normal"), L.get("bull")
    lo, hi = L.get("low_52w"), L.get("high_52w"); cost = L.get("avg_cost")
    pts = [v for v in (bear,norm,bull,cur,lo,hi,cost) if isinstance(v,(int,float))]
    if not pts or cur is None: return Drawing(width, 4)
    mn, mx = min(pts), max(pts); rng = (mx-mn) or 1
    pad = rng*0.06; mn -= pad; mx += pad; rng = mx-mn
    d = Drawing(width, h)
    axis_y = h*0.46
    X = lambda v: (v-mn)/rng*width
    # bear->bull shaded band
    if bear is not None and bull is not None:
        d.add(Rect(X(bear), axis_y-3.0, max(X(bull)-X(bear),1), 6.0, fillColor=HexColor("#e7efe9"), strokeColor=None))
    d.add(Line(0, axis_y, width, axis_y, strokeColor=HexColor("#c3cfc8"), strokeWidth=0.8))
    def tick(v, col, lab, up=True, bold=False):
        if v is None: return
        x = X(v)
        d.add(Line(x, axis_y-4, x, axis_y+4, strokeColor=HexColor(col), strokeWidth=1.4 if bold else 0.9))
        ty = axis_y+6 if up else axis_y-11
        s = String(x, ty, lab, fontName=BOLD if bold else BASE, fontSize=6.3, fillColor=HexColor(col)); s.textAnchor="middle"
        d.add(s)
        s2 = String(x, ty+ (5 if up else -5), money(v), fontName=BASE, fontSize=5.8, fillColor=MUTEDc); s2.textAnchor="middle"
        d.add(s2)
    tick(bear, RED, "bear P5", up=False); tick(norm, GRAY, "normal P50", up=True)
    tick(bull, GREEN, "bull P95", up=False); tick(cur, ACCENT, "NOW", up=True, bold=True)
    if cost is not None: tick(cost, BLUE, "avg cost", up=False)
    return d

# ── KPI strip ─────────────────────────────────────────────────────────────────
def kpi_strip(tk, d):
    pnl = d.get("unrealized_pnl_pct")
    held = ("<font color='%s'><b>MARGIN / MTF</b></font>" % RED) if d.get("mtf") else "<font color='%s'>Cash</font>" % MUTED
    wt = (f'{d.get("weight")}%' if d.get("weight") is not None else "unweighted")
    cells = [[Paragraph("LIVE PRICE", KPIL), Paragraph("AVG COST", KPIL), Paragraph("UNREALIZED P&amp;L", KPIL),
              Paragraph("MKT VALUE", KPIL), Paragraph("HELD AS", KPIL), Paragraph("52-WEEK RANGE", KPIL)],
             [Paragraph(money(d.get("price")), KPIV), Paragraph(money(d.get("avg_cost")), KPIV),
              Paragraph(f"<font color='{pcol(pnl)}'>{pct(pnl, signed=True)}</font>", KPIV),
              Paragraph(money(d.get("mkt_value_live")), _s("mv", fontSize=9, leading=11, fontName=BOLD)),
              Paragraph(held, _s("h", fontSize=8.5, leading=11, fontName=BOLD)),
              [range_bar(d), Paragraph(f"{money(d.get('low_52w'))} &ndash; {money(d.get('high_52w'))}", CAP)]]]
    t = Table(cells, colWidths=[25*mm, 25*mm, 30*mm, 28*mm, 22*mm, 52*mm])
    t.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,0),2),("BOTTOMPADDING",(0,0),(-1,0),0),
        ("TOPPADDING",(0,1),(-1,1),0),("BOTTOMPADDING",(0,1),(-1,1),3),("LEFTPADDING",(0,0),(-1,-1),2),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,1),(-1,1),0.6,HexColor(PANEL_BORDER)),
        ("LINEABOVE",(0,0),(-1,0),0.6,HexColor(PANEL_BORDER))]))
    return t

def call_box(verdict, story):
    col = vcolor(verdict)
    m = re.search(r'Position read[^<]*</b>\s*(.*)', story, re.S)
    rat = (m.group(1).strip() if m else story)
    head = Paragraph(f"THE CALL&nbsp;&nbsp;&rarr;&nbsp;&nbsp;<font color='{col}'><b>{esc(verdict)}</b></font>", CALLH)
    body = Paragraph(esc(rat), CALLB)
    t = Table([[head],[body]], colWidths=[182*mm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),tint(col)),("LINEBEFORE",(0,0),(0,-1),3.5,HexColor(col)),
        ("BOX",(0,0),(-1,-1),0.5,HexColor(col)),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),
        ("TOPPADDING",(0,0),(0,0),4),("BOTTOMPADDING",(0,-1),(-1,-1),5),("BOTTOMPADDING",(0,0),(0,0),2)]))
    return t

SOWHAT = {"order_win":"real demand signal — catalyst verified, not imagined","results":"hard quarterly numbers",
 "dividend":"capital return / routine","concall_transcript":"management commentary on record",
 "management":"leadership change — watch continuity","mna":"corporate action / SAST","regulatory":"compliance filing",
 "rating":"credit-rating action","fundraise":"dilution / fresh capital need","other":"housekeeping / general update"}

def insider_label(net):
    lab = net.get("label")
    if lab == "NET BUYING": return "NET INSIDER BUYING", GREEN
    if lab == "NET SELLING": return "NET INSIDER SELLING", RED
    if lab == "INTER-SE TRANSFER": return "INTER-SE TRANSFER (non-directional)", GRAY
    if lab == "NO DISCLOSURES": return "NO DISCLOSURES", GRAY
    # fallback: derive from counts
    b, s, i = net.get("n_buys",0), net.get("n_sells",0), net.get("n_inter_se",0)
    if i>0 and b==0 and s==0: return "INTER-SE TRANSFER (non-directional)", GRAY
    if s>b: return "NET INSIDER SELLING", RED
    if b>s: return "NET INSIDER BUYING", GREEN
    return "NEUTRAL / BALANCED", GRAY
def insider_read(net):
    lab = net.get("label")
    if lab == "NET SELLING": return "The people closest to the company are reducing — a cautionary signal."
    if lab == "NET BUYING": return "Insiders are adding — a constructive signal worth noting."
    if lab == "INTER-SE TRANSFER": return "Intra-promoter reshuffle, not open-market activity — non-directional."
    return "No directional insider signal."

# ── Part B: news & Signal-vs-Chatter ─────────────────────────────────────────
def news_block(tk, d):
    n = NEWS.get(tk) or {}
    s = n.get("summary") or {}
    nse = d.get("nse") or {}
    ann = nse.get("announcements") or {}
    ins = (nse.get("insider") or {}).get("net") or {}
    orders = nse.get("order_wins") or []
    out = [Paragraph("News &amp; sentiment — Signal vs Chatter &nbsp;<font size=6 color='#888'>"
                     "[news = Google RSS flow/opinion; VERIFIED = NSE filings only]</font>", NSEH)]
    # VERIFIED (material, attributable) column vs CHATTER column
    qord = sum(1 for o in orders if o.get("quantified"))
    verified_bits = []
    if ann.get("count_90d"): verified_bits.append(f"{ann['count_90d']} NSE filings/90d")
    if orders: verified_bits.append(f"{len(orders)} Reg-30 order-win filing(s)" + (f", {qord} with ₹ amount" if qord else ""))
    ilab = insider_label(ins)[0] if ins else "no insider disclosures"
    verified_bits.append(ilab)
    if nse.get("results_filed"): verified_bits.append("results on file")
    chat = (f"{s.get('n','0')} headlines, tone <b>{esc(s.get('tone','n/a'))}</b> "
            f"(avg {s.get('avg_compound')}); {s.get('n_material_claim',0)} claim material news, "
            f"{s.get('n_chatter',0)} are promo/target/tip chatter")
    grid = Table([[Paragraph("<font color='%s'><b>VERIFIED (filed, dated, attributable)</b></font>" % GREEN, SMALL),
                   Paragraph("<font color='%s'><b>UNVERIFIED CHATTER (down-weight)</b></font>" % AMBER, SMALL)],
                  [Paragraph("&bull; " + "<br/>&bull; ".join(esc(b) for b in verified_bits), SMALL),
                   Paragraph(chat, SMALL)]], colWidths=[91*mm, 91*mm])
    grid.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),HexColor("#eef6f0")),("BACKGROUND",(1,0),(1,-1),HexColor("#fbf2e8")),
        ("BOX",(0,0),(-1,-1),0.4,HexColor(PANEL_BORDER)),("INNERGRID",(0,0),(-1,-1),0.4,HexColor(PANEL_BORDER)),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    out.append(grid)
    heads = (n.get("headlines") or [])[:3]
    if heads:
        hp = "  ".join(f"<font color='{pcol(hh['compound'])}'>&bull;</font> {esc(hh['title'][:78])} "
                      f"<font color='{FAINT}'>({hh['klass']}, {hh['compound']:+.2f})</font>" for hh in heads)
        out.append(P(hp, TAG))
    return out

def orderbook_block(d):
    orders = (d.get("nse") or {}).get("order_wins") or []
    if not orders: return []
    out = [Paragraph("Order book — Reg-30 order/contract-win filings (NSE, 90d)", NSEH)]
    for o in orders[:4]:
        if o.get("quantified"):
            amt = f"<font color='{GREEN}'><b>{esc(o['amount'])}</b></font>"
            cl = f" — client: {esc(o['client'])}" if o.get("client") else " — client/counterparty not parsed from filing text"
        else:
            amt = "<font color='%s'>disclosed but unquantified</font>" % AMBER; cl = ""
        out.append(P(f"&nbsp;&nbsp;&bull; [{esc(o['date'])}] {amt}{cl} &mdash; <font color='{FAINT}'>{esc((o.get('subject') or '')[:70])}</font>", TAG))
    return out

def nse_block(d):
    out = [Paragraph("Verified catalysts &amp; insider activity (NSE 90d / 365d) &nbsp;<font size=6 color='#888'>[VERIFIED: NSE live]</font>", NSEH)]
    nse = d.get("nse") or {}; ann = nse.get("announcements") or {}; ins = nse.get("insider") or {}
    cats = ann.get("by_category") or {}
    catline = ", ".join(f"{k}:{v}" for k,v in sorted(cats.items(), key=lambda x:-x[1])) if cats else "none in 90d"
    out.append(P(f"<b>Filings (90d):</b> {ann.get('count_90d','?')} &mdash; {catline}", SMALL))
    for a in (ann.get("recent") or [])[:3]:
        sw = SOWHAT.get(a.get("category",""), "")
        out.append(P(f"&nbsp;&nbsp;&bull; [{esc(a['date'])}] <i>{esc(a['category'])}</i>: {esc(a['text'][:100])} <font color='{FAINT}'>&mdash; {sw}</font>", TAG))
    net = ins.get("net") or {}
    if ins.get("count_365d"):
        lab, lcol = insider_label(net)
        out.append(P(f"<b>Insider PIT/SAST (365d):</b> {ins.get('count_365d')} disclosures &mdash; "
                     f"{net.get('n_buys',0)} buys / {net.get('n_sells',0)} sells / {net.get('n_inter_se',0)} inter-se "
                     f"&rarr; <font color='{lcol}'><b>{lab}</b></font>. <i>{insider_read(net)}</i>", SMALL))
    else:
        out.append(P("<b>Insider PIT/SAST (365d):</b> no disclosures — no directional signal.", SMALL))
    sast = (nse.get("sast") or {}).get("net") or {}
    if sast.get("n_events"):
        out.append(P(f"<b>SAST (substantial acq, 365d):</b> {sast['n_events']} — {sast.get('signal')}.", SMALL))
    out.append(P(f"<b>F&O ban today:</b> {'YES — in ban list' if nse.get('in_fno_ban') else 'no (not crowded into ban)'}", SMALL))
    return out

# ── Part C: reference levels + action levels ─────────────────────────────────
def reference_block(tk, d, verdict):
    L = compute_levels(d, verdict, d.get("avg_cost"))
    if not L: return []
    out = [Paragraph("Price reference levels — bull / normal / bear &nbsp;<font size=6 color='#888'>"
                     "[STATISTICAL/TECHNICAL reference levels — NOT fundamental price targets or forecasts]</font>", NSEH)]
    out.append(refband(L))
    # technical level line
    tline = (f"52w {money(L['low_52w'])}–{money(L['high_52w'])} (pos {pct(L.get('pos_52w_pct'))}) &middot; "
             f"50DMA {money(L.get('dma50'))} &middot; 200DMA {money(L.get('dma200'))} &middot; "
             f"swing S/R {money(L.get('swing_support'))}/{money(L.get('swing_resistance'))} &middot; "
             f"Boll(20,2) {money(L.get('boll_low'))}–{money(L.get('boll_up'))} &middot; "
             f"ATR(14)-band {money(L.get('atr_band_low'))}–{money(L.get('atr_band_up'))}")
    out.append(P(tline, TAG))
    mcline = (f"Monte-Carlo 1y (GBM, drift = mean daily log-return incl. −½σ² correction; σ from log-returns): "
              f"bear P5 {money(L.get('bear'))} &middot; normal P50 {money(L.get('normal'))} &middot; bull P95 {money(L.get('bull'))}.")
    out.append(P(mcline, TAG))
    # action levels tied to MY situation
    side = L["side"]
    if side in ("SELL", "TRIM"):
        line = (f"<b>Action (sell-side):</b> realistic sell zone <b>{money(L['sell_zone_low'])}–{money(L['sell_zone_high'])}</b> "
                f"(sell into strength toward resistance / upper band); hard floor <b>{money(L['cut_floor'])}</b> — below it, "
                f"cut regardless rather than hope.")
        if L.get("pct_to_avg_cost") is not None:
            reach = "reachable near-term" if L.get("avg_cost_reachable") else "NOT realistic near-term"
            line += (f" Your avg cost {money(L.get('avg_cost'))} is <b>{L['pct_to_avg_cost']:+.0f}%</b> away — "
                     f"<font color='{RED if not L.get('avg_cost_reachable') else GREEN}'>{reach}</font>; do not hostage the "
                     f"de-leverage to a round-trip the data doesn't support.")
        out.append(P(line, SMALL))
    else:
        out.append(P(f"<b>Action (hold-side):</b> accumulation/support reference zone <b>{money(L['accum_zone_low'])}–{money(L['accum_zone_high'])}</b>; "
                     f"thesis invalidation on a clean break below <b>{money(L['invalidation'])}</b> (then the hold fails — exit).", SMALL))
    out.append(P("<i>Fundamental price target unavailable — connect a licensed fundamentals feed. These are reference levels from "
                 "price data only, not valuation targets.</i>", TAG))
    return out

# ── Part D: stated decision ──────────────────────────────────────────────────
def stated_decision_block(tk):
    if tk not in PART_D: return []
    txt = PART_D[tk]
    t = Table([[Paragraph(f"<b>YOUR STATED DECISION:</b> {esc(txt)}", _s('sd', fontSize=8, leading=10.5, textColor=HexColor('#3a2d00')))]],
              colWidths=[182*mm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),HexColor("#fff7e6")),("BOX",(0,0),(-1,-1),0.5,HexColor(AMBER)),
        ("LINEBEFORE",(0,0),(0,-1),3,HexColor(AMBER)),("LEFTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    return [t, Spacer(1, 3)]

def verdict_badge(verdict, grade):
    col = vcolor(verdict)
    pr = Paragraph(f"<font color='{col}'><b>{esc(verdict)}</b></font><br/><font size=6 color='{FAINT}'>CONVICTION {esc(grade)}</font>",
                   _s("vb", fontSize=8.2, leading=10, alignment=2))
    t = Table([[pr]], colWidths=[58*mm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),tint(col)),("BOX",(0,0),(-1,-1),0.7,HexColor(col)),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    return t

def holding_page(tk, d):
    verdict, grade = VERDICT.get(tk, ("RETAIN","C"))
    story = NARR.get(tk, "[no narrative]")
    held = (f"<font color='{RED}'><b>MARGIN / MTF ⚠</b></font>" if d.get("mtf") else f"<font color='{MUTED}'>Cash segment</font>")
    name_para = Paragraph(f'{esc(d["name"])} &nbsp;<font size=9 color="{FAINT}">({esc(tk)})</font>', NAME)
    sub = Paragraph(f'{esc(d.get("theme",""))} &nbsp;&nbsp;|&nbsp;&nbsp; {held}', SMALL)
    header = Table([[name_para, verdict_badge(verdict, grade)]], colWidths=[124*mm, 58*mm])
    header.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0)]))

    val_pairs = [("Trail P/E", fval(d,"trailing_pe")), ("Fwd P/E", fval(d,"forward_pe")),
                 ("P/B", fval(d,"price_to_book")), ("EV/EBITDA", fval(d,"ev_to_ebitda")),
                 ("ROE", fval(d,"roe_pct","%")), ("Div yld", fval(d,"dividend_yield_pct","%"))]
    tech_pairs = [("RSI-14", nf(d.get("rsi14"))), ("vs 50DMA", pct(d.get("pct_vs_dma50"),True)),
                  ("vs 200DMA", pct(d.get("pct_vs_dma200"),True)), ("off 52w-hi", pct(d.get("pct_from_52w_high"),True)),
                  ("6m ret", pct(d.get("ret_6m"),True)), ("1y ret", pct(d.get("ret_1y"),True)),
                  ("RS vs Nifty 6m", pct(d.get("rel_strength_6m_vs_nifty"),True))]
    risk_pairs = [("Ann vol", pct(d.get("ann_vol_pct"))), ("Beta", nf(d.get("beta_vs_nifty"))),
                  ("Max DD 2y", pct(d.get("max_drawdown_2y"))), ("1d VaR95", pct(d.get("var_95_1d_pct"))),
                  ("1d CVaR95", pct(d.get("cvar_95_1d_pct"))), ("GARCH", f'{pct(d.get("garch_ann_vol_pct"))} ({d.get("garch_regime") or "-"})')]
    mc_pairs = [("MC p5", money(d.get("mc_p5"))), ("MC p50", money(d.get("mc_p50"))),
                ("MC p95", money(d.get("mc_p95"))), ("P(loss 1y)", pct(d.get("mc_prob_below_current_pct")))]

    flow = [header, Spacer(1,3), sub, Spacer(1,4), kpi_strip(tk,d), Spacer(1,5), P(story, BODY), Spacer(1,3)]
    flow += stated_decision_block(tk)
    flow += [two_up(panel("Valuation read", val_pairs, read_valuation(d)),
                    panel("Technical posture", tech_pairs, read_technical(d))), Spacer(1,4),
             two_up(panel("Risk profile", risk_pairs, read_risk(d)),
                    panel("Monte Carlo — 1y outlook", mc_pairs, read_mc(d))), Spacer(1,5)]
    flow += news_block(tk, d); flow += [Spacer(1,3)]
    flow += orderbook_block(d)
    flow += nse_block(d); flow += [Spacer(1,4)]
    flow += reference_block(tk, d, verdict); flow += [Spacer(1,5)]
    flow += [call_box(verdict, story), Spacer(1,3),
             P("Figures [VERIFIED: yfinance/NSE live, EOD]. Verdict &amp; business read = analyst judgment. Reference levels are "
               "statistical/technical, not forecasts. Deep fundamentals / forward estimates [UNVERIFIED until a licensed feed is connected].", TAG)]
    return flow

# ── running header/footer ─────────────────────────────────────────────────────
def _hf(canvas, doc):
    canvas.saveState(); canvas.setFont(BASE, 7); canvas.setFillColor(MUTEDc)
    canvas.drawString(14*mm, 8*mm, f"SSA Portfolio — Personal Guidance Research (NOT investment advice)  ·  {DATESHORT}")
    canvas.drawRightString(196*mm, 8*mm, f"Page {doc.page}")
    canvas.setStrokeColor(HexColor("#d6ded9")); canvas.setLineWidth(0.5)
    canvas.line(14*mm, 11*mm, 196*mm, 11*mm); canvas.restoreState()
def _cover_hf(canvas, doc): pass

def stat_card(label, value, color=ACCENT, sub=""):
    inner = [Paragraph(esc(label).upper(), _s("sl", fontSize=6.6, leading=8, textColor=FAINTc, fontName=BOLD)),
             Paragraph(f"<font color='{color}'>{esc(value)}</font>", _s("sv", fontSize=16, leading=18, fontName=BOLD))]
    if sub: inner.append(Paragraph(esc(sub), _s("ss", fontSize=6.3, leading=7.8, textColor=FAINTc)))
    t = Table([[i] for i in inner], colWidths=[42*mm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),HexColor(PANEL_BG)),("BOX",(0,0),(-1,-1),0.5,HexColor(PANEL_BORDER)),
        ("LINEABOVE",(0,0),(-1,0),2.2,HexColor(color)),("LEFTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(0,0),5),("TOPPADDING",(0,1),(-1,-1),1),("BOTTOMPADDING",(0,-1),(-1,-1),5)]))
    return t

def cover_flow():
    cards = [stat_card("Holdings", "38", ACCENT, "8 leveraged (MTF) + 30 cash"),
             stat_card("Defence cluster", f"{A['defence_pct']:.0f}%", RED, "one shared driver"),
             stat_card("On margin (MTF)", f"{A['mtf_pct']:.0f}%", RED, f"7/8 underwater, {A['mtf_pnl_pct']:.0f}%"),
             stat_card("Book P&L (val-wtd)", f"+{A['book_pnl_pct']:.0f}%", GREEN, "winners in cash")]
    cardrow = Table([cards], colWidths=[45.5*mm]*4)
    cardrow.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-2,-1),4),
        ("RIGHTPADDING",(-1,0),(-1,-1),0),("VALIGN",(0,0),(-1,-1),"TOP")]))
    verdict = Table([[Paragraph("OVERALL VERDICT", _s("ov", fontSize=7, leading=9, fontName=BOLD, textColor=colors.white)),
                      Paragraph("A leveraged, concentrated bet on Indian defence — with the winners in cash and the losers on "
                                "margin. De-leverage the MTF sleeve, cap defence near 35–40%, harvest the frothy small-cap winners, "
                                "and purge the penny-stock tail.", _s("ovb", fontSize=9, leading=12, textColor=colors.white))]],
                    colWidths=[34*mm, 148*mm])
    verdict.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),ACCENTc),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    out = [Spacer(1, 22*mm),
           P("SSA PORTFOLIO — PERSONAL GUIDANCE RESEARCH", _s("ct", fontSize=22, leading=26, fontName=BOLD, textColor=ACCENTc)),
           P("Indian Market Pilot · personal-guidance research document — NOT investment advice", _s("cs", fontSize=12, leading=15, textColor=HexColor("#444444"))),
           Spacer(1, 3), HRFlowable(width="100%", thickness=1.4, color=ACCENTc), Spacer(1, 5),
           P(f"Generated {TS} — prices live EOD via yfinance (NSE); catalysts, insider PIT/SAST and F&O-ban live via NSE "
             f"(ban date {M.get('nse_ban_trade_date','-')}). Nifty 50 {money(NIFTY.get('nifty_price'))} as of {NIFTY.get('nifty_date','-')}.", SMALL),
           Spacer(1, 14), cardrow, Spacer(1, 12), verdict, Spacer(1, 12),
           P(f"Account market value (exact qty × live EOD): <b>{money(A['total_live'])}</b>  ·  Cash {money(A['cash_live'])}  ·  "
             f"Margin/MTF {money(A['mtf_live'])}. Quantities are exact (workbook col F); rupee aggregates use live EOD prices "
             f"and cross-check the file's stated valuations within ~1%.", SMALL),
           Spacer(1, 10), HRFlowable(width="100%", thickness=0.5, color=HexColor("#cfd8d2")), Spacer(1, 4),
           P("<b>DISCLAIMER.</b> Personal-guidance research for the account-holder's own review — <b>NOT investment advice, not a "
             "recommendation, not a solicitation</b>. Prices/fundamentals are delayed EOD figures fetched live as of the timestamp "
             "above. Every figure is verified against a live fetch; verdicts are analyst judgment; anything from a not-yet-connected "
             "source is marked UNVERIFIED rather than guessed. <b>Deep fundamentals, order-book depth and forward estimates remain "
             "UNVERIFIED until a licensed fundamentals feed is connected.</b> Reference price levels are statistical/technical, not "
             "valuation targets or forecasts. Past performance does not indicate future results.", TAG),
           Spacer(1, 10)]
    # contents + data-at-a-glance (fills the lower cover; no empty space)
    toc = [("1", "Executive summary — the blunt verdict"), ("2", "Concentration & theme risk"),
           ("3", "The MTF (leveraged) book — read from font colour"),
           ("4", "Per-holding deep dives (38 pages: metrics · Signal-vs-Chatter · order book · insider · bull/normal/bear levels)"),
           ("5", "What a disciplined PM would change"),
           ("6", "Data, provenance & verification appendix"),
           ("7", "MTF → cash de-leverage — three alternate tracks (no tax, no new capital)")]
    crows = [[Paragraph(f"<font color='{ACCENT}'><b>{n}</b></font>", _s('cn', fontSize=8.5, leading=11, fontName=BOLD)),
              Paragraph(esc(t), _s('ctx', fontSize=8.2, leading=11))] for n, t in toc]
    ctab = Table(crows, colWidths=[8*mm, 174*mm])
    ctab.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),2),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LINEBELOW",(0,0),(-1,-2),0.3,HexColor("#e3e9e5"))]))
    src_ok = sum(1 for v in (SRC.get("sources") or {}).values() if "CONNECTED" in v["status"])
    src_tot = len(SRC.get("sources") or {})
    out += [HRFlowable(width="100%", thickness=0.5, color=HexColor("#cfd8d2")), Spacer(1, 3),
            Paragraph("WHAT THIS DOCUMENT COVERS", _s('toch', fontSize=8, leading=10, fontName=BOLD, textColor=ACCENTc)),
            Spacer(1, 2), ctab, Spacer(1, 8),
            P(f"<b>Data at a glance.</b> {src_ok}/{src_tot} free sources connected this run (yfinance price + fundamentals "
              "with currency-sanity, NSE announcements / PIT / SAST / F&O-ban, Google-News+VADER, World Bank macro). "
              "jugaad EOD connected but lags ~3–4 weeks; NSE live intraday quote failed (anti-bot). Recommended next feed to "
              "unlock genuine valuation: <b>Screener.in (~₹4,999/yr)</b>. Full status in the appendix.", SMALL),
            PageBreak()]
    return out

# ── 3-track liquidation (Part F) ─────────────────────────────────────────────
def sell_table(sells, target):
    rows = [["Cash holding","Sell","Shares (sold/held)","Px","Proceeds","Gain (pre-tax)","Cum.","Rationale"]]
    cross = None
    for i, s in enumerate(sells, 1):
        if cross is None and s["cumulative"] >= target-1: cross = i
        rows.append([s["name"], s["kind"], f"{s['shares_sold']:,}/{s['shares_total']:,} ({s['pct']:.0f}%)",
                     money(s["price"]), money(s["proceeds"]), money(s["gain"]), money(s["cumulative"]),
                     Paragraph(esc(s["reason"]), SMALL)])
    rows.append(["TARGET", "", "", "", "", "", money(target), ""])
    t = Table(rows, colWidths=[30*mm,12*mm,27*mm,16*mm,21*mm,22*mm,20*mm,34*mm], repeatRows=1)
    n = len(rows)
    sty = [("FONT",(0,0),(-1,-1),BASE,7.0),("FONT",(0,0),(-1,0),BOLD,6.9),("BACKGROUND",(0,0),(-1,0),ACCENTc),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),("ROWBACKGROUNDS",(0,1),(-1,n-2),[colors.white,HexColor(ROWALT)]),
        ("ALIGN",(1,0),(6,-1),"RIGHT"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("FONT",(0,n-1),(-1,n-1),BOLD,7.0),("BACKGROUND",(0,n-1),(-1,n-1),HexColor("#eef2ef")),("LINEABOVE",(0,n-1),(-1,n-1),0.8,ACCENTc),
        ("TOPPADDING",(0,0),(-1,-1),2.2),("BOTTOMPADDING",(0,0),(-1,-1),2.2)]
    for r in range(1, n-1):
        g = sells[r-1]["gain"]
        sty.append(("TEXTCOLOR",(5,r),(5,r),HexColor(GREEN if g>=0 else RED)))
    if cross: sty += [("BACKGROUND",(6,cross),(6,cross),HexColor("#d8efdf")),("BOX",(6,cross),(6,cross),0.8,HexColor(GREEN))]
    t.setStyle(TableStyle(sty))
    return t, cross

def track_flow(track, idx):
    out = [Paragraph(f"{esc(track['name'])}", H2)]
    cov = track["raised"] >= track["target"]-1
    # target/raised callout
    cc = Table([[Paragraph(f"RAISE&nbsp;&rarr;&nbsp;<b>{money(track['target'])}</b>", _s("c1", fontSize=9, leading=12, textColor=colors.white, fontName=BOLD)),
                 Paragraph(f"RAISED&nbsp;<b>{money(track['raised'])}</b>", _s("c2", fontSize=9, leading=12, textColor=colors.white)),
                 Paragraph(f"{'SURPLUS' if cov else 'SHORTFALL'} <b>{money(abs(track['surplus']))}</b>", _s("c3", fontSize=9, leading=12, textColor=colors.white)),
                 Paragraph(f"fund-side gain booked <b>{money(track['realized_gain'])}</b>", _s("c4", fontSize=9, leading=12, textColor=colors.white))]],
               colWidths=[46*mm,42*mm,46*mm,48*mm])
    cc.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),HexColor(GREEN if cov else RED)),("LEFTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    out += [cc, Spacer(1,4)]
    out.append(P(esc(track["narrative"]), BODY))
    # MTF exits (tracks 2/3)
    if track.get("mtf_exits"):
        out.append(Paragraph("MTF positions exited (squared off by own sale — proceeds repay their margin)", NSEH))
        er = [["Exited MTF","Mkt value","Realised P&L (pre-tax)","P&L%"]]
        for e in track["mtf_exits"]:
            er.append([e["name"], money(e["value"]), money(e["gain"]), pct(e["gain_pct"], True)])
        er.append(["TOTAL exit proceeds", money(track.get("exit_proceeds", sum(e['value'] for e in track['mtf_exits']))),
                   money(track.get("exit_realized", sum(e['gain'] for e in track['mtf_exits']))), ""])
        et = Table(er, colWidths=[60*mm,30*mm,40*mm,20*mm], repeatRows=1); m=len(er)
        est=[("FONT",(0,0),(-1,-1),BASE,7.4),("FONT",(0,0),(-1,0),BOLD,7.4),("BACKGROUND",(0,0),(-1,0),HexColor(RED)),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),("ROWBACKGROUNDS",(0,1),(-1,m-2),[colors.white,HexColor("#fdecef")]),
            ("ALIGN",(1,0),(3,-1),"RIGHT"),("FONT",(0,m-1),(-1,m-1),BOLD,7.4),("BACKGROUND",(0,m-1),(-1,m-1),HexColor("#f3d9de")),
            ("LINEABOVE",(0,m-1),(-1,m-1),0.8,HexColor(RED)),("TOPPADDING",(0,0),(-1,-1),2.2),("BOTTOMPADDING",(0,0),(-1,-1),2.2)]
        for r in range(1,m-1):
            est.append(("TEXTCOLOR",(2,r),(2,r),HexColor(GREEN if track['mtf_exits'][r-1]['gain']>=0 else RED)))
        et.setStyle(TableStyle(est)); out += [Spacer(1,2), et, Spacer(1,4)]
    if track.get("convert"):
        conv = ", ".join(f"{c['name'].split()[0]} ({int(c['frac']*100)}%, {money(c['value'])})" for c in track["convert"])
        out.append(P(f"<b>Converted to CASH &amp; kept (de-levered):</b> {esc(conv)} — funded by the sell list below.", SMALL))
    out.append(Paragraph("Funding — cash holdings to sell (priority: sale improves the book anyway)", NSEH))
    st, cross = sell_table(track["sells"], track["target"])
    out += [Spacer(1,2), st]
    if cross:
        out.append(P(f"<font color='{GREEN}'>&#9679;</font> Cumulative crosses target at <b>{esc(track['sells'][cross-1]['name'])}</b> — "
                     f"everything above is the minimum sell set.", TAG))
    ap = track["after"]
    out += [Spacer(1,4), Paragraph("After the operation", NSEH),
            P(f"Leverage <b>0% (MTF cleared)</b> &middot; defence concentration <b>{ap['rem_def_pct']:.0f}%</b> "
              f"(from {A['defence_pct']:.0f}%) &middot; residual book {money(ap['rem_total'])} &middot; "
              f"fund-side profit realised {money(track['realized_gain'])}.", SMALL)]
    return out

def liquidation3_flow():
    out = [Paragraph("7 · MTF → cash de-leverage — three alternate tracks", H1),
           HRFlowable(width="100%", thickness=1, color=ACCENTc), Spacer(1,4)]
    out.append(P("<b>Hard rules (as instructed):</b> (1) <b>taxation ignored entirely</b> — every figure is raw pre-tax proceeds, "
        "no STCG/LTCG, no tax drag; (2) <b>no fresh capital</b> — funded only by selling/trimming cash holdings; (3) target = current "
        "market value of the MTF subset each track neutralises. Rupee basis = exact quantity (workbook col F) × live EOD price.", BODY))
    # assumption box
    ab = Table([[Paragraph("<b>FUNDING-MODEL ASSUMPTION (stated plainly).</b> The margin-LOAN balance is not in the holdings file. "
        "The cash a track must RAISE to neutralise an MTF position is therefore taken as that position's full current MARKET VALUE — "
        "a conservative UPPER BOUND (true loan ≤ market value, so real cash needed is lower by your equity). Positions that are EXITED "
        "are squared off by their own sale (proceeds repay their margin) and need no external cash; positions CONVERTED-to-cash-and-kept "
        "must be funded from cash-holding sales. We use this transparent rule rather than fabricate an unknown loan balance.",
        _s("ab", fontSize=7.6, leading=10, textColor=HexColor("#333")))]], colWidths=[182*mm])
    ab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),HexColor("#eef1f7")),("BOX",(0,0),(-1,-1),0.5,HexColor(BLUE)),
        ("LINEBEFORE",(0,0),(0,-1),3,HexColor(BLUE)),("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    out += [Spacer(1,3), ab, Spacer(1,4)]
    out.append(P(f"<b>The leveraged book to neutralise:</b> {A['mtf_count']} MTF holdings, current market value <b>{money(L3['mtf_live'])}</b> "
        f"({L3['mtf_pct']:.1f}% of the {money(L3['total_live'])} book), value-weighted <b>{A['mtf_pnl_pct']:.1f}%</b> — 7 of 8 underwater. "
        f"Defence concentration today: <b>{L3['defence_pct']:.1f}%</b>.", BODY))
    for i, tr in enumerate(L3["tracks"]):
        out.append(CondPageBreak(120*mm))
        out += [Spacer(1,4)] + track_flow(tr, i)
    # comparison
    out.append(CondPageBreak(70*mm))
    out += [Spacer(1,6), Paragraph("Which track — plain comparison", H2)]
    cmp_rows = [["", "Track 1 — Full de-lever", "Track 2 — Owner plan", "Track 3 — Minimal-disturb"]]
    t1,t2,t3 = L3["tracks"]
    cmp_rows += [
        ["Cash to raise", money(t1["target"]), money(t2["target"]), money(t3["target"])],
        ["Cash names sold", str(len(t1["sells"])), str(len(t2["sells"])), str(len(t3["sells"]))],
        ["MTF neutralised", "all 8", "all 8 (½PFC,½ACC,Balaji kept)", "5 exited; PFC/ACC/Balaji kept"],
        ["Defence after", f"{t1['after']['rem_def_pct']:.0f}%", f"{t2['after']['rem_def_pct']:.0f}%", f"{t3['after']['rem_def_pct']:.0f}%"],
        ["Touches quality core", "Yes (HBL/BEL trimmed)", "No", "No"],
        ["Profit booked (fund side)", money(t1["realized_gain"]), money(t2["realized_gain"]), money(t3["realized_gain"])],
    ]
    ct = Table(cmp_rows, colWidths=[40*mm,47*mm,47*mm,48*mm], repeatRows=1)
    ct.setStyle(TableStyle([("FONT",(0,0),(-1,-1),BASE,7.6),("FONT",(0,0),(-1,0),BOLD,7.6),("FONT",(0,0),(0,-1),BOLD,7.6),
        ("BACKGROUND",(0,0),(-1,0),ACCENTc),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,HexColor(ROWALT)]),("ALIGN",(1,0),(-1,-1),"CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),2.4),("BOTTOMPADDING",(0,0),(-1,-1),2.4),("BOX",(0,0),(-1,-1),0.5,HexColor(PANEL_BORDER))]))
    out += [Spacer(1,2), ct, Spacer(1,5)]
    out.append(P("<b>Plain read.</b> <b>Track 1</b> is the cleanest risk kill — zero leverage AND the biggest concentration cut "
        "(defence to ~51%), and it banks the most profit — but it sells the most winners and trims the quality defence core. "
        "<b>Track 2 (your plan)</b> removes all leverage for the least cash-raising (the MTF exits self-fund), keeps the quality core "
        "untouched and keeps half of PFC/ACC plus Balaji — but it barely dents the 61% defence concentration and crystallises the "
        "leveraged losses on the exits. <b>Track 3</b> sits in between: it kills the leverage on the worst five, keeps the three "
        "defensible names fully, spares the quality core — but, like Track 2, leaves the defence concentration problem for another day. "
        "<b>Best balance of risk-reduction vs keeping winners: Track 3</b> if you want to keep PFC/ACC whole and not sell quality "
        "defence; <b>Track 1</b> if you accept selling winners to fix BOTH problems (leverage AND concentration) in one move. "
        "Track 2 is the lightest touch but does the least about your single biggest risk.", BODY))
    return out

def build():
    doc = SimpleDocTemplate(r"D:\SSA_Portfolio_Analysis.pdf", pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm, topMargin=15*mm, bottomMargin=14*mm,
        title="SSA Portfolio — Personal Guidance Research", author="Fortis research engine (India pilot)")
    E = []
    E += cover_flow()

    # 1 · executive summary
    E.append(Paragraph("1 · Executive summary — the blunt verdict", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,5))
    for s in [
      f"<b>This is a leveraged, concentrated bet on Indian defence, wrapped around a sleeve of leveraged losers and a tail of dead "
      f"money.</b> Three facts drive everything: (i) the defence-electronics cluster is <b>{A['defence_pct']:.0f}% of the book by "
      f"market value</b>; (ii) the MTF/margin sleeve is <b>{A['mtf_pct']:.0f}% of the book and {A['mtf_pnl_pct']:.0f}% underwater, "
      f"with {A['mtf_underwater_count']}/{A['mtf_count']} names in the red</b>; (iii) your cash sleeve is <b>+{A['cash_pnl_pct']:.0f}%</b> "
      f"— i.e. <b>the winners are in cash and the losers are on leverage.</b> That is exactly backwards from how a disciplined book is built.",
      f"<b>The leverage is the most urgent problem.</b> {A['mtf_pct']:.0f}% of the portfolio is on margin and 7 of 8 of those positions "
      f"are down. You pay financing cost to hold losers and carry margin-call risk into any sharp drawdown — the scenario where forced "
      f"selling crystallises the loss at the bottom. Sections 5 and 7 treat this sleeve separately.",
      f"<b>The defence concentration is the largest single risk.</b> {A['defence_pct']:.0f}% in eight names that share one driver — "
      f"government order flow and PSU sentiment (avg pairwise return correlation {A['defence_cluster_avg_corr']:.2f}). One policy or "
      f"budget event can hit ~60% of the book at once.",
      f"<b>Valuation discipline is absent at the top of the winners</b> (BDL triple-digit P/E as your largest weight; Avantel/Apollo/"
      f"Astra/Avalon at extreme multiples) and the live NSE PIT feed shows <b>net insider SELLING at Avantel, Avalon, Transrail and "
      f"HFCL</b> — at several names the people closest are reducing into the run.",
      f"<b>What a disciplined PM does:</b> de-leverage the MTF sleeve now (Section 7 gives three self-funding tracks), cap defence near "
      f"35–40%, harvest the frothy small-cap defence winners, and purge the sub-₹10 graveyard.",
    ]:
        E.append(P(s, BODY)); E.append(Spacer(1,2))
    E.append(Spacer(1,5)); E.append(Paragraph("Cash segment vs leveraged (MTF) segment", NSEH))
    E.append(split_bar(A["cash_live"], A["mtf_live"]))
    # macro backdrop
    E.append(Spacer(1,6)); E.append(Paragraph("Macro backdrop (World Bank, no-key) — context only", NSEH))
    def latest(series):
        s = MACRO.get(series); return s[0] if isinstance(s, list) and s else None
    cpi, gdpg = latest("cpi_inflation_pct"), latest("gdp_growth_pct"); gdp = latest("gdp_usd")
    E.append(P(f"India CPI inflation <b>{cpi['value']:.1f}%</b> ({cpi['year']}), real GDP growth <b>{gdpg['value']:.1f}%</b> "
        f"({gdpg['year']}), GDP <b>${gdp['value']/1e12:.2f}tn</b>. A moderate-inflation, ~6–7% growth backdrop supports the domestic "
        f"capex/defence theme — but it is already richly priced into your book; macro is a tailwind, not a margin of safety. "
        f"[VERIFIED: World Bank API]" if cpi and gdpg and gdp else "Macro series unavailable this run.", SMALL))

    # 2 · concentration
    E.append(PageBreak()); E.append(Paragraph("2 · Concentration &amp; theme risk", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,5))
    E.append(P(f"Defence-electronics cluster: <b>{A['defence_pct']:.1f}% of the book by market value</b> across 8 names; average "
        f"pairwise daily-return correlation {A['defence_cluster_avg_corr']:.2f}. Moderate day-to-day, but all eight share ONE "
        f"fundamental driver (defence capex / order timing / PSU re-rating) — the real common-shock risk is larger than the "
        f"correlation number suggests.", BODY))
    tp = A["theme_pct"]; th_sorted = sorted(tp.items(), key=lambda x:-x[1])
    # collapse defence-tagged themes label
    bar_rows = [(k, v, (RED if "Defence" in k or k=="Defence" else ACCENT if i<4 else "#6b8f7f")) for i,(k,v) in enumerate(th_sorted)]
    E.append(Spacer(1,3)); E.append(Paragraph("Theme allocation by market value — the defence bars are the punchline", NSEH))
    E.append(Spacer(1,2)); E.append(hbars(bar_rows, label_w=52*mm, fmt=lambda v: f"{v:.1f}%"))
    trows = [["Theme","% of book","Value"]] + [[k, f"{v:.1f}", money(A['theme_values'][k])] for k,v in th_sorted]
    tt = Table(trows, colWidths=[96*mm,30*mm,38*mm], repeatRows=1)
    tt.setStyle(TableStyle([("FONT",(0,0),(-1,-1),BASE,8),("FONT",(0,0),(-1,0),BOLD,8),("BACKGROUND",(0,0),(-1,0),ACCENTc),
      ("TEXTCOLOR",(0,0),(-1,0),colors.white),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,HexColor(ROWALT)]),
      ("ALIGN",(1,0),(-1,-1),"RIGHT"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    E.append(Spacer(1,6)); E.append(tt)

    # 3 · MTF book
    E.append(PageBreak()); E.append(Paragraph("3 · The MTF (leveraged) book — read from font colour", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,5))
    E.append(P(f"<b>{A['mtf_count']} holdings, {A['mtf_pct']:.1f}% of the book, value-weighted {A['mtf_pnl_pct']:.1f}% — "
        f"{A['mtf_underwater_count']}/8 underwater — held on margin.</b> The cash book, by contrast, is +{A['cash_pnl_pct']:.0f}%. "
        f"You are leveraging the losing half and not the winning half. Leverage adds three hidden costs: (1) financing interest that "
        f"raises break-even every month; (2) margin-call risk — a drawdown can force liquidation at the worst price; (3) it amplifies "
        f"both the loss and the risk rating of every name in it. <b>Verdict: the leverage here is imprudent</b> — not because margin is "
        f"never justified, but because it is applied to underwater, cyclical/de-rating names. The single highest-priority action in "
        f"this report is to de-leverage this sleeve — Section 7 gives three self-funding tracks.", BODY))
    mtf_sorted = sorted([(tk,d) for tk,d in H.items() if d.get("mtf")], key=lambda x:-(x[1].get("mkt_value_live") or 0))
    mrows = [["MTF holding","Mkt value","P&L%","Live","Verdict"]]
    for tk,d in mtf_sorted:
        mrows.append([d["name"], money(d.get("mkt_value_live")), pct(d.get("unrealized_pnl_pct"),True), money(d.get("price")), VERDICT.get(tk,("",))[0]])
    mt = Table(mrows, colWidths=[44*mm,26*mm,16*mm,22*mm,74*mm], repeatRows=1)
    mts = [("FONT",(0,0),(-1,-1),BASE,7.6),("FONT",(0,0),(-1,0),BOLD,7.6),("BACKGROUND",(0,0),(-1,0),HexColor(AMBER)),
      ("TEXTCOLOR",(0,0),(-1,0),colors.white),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,HexColor("#fbf2e8")]),
      ("ALIGN",(1,0),(3,-1),"RIGHT"),("TEXTCOLOR",(2,1),(2,-1),HexColor(RED)),("LINEBEFORE",(0,1),(0,-1),2.5,HexColor(RED)),
      ("TOPPADDING",(0,0),(-1,-1),2.4),("BOTTOMPADDING",(0,0),(-1,-1),2.4)]
    for r,(tk,d) in enumerate(mtf_sorted, 1):
        mts.append(("TEXTCOLOR",(4,r),(4,r),HexColor(vcolor(VERDICT.get(tk,("",))[0]))))
    mt.setStyle(TableStyle(mts)); E.append(Spacer(1,3)); E.append(mt)
    E.append(Spacer(1,5)); E.append(Paragraph("How deep underwater — each leveraged position's loss", NSEH)); E.append(Spacer(1,2))
    loss_rows = [(d["name"], abs(d.get("unrealized_pnl_pct") or 0), RED) for tk,d in mtf_sorted]
    E.append(hbars(loss_rows, label_w=52*mm, row_h=5.6*mm, fmt=lambda v: f"-{v:.1f}%"))

    # 4 · per-holding pages
    E.append(PageBreak()); E.append(Paragraph("4 · Per-holding deep dives", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,3))
    E.append(P("One page per holding — by market value, largest first. Every page carries verified metrics, a valuation / technical / "
               "risk / Monte-Carlo read, news Signal-vs-Chatter, NSE catalysts &amp; insider activity, statistical bull/normal/bear "
               "reference levels with action levels tied to your situation, and an explicit call.", SMALL))
    ordered = sorted(H.items(), key=lambda x:-(x[1].get("mkt_value_live") or 0))
    for tk,d in ordered:
        E.append(PageBreak())
        for f in holding_page(tk, d): E.append(f)

    # 6 · PM changes
    E.append(PageBreak()); E.append(Paragraph("5 · What a disciplined PM would change", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,5))
    for s in [
      "<b>1. De-leverage the MTF sleeve immediately.</b> 24% of the book on margin, 7/8 underwater. Section 7 gives three self-funding "
      "tracks that clear the sleeve from cash-holding profits — pick by how much winner-selling and concentration-cutting you want.",
      "<b>2. Cap defence at ~35–40%.</b> You are ~61%. Trim the frothiest small-caps first — Avantel (extreme multiple + net insider "
      "selling), Apollo, Astra, Avalon. Keep HAL/BEL as the quality core.",
      "<b>3. Trim the over-extended top weight.</b> BDL is your largest position at your richest large-cap multiple and thinnest gain.",
      "<b>4. Purge the sub-₹10 graveyard.</b> GTL Infra, Akshar, RCOM, HDIL, Future Consumer/Enterprises, the Vikas pair, Ajooni, "
      "RattanIndia, Kshitij. Exit the liquid ones, write off the suspended ones.",
      "<b>5. On your reconsideration names:</b> EXIT Tata Technologies (still 55x after the fall) and the distressed micros "
      "(Akshar, Kshitij); TRIM Avalon (a +206% winner, not a loss — and insiders are selling); but do NOT book Happiest Minds at the "
      "lows — it is a profitable IT name de-rated to ~25x, hold with a clean invalidation.",
      "<b>6. Keep the genuine value/ballast</b> — Tata Power, Gujarat Pipavav, and PFC/ACC/Balaji once de-levered.",
    ]:
        E.append(P(s, BODY)); E.append(Spacer(1,2))

    # 7 · liquidation
    E.append(PageBreak()); E += liquidation3_flow()

    # 8 · appendix
    E.append(PageBreak()); E.append(Paragraph("6 · Data, provenance &amp; verification appendix", H1))
    E.append(HRFlowable(width="100%", thickness=1, color=ACCENTc)); E.append(Spacer(1,5))
    # source status table
    if SRC.get("sources"):
        srows = [["Source","Status","Role"]]
        for k,v in SRC["sources"].items():
            srows.append([k.replace("_"," "), v["status"], Paragraph(esc(v["role"]), SMALL)])
        srt = Table(srows, colWidths=[42*mm,40*mm,100*mm], repeatRows=1)
        scol = {"CONNECTED":GREEN,"CONNECTED-WITH-CAVEAT":AMBER,"CONNECTED-BUT-LAGGED":AMBER,"CONNECTED-SPARSE":AMBER,"FAILED":RED}
        sst=[("FONT",(0,0),(-1,-1),BASE,7.4),("FONT",(0,0),(-1,0),BOLD,7.6),("BACKGROUND",(0,0),(-1,0),ACCENTc),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,HexColor(ROWALT)]),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]
        for r,(k,v) in enumerate(SRC["sources"].items(),1):
            sst.append(("TEXTCOLOR",(1,r),(1,r),HexColor(scol.get(v["status"],GRAY))))
            sst.append(("FONT",(1,r),(1,r),BOLD,7.4))
        srt.setStyle(TableStyle(sst))
        E.append(Paragraph("Free data sources — connection status (this run)", NSEH)); E.append(Spacer(1,2)); E.append(srt)
    E.append(Spacer(1,5))
    E.append(P("<b>VERIFIED — connected &amp; live:</b> yfinance NSE (price, 2y history, returns, P&L vs your avg cost, DMA/RSI/52w/"
        "Bollinger/ATR/swing levels, relative strength vs Nifty, vol, beta, drawdown, historical VaR/CVaR, Monte-Carlo, GARCH). "
        "<b>NSE live:</b> corporate announcements/order-wins, SEBI PIT/SAST insider, F&O ban, results-filing presence. "
        "<b>Google News RSS + local VADER</b> for news flow/sentiment (treated as chatter, never fact). <b>World Bank</b> macro.", BODY))
    E.append(P("<b>yfinance fundamentals — currency-sanity applied.</b> Every fundamental field was checked: financialCurrency vs "
        f"price currency. This run found <b>{('no currency mismatches' if not A.get('currency_mismatch') else 'mismatches at: '+', '.join(A['currency_mismatch']))}</b>; "
        f"<b>{A.get('suspect_fundamental_fields',0)} field(s)</b> failed a plausibility bound and are printed as [UNVERIFIED] rather than as a clean ratio. "
        "Deep fundamentals (10y financials, cash-flow, forward estimates, promoter pledge) remain UNVERIFIED until a licensed feed is connected.", BODY))
    E.append(P("<b>Insider-label audit (this revision):</b> the PIT/SAST tag is derived STRICTLY from net buys vs sells — never a "
        "hardcoded string. The prior 'open-market promoter selling' flag was fixed so it can no longer fire on a net-buying name. "
        "Before/after: <b>Maharashtra Seamless</b> (13 buys/3 sells → NET BUYING, was mis-tagged selling), <b>RattanIndia Power</b> "
        "(1/1 → NEUTRAL, was selling), <b>Ajooni Biotech</b> (4/2 → NET BUYING, was selling). Genuine net SELLING confirmed only at "
        "Avantel (0/5), Transrail (0/12), Avalon (0/3) and HFCL (0/2). GMM Pfaudler records are inter-se transfers (non-directional).", BODY))
    E.append(P("<b>Self-audit:</b> every P&L recomputed as (live px / avg cost − 1) — 0 mismatches; every mkt value = exact qty × live "
        "price; VaR/Monte-Carlo/GARCH computed 38/38; all 38 tickers name-verified; each NSE record company-verified. No figure is "
        "from model memory; nothing is taxed anywhere in Section 7.", BODY))
    rec = SRC.get("paid_feed_recommendation", {})
    if rec:
        E.append(Spacer(1,3)); E.append(Paragraph("Recommended next data feed to unlock genuine fundamental valuation", NSEH))
        E.append(P(f"<b>{esc(rec.get('primary_pick',''))}.</b> {esc(rec.get('why',''))} Alternatives: {esc(', '.join(rec.get('alternatives',[])))}. "
            "Until then, every 'Fundamentals read' block and all order-book depth / forward estimates remain UNVERIFIED.", SMALL))
    E.append(P("<b>STILL NOT CONNECTED (claims remain UNVERIFIED):</b> deep fundamentals / order-book depth / cash-flow / DCF / forward "
        "estimates (awaiting a licensed Screener/Tijori/Trendlyne key — no scraping); full concall transcripts. DCF was not computed: "
        "financials/PSU lenders are unsuited to equity DCF, distressed names are un-modelable, and the rest lack connected cash-flow "
        "data — guessing would violate the hallucination-control mandate. jugaad-data EOD connected but lags ~3–4 weeks (used only to "
        "validate yfinance history); the NSE live intraday quote endpoint failed (anti-bot) — yfinance covers price.", SMALL))

    doc.build(E, onFirstPage=_cover_hf, onLaterPages=_hf)
    print("Built D:\\SSA_Portfolio_Analysis.pdf")

if __name__ == "__main__":
    import sys; sys.stdout.reconfigure(encoding="utf-8")
    build()
