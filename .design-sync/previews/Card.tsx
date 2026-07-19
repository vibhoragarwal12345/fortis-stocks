import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, Badge, Button } from "fortis-stocks";
export function ResearchDossier() {
  return (
    <div style={{ padding: 24, maxWidth: 420 }}>
      <Card>
        <CardHeader>
          <CardTitle>NVDA · NVIDIA Corp</CardTitle>
          <CardDescription>Semiconductors · Composite score 92</CardDescription>
        </CardHeader>
        <CardContent>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <Badge variant="success">Grade A</Badge>
            <Badge variant="info">Insider buy</Badge>
          </div>
          <p style={{ margin: 0, opacity: 0.8, lineHeight: 1.6 }}>
            Accelerating datacenter demand and durable pricing power; smart-money
            accumulation confirmed across the last three filings.
          </p>
        </CardContent>
        <CardFooter style={{ display: "flex", gap: 12 }}>
          <Button size="sm">Open dossier</Button>
          <Button size="sm" variant="ghost">Track</Button>
        </CardFooter>
      </Card>
    </div>
  );
}
