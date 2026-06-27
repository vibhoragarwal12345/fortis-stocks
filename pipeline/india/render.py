"""
Dossier rendering — warm-ivory editorial A4 HTML, then PDF via headless Edge.

Private research-note design: Source Serif 4 / IBM Plex Sans / IBM Plex Mono,
deep-green accent on ivory paper, flat (no gradients or glows), full-bleed paper
so there are no white margins. Numbers are formatted for Indian readers
(₹, Lakh Cr, %).
"""

from __future__ import annotations

import html
import logging
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("india.render")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


# ── formatters ────────────────────────────────────────────────────────────────

def _n(v):
    return v is not None and not (isinstance(v, float) and (v != v))


def _indian_group(intpart: str) -> str:
    """Group an integer string the Indian way: 25777084 -> 2,57,77,084."""
    neg = intpart.startswith("-")
    if neg:
        intpart = intpart[1:]
    if len(intpart) <= 3:
        out = intpart
    else:
        head, tail = intpart[:-3], intpart[-3:]
        head = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", head)
        out = head + "," + tail
    return ("-" if neg else "") + out


def inr(x):
    """Exact rupees, Indian grouping, 2 decimals. No rounding to crore/lakh."""
    if not _n(x):
        return "—"
    neg = x < 0
    s = f"{abs(x):.2f}"
    intp, dec = s.split(".")
    return ("-" if neg else "") + "₹" + _indian_group(intp) + "." + dec


def inr0(x):
    """Exact rupees, Indian grouping, no decimals (used for market cap)."""
    if not _n(x):
        return "—"
    neg = x < 0
    return ("-" if neg else "") + "₹" + _indian_group(f"{abs(x):.0f}")


def qtyfmt(x):
    """Share quantity, Indian-grouped integer, no currency symbol."""
    if not _n(x):
        return "—"
    return _indian_group(f"{abs(x):.0f}")


# Back-compat aliases: every rupee figure is now exact to the paisa.
money = inr
rupees = inr


def pct(x, sign=False, dp=1):
    if not _n(x):
        return "—"
    s = f"{x:+.{dp}f}%" if sign else f"{x:.{dp}f}%"
    return s


def ratio(x, dp=2):
    return f"{x:.{dp}f}" if _n(x) else "—"


def cls_sign(x):
    if not _n(x):
        return ""
    return "pos" if x >= 0 else "neg"


def esc(s):
    return html.escape(str(s)) if s is not None else ""


# ── Stylesheet: deep-navy / cream private research note ───────────────────────
# Flat, print-first (no gradients, glows, or rounded "app" cards). Navy paper is
# painted full-bleed via a fixed backdrop, so there are no margins even though
# @page keeps a small safe text inset on every physical page.

_STYLE = """
  @page { size: A4; margin: 9mm 10mm; }
  *{ box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  :root{
    --paper:#0e1d33; --panel:#172c49; --panel2:#1e3656;
    --ink:#f6edd4; --ink2:#e7ddc2; --muted:#9fb0c5; --rule:rgba(246,237,212,0.16);
    --accent:#d8bd7a; --accent2:#c9a24b;
    --gain:#76c79a; --loss:#e88f86; --ochre:#d8b86a;
    --f-head:'Source Serif 4',Georgia,'Times New Roman',serif;
    --f-body:'IBM Plex Sans','Segoe UI',Arial,sans-serif;
    --f-mono:'IBM Plex Mono',ui-monospace,Consolas,monospace;
  }
  html,body{ margin:0; padding:0; background:var(--paper); }
  body::before{ content:""; position:fixed; inset:0; background:var(--paper); z-index:-1; }
  body{ font-family:var(--f-body); color:var(--ink2); font-size:9.3pt; line-height:1.4;
        font-feature-settings:"kern","ss01"; -webkit-font-smoothing:antialiased; }
  .dossier{ break-before:page; }
  .dossier:first-child{ break-before:auto; }
  table, .callout, .oneliner, .posbox, .grid, .band, .cols, footer{ break-inside:avoid; }
  h2, h3{ break-after:avoid; }
  h1,h2,h3{ font-family:var(--f-head); color:var(--ink); font-weight:600; }
  h1{ font-size:22pt; letter-spacing:-0.01em; line-height:1.04; margin:0 0 2pt; }
  h2{ font-size:12.5pt; letter-spacing:0; margin:12pt 0 5pt; padding-bottom:3pt;
      border-bottom:1.5px solid var(--accent); color:var(--accent); }
  h3{ font-size:10pt; color:var(--ink); margin:8pt 0 3pt; font-weight:600; }
  p{ margin:0 0 6pt; }
  strong,b{ color:var(--ink); font-weight:600; }
  .eyebrow{ font-family:var(--f-body); font-size:7.4pt; font-weight:600; letter-spacing:.18em;
            text-transform:uppercase; color:var(--muted); margin-bottom:6pt;
            padding-bottom:5pt; border-bottom:1px solid var(--rule); }
  .sub{ font-family:var(--f-mono); font-size:8.8pt; color:var(--accent); margin:0 0 4pt;
        letter-spacing:.04em; }
  .lede{ font-family:var(--f-head); font-size:11pt; line-height:1.45; color:var(--ink); margin:6pt 0; }
  .muted{ color:var(--muted); }
  .small{ font-size:8.4pt; }
  .pos{ color:var(--gain); } .neg{ color:var(--loss); }
  table{ border-collapse:collapse; width:100%; margin:5pt 0 8pt; font-size:9pt; }
  thead th{ font-family:var(--f-body); text-transform:uppercase; letter-spacing:.08em;
            font-size:7.3pt; font-weight:600; color:var(--muted); text-align:left;
            padding:4pt 8pt; border-bottom:1.5px solid var(--accent); }
  td{ padding:3.5pt 8pt; border-bottom:1px solid var(--rule); vertical-align:top; color:var(--ink2); }
  td.k{ color:var(--muted); }
  td.num{ text-align:right; white-space:nowrap; font-family:var(--f-mono);
          font-variant-numeric:tabular-nums; color:var(--ink); }
  tr:nth-child(even) td{ background:rgba(255,255,255,.035); }
  ul{ margin:2pt 0 6pt; padding-left:14pt; } li{ margin:0 0 3pt; } li::marker{ color:var(--accent); }
  .callout{ background:var(--panel); border:1px solid var(--rule); border-left:3px solid var(--accent);
            padding:8pt 12pt; margin:8pt 0; }
  .oneliner{ font-family:var(--f-head); font-size:10.8pt; line-height:1.45; color:var(--ink);
             background:var(--panel); border:1px solid var(--rule); border-top:2.5px solid var(--accent);
             padding:11pt 15pt; margin:9pt 0; }
  .oneliner .label{ display:block; font-family:var(--f-body); font-weight:600; letter-spacing:.14em;
                    text-transform:uppercase; font-size:7.4pt; color:var(--accent); margin-bottom:4pt; }
  .grid{ display:grid; grid-template-columns:repeat(4,1fr); gap:0; margin:8pt 0;
         border:1px solid var(--rule); border-right:0; }
  .stat{ border-right:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:7pt 9pt; background:var(--panel); }
  .stat .lab{ font-size:7.1pt; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); }
  .stat .val{ font-family:var(--f-mono); font-size:10pt; color:var(--ink); margin-top:3pt;
              font-variant-numeric:tabular-nums; word-break:break-word; line-height:1.18; }
  .cols{ display:grid; grid-template-columns:1fr 1fr; gap:16pt; }
  .pill{ display:inline-block; font-family:var(--f-body); font-weight:700; font-size:8.2pt;
         letter-spacing:.08em; text-transform:uppercase; padding:1.5pt 8pt; border:1.2px solid currentColor; }
  .v-accumulate{ color:var(--gain); } .v-hold{ color:var(--ink2); }
  .v-trim{ color:var(--ochre); } .v-avoid{ color:var(--loss); }
  .band{ display:flex; align-items:center; gap:10pt; font-family:var(--f-mono); font-size:10pt;
         margin:6pt 0; }
  .band .seg{ flex:1; height:4px; background:var(--rule); position:relative; }
  .band .seg::after{ content:""; position:absolute; left:18%; right:18%; top:0; bottom:0; background:var(--accent); opacity:.55; }
  .posbox{ background:var(--panel); border:1px solid var(--rule); border-left:3px solid var(--accent);
           padding:9pt 13pt; margin:8pt 0; }
  .posbox .row{ display:grid; grid-template-columns:repeat(6,1fr); gap:6pt; }
  .posbox .lab{ font-size:7pt; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
  .posbox .val{ font-family:var(--f-mono); font-size:10pt; color:var(--ink); margin-top:2pt; }
  footer{ margin-top:10pt; padding-top:6pt; border-top:1px solid var(--rule);
          font-size:7.6pt; color:var(--muted); line-height:1.42; }
  .gap{ color:var(--ochre); }
  .spark{ border:1px solid var(--rule); background:var(--panel); padding:6pt 9pt 4pt; margin:4pt 0 8pt; }
  .sparkcap{ font-size:7pt; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; margin-top:2pt; }
"""


def _sparkline_svg(pts, avg_cost=None) -> str:
    """Flat inline-SVG price sparkline. Line is green if the window is up, red if
    down; a dashed line marks the holder's average cost. Colours come from the
    palette via inline-style var() so they track the theme."""
    vals = [v for v in (pts or []) if _n(v)]
    if len(vals) < 3:
        return ""
    W, H, pad = 320.0, 44.0, 5.0
    rng = vals + ([avg_cost] if _n(avg_cost) else [])
    vmin, vmax = min(rng), max(rng)
    if vmax == vmin:
        vmax = vmin + 1
    def X(i):
        return 1 + i * (W - 2) / (len(vals) - 1)
    def Y(v):
        return pad + (H - 2 * pad) * (1 - (v - vmin) / (vmax - vmin))
    up = vals[-1] >= vals[0]
    col = "var(--gain)" if up else "var(--loss)"
    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    area = ("M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
            + f" L{X(len(vals)-1):.1f},{H:.1f} L{X(0):.1f},{H:.1f} Z")
    avg_line = ""
    if _n(avg_cost) and vmin <= avg_cost <= vmax:
        ay = Y(avg_cost)
        avg_line = (f'<line x1="0" y1="{ay:.1f}" x2="{W:.0f}" y2="{ay:.1f}" '
                    f'stroke-dasharray="3 3" style="stroke:var(--muted);stroke-width:0.8;opacity:.85"/>')
    return (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none" '
            f'width="100%" height="44" style="display:block">'
            f'<path d="{area}" style="fill:{col};opacity:.12"/>'
            f'{avg_line}'
            f'<polyline points="{poly}" style="fill:none;stroke:{col};stroke-width:1.5"/>'
            f'<circle cx="{X(len(vals)-1):.1f}" cy="{Y(vals[-1]):.1f}" r="2.3" style="fill:{col}"/>'
            f'</svg>')


# ── one dossier (two pages) ───────────────────────────────────────────────────

def _stat(lab, val):
    return f'<div class="stat"><div class="lab">{lab}</div><div class="val">{val}</div></div>'


def _kv_rows(pairs):
    out = []
    for k, v, c in pairs:
        out.append(f'<tr><td class="k">{k}</td><td class="num {c}">{v}</td></tr>')
    return "".join(out)


def dossier_pages(d) -> str:
    t = d.technicals or {}
    s = d.snapshot or {}
    f = d.fundamentals or {}
    a = d.analysts or {}
    th = d.thesis or {}
    pos = d.position
    verdict = (th.get("verdict") or "Hold").strip()
    vclass = "v-" + verdict.lower()

    # position box
    posbox = ""
    if pos:
        ret = pos.get("return_pct")
        posbox = f"""
        <div class="posbox">
          <div class="row">
            <div><div class="lab">Qty</div><div class="val">{qtyfmt(pos.get('qty'))}</div></div>
            <div><div class="lab">Avg cost</div><div class="val">{money(pos.get('avg_cost'))}</div></div>
            <div><div class="lab">Last price</div><div class="val">{money(pos.get('current_price'))}</div></div>
            <div><div class="lab">Invested</div><div class="val">{rupees(pos.get('invested'))}</div></div>
            <div><div class="lab">Value</div><div class="val">{rupees(pos.get('current_value'))}</div></div>
            <div><div class="lab">P&amp;L</div><div class="val {cls_sign(ret)}">{pct(ret, sign=True)}</div></div>
          </div>
          <div class="small muted" style="margin-top:6pt">Weight in your book: {pct(pos.get('weight_in_book_pct'))} · Unrealized: <span class="{cls_sign(pos.get('unrealized_pnl'))}">{rupees(pos.get('unrealized_pnl'))}</span></div>
        </div>"""

    snapshot_grid = "".join([
        _stat("Price", money(t.get("price"))),
        _stat("Mkt cap", inr0(s.get("market_cap"))),
        _stat("52w range", f"{rupees(s.get('low_52w'))}&nbsp;–&nbsp;{rupees(s.get('high_52w'))}"),
        _stat("From 52w high", f'<span class="{cls_sign(t.get("from_high_pct"))}">{pct(t.get("from_high_pct"), sign=True)}</span>'),
        _stat("Trailing P/E", ratio(s.get("trailing_pe"), 1)),
        _stat("Forward P/E", ratio(s.get("forward_pe"), 1)),
        _stat("Price / Book", ratio(s.get("price_to_book"), 2)),
        _stat("ROE", pct(s.get("roe"))),
    ])

    tech_rows = _kv_rows([
        ("Return 1M", pct(t.get("ret_1m"), sign=True), cls_sign(t.get("ret_1m"))),
        ("Return 3M", pct(t.get("ret_3m"), sign=True), cls_sign(t.get("ret_3m"))),
        ("Return 6M", pct(t.get("ret_6m"), sign=True), cls_sign(t.get("ret_6m"))),
        ("Return 1Y", pct(t.get("ret_1y"), sign=True), cls_sign(t.get("ret_1y"))),
        ("vs Nifty (6M)", pct(t.get("vs_nifty_6m"), sign=True), cls_sign(t.get("vs_nifty_6m"))),
        ("Volatility (ann.)", pct(t.get("vol_ann_pct")), ""),
        ("RSI (14)", ratio(t.get("rsi14"), 1), ""),
        ("50 / 200 DMA", f"{rupees(t.get('dma50'))} / {rupees(t.get('dma200'))}", ""),
        ("Above 200-DMA", "Yes" if t.get("above_dma200") else "No", "pos" if t.get("above_dma200") else "neg"),
        ("Max drawdown 1Y", pct(t.get("max_drawdown_1y_pct"), sign=True), "neg"),
    ])

    band = d.band
    band_html = '<p class="muted small">Insufficient history for a reference band.</p>'
    if band:
        band_html = f"""
        <div class="band"><span class="neg">{rupees(band['low'])}</span>
          <span class="seg"></span><span class="pos">{rupees(band['high'])}</span></div>
        <p class="small muted">21-trading-day zero-drift Monte-Carlo cone (5th–95th pct) from a {band.get('distribution')} fit
          (calibration {ratio(band.get('calibration'))}). Midpoint {rupees(band['mid'])}, basis {rupees(band['basis'])}.
          A reference range, not a forecast.</p>"""

    fund_rows = _kv_rows([
        ("Revenue CAGR (3Y)", pct(f.get("revenue_cagr_3y")), cls_sign(f.get("revenue_cagr_3y"))),
        ("Revenue growth (TTM YoY)", pct(f.get("revenue_growth_ttm")), cls_sign(f.get("revenue_growth_ttm"))),
        ("Revenue growth (latest qtr YoY)", pct(f.get("revenue_growth_qtr")), cls_sign(f.get("revenue_growth_qtr"))),
        ("Gross margin (TTM / 5Y avg)", f"{pct(f.get('gross_margin_ttm'))} / {pct(f.get('gross_margin_5y'))}", ""),
        ("Operating margin (TTM / 5Y avg)", f"{pct(f.get('operating_margin_ttm'))} / {pct(f.get('operating_margin_5y'))}", ""),
        ("Debt / Equity", ratio(f.get("debt_to_equity")), ""),
        ("Dividend yield", pct(s.get("dividend_yield")), ""),
        ("Analyst coverage", f"{a.get('count','—')} · {esc(a.get('recommendation') or '—')} · target {rupees(a.get('target_mean'))}", ""),
    ])

    def _ul(items):
        vals = [x for x in (items or []) if x and str(x).strip()]
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in vals) + "</ul>" if vals else '<p class="muted small">None in the available data.</p>'

    # ownership / insider
    o = d.ownership or {}
    if _n(o.get("institutions_pct")):
        inst_str = pct(o.get("institutions_pct"))
        if o.get("institutions_count"):
            inst_str += f' ({o["institutions_count"]} holders)'
    else:
        inst_str = "—"
    if o.get("insider_txn_count"):
        txn_str = f'{o["insider_txn_count"]} filed'
        if o.get("insider_buys") is not None:
            txn_str += f' · {o.get("insider_buys", 0)} buy / {o.get("insider_sells", 0)} sell'
    else:
        txn_str = "None reported"
    own_rows = _kv_rows([
        ("Promoter / insider holding", pct(o.get("promoter_insider_pct")),
         "pos" if _n(o.get("promoter_insider_pct")) and o["promoter_insider_pct"] >= 40 else ""),
        ("Institutional holding", inst_str, ""),
        ("Insider transactions (Yahoo)", txn_str, ""),
    ]) if o else ""
    own_html = f'<h2>Ownership &amp; insiders</h2><table><tbody>{own_rows}</tbody></table>' if own_rows else ""

    # corporate events
    ev = d.events or {}
    ev_pairs = []
    if ev.get("next_earnings"):
        ev_pairs.append(("Next earnings", esc(ev["next_earnings"]), "pos"))
    if ev.get("ex_dividend"):
        ev_pairs.append(("Ex-dividend date", esc(ev["ex_dividend"]), ""))
    if ev.get("eps_estimate") is not None:
        ev_pairs.append(("Consensus EPS estimate", ratio(ev["eps_estimate"], 2), ""))
    if ev.get("recent_dividends"):
        last = ev["recent_dividends"][-1]
        ev_pairs.append(("Last dividend", f'₹{last["amount"]} ({esc(last["date"])})', ""))
    if ev.get("last_split"):
        ev_pairs.append(("Last split", f'{ratio(ev["last_split"]["ratio"],0)}:1 ({esc(ev["last_split"]["date"])})', ""))
    ev_html = (f'<h2>Corporate events</h2><table><tbody>{_kv_rows(ev_pairs)}</tbody></table>'
               if ev_pairs else "")

    # news
    news_items = ""
    for n in (d.news or [])[:6]:
        news_items += (f'<li><strong>{esc(n.get("title"))}</strong>'
                       f'<span class="muted small"> · {esc(n.get("publisher"))}'
                       f'{" · " + esc(n.get("when")) if n.get("when") else ""}</span></li>')
    news_html = (f'<h2>Recent news</h2><ul class="news">{news_items}</ul>'
                 if news_items else '<h2>Recent news</h2><p class="muted small">No recent headlines surfaced for this name.</p>')

    # price sparkline with the holder's average-cost line
    avg_cost = (pos or {}).get("avg_cost")
    spark = _sparkline_svg(d.spark, avg_cost)
    spark_html = (
        f'<div class="spark">{spark}<div class="sparkcap">1-year price'
        + (f' · dashed line = your average cost {inr(avg_cost)}' if _n(avg_cost) else '')
        + '</div></div>'
    ) if spark else ""

    catalysts = [c for c in (th.get("catalysts") or []) if c and str(c).strip()]
    catalyst_html = (f'<div class="callout"><strong>Near-term catalysts.</strong> '
                     + " ".join(f"{esc(c)}." for c in catalysts) + "</div>") if catalysts else ""

    gaps = "".join(f"<li>{esc(x)}</li>" for x in (d.flags or []))

    return f"""
    <article class="dossier">
      <div class="eyebrow">Portfolio Review · Equity Dossier · {esc(d.generated_at)}</div>
      <h1>{esc(d.name)}</h1>
      <div class="sub">{esc(d.ticker)} · {esc(d.sector) or 'Equity'} · NSE</div>
      <div class="oneliner"><span class="label">House view</span>
        <span class="pill {vclass}">{esc(verdict)}</span>&nbsp;&nbsp;{esc(th.get('verdict_rationale') or '')}</div>
      {posbox}
      <h2>Snapshot</h2>
      <div class="grid">{snapshot_grid}</div>
      <h2>Price &amp; technicals</h2>
      {spark_html}
      <table><tbody>{tech_rows}</tbody></table>
      <h2>Price reference band</h2>
      {band_html}
      <h2>Fundamentals</h2>
      <table><tbody>{fund_rows}</tbody></table>
      <p class="small muted">Fundamentals coverage {esc(f.get('coverage'))} from the India adapter (yfinance source).</p>
      {own_html}
      {ev_html}
      {news_html}
      <h2>Investment thesis</h2>
      <p class="lede">{esc(th.get('snapshot_read') or '')}</p>
      <div class="cols">
        <div><h3>Bull case</h3>{_ul(th.get('bull'))}</div>
        <div><h3>Bear case</h3>{_ul(th.get('bear'))}</div>
      </div>
      <div class="callout"><strong>Valuation.</strong> {esc(th.get('valuation_view') or '')}</div>
      {catalyst_html}
      <h3>What to watch</h3>{_ul(th.get('what_to_watch'))}
      <div class="oneliner"><span class="label">Verdict · {esc(verdict)}</span>{esc(th.get('position_action') or th.get('verdict_rationale') or '')}</div>
      <footer>
        <strong>Data:</strong> prices &amp; technicals via yfinance (NSE); fundamentals via the India adapter;
        price band via zero-drift Monte-Carlo; thesis generated by the LLM gateway, grounded only in the data shown.
        <br><strong class="gap">Not yet wired for India (disclosed, not estimated):</strong>
        <ul style="margin:3pt 0 0">{gaps}</ul>
        <br>Personal research note. Figures from public market data; verify independently. Not investment advice.
      </footer>
    </article>"""


def _short_sector(s):
    s = (s or "").split(" - ")[0].split(" -")[0]
    return s[:24]


def portfolio_pages(summary: dict, verdicts: dict | None = None,
                    notes: list | None = None) -> str:
    """Cover + allocation/recommendations overview + holdings summary table +
    appendix for positions that cannot carry a market dossier."""
    verdicts = verdicts or {}
    notes = notes or []
    note_names = {n["name"] for n in notes}
    s = summary
    ret = s.get("total_ret_pct")
    cover_grid = "".join([
        _stat("Current value", inr(s["total_current"])),
        _stat("Invested", inr(s["total_invested"])),
        _stat("Unrealized P&L", f'<span class="{cls_sign(s["total_gain"])}">{inr(s["total_gain"])}</span>'),
        _stat("Total return", f'<span class="{cls_sign(ret)}">{pct(ret, sign=True)}</span>'),
    ])

    # asset allocation rows
    alloc_rows = (
        f'<tr><td>Gold / Sovereign Gold Bonds</td><td class="num">{pct(s["gold_weight"])}</td>'
        f'<td class="num">{inr(s["gold_current"])}</td></tr>'
        f'<tr><td>Equity (stocks + funds)</td><td class="num">{pct(s["equity_weight"])}</td>'
        f'<td class="num">{inr(s["equity_current"])}</td></tr>'
    )
    gold_rows = "".join(
        f'<tr><td>{esc(g["name"][:38])}</td><td class="num">{pct(g["weight"])}</td>'
        f'<td class="num {cls_sign(g["ret"])}">{pct(g["ret"], sign=True)}</td></tr>'
        for g in s["gold"][:4]
    )

    # holdings summary table with verdicts — every holding, nothing dropped
    hold_rows = []
    for h in s["holdings"]:
        v = verdicts.get(h["name"], ("", ""))
        if v[0]:
            view = f'<span class="pill v-{v[0].lower()}" style="font-size:7pt;padding:1pt 6pt">{esc(v[0])}</span>'
        elif h["is_gold"]:
            view = '<span class="muted small">Core · gold</span>'
        elif h["name"] in note_names:
            view = '<span class="small" style="color:var(--amber)">Appendix</span>'
        else:
            view = '<span class="muted small">—</span>'
        tag = "Gold/SGB" if h["is_gold"] else _short_sector(h["sector"])
        hold_rows.append(
            f'<tr><td>{esc(h["name"][:36])}</td><td class="muted small">{esc(tag)}</td>'
            f'<td class="num">{pct(h["weight"])}</td>'
            f'<td class="num {cls_sign(h["ret"])}">{pct(h["ret"], sign=True)}</td>'
            f'<td>{view}</td></tr>'
        )

    # appendix: positions not separately dossiered
    appendix = ""
    if notes:
        arows = ""
        for n in notes:
            h = n.get("holding") or {}
            arows += (
                f'<tr><td>{esc(n["name"][:34])}</td>'
                f'<td class="num">{pct(h.get("weight"))}</td>'
                f'<td class="num {cls_sign(h.get("ret"))}">{pct(h.get("ret"), sign=True)}</td>'
                f'<td class="small">{esc(n["status"])}</td></tr>'
            )
        appendix = (
            '<h2>Appendix · positions not separately dossiered</h2>'
            '<p class="small muted">Delisted, under-insolvency, demerger-stub, fund, or temporarily data-unavailable holdings. '
            'Included for completeness with the recommended action.</p>'
            '<table><thead><tr><th>Holding</th><th style="text-align:right">Wt</th>'
            '<th style="text-align:right">Return</th><th>Status &amp; action</th></tr></thead>'
            f'<tbody>{arows}</tbody></table>'
        )

    gain_share = s["gold_gain_share"]
    return f"""
    <article class="dossier">
      <div class="eyebrow">Portfolio Review · Holdings as of {esc(s['holding_date'])}</div>
      <h1>Portfolio Review</h1>
      <div class="sub">{esc(s['client'])}</div>
      <div class="grid" style="grid-template-columns:repeat(4,1fr);margin-top:10pt">{cover_grid}</div>
      <div class="oneliner"><span class="label">House view</span>
        This is, in substance, a <strong>gold portfolio</strong>: {pct(s['gold_weight'])} sits in gold and Sovereign Gold Bonds,
        and roughly {pct(gain_share, dp=0)} of the {inr(s['total_gain'])} gain came from two SGB tranches. The {s['n_equity']}-name
        equity sleeve is a sound large-cap core wrapped in a long tail that adds clutter, not return.</div>

      <h2>Asset allocation</h2>
      <table><thead><tr><th>Sleeve</th><th style="text-align:right">Weight</th><th style="text-align:right">Value</th></tr></thead>
        <tbody>{alloc_rows}</tbody></table>
      <h3>Gold sleeve detail</h3>
      <table><thead><tr><th>Instrument</th><th style="text-align:right">Wt</th><th style="text-align:right">Return</th></tr></thead>
        <tbody>{gold_rows}</tbody></table>

      <h2>What stands out</h2>
      <ul>
        <li><strong>Concentration.</strong> ~{pct(s['gold_weight'], dp=0)} in one asset class (gold). The headline {pct(ret, dp=0)} return is a gold story, not stock selection.</li>
        <li><strong>Two bonds carry the book.</strong> The two SGB tranches alone are the bulk of both value and gains (~{pct(gain_share, dp=0)} of profit).</li>
        <li><strong>Equity tail.</strong> {s['n_equity']} equity names, but a handful of large-caps hold almost all the weight; the rest are sub-1% stubs.</li>
        <li><strong>Dead weight.</strong> {len(s['cleanup'])} positions are near-zero or down &gt;40% (delisted / demerger stubs) and only add noise.</li>
      </ul>

      <h2>Recommended adjustments</h2>
      <ol>
        <li><strong>Do not sell the SGBs to rebalance.</strong> Held to maturity they redeem capital-gains-tax-free; selling early on the exchange triggers taxable LTCG. With a large unrealized gain sitting in them, that would be the most tax-inefficient move available.</li>
        <li><strong>Reduce gold weight the tax-smart way:</strong> hold each SGB to its maturity date, stop adding to gold, and direct new money to equity so the weight dilutes over time without a taxable event.</li>
        <li><strong>Clean up the tail.</strong> Write off the zombies (delisted / near-zero names) and harvest the demerger-stub losses to offset realized equity gains.</li>
        <li><strong>Consolidate.</strong> Collapse duplicate and sub-1% positions; take the equity book from many names to a focused 12 to 15.</li>
        <li><strong>Rebalance the core.</strong> Trim the biggest equity winners if any single name gets uncomfortable, and decide whether the laggards still earn their place. Per-stock calls follow.</li>
      </ol>

      <h2>Holdings &amp; house view</h2>
      <table><thead><tr><th>Holding</th><th>Sleeve / sector</th><th style="text-align:right">Wt</th><th style="text-align:right">Return</th><th>View</th></tr></thead>
        <tbody>{''.join(hold_rows)}</tbody></table>
      <p class="small muted">"View" is the data-grounded house call from each stock's dossier (following pages). Gold/SGB holdings are core holds, addressed above; "Appendix" names are in the table below.</p>
      {appendix}
      <footer>Personal portfolio working note. Figures are computed from the holdings statement and public market data.
        Verify each SGB's maturity date and current tax treatment independently before acting. Not investment advice.</footer>
    </article>"""


def report(summary: dict, dossiers: list, verdicts: dict | None = None,
           notes: list | None = None) -> str:
    """Full single-PDF portfolio report: cover + overview + per-stock dossiers + appendix."""
    body = portfolio_pages(summary, verdicts, notes) + "\n".join(dossier_pages(d) for d in dossiers)
    return _wrap(body)


def document(dossiers: list) -> str:
    body = "\n".join(dossier_pages(d) for d in dossiers)
    return _wrap(body)


def _wrap(body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Portfolio Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>{_STYLE}</style></head><body>{body}</body></html>"""


# ── PDF via headless Edge ─────────────────────────────────────────────────────

def to_pdf(html_text: str, out_pdf: Path) -> Path:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
        fh.write(html_text)
        html_path = Path(fh.name)
    with tempfile.TemporaryDirectory() as udd:
        cmd = [
            EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={udd}", "--virtual-time-budget=20000",
            f"--print-to-pdf={out_pdf}", html_path.as_uri(),
        ]
        log.info("Rendering PDF → %s", out_pdf)
        subprocess.run(cmd, timeout=120, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        html_path.unlink()
    except OSError:
        pass
    return out_pdf
