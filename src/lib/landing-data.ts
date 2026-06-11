import { createServiceClient } from "@/lib/supabase/service"

// Live market data for the public landing page.
//
// The gateway shows real numbers from the latest completed scan — fake
// prices on an honesty-first product would be self-defeating. Reads run
// server-side with the service client (the page itself is public; RLS
// only opens these tables to authenticated users) and the page revalidates
// every 15 minutes, so the render stays at most 15 minutes behind the
// freshest scan. Every read degrades to null so the page can fall back to
// clearly-labeled illustrative data when no scan exists (fresh DB, CI).

export type LandingTapeItem = {
  ticker: string
  price: string
  change: number
}

export type LandingPreviewRow = {
  rank: number
  /** null when the row is masked (teaser) — identity never leaves the server. */
  ticker: string | null
  grade: "A" | "B" | "C" | null
  score: string
  price: string | null
  change: number | null
  /** Teaser rows: identity withheld, rendered blurred. */
  masked: boolean
}

export type LandingMarketData = {
  tape: LandingTapeItem[]
  preview: LandingPreviewRow[]
  /** Completion timestamp of the scan backing this data. */
  asOf: string
}

// Recognizable, ultra-liquid names for the tape. All are in the scan
// universe ($1M+ ADV floor), so the latest scan always carries them.
const TAPE_TICKERS = [
  "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META",
  "TSLA", "AVGO", "LLY", "JPM", "XOM", "UNH",
]

const fmtPrice = (n: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export async function getLandingMarketData(): Promise<LandingMarketData | null> {
  try {
    const supabase = createServiceClient()

    const { data: state } = await supabase
      .from("scan_state")
      .select("latest_scan_id, latest_scan_completed_at")
      .eq("id", 1)
      .maybeSingle()
    if (!state?.latest_scan_id || !state.latest_scan_completed_at) return null
    const scanId = state.latest_scan_id as number

    const [tapeRes, picksRes] = await Promise.all([
      supabase
        .from("scan_results")
        .select("ticker, price, day_change_pct")
        .eq("scan_id", scanId)
        .in("ticker", TAPE_TICKERS),
      supabase
        .from("ranked_focus_list")
        .select("ticker, rank, conviction_grade, composite_score")
        .eq("scan_id", scanId)
        .order("rank", { ascending: true })
        .limit(5),
    ])

    const tape: LandingTapeItem[] = (tapeRes.data ?? [])
      .filter((r) => r.price != null)
      .sort(
        (a, b) =>
          TAPE_TICKERS.indexOf(a.ticker) - TAPE_TICKERS.indexOf(b.ticker),
      )
      .map((r) => ({
        ticker: r.ticker,
        price: fmtPrice(Number(r.price)),
        change: Number(r.day_change_pct ?? 0),
      }))

    const picks = picksRes.data ?? []
    // Join Layer-1 price/change for the picked tickers.
    const pickTickers = picks.map((p) => p.ticker)
    const { data: pickMetrics } = pickTickers.length
      ? await supabase
          .from("scan_results")
          .select("ticker, price, day_change_pct")
          .eq("scan_id", scanId)
          .in("ticker", pickTickers)
      : { data: [] }
    const metricsByTicker = new Map(
      (pickMetrics ?? []).map((m) => [m.ticker, m]),
    )

    // Teaser policy: only the #1 pick is shown in full. Ranks 2–5 keep
    // grade + score (the shape of the product) but their identity — ticker,
    // price, day change — is withheld server-side, not just blurred, so
    // view-source reveals nothing.
    const preview: LandingPreviewRow[] = picks.map((p, i) => {
      const masked = i > 0
      const m = metricsByTicker.get(p.ticker)
      const grade = p.conviction_grade
      return {
        rank: p.rank ?? i + 1,
        ticker: masked ? null : p.ticker,
        grade: grade === "A" || grade === "B" || grade === "C" ? grade : null,
        score:
          p.composite_score != null ? Number(p.composite_score).toFixed(1) : "—",
        price: masked || m?.price == null ? null : fmtPrice(Number(m.price)),
        change:
          masked || m?.day_change_pct == null
            ? null
            : Number(m.day_change_pct),
        masked,
      }
    })

    // A scan with no tape and no picks is as good as no scan.
    if (tape.length === 0 && preview.length === 0) return null

    return { tape, preview, asOf: state.latest_scan_completed_at as string }
  } catch {
    // Missing env keys or an unreachable DB must never break the gateway.
    return null
  }
}

/** "09:21 ET · Jun 11" from a timestamptz, in market time. */
export function formatScanTime(iso: string): string {
  const d = new Date(iso)
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d)
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
  }).format(d)
  return `${time} ET · ${day}`
}
