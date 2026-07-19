import { Section } from "fortis-stocks";
export function Bands() {
  return (
    <div style={{ padding: 0 }}>
      <Section spacing="default" width="default">
        <h2 style={{ margin: 0 }}>Today's focus list</h2>
        <p style={{ opacity: 0.75 }}>The highest-scoring opportunities from the latest scan.</p>
      </Section>
      <Section spacing="default" width="default" surface>
        <h2 style={{ margin: 0 }}>Emerging watchlist</h2>
        <p style={{ opacity: 0.75 }}>Speculative, long-horizon asymmetric bets — on an elevated surface band.</p>
      </Section>
    </div>
  );
}
