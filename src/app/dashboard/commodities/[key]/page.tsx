import Link from "next/link"
import { notFound } from "next/navigation"

import {
  CATEGORY_LABEL,
  CURVE_LABEL,
  RESEARCH_BANNER,
  SD_VARIANT,
  fmtPrice,
  getLatestCommodityScan,
  type CommodityEntry,
  type NarrativeRead,
  type ReferenceBand,
} from "@/lib/commodities"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const dynamic = "force-dynamic"

// Strip the [DATA REF: key] citations for display — they are the audit trail
// (kept verbatim in the stored payload), not reading copy.
function displayNarrative(text: string): string {
  return text.replace(/\s*\[DATA REF:[^\]]*\]/g, "").replace(/\s{2,}/g, " ").trim()
}

function NarrativeBlock({ read }: { read: NarrativeRead }) {
  if (read?.status === "OK" && read.narrative) {
    return (
      <div className="space-y-4">
        {displayNarrative(read.narrative)
          .split(/\n\n+/)
          .map((p, i) => (
            <p key={i} className="text-small leading-relaxed text-foreground/90">
              {p}
            </p>
          ))}
        {read.factcheck && (
          <p className="text-caption text-muted-foreground">
            Fact-checked: {Math.round(read.factcheck.score * 100)}% of claims
            traced to fetched data · audited by independent critic
          </p>
        )}
      </div>
    )
  }
  return (
    <p className="text-small text-muted-foreground">
      No narrative published for this horizon —{" "}
      {read?.note ??
        "the generated text did not pass verification and was withheld rather than shown."}
    </p>
  )
}

function BandRow({ band, horizon }: { band?: ReferenceBand; horizon: string }) {
  if (!band) return null
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <p className="text-eyebrow">{horizon} reference band</p>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "Bear P5", v: band.bear_p5 },
          { label: "Median P50", v: band.normal_p50 },
          { label: "Bull P95", v: band.bull_p95 },
        ].map((b) => (
          <div key={b.label} className="rounded-lg border border-border bg-card/50 px-2 py-2.5">
            <p className="text-caption text-muted-foreground">{b.label}</p>
            <p className="text-data text-[15px]">{fmtPrice(b.v)}</p>
          </div>
        ))}
      </div>
      <p className="text-caption text-muted-foreground">
        Zero-drift volatility cone — statistical reference levels, not
        forecasts or targets.
      </p>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | null }) {
  if (value == null) return null
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5 last:border-0">
      <span className="text-caption text-muted-foreground">{label}</span>
      <span className="text-data text-[13px] text-right">{value}</span>
    </div>
  )
}

export default async function CommodityDetailPage({
  params,
}: {
  params: Promise<{ key: string }>
}) {
  const { key } = await params
  const scan = await getLatestCommodityScan()
  const e: CommodityEntry | undefined = scan?.payload.commodities[key]
  if (!scan || !e) notFound()

  const tech = e.layers.technicals ?? {}
  const sd = e.layers.supply_demand ?? {}
  const curve = e.layers.curve ?? {}
  const pos = e.layers.positioning ?? {}
  const season = e.layers.seasonality?.current_month ?? {}
  const bands = tech.reference_bands ?? {}

  const fmtTime = new Date(scan.scan_time).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })

  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-14 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-5">
        <div className="space-y-3">
          <p className="text-eyebrow">
            <Link href="/dashboard/commodities" className="hover:text-foreground">
              Commodities
            </Link>{" "}
            · {CATEGORY_LABEL[e.category]} · scan {fmtTime}
          </p>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
            <h1 className="text-h1">{e.name}</h1>
            <span className="text-data text-[28px] font-semibold">
              {fmtPrice(e.price?.close)}
            </span>
            <span className="text-caption text-muted-foreground">
              as of {e.price?.asof ?? "—"} · delayed data
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant={SD_VARIANT[sd.classification ?? "unknown"] ?? "outline"}>
              {sd.classification === "unknown" || !sd.classification
                ? "S/D unverified"
                : `Supply/demand: ${sd.classification}`}
            </Badge>
            {curve.structure && curve.structure !== "unknown" && (
              <Badge variant="neutral">{CURVE_LABEL[curve.structure] ?? curve.structure}</Badge>
            )}
            {pos.spec_stance && (
              <Badge variant="neutral">
                Specs {pos.spec_stance.replace(/_/g, " ")}
              </Badge>
            )}
            {pos.crowding && pos.crowding !== "not_extreme" && (
              <Badge variant="warning">{pos.crowding.replace(/_/g, " ")}</Badge>
            )}
          </div>
        </div>
        <div className="max-w-[820px] rounded-lg border border-warning/25 bg-warning/5 px-4 py-3">
          <p className="text-caption text-muted-foreground">{RESEARCH_BANNER}</p>
        </div>
      </Reveal>

      {/* ── The dual-horizon split: two reads, two audiences, never mixed ── */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Reveal>
          <Card className="h-full border-highlight/20">
            <CardHeader>
              <p className="text-eyebrow text-highlight">Short-term · tactical</p>
              <CardTitle className="text-[15px] font-medium text-muted-foreground">
                Days-to-weeks horizon, written for traders
              </CardTitle>
              <p className="text-caption text-warning/90">
                Not an investment thesis — acting on this read with long-horizon
                capital is the overtrading failure this split exists to prevent.
              </p>
            </CardHeader>
            <CardContent className="space-y-6">
              <NarrativeBlock read={e.tactical.narrative} />
              <BandRow band={bands.tactical_21d} horizon="21-day" />
              <div>
                <p className="text-eyebrow mb-1.5">Tactical levels</p>
                <Fact
                  label="RSI (14)"
                  value={tech.momentum?.rsi_14 != null ? String(tech.momentum.rsi_14) : null}
                />
                <Fact
                  label="vs 50-day average"
                  value={
                    tech.trend?.above_sma50 == null
                      ? null
                      : tech.trend.above_sma50
                        ? "above"
                        : "below"
                  }
                />
                <Fact
                  label="Nearest support"
                  value={
                    tech.support_resistance?.nearest_support != null
                      ? fmtPrice(tech.support_resistance.nearest_support)
                      : null
                  }
                />
                <Fact
                  label="Nearest resistance"
                  value={
                    tech.support_resistance?.nearest_resistance != null
                      ? fmtPrice(tech.support_resistance.nearest_resistance)
                      : null
                  }
                />
                <Fact
                  label="Seasonal tendency (this month)"
                  value={
                    season.tendency
                      ? `${season.tendency.replace(/_/g, " ")}${
                          season.avg_return_pct != null
                            ? ` · avg ${season.avg_return_pct > 0 ? "+" : ""}${season.avg_return_pct}%`
                            : ""
                        }`
                      : null
                  }
                />
                <Fact
                  label="COT positioning"
                  value={
                    pos.spec_stance
                      ? `${pos.spec_stance.replace(/_/g, " ")}${
                          pos.spec_net_percentile_3y != null
                            ? ` · ${Math.round(pos.spec_net_percentile_3y)}th pctile (3y)`
                            : ""
                        }`
                      : null
                  }
                />
              </div>
            </CardContent>
          </Card>
        </Reveal>

        <Reveal delay={100}>
          <Card className="h-full">
            <CardHeader>
              <p className="text-eyebrow">Structural · long-term</p>
              <CardTitle className="text-[15px] font-medium text-muted-foreground">
                Multi-quarter horizon, written for investors
              </CardTitle>
              <p className="text-caption text-warning/90">
                Carries no entry/exit timing information — do not trade off this
                read.
              </p>
            </CardHeader>
            <CardContent className="space-y-6">
              <NarrativeBlock read={e.structural.narrative} />
              <BandRow band={bands.structural_252d} horizon="1-year" />
              <div>
                <p className="text-eyebrow mb-1.5">Structural state</p>
                <Fact
                  label="Supply/demand balance"
                  value={sd.classification ?? null}
                />
                <Fact label="Assessment confidence" value={sd.confidence ?? null} />
                <Fact
                  label="Futures curve"
                  value={
                    curve.structure && curve.structure !== "unknown"
                      ? `${CURVE_LABEL[curve.structure] ?? curve.structure}${
                          curve.slope?.annualized_slope_pct != null
                            ? ` · ${curve.slope.annualized_slope_pct > 0 ? "+" : ""}${curve.slope.annualized_slope_pct}%/yr slope`
                            : ""
                        }`
                      : null
                  }
                />
                <Fact
                  label="vs 200-day average"
                  value={
                    tech.trend?.above_sma200 == null
                      ? null
                      : tech.trend.above_sma200
                        ? "above"
                        : "below"
                  }
                />
                <Fact
                  label="52-week range position"
                  value={
                    tech.range_52w?.position_pct != null
                      ? `${tech.range_52w.position_pct}%`
                      : null
                  }
                />
                <Fact
                  label="Macro backdrop"
                  value={e.layers.macro?.net_macro_read ?? null}
                />
              </div>
              {sd.note && (
                <p className="text-caption text-muted-foreground">
                  Data caveat: {sd.note}
                </p>
              )}
            </CardContent>
          </Card>
        </Reveal>
      </div>

      {tech.status === "NO_DAILY_DATA" && (
        <Reveal>
          <Card>
            <CardContent className="py-6">
              <p className="text-small text-muted-foreground">{tech.note}</p>
            </CardContent>
          </Card>
        </Reveal>
      )}
    </div>
  )
}
