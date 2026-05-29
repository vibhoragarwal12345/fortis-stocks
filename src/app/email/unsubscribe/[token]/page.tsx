import { notFound } from "next/navigation"

import { Badge } from "@/components/ui/badge"
import { buttonVariants } from "@/components/ui/button"
import { createServiceClient } from "@/lib/supabase/service"
import { cn } from "@/lib/utils"

import { updatePrefsByToken } from "./actions"

export const metadata = { title: "Email preferences — Fortis" }
export const dynamic = "force-dynamic"

type Prefs = {
  user_id: string
  premarket_enabled: boolean
  midday_enabled: boolean
  close_enabled: boolean
  urgent_alerts_only: boolean
  unsubscribe_token: string
  bounced: boolean
}

export default async function UnsubscribePage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>
  searchParams: Promise<{ saved?: string }>
}) {
  const { token } = await params
  const sp = await searchParams

  const sb = createServiceClient()
  const { data: prefs } = await sb
    .from("email_preferences")
    .select("*")
    .eq("unsubscribe_token", token)
    .maybeSingle<Prefs>()

  if (!prefs) notFound()

  const allOff =
    !prefs.premarket_enabled &&
    !prefs.midday_enabled &&
    !prefs.close_enabled

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-lg rounded-xl bg-card p-8 ring-1 ring-foreground/10">
        <header className="mb-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
            Fortis · Email preferences
          </p>
          <h1 className="mt-2 font-heading text-2xl font-semibold tracking-tight">
            Manage your subscription
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose which Fortis briefs you would like to receive. Changes
            apply immediately.
          </p>
        </header>

        {sp.saved && (
          <div className="mb-5 rounded-md border border-emerald-300/40 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800/40 dark:bg-emerald-950/40 dark:text-emerald-200">
            Preferences saved.{" "}
            {allOff && "You are now unsubscribed from every Fortis brief."}
          </div>
        )}

        {prefs.bounced && (
          <div className="mb-5 rounded-md border border-red-300/40 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/40 dark:bg-red-950/40 dark:text-red-200">
            Recent sends to your address bounced — delivery is paused while we
            investigate. Saving below also re-enables delivery on this address.
          </div>
        )}

        <form action={updatePrefsByToken} className="space-y-4">
          <input type="hidden" name="token" value={token} />

          <Row
            name="premarket_enabled"
            label="Pre-Market Brief"
            sub="6:00am ET — setup, macro snapshot, today's A-grade picks"
            defaultChecked={prefs.premarket_enabled}
          />
          <Row
            name="midday_enabled"
            label="Midday Brief"
            sub="12:30pm ET — intraday focus list, portfolio alerts, pivots"
            defaultChecked={prefs.midday_enabled}
          />
          <Row
            name="close_enabled"
            label="Close Brief"
            sub="5:00pm ET — daily wrap, earnings takeaways, tomorrow's setup"
            defaultChecked={prefs.close_enabled}
          />

          <div className="border-t pt-4">
            <Row
              name="urgent_alerts_only"
              label="Urgent reviews only"
              sub="Only send briefs flagged URGENT REVIEW (critical portfolio alerts)"
              defaultChecked={prefs.urgent_alerts_only}
            />
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="submit"
              className={buttonVariants({ variant: "default", size: "sm" })}
            >
              Save preferences
            </button>
            <button
              type="submit"
              name="unsub_all"
              value="1"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Unsubscribe from all
            </button>
          </div>
        </form>

        <footer className="mt-8 border-t pt-4 text-xs text-muted-foreground">
          <p>
            This page uses a one-click capability token.{" "}
            <Badge variant="neutral">Keep this URL private</Badge> — anyone with
            it can change these preferences.
          </p>
        </footer>
      </div>
    </div>
  )
}

function Row({
  name,
  label,
  sub,
  defaultChecked,
}: {
  name: string
  label: string
  sub: string
  defaultChecked: boolean
}) {
  return (
    <label
      htmlFor={name}
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-md border bg-background px-3 py-3",
        "transition-colors hover:bg-muted/50"
      )}
    >
      <input
        id={name}
        name={name}
        type="checkbox"
        defaultChecked={defaultChecked}
        className="mt-1 size-4 accent-foreground"
      />
      <div className="flex-1">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{sub}</div>
      </div>
    </label>
  )
}
