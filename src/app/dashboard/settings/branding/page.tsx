import { redirect } from "next/navigation"

import { getActiveTenantMember } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { updateBranding } from "./actions"

export const metadata = { title: "Branding — Settings" }
export const dynamic = "force-dynamic"

export default async function BrandingPage() {
  const membership = await getActiveTenantMember()
  if (!membership) redirect("/login")
  const { tenant, role } = membership

  if (role !== "admin") {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <Card>
          <CardHeader>
            <CardTitle>Branding</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Only tenant admins can edit branding. Ask your tenant admin to make
            changes.
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Branding</h1>
        <p className="text-sm text-muted-foreground">
          Customize how your workspace and emails are presented.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Workspace</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={updateBranding} className="grid gap-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="name">Display name</Label>
                <Input id="name" name="name" defaultValue={tenant.name} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="primary_color">Primary color</Label>
                <div className="flex items-center gap-3">
                  <Input
                    id="primary_color"
                    name="primary_color"
                    type="color"
                    defaultValue={tenant.primary_color}
                    className="h-9 w-16 p-1"
                  />
                  <code className="text-xs text-muted-foreground">
                    {tenant.primary_color}
                  </code>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="email_from_name">Email &ldquo;From&rdquo; name</Label>
              <Input
                id="email_from_name"
                name="email_from_name"
                defaultValue={tenant.email_from_name ?? ""}
                placeholder={tenant.name}
              />
              <p className="text-xs text-muted-foreground">
                Outgoing emails are sent from this name, within the verified
                Resend domain.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="logo_url">Logo URL</Label>
              <Input
                id="logo_url"
                name="logo_url"
                type="url"
                defaultValue={tenant.logo_url ?? ""}
                placeholder="https://..."
              />
              {tenant.logo_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={tenant.logo_url}
                  alt={tenant.name}
                  className="mt-2 h-10 w-auto rounded border bg-card p-1"
                />
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="logo_file">Or upload logo (PNG/SVG, &lt; 2 MB)</Label>
              <Input
                id="logo_file"
                name="logo_file"
                type="file"
                accept="image/png,image/jpeg,image/svg+xml,image/webp"
              />
              <p className="text-xs text-muted-foreground">
                Uploading a file replaces the URL above.
              </p>
            </div>

            <Button type="submit" className="w-fit">
              Save branding
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
