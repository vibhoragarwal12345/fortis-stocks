import { redirect } from "next/navigation";
import Link from "next/link";

import { createClient } from "@/lib/supabase/server";
import { trackEvent } from "@/lib/track";
import { Card, CardContent } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";

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
  label: string;
  dot: string;
} {
  if (s.requires_immediate_attention) {
    return { label: "Review", dot: "bg-destructive" };
  }
  if ((s.signal_strength ?? 0) >= 30 && s.signal_type !== "no_action") {
    return { label: "Monitor", dot: "bg-foreground" };
  }
  return { label: "OK", dot: "bg-muted-foreground/30" };
}

export default async function PositionsPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  await trackEvent("positions_opened");

  const { data: portfolios } = await supabase
    .from("portfolios")
    .select("id,name")
    .order("name");

  const portfolioList: Portfolio[] = (portfolios ?? []) as Portfolio[];

  if (portfolioList.length === 0) {
    return (
      <div className="mx-auto max-w-[1280px] px-6 py-14 md:px-10 md:py-16">
        <header className="space-y-2">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Positions
          </p>
          <h1 className="text-h1">No portfolios yet.</h1>
        </header>
        <Card className="mt-12">
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            Add a portfolio via{" "}
            <code className="font-mono">pipeline/data/load_sample_portfolio.py</code>
            {" "}or upload one from{" "}
            <Link
              href="/dashboard/settings/branding"
              className="underline-offset-4 hover:text-foreground hover:underline"
            >
              Settings
            </Link>
            .
          </CardContent>
        </Card>
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
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <Reveal as="header" className="flex flex-wrap items-end justify-between gap-6">
        <div className="space-y-3">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Positions
          </p>
          <h1 className="text-h1">Position-management signals.</h1>
          <p className="text-body-lg max-w-[640px] text-muted-foreground">
            Color-coded per holding: solid = review, foreground dot = monitor,
            muted = no action. <span className="text-foreground">For advisor review — not a recommendation.</span>
          </p>
        </div>
        {totalUrgent > 0 && (
          <p className="text-caption uppercase tracking-[0.14em] text-destructive">
            {totalUrgent} position{totalUrgent === 1 ? "" : "s"} requiring review
          </p>
        )}
      </Reveal>

      {portfolioList.map((p, pIdx) => {
        const latestSigs = latestSignalsByPortfolio.get(p.id) ?? [];
        const rb = latestRebalanceByPortfolio.get(p.id) ?? null;
        const urgent = latestSigs.filter((s) => s.requires_immediate_attention);

        return (
          <section key={p.id} className="space-y-8">
            <Reveal>
              <div className="space-y-2 border-b border-border pb-4">
                <h2 className="text-h2">{p.name}</h2>
                <p className="text-small text-muted-foreground tabular-nums">
                  {latestSigs.length} position{latestSigs.length === 1 ? "" : "s"}
                  {rb?.current_total_value && (
                    <> · {fmtMoney(rb.current_total_value)} total value</>
                  )}
                  {urgent.length > 0 && (
                    <>
                      {" · "}
                      <span className="text-destructive">
                        {urgent.length} requiring review
                      </span>
                    </>
                  )}
                </p>
              </div>
            </Reveal>

            {rb?.reasoning && (
              <Reveal>
                <p className="text-body text-muted-foreground leading-relaxed">
                  {rb.reasoning}
                </p>
              </Reveal>
            )}

            {rb?.top_3_priorities && rb.top_3_priorities.length > 0 && (
              <Reveal as="section" className="space-y-5">
                <h3 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
                  Top priorities
                </h3>
                <ol className="space-y-3">
                  {rb.top_3_priorities.map((pr, i) => (
                    <li key={i} className="grid grid-cols-[28px_1fr] gap-4">
                      <span className="text-h3 tabular-nums text-muted-foreground/60">
                        {i + 1}
                      </span>
                      <div className="space-y-1">
                        <p className="text-caption uppercase tracking-[0.14em]">
                          {(pr.action || "").replace(/_/g, " ")} ·{" "}
                          <span className="font-mono text-foreground">{pr.ticker}</span>
                        </p>
                        <p className="text-small text-muted-foreground">
                          {pr.reasoning}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              </Reveal>
            )}

            {rb?.concentration_alerts && rb.concentration_alerts.length > 0 && (
              <Reveal as="section" className="space-y-3">
                <h3 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
                  Concentration alerts
                </h3>
                <ul className="space-y-1.5 text-small text-muted-foreground">
                  {rb.concentration_alerts.map((c, i) => (
                    <li key={i}>
                      <span className="text-foreground">{c.severity}:</span>{" "}
                      {c.description}
                    </li>
                  ))}
                </ul>
              </Reveal>
            )}

            <Reveal as="section" className="space-y-5">
              <h3 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
                Holdings
              </h3>
              {latestSigs.length === 0 ? (
                <p className="text-small text-muted-foreground">
                  No position signals yet. Run{" "}
                  <code className="font-mono">pipeline/agents/position_manager.py</code>.
                </p>
              ) : (
                <ul className="divide-y divide-border border-y border-border">
                  {latestSigs
                    .slice()
                    .sort((a, b) => {
                      if (
                        a.requires_immediate_attention !==
                        b.requires_immediate_attention
                      ) {
                        return a.requires_immediate_attention ? -1 : 1;
                      }
                      return (
                        (b.signal_strength ?? 0) - (a.signal_strength ?? 0)
                      );
                    })
                    .map((s) => {
                      const status = statusForSignal(s);
                      const historyKey = `${s.portfolio_id}::${s.ticker}`;
                      const history =
                        historyByPortfolioTicker.get(historyKey) ?? [];
                      const ret = s.unrealized_return_pct ?? 0;
                      return (
                        <li key={s.id}>
                          <details className="group">
                            <summary className="grid cursor-pointer list-none grid-cols-[auto_1fr_auto] items-center gap-4 py-4 transition-premium hover:bg-secondary/40 px-2 -mx-2 rounded-md">
                              <span className="flex items-center gap-3">
                                <span
                                  aria-hidden
                                  className={`inline-block size-1.5 rounded-full ${status.dot}`}
                                />
                                <span className="font-mono text-[16px] font-semibold tracking-tight min-w-[3rem]">
                                  {s.ticker}
                                </span>
                              </span>
                              <span className="text-small text-muted-foreground">
                                <span className="text-foreground">
                                  {status.label}
                                </span>
                                {" · "}
                                {(s.signal_type || "").replace(/_/g, " ")}
                                {s.signal_strength
                                  ? ` · strength ${s.signal_strength}`
                                  : ""}
                              </span>
                              <span className="flex items-center gap-3 text-small tabular-nums">
                                <span
                                  className={
                                    ret >= 0
                                      ? "text-foreground"
                                      : "text-destructive"
                                  }
                                >
                                  {fmtPct(s.unrealized_return_pct)}
                                </span>
                                <span className="text-muted-foreground transition-transform group-open:rotate-90">
                                  ›
                                </span>
                              </span>
                            </summary>
                            <div className="space-y-5 px-2 pb-6">
                              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-small sm:grid-cols-4">
                                <Cell label="Cost basis" value={`$${Number(s.cost_basis ?? 0).toFixed(2)}`} />
                                <Cell label="Current" value={`$${Number(s.current_price ?? 0).toFixed(2)}`} />
                                <Cell label="Held" value={`${s.holding_period_days ?? "—"}d`} />
                                <Cell
                                  label="Action"
                                  value={
                                    (s.recommended_action || "—").replace(/_/g, " ") +
                                    (s.recommended_size_change_pct
                                      ? ` (${s.recommended_size_change_pct}%)`
                                      : "")
                                  }
                                />
                              </dl>
                              {s.reasoning && (
                                <p className="text-small text-muted-foreground leading-relaxed">
                                  {s.reasoning}
                                </p>
                              )}
                              {history.length > 1 && (
                                <div className="space-y-2">
                                  <p className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
                                    History · {history.length - 1} prior signal
                                    {history.length - 1 === 1 ? "" : "s"}
                                  </p>
                                  <ul className="space-y-1 text-caption tabular-nums">
                                    {history.slice(1, 6).map((h) => (
                                      <li key={h.id}>
                                        <span className="font-mono text-muted-foreground">
                                          {h.snapshot_date}
                                        </span>{" "}
                                        ·{" "}
                                        {(h.signal_type || "").replace(
                                          /_/g,
                                          " ",
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
                        </li>
                      );
                    })}
                </ul>
              )}
            </Reveal>
          </section>
        );
      })}
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </dt>
      <dd className="tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
