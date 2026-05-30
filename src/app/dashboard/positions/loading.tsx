import { Skeleton } from "@/components/ui/skeleton"

export default function PositionsLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-10 w-80 md:h-12" />
        <Skeleton className="h-5 w-[520px] max-w-full" />
      </div>

      <section className="space-y-6">
        <Skeleton className="h-8 w-60 border-b border-border pb-4" />
        <div className="space-y-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      </section>
    </div>
  )
}
