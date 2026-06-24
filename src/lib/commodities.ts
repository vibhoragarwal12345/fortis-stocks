import { createClient } from "@/lib/supabase/server"

// ── Types mirroring pipeline/commodities/analyze.py output ──────────────────
// Only the fields the dashboard renders; the payload carries more.

export type NarrativeRead = {
  status: string // OK | DISCARDED_* | LLM_UNAVAILABLE | NO_DATA | SKIPPED
  narrative: string | null
  note?: string
  factcheck?: { score: number; grade: string; claims: number }
  critic?: { verdict: string; issues?: string[] }
}

export type HorizonBlock = {
  audience_warning: string
  data: Record<string, unknown>
  narrative: NarrativeRead
  // Set on the structural block when it was carried forward from the last
  // weekly full run (daily tactical scans reuse it). ISO timestamp.
  as_of?: string
}

export type ReferenceBand = {
  bear_p5: number
  normal_p50: number
  bull_p95: number
  label: string
}

export type CommodityEntry = {
  name: string
  category: "energy" | "metals" | "ags"
  price: { close: number | null; asof: string; verification: string } | null
  data_delay_note: string
  tactical: HorizonBlock
  structural: HorizonBlock
  layers: {
    technicals?: {
      status?: string
      note?: string
      trend?: { above_sma50: boolean | null; above_sma200: boolean | null }
      momentum?: { rsi_14: number | null }
      range_52w?: { high: number | null; low: number | null; position_pct: number | null }
      support_resistance?: { nearest_support: number | null; nearest_resistance: number | null }
      reference_bands?: {
        tactical_1w?: ReferenceBand
        structural_252d?: ReferenceBand
        label?: string
      }
    }
    supply_demand?: { classification?: string; confidence?: string; note?: string }
    curve?: { structure?: string; slope?: { annualized_slope_pct?: number | null } }
    positioning?: {
      spec_stance?: string
      crowding?: string
      spec_net_percentile_3y?: number | null
      report_date?: string
    }
    seasonality?: {
      current_month?: { tendency?: string; avg_return_pct?: number | null; hit_rate_pct?: number | null }
    }
    macro?: { net_macro_read?: string }
  }
}

export type CommodityScan = {
  id: number
  scan_time: string
  status: string
  payload: {
    generated_at: string
    disclaimer: string
    commodities: Record<string, CommodityEntry>
  }
}

export async function getLatestCommodityScan(): Promise<CommodityScan | null> {
  const supabase = await createClient()
  const { data } = await supabase
    .from("commodity_scans")
    .select("id, scan_time, status, payload")
    .neq("status", "failed")
    .order("scan_time", { ascending: false })
    .limit(1)
    .maybeSingle()
  return (data as CommodityScan | null) ?? null
}

// ── Display maps ─────────────────────────────────────────────────────────────

export const CATEGORY_LABEL: Record<string, string> = {
  energy: "Energy",
  metals: "Metals",
  ags: "Agriculture",
}
export const CATEGORY_ORDER = ["energy", "metals", "ags"] as const

// Deliberately non-P&L colors: a supply state is information, not a verdict.
export const SD_VARIANT: Record<string, "warning" | "neutral" | "info" | "outline"> = {
  tightening: "warning", // supply stress
  balanced: "neutral",
  loosening: "info", // surplus building
  unknown: "outline",
}

export const CURVE_LABEL: Record<string, string> = {
  backwardation: "Backwardation",
  contango: "Contango",
  flat: "Flat curve",
  unknown: "Curve n/a",
}

export const RESEARCH_BANNER =
  "Research intelligence, not investment advice. Commodities carry higher " +
  "risk than equities: leverage, volatility, geopolitical shocks. Data is " +
  "delayed and partially incomplete; gaps are labeled, never filled. Reference " +
  "levels are statistical, not forecasts. A qualified human must review any " +
  "decision made downstream of this research."

export function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—"
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: v < 10 ? 4 : 2,
  })
}
