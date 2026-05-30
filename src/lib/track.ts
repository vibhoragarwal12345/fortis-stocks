/**
 * Step 7.5 -- tracking helpers.
 *
 * The canonical event-type list lives here as a TypeScript union so callers
 * get autocomplete + compile-time checks. The DB column is plain `text` so
 * a new event type does not require a migration -- add it to the union and
 * ship the caller.
 */

import "server-only"

import { createClient } from "@/lib/supabase/server"

export type EventType =
  // Page opens
  | "dashboard_today_opened"
  | "track_record_opened"
  // Report viewer
  | "report_opened"
  | "pick_clicked"
  | "thesis_expanded"
  | "quant_expanded"
  | "smart_money_expanded"
  | "peer_expanded"
  // Ad-hoc research
  | "ticker_searched"
  // Explicit feedback
  | "pick_marked_useful"
  | "pick_marked_unhelpful"
  // Watchlist
  | "sub_added"
  | "unsub_added"

export type TrackPayload = Record<string, unknown>

/**
 * Server-component helper. Call from any async server component to log a
 * page-view event. Failures are swallowed -- tracking must never break a
 * dashboard render. The UNIQUE constraint on (user_id, event_type,
 * 5-second bucket, data hash) makes repeat calls idempotent.
 */
export async function trackEvent(
  type: EventType,
  data: TrackPayload = {}
): Promise<void> {
  try {
    const sb = await createClient()
    const {
      data: { user },
    } = await sb.auth.getUser()
    if (!user) return
    await sb
      .from("dashboard_events")
      .insert({
        user_id: user.id,
        event_type: type,
        event_data: data,
      })
      // The RLS policy + the UNIQUE constraint together mean a duplicate
      // landing inside the 5s bucket comes back as code 23505 (unique
      // violation). We don't care -- it's the dedup working as designed.
  } catch {
    // tracking is fire-and-forget; never block the render
  }
}
