import { Skeleton } from "@/components/ui/skeleton"

// Today page skeleton -- mirrors the new market-scan layout so hydration
// doesn't shift. Last-updated banner + shortlist grid.
export default function DashboardLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-12 md:space-y-16 md:px-10 md:py-14">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div className="space-y-3">
          <Skeleton className="h-3 w-44" />
          <Skeleton className="h-10 w-80 md:h-12" />
        </div>
        <Skeleton className="h-20 w-72 rounded-md" />
      </div>

      <section className="space-y-6">
        <Skeleton className="h-3 w-56" />
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full rounded-xl" />
          ))}
        </div>
      </section>
    </div>
  )
}
