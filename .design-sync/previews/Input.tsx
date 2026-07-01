import { Input, Label } from "fortis-stocks";
export function Field() {
  return (
    <div style={{ padding: 24, maxWidth: 360, display: "grid", gap: 8 }}>
      <Label htmlFor="ticker">Ticker symbol</Label>
      <Input id="ticker" placeholder="e.g. NVDA" defaultValue="NVDA" />
    </div>
  );
}
export function States() {
  return (
    <div style={{ padding: 24, maxWidth: 360, display: "grid", gap: 12 }}>
      <Input placeholder="Search the focus list…" />
      <Input defaultValue="Read-only value" readOnly />
      <Input placeholder="Disabled" disabled />
    </div>
  );
}
