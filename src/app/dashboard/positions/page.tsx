import { redirect } from "next/navigation";
import Link from "next/link";

import { createClient } from "@/lib/supabase/server";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata = { title: "Positions — Fortis" };
export const dynamic = "force-dynamic";

type Portfolio = { id: string; name: string };

type PositionSignal = {
  id: number;
  portfolio_id: string;
  ticker: string;
  snapshot_date: string;
  run_type: string;
  entry_basis: Record<string, unknown> | null;
  current_price: number | null;
  cost_basis: number | null;
  unrealized_return_pct: number | null;
  holding_period_days: number | null;
  signal_type: string;
  signal_strength: number | null;
  recommended_action: string | null;
  recommended_size_change_pct: number | null;
  reasoning: string | null;
  supporting_data: Record<string, unknown> | null;
  requires_immediate_attention: boolean;
  reviewed_at: string | null;
};

type Rebalance = {
  portfolio_id: string;
  snapshot_date: string;
  current_total_value: number | null;
  overweight_positions: Array<{ ticker: string; weight: number; target_weight: number; excess_pct: number }> | null;
  underweight_positions: Array<{ ticker: string; weight: number; target_weight: number; excess_pct: number }> | null;
  concentration_alerts: Array<{ severity: string; description: string }> | null;
  top_3_priorities: Array<{ action: string; ticker: string; reasoning: string }> | null;
  reasoning: string | null;
};

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const num = Number(v);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(digits)}%`;
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function statusForSignal(s: PositionSignal): {
  color: string;
  label: string;
  bgClass: string;
} {
  if (s.requires_immediate_attention) {
    return {
      color: "red",
      label: "Review",
      bgClass:
        "bg-rose-50 border-rose-200 text-rose-800 dark:bg-rose-950/40 dark:border-rose-800 dark:text-rose-200",
    };
  }
  if ((s.signal_strength ?? 0) >= 30 && s.signal_type !== "no_action") {
    return {
      color: "yellow",
      label: "Monitor",
      bgClass:
        "bg-amber-50 border-amber-200 text-amber-900 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-200",
    };
  }
  return {
    color: "green",
    label: "OK",
    bgClass:
      "bg-emerald-50 border-emerald-200 text-emerald-900 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-200",
  };
}

export default async function PositionsPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: portfolios } = await supabase
    .from("portfolios")
    .select("id,name")
    .order("name");

  const portfolioList: Portfolio[] = (portfolios ?? []) as Portfolio[];

  if (portfolioList.length === 0) {
    return (
      <div className="flex min-h-screen flex-col">
        <Header email={user.email ?? ""} />
        <main className="flex-1 px-6 py-10">
          <div className="mx-auto max-w-7xl">
            <h1 className="text-3xl font-bold">Positions</h1>
            <p className="mt-1 text-muted-foreground">
              You have no tracked portfolios yet.
            </p>
          </div>
        </main>
      </div>
    );
  }

  const portfolioIds = portfolioList.map((p) => p.id);

  const [{ data: signalsRaw }, { data: rebalanceRaw }] = await Promise.all([
    supabase
      .from("position_signals")
      .select("*")
      .in("portfolio_id", portfolioIds)
      .order("snapshot_date", { ascending: false })
      .order("signal_strength", { ascending: false })
      .limit(500),
    supabase
      .from("portfolio_rebalance_suggestions")
      .select("*")
      .in("portfolio_id", portfolioIds)
      .order("snapshot_date", { ascending: false })
      .limit(50),
  ]);

  const signals: PositionSignal[] = (signalsRaw ?? []) as PositionSignal[];
  const rebalances: Rebalance[] = (rebalanceRaw ?? []) as Rebalance[];

  // Latest snapshot per portfolio
  const latestSignalsByPortfolio = new Map<string, PositionSignal[]>();
  const latestDatePerPortfolio = new Map<string, string>();
  for (const s of signals) {
    const cur = latestDatePerPortfolio.get(s.portfolio_id);
    if (!cur || s.snapshot_date > cur) {
      latestDatePerPortfolio.set(s.portfolio_id, s.snapshot_date);
    }
  }
  for (const s of signals) {
    if (latestDatePerPortfolio.get(s.portfolio_id) === s.snapshot_date) {
      const arr = latestSignalsByPortfolio.get(s.portfolio_id) ?? [];
      arr.push(s);
      latestSignalsByPortfolio.set(s.portfolio_id, arr);
    }
  }

  const latestRebalanceByPortfolio = new Map<string, Rebalance>();
  for (const r of rebalances) {
    if (!latestRebalanceByPortfolio.has(r.portfolio_id)) {
      latestRebalanceByPortfolio.set(r.portfolio_id, r);
    }
  }

  // Historical per (portfolio, ticker)
  const historyByPortfolioTicker = new Map<string, PositionSignal[]>();
  for (const s of signals) {
    const key = `${s.portfolio_id}::${s.ticker}`;
    const arr = historyByPortfolioTicker.get(key) ?? [];
    arr.push(s);
    historyByPortfolioTicker.set(key, arr);
  }

  const totalUrgent = Array.from(latestSignalsByPortfolio.values())
    .flat()
    .filter((s) => s.requires_immediate_attention).length;

  return (
    <div className="flex min-h-screen flex-col">
      <Header email={user.email ?? ""} />

      <main className="flex-1 px-6 py-10">
        <div className="mx-auto max-w-7xl space-y-8">
          <div className="flex items-baseline justify-between">
            <div>
              <h1 className="text-3xl font-bold">Positions</h1>
              <p className="mt-1 text-muted-foreground">
                Position-management signals for every tracked portfolio.
                Color-coded per holding: green = no action, yellow = monitor,
                red = review. <strong>For advisor review — not a recommendation.</strong>
              </p>
            </div>
            {totalUrgent > 0 && (
              <div className="rounded-lg border border-rose-300/60 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-700/60 dark:bg-rose-950/40 dark:text-rose-200">
                <strong>{totalUrgent}</strong> position
                {totalUrgent === 1 ? "" : "s"} requiring review
              </div>
            )}
          </div>

          {portfolioList.map((p) => {
            const latestSigs = latestSignalsByPortfolio.get(p.id) ?? [];
            const rb = latestRebalanceByPortfolio.get(p.id) ?? null;
            const urgent = latestSigs.filter(
              (s) => s.requires_immediate_attention
            );
            return (
              <Card key={p.id}>
                <CardHeader>
                  <CardTitle>{p.name}</CardTitle>
                  <CardDescription>
                    {latestSigs.length} position{latestSigs.length === 1 ? "" : "s"}
                    {rb?.current_total_value && (
                      <> · {fmtMoney(rb.current_total_value)} total value</>
                    )}
                    {urgent.length > 0 && (
                      <>
                        {" · "}
                        <span className="font-medium text-rose-700 dark:text-rose-400">
                          {urgent.length} requiring review
                        </span>
                      </>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6 px-4">
                  {rb?.reasoning && (
                    <div className="rounded-md border bg-muted/30 px-4 py-3 text-sm">
                      {rb.reasoning}
                    </div>
                  )}

                  {rb?.top_3_priorities && rb.top_3_priorities.length > 0 && (
                    <div>
                      <h3 className="mb-2 text-sm font-semibold tracking-tight">
                        Top priorities
                      </h3>
                      <ol className="space-y-2 text-sm">
                        {rb.top_3_priorities.map((pr, i) => (
                          <li
                            key={i}
                            className="rounded-md border bg-card px-3 py-2"
                          >
                            <div className="text-xs font-medium uppercase text-muted-foreground">
                              {(pr.action || "").replace(/_/g, " ")} · {pr.ticker}
                            </div>
                            <div className="mt-1">{pr.reasoning}</div>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {rb?.concentration_alerts && rb.concentration_alerts.length > 0 && (
                    <div>
                      <h3 className="mb-2 text-sm font-semibold tracking-tight">
                        Concentration alerts
                      </h3>
                      <ul className="space-y-1 text-sm">
                        {rb.concentration_alerts.map((c, i) => (
                          <li key={i} className="text-muted-foreground">
                            <span className="font-medium">{c.severity}:</span>{" "}
                            {c.description}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div>
                    <h3 className="mb-2 text-sm font-semibold tracking-tight">
                      Holdings
                    </h3>
                    {latestSigs.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No position signals yet. Run{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-xs">
                          pipeline/agents/position_manager.py
                        </code>
                        .
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {latestSigs
                          .slice()
                          .sort((a, b) => {
                            // urgent first, then by strength desc
                            if (
                              a.requires_immediate_attention !==
                              b.requires_immediate_attention
                            ) {
                              return a.requires_immediate_attention ? -1 : 1;
                            }
                            return (
                              (b.signal_strength ?? 0) -
                              (a.signal_strength ?? 0)
                            );
                          })
                          .map((s) => {
                            const status = statusForSignal(s);
                            const historyKey = `${s.portfolio_id}::${s.ticker}`;
                            const history =
                              historyByPortfolioTicker.get(historyKey) ?? [];
                            return (
                              <details
                                key={s.id}
                                className={`group rounded-md border px-4 py-3 ${status.bgClass}`}
                              >
                                <summary className="flex cursor-pointer items-center justify-between gap-3 list-none">
                                  <div className="flex items-center gap-3">
                                    <span className="inline-block min-w-[3rem] font-mono text-base font-semibold">
                                      {s.ticker}
                                    </span>
                                    <span className="rounded bg-background/60 px-2 py-0.5 text-xs font-medium">
                                      {status.label}
                                    </span>
                                    <span className="text-xs">
                                      {(s.signal_type || "").replace(/_/g, " ")}
                                      {s.signal_strength
                                        ? ` · strength ${s.signal_strength}`
                                        : ""}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-4 text-xs tabular-nums">
                                    <span>{fmtPct(s.unrealized_return_pct)}</span>
                                    <span className="text-muted-foreground group-open:rotate-90 transition-transform">
                                      ›
                                    </span>
                                  </div>
                                </summary>
                                <div className="mt-3 space-y-3 text-sm">
                                  <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                                    <div>
                                      <div className="text-muted-foreground">
                                        Cost basis
                                      </div>
                                      <div className="tabular-nums">
                                        ${Number(s.cost_basis ?? 0).toFixed(2)}
                                      </div>
                                    </div>
                                    <div>
                                      <div className="text-muted-foreground">
                                        Current
                                      </div>
                                      <div className="tabular-nums">
                                        $
                                        {Number(s.current_price ?? 0).toFixed(2)}
                                      </div>
                                    </div>
                                    <div>
                                      <div className="text-muted-foreground">
                                        Held
                                      </div>
                                      <div className="tabular-nums">
                                        {s.holding_period_days ?? "—"}d
                                      </div>
                                    </div>
                                    <div>
                                      <div className="text-muted-foreground">
                                        Action
                                      </div>
                                      <div>
                                        {(s.recommended_action || "—").replace(
                                          /_/g,
                                          " "
                                        )}
                                        {s.recommended_size_change_pct
                                          ? ` (${s.recommended_size_change_pct}%)`
                                          : ""}
                                      </div>
                                    </div>
                                  </div>
                                  {s.reasoning && (
                                    <div className="rounded bg-background/60 px-3 py-2 text-sm leading-relaxed">
                                      {s.reasoning}
                                    </div>
                                  )}
                                  {history.length > 1 && (
                                    <div>
                                      <div className="mb-1 text-xs text-muted-foreground">
                                        History ({history.length - 1} prior
                                        signal{history.length - 1 === 1 ? "" : "s"})
                                      </div>
                                      <ul className="space-y-1 text-xs">
                                        {history.slice(1, 6).map((h) => (
                                          <li key={h.id} className="tabular-nums">
                                            <span className="font-mono text-muted-foreground">
                                              {h.snapshot_date}
                                            </span>{" "}
                                            · {(h.signal_type || "").replace(
                                              /_/g,
                                              " "
                                            )}
                                            {h.signal_strength
                                              ? ` (${h.signal_strength})`
                                              : ""}
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                </div>
                              </details>
                            );
                          })}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}

          <div className="flex justify-end">
            <Link
              href="/dashboard"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Back to dashboard
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}

function Header({ email }: { email: string }) {
  return (
    <header className="border-b bg-card px-6 py-3">
      <div className="mx-auto flex max-w-7xl items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-base font-semibold tracking-tight">
            Fortis Stock Intelligence
          </span>
          <Link
            href="/dashboard"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Dashboard
          </Link>
          <Link
            href="/dashboard/track-record"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Track Record
          </Link>
          <span className="text-sm font-medium">Positions</span>
        </div>
        <span className="text-sm text-muted-foreground">{email}</span>
      </div>
    </header>
  );
}
