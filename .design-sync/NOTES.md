# CEFA Terminal — design-sync notes

- Claude Design project: **CEFA Terminal** (`e0a49a9a-1562-4fd2-8ceb-df2a2d427b42`).
- This is a **Next.js app**, not a component-library package: no `dist/` → converter runs in **synth-from-src** mode over `src/components/ui/`.
- **CSS**: Tailwind v4. `src/app/globals.css` is Tailwind source (`@import "tailwindcss"`), not shippable CSS. We compile it to a static stylesheet at `.design-sync/.cache/compiled.css` via `npx @tailwindcss/cli@4 -i src/app/globals.css -o .design-sync/.cache/compiled.css` and point `cfg.cssEntry` there. **Re-sync must recompile this first.**
- Fonts (Geist / Fraunces / DM Serif) load via `next/font` at runtime — expect `[FONT_MISSING]`; resolve via extraFonts/remote import or runtimeFontPrefixes.
- Components importing `next/link` / `next/navigation` / media-query hooks may not render statically → floor cards unless shimmed.

## Build recipe (synth-from-src for an app, not a package)
The converter expects a package under `node_modules/<pkg>`. This app isn't self-installed, so re-sync must recreate a **non-recursive self-package** before building:
```
rm -rf node_modules/fortis-stocks && mkdir -p node_modules/fortis-stocks
python3 -c "import json;d=json.load(open('package.json'));open('node_modules/fortis-stocks/package.json','w').write(json.dumps({'name':d['name'],'version':d['version']}))"
ln -s ../../src node_modules/fortis-stocks/src
ln -s ../../tsconfig.json node_modules/fortis-stocks/tsconfig.json
# compiled CSS must live INSIDE the pkg bound (symlinks resolve outside → skipped):
npx @tailwindcss/cli@4 -i src/app/globals.css -o .design-sync/.cache/compiled.css
cat .design-sync/.cache/font-head.css .design-sync/.cache/compiled.css > node_modules/fortis-stocks/_compiled.css
```
- Build cmd: `node .ds-sync/package-build.mjs --config .design-sync/config.json --node-modules ./node_modules --out ./ds-bundle` (NO `--entry` — that disables synth discovery).
- `cfg.srcDir="src/components/ui"` scopes discovery (bare `src` drags in `src/app`→globals.css and Next server code).
- `cfg.cssEntry="_compiled.css"` (pkg-relative, real file inside `node_modules/fortis-stocks/`).
- `cfg.componentSrcMap`: `TickerTape`/`PageTransition`=null (they import `next/link`/`next/navigation` → unbundleable `@opentelemetry`); 19 Card/Form/Table sub-components=null (kept importable via the wholesale bundle, just not separate cards). Net: **16 cards**.
- `font-head.css` defines the next/font var families (`--font-geist-sans` etc.) from Google Fonts so previews render in Geist/Fraunces/DM Serif instead of fallbacks.

## Re-sync risks
- TickerTape + PageTransition are NOT synced (Next-router coupled). Revisit if they gain static-renderable variants or a next shim.
- font-head + compiled.css are gitignored cache; recompile every re-sync (recipe above).
- The self-package dir is gitignored (node_modules); recreate every re-sync.


## CORRECTION — how the 2 Next components are excluded (persistent)
Do NOT temp-move source files. Instead discovery points at a **curated symlink dir** so `ticker-tape`/`page-transition` never enter the synth barrel:
```
rm -rf node_modules/fortis-stocks/ds-ui && mkdir -p node_modules/fortis-stocks/ds-ui
for f in button badge card input label form table skeleton section count-up aurora-field cursor-glow glow-card reveal parallax word-reveal; do
  ln -s ../../../src/components/ui/$f.tsx node_modules/fortis-stocks/ds-ui/$f.tsx
done
```
- `cfg.srcDir="ds-ui"` (NOT `src/components/ui`). `@/…` imports still resolve via the pkg-local `tsconfig.json` symlink → repo `src`.
- `cfg.componentSrcMap`: 19 Card/Form/Table sub-components = null (importable via wholesale bundle, not separate cards). No TickerTape/PageTransition entries needed — their files aren't in `ds-ui`.

## Final state (first sync, 2026-07-01)
- Uploaded to Claude Design project **CEFA Terminal** — 16 components, 11 authored preview cards (Button, Badge, Card, Input, Label, Section, Skeleton, Table, CountUp, GlowCard, AuroraField), 5 floor cards (CursorGlow, Form, Parallax, Reveal, WordReveal — motion/pointer/RHF-context, not statically renderable as-is).
- Render check 16/16 clean, 0 bad. Fonts via remote Google Fonts @import ([FONT_REMOTE], expected).
- **Previews render in LIGHT mode though the brand is dark-mode-first** — flagged to the user for a brand call. To switch: wrap preview bodies (or add a provider) with `class="dark"` + `bg-background`.
- Render check needs playwright+chromium (installed at ~/Library/Caches/ms-playwright, chromium-headless-shell v1228).

## Re-sync checklist (in order)
1. Recreate self-package: package.json + `src`/`tsconfig.json` symlinks + `ds-ui` curated dir (above).
2. Recompile CSS: `npx @tailwindcss/cli@4 -i src/app/globals.css -o .design-sync/.cache/compiled.css` then `cat .design-sync/.cache/font-head.css .design-sync/.cache/compiled.css > node_modules/fortis-stocks/_compiled.css`.
3. Fetch project _ds_sync.json → .design-sync/.cache/remote-sync.json, run resync.mjs with --remote.
