import { Label, Input } from "fortis-stocks";
export function WithInput() {
  return (
    <div style={{ padding: 24, maxWidth: 360, display: "grid", gap: 8 }}>
      <Label htmlFor="alert">Price alert threshold</Label>
      <Input id="alert" defaultValue="$145.00" />
    </div>
  );
}
