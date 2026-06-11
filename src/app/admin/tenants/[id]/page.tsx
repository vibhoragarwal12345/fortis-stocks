import Link from "next/link"
import { notFound } from "next/navigation"

import { createServiceClient } from "@/lib/supabase/service"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { createInvite, revokeInvite, updateTenant } from "../../actions"

export const dynamic = "force-dynamic"

type Tenant = {
  id: string
  name: string
  slug: string
  primary_color: string
  logo_url: string | null
  email_from_name: string | null
  access_status: "active" | "suspended" | "archived"
  access_granted_until: string | null
  feature_flags: Record<string, boolean | number>
  notes: string | null
}

type Member = {
  user_id: string
  role: "admin" | "member"
  invited_at: string
  accepted_at: string | null
}

type Invite = {
  id: number
  email: string
  role: "admin" | "member"
  token: string
  invited_at: string
  expires_at: string
  accepted_at: string | null
}

export default async function TenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  const service = createServiceClient()

  const { data: tenant } = await service
    .from("tenants")
    .select("*")
    .eq("id", id)
    .maybeSingle<Tenant>()
  if (!tenant) notFound()

  const { data: members } = await service
    .from("tenant_members")
    .select("user_id, role, invited_at, accepted_at")
    .eq("tenant_id", id)
    .order("invited_at", { ascending: true })

  const { data: invites } = await service
    .from("tenant_invites")
    .select("id, email, role, token, invited_at, expires_at, accepted_at")
    .eq("tenant_id", id)
    .order("invited_at", { ascending: false })

  const memberRows = (members ?? []) as Member[]
  const inviteRows = (invites ?? []) as Invite[]

  // Resolve member emails via auth.users (service role can read auth schema).
  // One getUserById per member in parallel beats listUsers({perPage:1000})
  // which was scanning every auth user on every render.
  const emailByUserId = new Map<string, string>()
  if (memberRows.length) {
    const lookups = await Promise.all(
      memberRows.map((m) => service.auth.admin.getUserById(m.user_id)),
    )
    for (const r of lookups) {
      const u = r.data?.user
      if (u?.id && u.email) emailByUserId.set(u.id, u.email)
    }
  }

  const grantedUntilInput = tenant.access_granted_until
    ? tenant.access_granted_until.slice(0, 10)
    : ""

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 space-y-8">
      <div className="space-y-1">
        <Link
          href="/admin"
          className="text-xs text-muted-foreground underline-offset-4 hover:underline"
        >
          ← All tenants
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{tenant.name}</h1>
        <p className="text-xs text-muted-foreground">{tenant.slug}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Access &amp; feature flags</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={updateTenant} className="grid gap-4">
            <input type="hidden" name="id" value={tenant.id} />
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="access_status">Access status</Label>
                <select
                  id="access_status"
                  name="access_status"
                  defaultValue={tenant.access_status}
                  className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                >
                  <option value="active">active</option>
                  <option value="suspended">suspended</option>
                  <option value="archived">archived</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="access_granted_until">Granted until (blank = indefinite)</Label>
                <Input
                  id="access_granted_until"
                  name="access_granted_until"
                  type="date"
                  defaultValue={grantedUntilInput}
                />
              </div>
              <div className="space-y-2 sm:col-span-1">
                <Label htmlFor="notes">Notes</Label>
                <Input
                  id="notes"
                  name="notes"
                  defaultValue={tenant.notes ?? ""}
                  placeholder="Internal notes"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="feature_flags">Feature flags (JSON)</Label>
              <textarea
                id="feature_flags"
                name="feature_flags"
                defaultValue={JSON.stringify(tenant.feature_flags, null, 2)}
                rows={10}
                className="w-full rounded-md border bg-background p-3 font-mono text-xs"
              />
            </div>
            <Button type="submit" className="w-fit">Save changes</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Members</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="py-2">Email</th>
                <th className="py-2">Role</th>
                <th className="py-2">Joined</th>
              </tr>
            </thead>
            <tbody>
              {memberRows.map((m) => (
                <tr key={m.user_id} className="border-t">
                  <td className="py-2">{emailByUserId.get(m.user_id) ?? m.user_id}</td>
                  <td className="py-2">{m.role}</td>
                  <td className="py-2 text-muted-foreground">
                    {m.accepted_at
                      ? new Date(m.accepted_at).toLocaleDateString()
                      : "pending"}
                  </td>
                </tr>
              ))}
              {memberRows.length === 0 && (
                <tr>
                  <td colSpan={3} className="py-4 text-center text-muted-foreground">
                    No members yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Invites</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <form action={createInvite} className="grid gap-4 sm:grid-cols-4">
            <input type="hidden" name="tenant_id" value={tenant.id} />
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="role">Role</Label>
              <select
                id="role"
                name="role"
                defaultValue="member"
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="member">member</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <div className="flex items-end">
              <Button type="submit" className="w-full">Send invite</Button>
            </div>
          </form>

          <div className="rounded-md border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Email</th>
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">Expires</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Invite link</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {inviteRows.map((i) => (
                  <tr key={i.id} className="border-t align-top">
                    <td className="px-3 py-2">{i.email}</td>
                    <td className="px-3 py-2">{i.role}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {new Date(i.expires_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2">
                      {i.accepted_at
                        ? <span className="text-foreground">accepted</span>
                        : new Date(i.expires_at).getTime() < Date.now()
                        ? <span className="text-muted-foreground">expired</span>
                        : <span className="text-muted-foreground">pending</span>}
                    </td>
                    <td className="px-3 py-2">
                      {i.accepted_at ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <code className="block max-w-[280px] truncate rounded bg-muted px-2 py-1 text-[11px]">
                          /signup?token={i.token}
                        </code>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {!i.accepted_at && (
                        <form action={revokeInvite}>
                          <input type="hidden" name="id" value={i.id} />
                          <input type="hidden" name="tenant_id" value={tenant.id} />
                          <button
                            type="submit"
                            className="text-xs text-destructive underline-offset-4 hover:underline"
                          >
                            Revoke
                          </button>
                        </form>
                      )}
                    </td>
                  </tr>
                ))}
                {inviteRows.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-center text-muted-foreground">
                      No invites yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
