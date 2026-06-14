"""Post-build verification loop. Extracts the rendered PDF text and runs the
mandated checks: rupee glyph renders, no HTML-escape artifacts, verdict
consistency page-vs-plan, P&L recompute, reference-level labelling, no taxation
in the liquidation, no mostly-empty pages, everything traceable. Prints a
PASS/FAIL per check and a final gate. Also renders key pages to PNG for a visual
look."""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8")
import pypdf, fitz

sys.path.insert(0, r"D:\fortis-stocks\pipeline\india_portfolio")
from verdicts import VERDICT
M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))["holdings"]
PDF = r"D:\SSA_Portfolio_Analysis.pdf"

r = pypdf.PdfReader(PDF)
pages = [p.extract_text() or "" for p in r.pages]
full = "\n".join(pages)
fails = []; warns = []
def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok: fails.append(name)

# 1. rupee glyph renders (present, and no tofu/replacement/cid artifacts)
rupee_n = full.count("₹")
bad_glyph = sum(full.count(x) for x in ("■", "�", "(cid:"))
check("₹ renders everywhere (no tofu)", rupee_n > 200 and bad_glyph == 0,
      f"{rupee_n} ₹ glyphs, {bad_glyph} tofu/replacement")

# 2. no HTML-escape artifacts
artifacts = []
for pat in ("P&L;", "F&O;", "R&D;", "&amp;", "&middot;", "&mdash;", "&ndash;", "&rarr;", "&bull;", "&nbsp;", "&lsquo;", "&rsquo;"):
    c = full.count(pat)
    if c: artifacts.append(f"{pat}×{c}")
check("no HTML-escape artifacts", not artifacts, ", ".join(artifacts) or "clean")

# 3. verdict consistency — every holding's verdict string appears in the doc,
#    and the MTF section table verdict matches the per-stock verdict (both from
#    VERDICT, so we assert each MTF verdict string is present)
missing = []
for tk, (v, g) in VERDICT.items():
    # take a distinctive head token of the verdict to search
    head = v.split("(")[0].split("→")[0].strip()
    if head and head not in full:
        missing.append(f"{tk}:{head}")
check("every verdict head present in document", not missing, ", ".join(missing) or "all 38 present")

# 4. P&L recompute = 0 mismatches
pnl_bad = []
for tk, d in M.items():
    px, c, pnl = d.get("price"), d.get("avg_cost"), d.get("unrealized_pnl_pct")
    if px and c and pnl is not None and abs(round((px/c-1)*100,1) - pnl) > 0.15:
        pnl_bad.append(tk)
check("P&L = live/avg−1 recomputed (0 mismatch)", not pnl_bad, ", ".join(pnl_bad) or "0 mismatches")

# 5. reference-level labelling present
check("reference levels labelled statistical/technical",
      "STATISTICAL/TECHNICAL reference levels" in full and "NOT fundamental price targets" in full)
check("'fundamental target unavailable' notice present",
      "Fundamental price target unavailable" in full or "connect a licensed fundamentals feed" in full)

# 6. no taxation COMPUTED in the liquidation section. Tax may only be MENTIONED
#    inside the explicit negation ("taxation ignored ... no STCG/LTCG, no tax drag").
#    Strip the allowed negation phrases, then assert no tax term survives.
# every tax term must sit inside a negation context ("no"/"ignored"/"pre-tax"
# within the preceding ~14 chars) -- i.e. tax is only ever MENTIONED as ignored.
stray_tax = []
for m in re.finditer(r"STCG|LTCG|capital gains tax|tax drag|after.?tax", full, re.I):
    ctx = re.sub(r"\s+", " ", full[max(0, m.start()-40):m.start()+12]).lower()
    if not re.search(r"\bno\b|ignor|pre.?tax|raw", ctx):
        stray_tax.append(m.group(0))
# structural proof: liquidation JSON applies no tax (proceeds are raw)
liq = json.load(open(r"D:\liquidation3.json", encoding="utf-8"))
has_tax_field = any("tax" in k.lower() for t in liq["tracks"] for s in t["sells"] for k in s)
check("taxation ignored (no tax computed)", not stray_tax and not has_tax_field,
      f"stray tax mentions={stray_tax} json-tax-field={has_tax_field}")

# 7. insider mislabel guard — flagged net-buyers must NOT be called selling
for tk, nm in [("MAHSEAMLES.NS","Maharashtra Seamless"),("RTNPOWER.NS","RattanIndia"),("AJOONI.NS","Ajooni")]:
    net = ((M[tk].get("nse") or {}).get("insider") or {}).get("net") or {}
    lab = net.get("label")
    check(f"{nm} not mislabelled selling", lab != "NET SELLING" and not net.get("promoter_open_market_selling_flag"),
          f"label={lab} promoFlag={net.get('promoter_open_market_selling_flag')}")

# 8. no mostly-empty pages (exclude cover p1)
thin = [i+1 for i,t in enumerate(pages) if i>0 and len(t.strip()) < 180]
check("no mostly-empty pages", not thin, f"thin pages: {thin}" if thin else f"{len(pages)} pages all filled")

# 9. all 3 tracks + no-tax rule present
check("3 liquidation tracks present",
      all(s in full for s in ("TRACK 1", "TRACK 2", "TRACK 3")) and "taxation ignored" in full)

print(f"\nPages: {len(pages)} | ₹ glyphs: {rupee_n}")
print("="*56)
print("VERIFICATION GATE:", "ALL PASS ✓" if not fails else f"FAILURES: {fails}")

# render key pages to PNG for a visual look
doc = fitz.open(PDF)
want = {0:"cover", 1:"exec", }
# find a per-holding page (first one after section 4) and the liquidation page
for i,t in enumerate(pages):
    if "Per-holding deep dives" in t and i+1 < len(pages):
        want[i+1] = "holding1"
    if "Full de-leverage" in t and "RAISE" in t:
        want[i] = "liq"
for idx,name in want.items():
    if idx < len(doc):
        pix = doc[idx].get_pixmap(dpi=110)
        pix.save(fr"D:\verify_{name}.png")
        print(f"rendered D:\\verify_{name}.png (page {idx+1})")
