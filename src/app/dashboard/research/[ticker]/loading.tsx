import { Skeleton } from "@/components/ui/skeleton"

export default function ResearchLoading() {
  return (
    <div className="mx-auto max-w-[1120px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-14 w-48 md:h-16" />
        <Skeleton className="h-4 w-40" />
      </div>

      <section className="space-y-4">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-[90%]" />
        <Skeleton className="h-6 w-[75%]" />
      </section>

      <section className="grid gap-5 md:grid-cols-2">
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </section>

      <section className="space-y-5">
        <Skeleton className="h-3 w-40" />
        <div className="grid gap-6 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-1">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-9 w-12" />
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
