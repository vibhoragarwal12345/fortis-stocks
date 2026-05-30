import { Skeleton } from "@/components/ui/skeleton"

export default function FocusListLoading() {
  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <div className="space-y-3">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="h-10 w-72 md:h-12 md:w-96" />
        <Skeleton className="h-5 w-[420px] max-w-full" />
      </div>

      {[0, 1, 2].map((g) => (
        <section key={g} className="space-y-6">
          <Skeleton className="h-3 w-48" />
          <div className="grid gap-5 md:grid-cols-2">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-36 w-full rounded-xl" />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
