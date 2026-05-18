# Fortis Stock Intelligence — CLAUDE.md

## Project Purpose
Stock intelligence SaaS for **The Fortis Agency**, a financial advisory firm. The platform provides institutional-grade stock research, screening, and analytics tools for financial advisors and their clients.

## Tech Stack
- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS + shadcn/ui (neutral palette)
- **Database / Auth**: Supabase (PostgreSQL + Row Level Security)
- **Package Manager**: npm

## Folder Structure
```
src/
├── app/                    # App Router pages and API routes
│   ├── (auth)/             # Auth route group — login, signup, password reset
│   ├── (dashboard)/        # Protected dashboard route group
│   └── api/                # API route handlers
├── components/
│   ├── ui/                 # shadcn/ui primitives (auto-generated, do not edit manually)
│   └── ...                 # Feature-level components
├── lib/
│   ├── supabase/           # Supabase client helpers (server.ts, client.ts, middleware.ts)
│   └── utils.ts            # Shared utilities (cn helper, etc.)
└── types/                  # Shared TypeScript type definitions
```

## Conventions
- **Server Components by default.** Only add `"use client"` at the top of a file when you need interactivity, browser APIs, or React hooks (`useState`, `useEffect`, etc.).
- **Route groups** (`(auth)`, `(dashboard)`) each have their own `layout.tsx` for shared UI (e.g., sidebar, nav).
- **Supabase clients**: use SSR-aware helpers from `@/lib/supabase/`:
  - `createServerClient()` — for Server Components, Server Actions, and Route Handlers
  - `createBrowserClient()` — for Client Components only
- **Environment variables**: all secrets live in `.env.local` (never committed). See `.env.local.example` for required keys.
- **Components**: shadcn/ui primitives go in `src/components/ui/`. Feature components go directly in `src/components/`.
- **Imports**: use the `@/` alias (maps to `src/`).

## Database Tables
_To be documented as tables are added._

| Table | Description |
|-------|-------------|
| _(none yet)_ | |

## Environment Variables
See `.env.local.example` for all required variables.

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/public key (safe for browser) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (server-side only — never expose to client) |
