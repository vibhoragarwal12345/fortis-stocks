import { Skeleton } from "@/components/ui/skeleton"

export default function CommodityDetailLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-14 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-10 w-96 max-w-full md:h-12" />
        <Skeleton className="h-5 w-72" />
        <Skeleton className="h-16 w-full max-w-[820px] rounded-lg" />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-[520px] w-full rounded-xl" />
        <Skeleton className="h-[520px] w-full rounded-xl" />
      </div>
    </div>
  )
}
