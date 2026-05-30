import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";
import { trackEvent } from "@/lib/track";
import { CountUp } from "@/components/ui/count-up";
import { Reveal } from "@/components/ui/reveal";

export const metadata = { title: "Track Record — Fortis" };
export const dynamic = "force-dynamic";

// Below this matured-pick count we never make strong claims about performance.
const MIN_SAMPLE_FOR_CLAIMS = 30;

type PickOutcome = {
  ticker: string;
  recommended_date: string;
  recommended_run_type: string;
  conviction_grade: "A" | "B" | "C";
  composite_score: number | null;
  entry_price: number | null;
  return_1d: number | null;
  return_5d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  alpha_1d: number | null;
  alpha_5d: number | null;
  alpha_20d: number | null;
  alpha_60d: number | null;
  benchmark_return_20d: number | null;
  max_drawdown_during_period: number | null;
  max_gain_during_period: number | null;
  still_active: boolean;
};

type RollupRow = {
  rollup_date: string;
  run_type: string;
  conviction_grade: "A" | "B" | "C" | "all";
  pick_count: number;
  avg_return_5d: number | null;
  avg_return_20d: number | null;
  avg_alpha_5d: number | null;
  avg_alpha_20d: number | null;
  win_rate_5d: number | null;
  win_rate_20d: number | null;
  alpha_win_rate_5d: number | null;
  alpha_win_rate_20d: number | null;
  sharpe_ratio_estimated: number | null;
  t_statistic_alpha_20d: number | null;
  is_statistically_significant: boolean | null;
  max_drawdown_avg: number | null;
  max_gain_avg: number | null;
  best_performing_pick: { ticker: string; return_20d: number | null; recommended_date: string } | null;
  worst_performing_pick: { ticker: string; return_20d: number | null; recommended_date: string } | null;
  sample_disclaimer: string | null;
};

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const num = Number(v);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(digits)}%`;
}

function fmtPlain(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

function maturedCount(outcomes: PickOutcome[]): number {
  return outcomes.filter((o) => o.return_20d != null).length;
}

function withinDays(outcomes: PickOutcome[], days: number): PickOutcome[] {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return outcomes.filter((o) => new Date(o.recommended_date) >= cutoff);
}

function meanOf(xs: (number | null | undefined)[]): number | null {
  const v = xs.filter((x): x is number => x != null && !Number.isNaN(Number(x))).map(Number);
  if (v.length === 0) return null;
  return v.reduce((a, b) => a + b, 0) / v.length;
}

function CumulativeChart({ outcomes }: { outcomes: PickOutcome[] }) {
  // Sort matured picks by date; cumulative-compounded line for picks vs SPY.
  const matured = outcomes
    .filter((o) => o.return_20d != null && o.benchmark_return_20d != null)
    .sort(
      (a, b) =>
        new Date(a.recommended_date).getTime() -
        new Date(b.recommended_date).getTime()
    );

  if (matured.length < 2) {
    return (
      <div className="flex h-48 items-center justify-center text-small text-muted-foreground">
        Need at least 2 matured picks to chart cumulative returns.
      </div>
    );
  }

  const points = matured.reduce(
    (acc, o, i) => {
      const prev = i === 0 ? { picks: 1, spy: 1 } : acc[i - 1];
      acc.push({
        picks: prev.picks * (1 + (o.return_20d as number) / 100),
        spy: prev.spy * (1 + (o.benchmark_return_20d as number) / 100),
      });
      return acc;
    },
    [] as { picks: number; spy: number }[]
  );

  const xs = points.map((_, i) => i);
  const allY = points.flatMap((p) => [p.picks, p.spy]);
  const minY = Math.min(...allY);
  const maxY = Math.max(...allY);
  const padY = (maxY - minY) * 0.1 || 0.05;
  const W = 600;
  const H = 180;
  const xScale = (x: number) => (x / (xs.length - 1)) * W;
  const yScale = (y: number) =>
    H - ((y - (minY - padY)) / (maxY + padY - (minY - padY))) * H;

  const path = (key: "picks" | "spy") =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(p[key]).toFixed(1)}`)
      .join(" ");

  return (
    <div className="space-y-4">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-48 w-full"
        preserveAspectRatio="none"
      >
        <line
          x1="0"
          y1={yScale(1)}
          x2={W}
          y2={yScale(1)}
          stroke="var(--border)"
          strokeDasharray="3 5"
        />
        <path
          d={path("spy")}
          fill="none"
          stroke="var(--muted-foreground)"
          strokeOpacity="0.45"
          strokeWidth="1.25"
        />
        <path
          d={path("picks")}
          fill="none"
          stroke="var(--foreground)"
          strokeWidth="1.75"
          strokeLinecap="round"
        />
      </svg>
      <div className="flex justify-end gap-6 text-caption">
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-px w-5 bg-foreground"
          />
          Picks (compounded 20d)
        </span>
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-px w-5 bg-muted-foreground/50"
          />
          SPY (same windows)
        </span>
      </div>
    </div>
  );
}

export default async function TrackRecordPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  await trackEvent("track_record_opened");

  const [{ data: outcomesRaw }, { data: rollupsRaw }] = await Promise.all([
    supabase
      .from("pick_outcomes")
      .select(
        "ticker,recommended_date,recommended_run_type,conviction_grade,composite_score,entry_price,return_1d,return_5d,return_20d,return_60d,alpha_1d,alpha_5d,alpha_20d,alpha_60d,benchmark_return_20d,max_drawdown_during_period,max_gain_during_period,still_active"
      )
      .order("recommended_date", { ascending: false })
      .limit(2000),
    supabase
      .from("system_performance_rollup")
      .select("*")
      .order("rollup_date", { ascending: false })
      .limit(200),
  ]);

  const outcomes: PickOutcome[] = (outcomesRaw ?? []) as PickOutcome[];
  const rollups: RollupRow[] = (rollupsRaw ?? []) as RollupRow[];

  const latestRollupDate =
    rollups.length > 0 ? rollups[0].rollup_date : null;
  const latestRollups = rollups.filter(
    (r) => r.rollup_date === latestRollupDate && r.run_type === "all"
  );
  const allGrade = latestRollups.find((r) => r.conviction_grade === "all");
  const aGrade = latestRollups.find((r) => r.conviction_grade === "A");
  const bGrade = latestRollups.find((r) => r.conviction_grade === "B");
  const cGrade = latestRollups.find((r) => r.conviction_grade === "C");

  const totalPicks = outcomes.length;
  const matured = maturedCount(outcomes);
  const last30 = withinDays(outcomes, 30);

  const last30AvgAlpha = meanOf(last30.map((o) => o.alpha_20d));
  const last30WinRate = (() => {
    const xs = last30
      .map((o) => o.return_20d)
      .filter((x): x is number => x != null);
    if (xs.length === 0) return null;
    return (xs.filter((x) => x > 0).length / xs.length) * 100;
  })();

  // Best / worst 5 by 20d return (entire history)
  const maturedSorted = outcomes
    .filter((o) => o.return_20d != null)
    .sort((a, b) => (b.return_20d as number) - (a.return_20d as number));
  const best5 = maturedSorted.slice(0, 5);
  const worst5 = maturedSorted.slice(-5).reverse();

  const buildingDisclaimer = matured < MIN_SAMPLE_FOR_CLAIMS;

  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Track record
        </p>
        <h1 className="text-h1">
          Forward performance of every pick — benchmarked against SPY.
        </h1>
        <p className="text-body-lg max-w-[680px] text-muted-foreground">
          A / B / C grades graded honestly at 1d / 5d / 20d / 60d windows.
          The system is only as good as the marks it stands by.
        </p>
      </Reveal>

      {buildingDisclaimer && (
        <Reveal>
          <div className="rounded-md border border-border bg-secondary/40 px-4 py-3 text-small text-muted-foreground">
            <span className="text-foreground">Track record building.</span>{" "}
            Only {matured} pick{matured === 1 ? "" : "s"} have reached the
            20-day window. At least 90 days of forward data is recommended
            before drawing conclusions. Treat the numbers below as
            directional only.
          </div>
        </Reveal>
      )}

      {/* ── Aggregate stats ────────────────────────────────────── */}
      <section className="space-y-6">
        <Reveal>
          <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            At a glance
          </h2>
        </Reveal>
        <dl className="grid gap-10 sm:grid-cols-2 md:grid-cols-4">
          <Reveal>
            <Stat label="Total picks tracked" detail={`${matured} matured to 20d`}>
              <CountUp value={totalPicks} />
            </Stat>
          </Reveal>
          <Reveal delay={60}>
            <Stat
              label="A-grade win rate · 20d"
              detail={`n = ${aGrade?.pick_count ?? 0}`}
            >
              {aGrade?.win_rate_20d != null ? (
                <>
                  <CountUp
                    value={aGrade.win_rate_20d}
                    format={(n) => `${n.toFixed(0)}%`}
                  />
                </>
              ) : (
                "—"
              )}
            </Stat>
          </Reveal>
          <Reveal delay={120}>
            <Stat
              label="Avg alpha vs SPY · 20d"
              detail={
                allGrade?.is_statistically_significant
                  ? `t = ${fmtPlain(allGrade?.t_statistic_alpha_20d ?? null)} · significant`
                  : allGrade?.sample_disclaimer ?? "—"
              }
            >
              {allGrade?.avg_alpha_20d != null ? (
                <CountUp
                  value={Number(allGrade.avg_alpha_20d)}
                  format={(n) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`}
                />
              ) : (
                "—"
              )}
            </Stat>
          </Reveal>
          <Reveal delay={180}>
            <Stat
              label="Last 30 days"
              detail={
                last30WinRate != null
                  ? `${last30.length} picks · ${last30WinRate.toFixed(0)}% win`
                  : `${last30.length} picks`
              }
            >
              {last30AvgAlpha != null ? (
                <CountUp
                  value={last30AvgAlpha}
                  format={(n) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`}
                />
              ) : (
                "—"
              )}
            </Stat>
          </Reveal>
        </dl>
      </section>

      {/* ── Cumulative chart ───────────────────────────────────── */}
      <section className="space-y-6">
        <Reveal>
          <div>
            <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
              Cumulative compounded 20d return
            </h2>
            <p className="mt-1 text-small text-muted-foreground">
              Each matured pick compounded into a running line; SPY uses the
              same 20-day windows.
            </p>
          </div>
        </Reveal>
        <Reveal>
          <CumulativeChart outcomes={outcomes} />
        </Reveal>
      </section>

      {/* ── Performance by grade ───────────────────────────────── */}
      <section className="space-y-6">
        <Reveal>
          <div>
            <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
              Performance by conviction grade
            </h2>
            <p className="mt-1 text-small text-muted-foreground">
              A should outperform B should outperform C — if not, the
              grading system has a problem.
            </p>
          </div>
        </Reveal>
        <Reveal>
          <div className="overflow-x-auto">
            <table className="w-full text-small">
              <thead>
                <tr className="text-left">
                  <TH>Grade</TH>
                  <TH>N</TH>
                  <TH>Avg ret 5d</TH>
                  <TH>Avg ret 20d</TH>
                  <TH>Avg alpha 20d</TH>
                  <TH>Alpha-win 20d</TH>
                  <TH>t-stat</TH>
                  <TH>Sig?</TH>
                </tr>
              </thead>
              <tbody>
                {[
                  { label: "A", r: aGrade },
                  { label: "B", r: bGrade },
                  { label: "C", r: cGrade },
                ].map(({ label, r }) => (
                  <tr
                    key={label}
                    className="border-t border-border transition-premium hover:bg-secondary/40"
                  >
                    <TD>
                      <span className="font-medium">{label}</span>
                    </TD>
                    <TD className="tabular-nums">{r?.pick_count ?? 0}</TD>
                    <TD className="tabular-nums">
                      {fmtPct(r?.avg_return_5d ?? null)}
                    </TD>
                    <TD className="tabular-nums">
                      {fmtPct(r?.avg_return_20d ?? null)}
                    </TD>
                    <TD className="tabular-nums">
                      {fmtPct(r?.avg_alpha_20d ?? null)}
                    </TD>
                    <TD className="tabular-nums">
                      {r?.alpha_win_rate_20d != null
                        ? `${r.alpha_win_rate_20d.toFixed(0)}%`
                        : "—"}
                    </TD>
                    <TD className="tabular-nums">
                      {fmtPlain(r?.t_statistic_alpha_20d ?? null)}
                    </TD>
                    <TD>
                      {r?.is_statistically_significant ? (
                        <span className="text-caption uppercase tracking-[0.14em] text-foreground">
                          Yes
                        </span>
                      ) : (
                        <span className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
                          No
                        </span>
                      )}
                    </TD>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Reveal>
      </section>

      {/* ── Best / worst ───────────────────────────────────────── */}
      <section className="grid gap-12 md:grid-cols-2">
        <Reveal>
          <div className="space-y-4">
            <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
              Best 5 calls · 20d return
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-small">
                <thead>
                  <tr className="text-left">
                    <TH>Ticker</TH>
                    <TH>Grade</TH>
                    <TH>Date</TH>
                    <TH>Return 20d</TH>
                    <TH>Alpha 20d</TH>
                  </tr>
                </thead>
                <tbody>
                  {best5.map((o, i) => (
                    <tr
                      key={`best-${o.ticker}-${o.recommended_date}-${i}`}
                      className="border-t border-border transition-premium hover:bg-secondary/40"
                    >
                      <TD>
                        <span className="font-mono font-medium">
                          {o.ticker}
                        </span>
                      </TD>
                      <TD>{o.conviction_grade}</TD>
                      <TD className="tabular-nums text-muted-foreground">
                        {o.recommended_date}
                      </TD>
                      <TD className="tabular-nums text-foreground">
                        {fmtPct(o.return_20d)}
                      </TD>
                      <TD className="tabular-nums">{fmtPct(o.alpha_20d)}</TD>
                    </tr>
                  ))}
                  {best5.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        className="py-10 text-center text-caption text-muted-foreground"
                      >
                        No matured picks yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Reveal>

        <Reveal delay={80}>
          <div className="space-y-4">
            <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
              Worst 5 calls · 20d return
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-small">
                <thead>
                  <tr className="text-left">
                    <TH>Ticker</TH>
                    <TH>Grade</TH>
                    <TH>Date</TH>
                    <TH>Return 20d</TH>
                    <TH>Max DD</TH>
                  </tr>
                </thead>
                <tbody>
                  {worst5.map((o, i) => (
                    <tr
                      key={`worst-${o.ticker}-${o.recommended_date}-${i}`}
                      className="border-t border-border transition-premium hover:bg-secondary/40"
                    >
                      <TD>
                        <span className="font-mono font-medium">
                          {o.ticker}
                        </span>
                      </TD>
                      <TD>{o.conviction_grade}</TD>
                      <TD className="tabular-nums text-muted-foreground">
                        {o.recommended_date}
                      </TD>
                      <TD className="tabular-nums text-destructive">
                        {fmtPct(o.return_20d)}
                      </TD>
                      <TD className="tabular-nums">
                        {fmtPct(o.max_drawdown_during_period)}
                      </TD>
                    </tr>
                  ))}
                  {worst5.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        className="py-10 text-center text-caption text-muted-foreground"
                      >
                        No matured picks yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Reveal>
      </section>

      <Reveal>
        <p className="text-caption">
          <span className="text-foreground">Methodology.</span> Forward
          returns use trading days (1, 5, 20, 60) measured from the close on
          each pick&apos;s recommendation date, with SPY as benchmark for
          alpha. Statistical significance is gated by{" "}
          <code className="font-mono">|t| ≥ 1.5</code> and{" "}
          <code className="font-mono">n ≥ 10</code>. Delisted and bankrupt
          tickers remain in the dataset; rows whose forward prices cannot
          be fetched will show &quot;—&quot;. Rollups are recomputed weekly
          by <code className="font-mono">outcome_tracker.compute_rollups</code>.
        </p>
      </Reveal>
    </div>
  );
}

function Stat({
  label,
  children,
  detail,
}: {
  label: string
  children: React.ReactNode
  detail?: string
}) {
  return (
    <div className="space-y-1.5">
      <dt className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </dt>
      <dd className="text-h2 tabular-nums">{children}</dd>
      {detail && <p className="text-caption">{detail}</p>}
    </div>
  )
}

function TH({ children }: { children: React.ReactNode }) {
  return (
    <th className="py-3 pr-6 text-caption uppercase tracking-[0.14em] font-medium text-muted-foreground">
      {children}
    </th>
  )
}

function TD({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return <td className={`py-3 pr-6 ${className ?? ""}`}>{children}</td>
}
