import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "fortis-stocks";
export function MarketScan() {
  const rows = [
    ["NVDA", "A", "92", "+3.1%"],
    ["AVGO", "A", "88", "+1.4%"],
    ["META", "B", "81", "-0.6%"],
    ["WSM", "B", "77", "+2.2%"],
  ];
  return (
    <div style={{ padding: 24 }}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Ticker</TableHead>
            <TableHead>Grade</TableHead>
            <TableHead>Score</TableHead>
            <TableHead>Day</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r[0]}>
              <TableCell style={{ fontWeight: 600 }}>{r[0]}</TableCell>
              <TableCell>{r[1]}</TableCell>
              <TableCell>{r[2]}</TableCell>
              <TableCell>{r[3]}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
