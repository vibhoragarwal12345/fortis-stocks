import { redirect } from "next/navigation";
import Link from "next/link";

import { createClient } from "@/lib/supabase/server";
import { trackEvent } from "@/lib/track";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

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
      <div className="flex h-40 items-center justify-center text-xs text-muted-foreground">
        Need at least 2 matured picks to chart cumulative returns
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
  const H = 160;
  const xScale = (x: number) => (x / (xs.length - 1)) * W;
  const yScale = (y: number) =>
    H - ((y - (minY - padY)) / (maxY + padY - (minY - padY))) * H;

  const path = (key: "picks" | "spy") =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(p[key]).toFixed(1)}`)
      .join(" ");

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-40 w-full"
        preserveAspectRatio="none"
      >
        <line
          x1="0"
          y1={yScale(1)}
          x2={W}
          y2={yScale(1)}
          stroke="currentColor"
          strokeOpacity="0.15"
          strokeDasharray="4 4"
        />
        <path d={path("spy")} fill="none" stroke="#94a3b8" strokeWidth="1.5" />
        <path d={path("picks")} fill="none" stroke="#0ea5e9" strokeWidth="2" />
      </svg>
      <div className="mt-1 flex justify-end gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-sky-500" /> Picks (compounded 20d)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-slate-400" /> SPY (same windows)
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
    <div className="px-6 py-10">
      <div className="mx-auto max-w-7xl space-y-8">
          <div>
            <h1 className="text-3xl font-bold">Track Record</h1>
            <p className="mt-1 text-muted-foreground">
              Forward performance of every A / B / C pick produced by the
              system, benchmarked against SPY at 1d / 5d / 20d / 60d.
            </p>
          </div>

          {buildingDisclaimer && (
            <div className="rounded-lg border border-amber-300/50 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/50 dark:text-amber-200">
              <strong>Track record building.</strong> Only {matured} pick
              {matured === 1 ? "" : "s"} have reached the 20-day window. At
              least 90 days of forward data is recommended before drawing
              conclusions. Treat the numbers below as directional only.
            </div>
          )}

          {/* Aggregate stats */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <Card>
              <CardHeader>
                <CardDescription>Total picks tracked</CardDescription>
                <CardTitle className="text-2xl font-semibold">
                  {totalPicks}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 text-xs text-muted-foreground">
                {matured} matured to 20d
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>A-grade win rate (20d)</CardDescription>
                <CardTitle className="text-2xl font-semibold">
                  {aGrade?.win_rate_20d != null
                    ? `${aGrade.win_rate_20d.toFixed(0)}%`
                    : "—"}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 text-xs text-muted-foreground">
                n = {aGrade?.pick_count ?? 0}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>Avg alpha vs SPY (20d, all)</CardDescription>
                <CardTitle className="text-2xl font-semibold">
                  {fmtPct(allGrade?.avg_alpha_20d ?? null)}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 text-xs text-muted-foreground">
                {allGrade?.is_statistically_significant
                  ? `t = ${fmtPlain(allGrade?.t_statistic_alpha_20d ?? null)} — significant`
                  : allGrade?.sample_disclaimer ?? "—"}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>Last 30 days</CardDescription>
                <CardTitle className="text-2xl font-semibold">
                  {fmtPct(last30AvgAlpha)}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 text-xs text-muted-foreground">
                {last30.length} picks; win rate{" "}
                {last30WinRate != null ? `${last30WinRate.toFixed(0)}%` : "—"}
              </CardContent>
            </Card>
          </div>

          {/* Cumulative chart */}
          <Card>
            <CardHeader>
              <CardTitle>Cumulative compounded 20d return</CardTitle>
              <CardDescription>
                Each matured pick is compounded into a running line; SPY uses
                the same 20-day windows.
              </CardDescription>
            </CardHeader>
            <CardContent className="px-4">
              <CumulativeChart outcomes={outcomes} />
            </CardContent>
          </Card>

          {/* Performance by grade */}
          <Card>
            <CardHeader>
              <CardTitle>Performance by Conviction Grade</CardTitle>
              <CardDescription>
                A should outperform B should outperform C — if not, the
                grading system has a problem.
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto px-0">
              <table className="w-full text-sm">
                <thead className="border-b text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2">Grade</th>
                    <th className="px-4 py-2">N</th>
                    <th className="px-4 py-2">Avg ret 5d</th>
                    <th className="px-4 py-2">Avg ret 20d</th>
                    <th className="px-4 py-2">Avg alpha 20d</th>
                    <th className="px-4 py-2">Alpha-win 20d</th>
                    <th className="px-4 py-2">t-stat</th>
                    <th className="px-4 py-2">Sig?</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: "A", r: aGrade },
                    { label: "B", r: bGrade },
                    { label: "C", r: cGrade },
                  ].map(({ label, r }) => (
                    <tr key={label} className="border-b last:border-0">
                      <td className="px-4 py-2 font-medium">{label}</td>
                      <td className="px-4 py-2 tabular-nums">{r?.pick_count ?? 0}</td>
                      <td className="px-4 py-2 tabular-nums">
                        {fmtPct(r?.avg_return_5d ?? null)}
                      </td>
                      <td className="px-4 py-2 tabular-nums">
                        {fmtPct(r?.avg_return_20d ?? null)}
                      </td>
                      <td className="px-4 py-2 tabular-nums">
                        {fmtPct(r?.avg_alpha_20d ?? null)}
                      </td>
                      <td className="px-4 py-2 tabular-nums">
                        {r?.alpha_win_rate_20d != null
                          ? `${r.alpha_win_rate_20d.toFixed(0)}%`
                          : "—"}
                      </td>
                      <td className="px-4 py-2 tabular-nums">
                        {fmtPlain(r?.t_statistic_alpha_20d ?? null)}
                      </td>
                      <td className="px-4 py-2">
                        {r?.is_statistically_significant ? (
                          <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                            yes
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            no
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>

          {/* Best / worst */}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Best 5 calls (20d return)</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto px-0">
                <table className="w-full text-sm">
                  <thead className="border-b text-left text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2">Ticker</th>
                      <th className="px-4 py-2">Grade</th>
                      <th className="px-4 py-2">Date</th>
                      <th className="px-4 py-2">Return 20d</th>
                      <th className="px-4 py-2">Alpha 20d</th>
                    </tr>
                  </thead>
                  <tbody>
                    {best5.map((o, i) => (
                      <tr
                        key={`best-${o.ticker}-${o.recommended_date}-${i}`}
                        className="border-b last:border-0"
                      >
                        <td className="px-4 py-2 font-medium">{o.ticker}</td>
                        <td className="px-4 py-2">{o.conviction_grade}</td>
                        <td className="px-4 py-2 tabular-nums">
                          {o.recommended_date}
                        </td>
                        <td className="px-4 py-2 tabular-nums text-emerald-700 dark:text-emerald-400">
                          {fmtPct(o.return_20d)}
                        </td>
                        <td className="px-4 py-2 tabular-nums">
                          {fmtPct(o.alpha_20d)}
                        </td>
                      </tr>
                    ))}
                    {best5.length === 0 && (
                      <tr>
                        <td
                          colSpan={5}
                          className="px-4 py-6 text-center text-xs text-muted-foreground"
                        >
                          No matured picks yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Worst 5 calls (20d return)</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto px-0">
                <table className="w-full text-sm">
                  <thead className="border-b text-left text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2">Ticker</th>
                      <th className="px-4 py-2">Grade</th>
                      <th className="px-4 py-2">Date</th>
                      <th className="px-4 py-2">Return 20d</th>
                      <th className="px-4 py-2">Max DD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {worst5.map((o, i) => (
                      <tr
                        key={`worst-${o.ticker}-${o.recommended_date}-${i}`}
                        className="border-b last:border-0"
                      >
                        <td className="px-4 py-2 font-medium">{o.ticker}</td>
                        <td className="px-4 py-2">{o.conviction_grade}</td>
                        <td className="px-4 py-2 tabular-nums">
                          {o.recommended_date}
                        </td>
                        <td className="px-4 py-2 tabular-nums text-rose-700 dark:text-rose-400">
                          {fmtPct(o.return_20d)}
                        </td>
                        <td className="px-4 py-2 tabular-nums">
                          {fmtPct(o.max_drawdown_during_period)}
                        </td>
                      </tr>
                    ))}
                    {worst5.length === 0 && (
                      <tr>
                        <td
                          colSpan={5}
                          className="px-4 py-6 text-center text-xs text-muted-foreground"
                        >
                          No matured picks yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </div>

          <div className="rounded-lg border bg-card px-4 py-3 text-xs text-muted-foreground">
            <strong className="text-foreground">Methodology.</strong>{" "}
            Forward returns use TRADING days (1, 5, 20, 60) measured from the
            close on each pick&apos;s recommendation date, with SPY as
            benchmark for alpha. Statistical significance is gated by{" "}
            <span className="font-mono">|t| ≥ 1.5</span> and{" "}
            <span className="font-mono">n ≥ 10</span>. Delisted and bankrupt
            tickers remain in the dataset; rows whose forward prices cannot
            be fetched will show &quot;—&quot;. Rollups are recomputed weekly
            by <span className="font-mono">outcome_tracker.compute_rollups</span>.
          </div>

          <div className="flex justify-end">
            <Link
              href="/dashboard"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Back to dashboard
            </Link>
          </div>
        </div>
    </div>
  );
}
