import { CountUp } from "fortis-stocks";
export function Stats() {
  return (
    <div style={{ padding: 24, display: "flex", gap: 40 }}>
      <div>
        <div style={{ fontSize: 40, fontWeight: 700 }}>
          <CountUp value={3347} startOnView={false} />
        </div>
        <div style={{ opacity: 0.7 }}>Tickers scanned</div>
      </div>
      <div>
        <div style={{ fontSize: 40, fontWeight: 700 }}>
          <CountUp value={12.4} decimals={1} suffix="%" signed startOnView={false} />
        </div>
        <div style={{ opacity: 0.7 }}>Avg forward return</div>
      </div>
    </div>
  );
}
