import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="flex flex-col items-center gap-6 text-center">
        <h1 className="text-5xl font-bold tracking-tight text-foreground">
          Fortis Stock Intelligence
        </h1>
        <p className="max-w-md text-lg text-muted-foreground">
          Institutional-grade stock research and analytics for The Fortis Agency.
        </p>
        <Link href="/login" className={buttonVariants({ size: "lg" })}>
          Login
        </Link>
      </div>
    </main>
  );
}
