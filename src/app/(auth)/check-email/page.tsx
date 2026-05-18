import Link from "next/link"
import { buttonVariants } from "@/components/ui/button"

export const metadata = { title: "Check your email — Fortis" }

export default function CheckEmailPage() {
  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-2xl">
        ✉️
      </div>
      <div className="space-y-2">
        <h1 className="text-2xl font-bold">Check your email</h1>
        <p className="text-sm text-muted-foreground">
          We sent a confirmation link to your email address. Click the link to
          activate your account, then sign in.
        </p>
      </div>
      <Link href="/login" className={buttonVariants({ variant: "outline" })}>
        Back to sign in
      </Link>
    </div>
  )
}
