import { Skeleton } from "@/components/ui/skeleton"

// Today page skeleton -- mirrors the real layout so hydration doesn't shift.
export default function DashboardLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-44" />
        <Skeleton className="h-10 w-72 md:h-12 md:w-96" />
      </div>

      <section className="space-y-6">
        <Skeleton className="h-3 w-32" />
        <div className="grid gap-5 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-44 w-full rounded-xl" />
          ))}
        </div>
      </section>

      <section className="space-y-6">
        <Skeleton className="h-3 w-36" />
        <div className="grid gap-5 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-48 w-full rounded-xl" />
          ))}
        </div>
      </section>

      <section className="space-y-6">
        <Skeleton className="h-3 w-40" />
        <div className="grid gap-5 sm:grid-cols-2">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-32 w-full rounded-xl" />
          ))}
        </div>
      </section>
    </div>
  )
}
