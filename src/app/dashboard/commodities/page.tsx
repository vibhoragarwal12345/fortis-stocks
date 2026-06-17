import Link from "next/link"

import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CURVE_LABEL,
  RESEARCH_BANNER,
  SD_VARIANT,
  fmtPrice,
  getLatestCommodityScan,
  type CommodityEntry,
} from "@/lib/commodities"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Commodities — Christopher Edwards Financial Associates" }
export const dynamic = "force-dynamic"

function positioningLine(e: CommodityEntry): string | null {
  const p = e.layers.positioning
  if (!p?.spec_stance) return null
  const pct =
    p.spec_net_percentile_3y != null
      ? ` · ${Math.round(p.spec_net_percentile_3y)}th pctile (3y)`
      : ""
  const crowd = p.crowding && p.crowding !== "not_extreme" ? ` · ${p.crowding.replace(/_/g, " ")}` : ""
  return `Specs ${p.spec_stance.replace(/_/g, " ")}${pct}${crowd}`
}

export default async function CommoditiesPage() {
  const scan = await getLatestCommodityScan()

  if (!scan) {
    return (
      <div className="mx-auto max-w-[1280px] px-6 py-14 md:px-10 md:py-16">
        <header className="space-y-2">
          <p className="text-eyebrow">Commodities</p>
          <h1 className="text-h1">No commodity scan yet.</h1>
        </header>
        <Card className="mt-12">
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            Commodity reads appear here after the first scan completes (once
            per trading day, post-close).
          </CardContent>
        </Card>
      </div>
    )
  }

  const entries = Object.entries(scan.payload.commodities)
  const fmtTime = new Date(scan.scan_time).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-5">
        <div className="space-y-3">
          <p className="text-eyebrow">
            Commodities · scan {fmtTime} · delayed data
          </p>
          <h1 className="text-h1">{entries.length} commodities, dual-horizon reads.</h1>
          <p className="text-body-lg max-w-[680px] text-muted-foreground">
            Supply/demand balance, futures-curve structure, trader positioning,
            and a tactical-versus-structural narrative for every commodity —
            every figure traced to a live fetch or labeled as a gap.
          </p>
        </div>
        <div className="max-w-[820px] rounded-lg border border-warning/25 bg-warning/5 px-4 py-3">
          <p className="text-caption text-muted-foreground">{RESEARCH_BANNER}</p>
        </div>
      </Reveal>

      {CATEGORY_ORDER.map((cat) => {
        const group = entries.filter(([, e]) => e.category === cat)
        if (group.length === 0) return null
        return (
          <section key={cat} className="space-y-6">
            <Reveal>
              <div className="flex items-baseline justify-between gap-4">
                <h2 className="text-eyebrow">{CATEGORY_LABEL[cat]}</h2>
                <span className="text-caption text-data">
                  {group.length} {group.length === 1 ? "market" : "markets"}
                </span>
              </div>
            </Reveal>
            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {group.map(([key, e], idx) => {
                const sd = e.layers.supply_demand?.classification ?? "unknown"
                const curve = e.layers.curve?.structure
                const pos = positioningLine(e)
                const tacticalOk = e.tactical.narrative?.status === "OK"
                const structuralOk = e.structural.narrative?.status === "OK"
                return (
                  <Reveal key={key} delay={Math.min(idx, 5) * 60}>
                    <Link
                      href={`/dashboard/commodities/${key}`}
                      className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                    >
                      <Card
                        size="sm"
                        className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                      >
                        <CardHeader>
                          <CardTitle className="flex items-center justify-between gap-2">
                            <span className="text-[16px] font-semibold">{e.name}</span>
                            <span className="text-data text-[16px]">
                              {fmtPrice(e.price?.close)}
                            </span>
                          </CardTitle>
                          <p className="text-caption text-data">
                            as of {e.price?.asof ?? "—"} · delayed
                          </p>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <div className="flex flex-wrap gap-1.5">
                            {sd !== "unknown" && (
                              <Badge variant={SD_VARIANT[sd] ?? "outline"}>
                                {sd}
                              </Badge>
                            )}
                            {curve && curve !== "unknown" && (
                              <Badge variant="neutral">
                                {CURVE_LABEL[curve] ?? curve}
                              </Badge>
                            )}
                          </div>
                          <p className="text-small text-muted-foreground">
                            {pos ?? "No positioning data for this market."}
                          </p>
                          <p className="text-caption text-muted-foreground">
                            Reads: tactical {tacticalOk ? "published" : "withheld"} ·
                            structural {structuralOk ? "published" : "withheld"}
                          </p>
                        </CardContent>
                      </Card>
                    </Link>
                  </Reveal>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
