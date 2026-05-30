import Link from "next/link"

import { createServiceClient } from "@/lib/supabase/service"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Reveal } from "@/components/ui/reveal"
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
    .select(
      "id, name, slug, access_status, access_granted_until, notes, created_at",
    )
    .order("created_at", { ascending: false })

  const { data: memberCounts } = await service
    .from("tenant_members")
    .select("tenant_id")
  const memberByTenant = new Map<string, number>()
  for (const r of memberCounts ?? []) {
    memberByTenant.set(r.tenant_id, (memberByTenant.get(r.tenant_id) ?? 0) + 1)
  }

  const rows = (tenants ?? []) as TenantRow[]

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Platform admin
        </p>
        <h1 className="text-h1">Tenants.</h1>
        <p className="text-body-lg max-w-[640px] text-muted-foreground">
          Manage tenant access, feature flags, and member invites.
        </p>
      </Reveal>

      <Reveal as="section" className="space-y-5">
        <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Create tenant
        </h2>
        <form
          action={createTenant}
          className="grid gap-4 border-y border-border py-6 sm:grid-cols-[1fr_1fr_auto]"
        >
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
            <Button type="submit" size="lg" className="w-full sm:w-auto">
              Create + invite
            </Button>
          </div>
        </form>
      </Reveal>

      <Reveal as="section" className="space-y-5">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            All tenants
          </h2>
          <span className="text-caption tabular-nums">
            {rows.length} {rows.length === 1 ? "tenant" : "tenants"}
          </span>
        </div>

        {rows.length === 0 ? (
          <p className="border-y border-border py-12 text-center text-small text-muted-foreground">
            No tenants yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-small">
              <thead>
                <tr className="text-left">
                  <TH>Name</TH>
                  <TH>Status</TH>
                  <TH>Members</TH>
                  <TH>Granted until</TH>
                  <TH>Notes</TH>
                  <TH>{""}</TH>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr
                    key={t.id}
                    className="border-t border-border transition-premium hover:bg-secondary/40"
                  >
                    <TD>
                      <Link
                        href={`/admin/tenants/${t.id}`}
                        className="block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                      >
                        <span className="font-medium text-foreground">
                          {t.name}
                        </span>
                        <span className="ml-2 text-caption text-muted-foreground">
                          {t.slug}
                        </span>
                      </Link>
                    </TD>
                    <TD>
                      <span className="text-caption uppercase tracking-[0.14em]">
                        {t.access_status}
                      </span>
                    </TD>
                    <TD className="tabular-nums">
                      {memberByTenant.get(t.id) ?? 0}
                    </TD>
                    <TD className="tabular-nums text-muted-foreground">
                      {t.access_granted_until
                        ? new Date(t.access_granted_until).toLocaleDateString()
                        : "Indefinite"}
                    </TD>
                    <TD className="max-w-[320px] truncate text-muted-foreground">
                      {t.notes ?? ""}
                    </TD>
                    <TD className="text-right">
                      <Link
                        href={`/admin/tenants/${t.id}`}
                        className="text-caption uppercase tracking-[0.14em] transition-premium hover:text-foreground"
                      >
                        Manage →
                      </Link>
                    </TD>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Reveal>
    </div>
  )
}

function TH({ children }: { children: React.ReactNode }) {
  return (
    <th className="py-3 pr-6 text-caption uppercase tracking-[0.14em] font-medium text-muted-foreground">
      {children}
    </th>
  )
}

function TD({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return <td className={`py-3 pr-6 ${className ?? ""}`}>{children}</td>
}
