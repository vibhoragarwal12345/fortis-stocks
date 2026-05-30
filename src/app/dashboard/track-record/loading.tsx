import { Skeleton } from "@/components/ui/skeleton"

export default function TrackRecordLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-10 w-[560px] max-w-full md:h-12" />
        <Skeleton className="h-5 w-[480px] max-w-full" />
      </div>

      <section className="space-y-6">
        <Skeleton className="h-3 w-24" />
        <div className="grid gap-10 sm:grid-cols-2 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-8 w-24" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-6">
        <Skeleton className="h-3 w-64" />
        <Skeleton className="h-48 w-full rounded-md" />
      </section>

      <section className="space-y-6">
        <Skeleton className="h-3 w-56" />
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </section>
    </div>
  )
}
