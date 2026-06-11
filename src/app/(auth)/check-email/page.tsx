import Link from "next/link"

export const metadata = { title: "Check your email — Fortis" }

export default function CheckEmailPage() {
  return (
    <div className="space-y-8 text-center">
      <div className="mx-auto flex size-12 items-center justify-center rounded-full border border-border bg-card text-foreground">
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
          <polyline points="22,6 12,13 2,6" />
        </svg>
      </div>
      <div className="space-y-3">
        <h1 className="text-h2">Check your email</h1>
        <p className="text-small text-muted-foreground">
          We sent a confirmation link. Click it to activate your account,
          then come back to sign in.
        </p>
      </div>
      <div className="pt-2">
        <Link
          href="/login"
          className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-background px-4 text-[14px] font-medium text-foreground transition-premium hover:bg-secondary"
        >
          Back to sign in
        </Link>
      </div>
    </div>
  )
}
