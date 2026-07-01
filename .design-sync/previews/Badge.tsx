import { Badge } from "fortis-stocks";
const row = { display: "flex", gap: 10, flexWrap: "wrap" as const, alignItems: "center", padding: 24 };
export function Grades() {
  return (
    <div style={row}>
      <Badge variant="success">A · High conviction</Badge>
      <Badge variant="accent">B · Watch</Badge>
      <Badge variant="warning">C · Caution</Badge>
      <Badge variant="destructive">Broken thesis</Badge>
    </div>
  );
}
export function Signals() {
  return (
    <div style={row}>
      <Badge variant="default">Live</Badge>
      <Badge variant="secondary">15-min delayed</Badge>
      <Badge variant="info">Insider buy</Badge>
      <Badge variant="neutral">Dossier completing</Badge>
      <Badge variant="outline">Emerging</Badge>
    </div>
  );
}
