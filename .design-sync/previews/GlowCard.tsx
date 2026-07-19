import { GlowCard } from "fortis-stocks";
export function Metric() {
  return (
    <div style={{ padding: 24, maxWidth: 360 }}>
      <GlowCard style={{ padding: 24, borderRadius: 12 }}>
        <div style={{ opacity: 0.7, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em" }}>
          Conviction grade
        </div>
        <div style={{ fontSize: 48, fontWeight: 700, lineHeight: 1.1 }}>A</div>
        <div style={{ opacity: 0.75 }}>Top 5 of 35 graded picks</div>
      </GlowCard>
    </div>
  );
}
