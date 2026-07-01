# CEFA Terminal — build conventions

CEFA Terminal is an **institutional financial-terminal** design system: **dark-mode-first**, deep blue-black layered surfaces (base → card), one electric-cyan **accent** that marks signal (never decoration), and semantic **gain/loss** greens and reds kept deliberately distinct from the accent. Components are React + Tailwind v4; style with the utility classes and tokens below.

## Wrapping & setup
- **Dark-mode-first.** The brand's primary look is dark. Put `class="dark"` on a top-level wrapper (or `<html>`); without it you get the light "daytime" variant. Surfaces layer as `bg-background` (page) → `bg-card` (raised panels).
- Import components from the bundle global (`window.CEFA.*`) — e.g. `Button`, `Card` (+ `CardHeader`/`CardTitle`/`CardContent`/`CardFooter`), `Table` (+ `TableHeader`/`TableBody`/`TableRow`/`TableHead`/`TableCell`), `Badge`, `Input`, `Label`, `Section`, `Skeleton`, `GlowCard`, `CountUp`, `AuroraField`, and the motion helpers `Reveal`/`Parallax`/`WordReveal`/`CursorGlow`.
- No provider needed — tokens live in CSS, applied via the classes below.

## Styling idiom — Tailwind utilities with CEFA tokens
Compose layout with these real utilities (all defined in the shipped stylesheet):

| Purpose | Classes |
|---|---|
| Surfaces | `bg-background`, `bg-card`, `bg-muted`, `bg-secondary`, `bg-primary` |
| Text | `text-foreground`, `text-muted-foreground`, `text-primary` |
| **Signal accent** (electric cyan) | token `--accent` / `--accent-foreground` — reserve for grades, focus, live data |
| **Gain / loss** (semantic, ≠ accent) | `text-gain` / `bg-gain` / `border-gain`, `text-loss` / `bg-loss` / `border-loss` |
| Borders | `border-border` |
| Type scale | `text-display`, `text-h1`, `text-h2`, `text-h3`, `text-body-lg`, `text-body`, `text-small`, `text-caption` |
| Fonts | `font-heading` (Fraunces, display/titles), `font-sans` (Geist, UI/labels), `font-mono` (Geist Mono, numeric data — use for prices/tickers) |

Rules of the system: the **accent marks signal, never decorates**; **gain/loss are semantic** and must not be swapped for the accent (grade ≠ return); prices, tickers, and tabular numbers use `font-mono`.

## Where the truth lives
- `styles.css` → `_ds_bundle.css` — every token value and utility. Read it before inventing styles.
- Per-component usage: each component's `.prompt.md`.

## Idiomatic snippet
```tsx
<div className="dark">
  <Card>
    <CardHeader>
      <CardTitle className="font-heading">NVDA · NVIDIA</CardTitle>
    </CardHeader>
    <CardContent>
      <div className="flex items-center gap-3">
        <Badge variant="success">Grade A</Badge>
        <span className="font-mono text-gain">+3.1%</span>
      </div>
    </CardContent>
  </Card>
</div>
```
