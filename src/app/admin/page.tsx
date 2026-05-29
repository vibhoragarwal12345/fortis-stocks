import Link from "next/link"

import { createServiceClient } from "@/lib/supabase/service"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { createTenant } from "./actions"

export const dynamic = "force-dynamic"

type TenantRow = {
  id: string
  name: string
  slug: string
  access_status: string
  access_granted_until: string | null
  notes: string | null
  created_at: string
}

export default async function AdminHome() {
  const service = createServiceClient()
  const { data: tenants } = await service
    .from("tenants")
    .select("id, name, slug, access_status, access_granted_until, notes, created_at")
    .order("created_at", { ascending: false })

  // Member counts in one query
  const { data: memberCounts } = await service
    .from("tenant_members")
    .select("tenant_id")
  const memberByTenant = new Map<string, number>()
  for (const r of memberCounts ?? []) {
    memberByTenant.set(r.tenant_id, (memberByTenant.get(r.tenant_id) ?? 0) + 1)
  }

  const rows = (tenants ?? []) as TenantRow[]

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tenants</h1>
        <p className="text-sm text-muted-foreground">
          Manage tenant access, feature flags, and member invites.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Create tenant</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={createTenant} className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="name">Tenant name</Label>
              <Input id="name" name="name" required placeholder="Acme Wealth" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="admin_email">Admin email</Label>
              <Input
                id="admin_email"
                name="admin_email"
                type="email"
                required
                placeholder="admin@acmewealth.com"
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" className="w-full">Create + invite admin</Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="rounded-lg border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2.5">Name</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Members</th>
              <th className="px-4 py-2.5">Granted until</th>
              <th className="px-4 py-2.5">Notes</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-t">
                <td className="px-4 py-2.5">
                  <Link
                    href={`/admin/tenants/${t.id}`}
                    className="font-medium text-foreground underline-offset-4 hover:underline"
                  >
                    {t.name}
                  </Link>
                  <div className="text-xs text-muted-foreground">{t.slug}</div>
                </td>
                <td className="px-4 py-2.5">
                  <StatusPill status={t.access_status} />
                </td>
                <td className="px-4 py-2.5">{memberByTenant.get(t.id) ?? 0}</td>
                <td className="px-4 py-2.5 text-muted-foreground">
                  {t.access_granted_until
                    ? new Date(t.access_granted_until).toLocaleDateString()
                    : "Indefinite"}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground truncate max-w-[280px]">
                  {t.notes ?? ""}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <Link
                    href={`/admin/tenants/${t.id}`}
                    className="text-xs font-medium text-foreground underline-offset-4 hover:underline"
                  >
                    Manage
                  </Link>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                  No tenants yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800",
    suspended: "bg-amber-100 text-amber-800",
    archived: "bg-zinc-200 text-zinc-700",
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        map[status] ?? "bg-zinc-100 text-zinc-700"
      }`}
    >
      {status}
    </span>
  )
}
