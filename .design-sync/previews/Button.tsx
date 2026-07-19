import { Button } from "fortis-stocks";
const row = { display: "flex", gap: 12, flexWrap: "wrap" as const, alignItems: "center", padding: 24 };
export function Variants() {
  return (
    <div style={row}>
      <Button>Run scan</Button>
      <Button variant="secondary">Export</Button>
      <Button variant="outline">Filter</Button>
      <Button variant="ghost">Reset</Button>
      <Button variant="destructive">Remove pick</Button>
      <Button variant="link">View dossier</Button>
    </div>
  );
}
export function Sizes() {
  return (
    <div style={row}>
      <Button size="sm">Small</Button>
      <Button size="default">Default</Button>
      <Button size="lg">Large</Button>
    </div>
  );
}
