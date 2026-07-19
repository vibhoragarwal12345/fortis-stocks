import { Skeleton } from "fortis-stocks";
export function LoadingCard() {
  return (
    <div style={{ padding: 24, maxWidth: 360, display: "grid", gap: 12 }}>
      <Skeleton style={{ height: 28, width: "60%" }} />
      <Skeleton style={{ height: 16, width: "90%" }} />
      <Skeleton style={{ height: 16, width: "80%" }} />
      <div style={{ display: "flex", gap: 10 }}>
        <Skeleton style={{ height: 36, width: 96, borderRadius: 8 }} />
        <Skeleton style={{ height: 36, width: 96, borderRadius: 8 }} />
      </div>
    </div>
  );
}
