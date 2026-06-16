import { createClient } from "@/lib/supabase/server"

// The displayed conviction board is a SHARP 15 (owner directive, June 2026):
// exactly the top 15 complete, fact-checked dossiers from the PROMOTED scan,
// ordered by rank. One constant + one scan-id source so the Today board and
// the Focus list can never drift (they previously used different limits,
// tables, and even different "latest scan" definitions).
//
// "Exactly 15" is reached ONLY by surfacing 15 fully-verified dossiers, never
// by padding or relaxing the fact-check bar. The promotion guard
// (SCAN_MIN_DISPLAY_DOSSIERS) only promotes a scan that fields >= BOARD_SIZE
// verified dossiers, so the board is a full 15 except on a cold start (no prior
// board), where it honestly shows however many are verified so far.
export const BOARD_SIZE = 15

// The promoted scan = scan_state.latest_scan_id -- NOT "latest completed scan",
// which can be a finished-but-thin scan the promotion guard refused to display.
// Keying off this matches the live ticker tape and the landing board.
export async function getPromotedScanId(
  supabase: Awaited<ReturnType<typeof createClient>>,
): Promise<number | null> {
  const { data } = await supabase
    .from("scan_state")
    .select("latest_scan_id")
    .eq("id", 1)
    .maybeSingle()
  return (data?.latest_scan_id as number | null) ?? null
}
