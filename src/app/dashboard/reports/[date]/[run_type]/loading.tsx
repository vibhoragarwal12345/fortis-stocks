import { Skeleton } from "@/components/ui/skeleton"

export default function ReportLoading() {
  return (
    <div className="mx-auto max-w-[860px] space-y-12 px-6 py-14 md:space-y-16 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-10 w-[420px] max-w-full md:h-12" />
        <Skeleton className="h-4 w-48" />
      </div>

      <div className="space-y-3">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    </div>
  )
}
