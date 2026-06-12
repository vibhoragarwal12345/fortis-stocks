import { Skeleton } from "@/components/ui/skeleton"

export default function CommoditiesLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-40" />
        <Skeleton className="h-10 w-80 md:h-12 md:w-[420px]" />
        <Skeleton className="h-5 w-[420px] max-w-full" />
        <Skeleton className="h-16 w-full max-w-[820px] rounded-lg" />
      </div>

      {[0, 1, 2].map((g) => (
        <section key={g} className="space-y-6">
          <Skeleton className="h-3 w-32" />
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-44 w-full rounded-xl" />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
