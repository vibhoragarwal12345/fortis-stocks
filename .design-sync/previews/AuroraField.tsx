import { AuroraField } from "fortis-stocks";
export function Hero() {
  return (
    <div style={{ position: "relative", height: 260, width: "100%", overflow: "hidden", borderRadius: 12 }}>
      <AuroraField />
      <div style={{ position: "relative", zIndex: 1, padding: 32 }}>
        <h2 style={{ margin: 0 }}>Institutional Terminal</h2>
        <p style={{ opacity: 0.8 }}>Aurora background field with the faint blueprint grid.</p>
      </div>
    </div>
  );
}
