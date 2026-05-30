import { Skeleton } from "@/components/ui/skeleton"

export default function EmergingLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-10 w-80 md:h-12" />
        <Skeleton className="h-5 w-[520px] max-w-full" />
      </div>

      <Skeleton className="h-40 w-full rounded-md" />

      {[0, 1].map((g) => (
        <section key={g} className="space-y-6">
          <Skeleton className="h-3 w-44" />
          <Skeleton className="h-48 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </section>
      ))}
    </div>
  )
}
