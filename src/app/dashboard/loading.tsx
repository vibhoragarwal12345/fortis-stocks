import { Skeleton } from "@/components/ui/skeleton"

// Today page skeleton -- mirrors the ACTUAL Today layout (header + "the wire"
// 4-cell strip + the shortlist table) so the loading->content swap doesn't
// reflow. Must track src/app/dashboard/page.tsx.
export default function DashboardLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-12 md:space-y-16 md:px-10 md:py-14">
      {/* Header: eyebrow + title + last-updated banner */}
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div className="space-y-3">
          <Skeleton className="h-3 w-44" />
          <Skeleton className="h-10 w-80 md:h-12" />
        </div>
        <Skeleton className="h-20 w-72 rounded-md" />
      </div>

      {/* The wire: eyebrow + lead line + 4-cell stat strip */}
      <section className="space-y-6">
        <Skeleton className="h-3 w-56" />
        <div className="space-y-2.5">
          <Skeleton className="h-6 w-full max-w-[760px]" />
          <Skeleton className="h-6 w-2/3 max-w-[520px]" />
        </div>
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-2 bg-card px-5 py-4">
              <Skeleton className="h-6 w-20" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
      </section>

      {/* The shortlist: eyebrow + table (header row + body rows) */}
      <section className="space-y-6">
        <Skeleton className="h-3 w-48" />
        <div className="space-y-3">
          <Skeleton className="h-9 w-full rounded-md" />
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full rounded-md" />
          ))}
        </div>
      </section>
    </div>
  )
}
