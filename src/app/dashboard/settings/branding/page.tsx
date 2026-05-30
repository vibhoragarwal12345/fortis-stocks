import { redirect } from "next/navigation"

import { getActiveTenantMember } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Reveal } from "@/components/ui/reveal"
import { updateBranding } from "./actions"

export const metadata = { title: "Branding — Settings" }
export const dynamic = "force-dynamic"

export default async function BrandingPage() {
  const membership = await getActiveTenantMember()
  if (!membership) redirect("/login")
  const { tenant, role } = membership

  if (role !== "admin") {
    return (
      <div className="mx-auto max-w-[720px] px-6 py-14 md:px-10 md:py-16">
        <header className="space-y-3">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Branding
          </p>
          <h1 className="text-h1">Admin only.</h1>
        </header>
        <p className="mt-8 text-small text-muted-foreground">
          Only tenant admins can edit branding. Ask your tenant admin to make
          changes.
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[720px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Settings · Branding
        </p>
        <h1 className="text-h1">Workspace identity.</h1>
        <p className="text-body-lg max-w-[560px] text-muted-foreground">
          Customize how your workspace and outgoing emails are presented.
        </p>
      </Reveal>

      <Reveal>
        <form action={updateBranding} className="space-y-10">
          <Field>
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              name="name"
              defaultValue={tenant.name}
              required
            />
          </Field>

          <Field>
            <Label htmlFor="primary_color">Primary color</Label>
            <div className="flex items-center gap-3">
              <Input
                id="primary_color"
                name="primary_color"
                type="color"
                defaultValue={tenant.primary_color}
                className="h-11 w-16 cursor-pointer p-1"
              />
              <code className="font-mono text-caption">
                {tenant.primary_color}
              </code>
            </div>
          </Field>

          <Field hint="Outgoing emails are sent from this name, within the verified Resend domain.">
            <Label htmlFor="email_from_name">Email &ldquo;From&rdquo; name</Label>
            <Input
              id="email_from_name"
              name="email_from_name"
              defaultValue={tenant.email_from_name ?? ""}
              placeholder={tenant.name}
            />
          </Field>

          <Field>
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
                className="mt-3 h-10 w-auto rounded border border-border bg-card p-1"
              />
            ) : null}
          </Field>

          <Field hint="Uploading a file replaces the URL above.">
            <Label htmlFor="logo_file">
              Or upload logo (PNG / SVG, &lt; 2 MB)
            </Label>
            <Input
              id="logo_file"
              name="logo_file"
              type="file"
              accept="image/png,image/jpeg,image/svg+xml,image/webp"
              className="cursor-pointer"
            />
          </Field>

          <div className="border-t border-border pt-6">
            <Button type="submit" size="lg" className="w-fit">
              Save branding
            </Button>
          </div>
        </form>
      </Reveal>
    </div>
  )
}

function Field({
  children,
  hint,
}: {
  children: React.ReactNode
  hint?: string
}) {
  return (
    <div className="space-y-2">
      {children}
      {hint && <p className="text-caption">{hint}</p>}
    </div>
  )
}
