import type { Bucket } from "@/lib/api";

// Bars scaled to the largest count, matching the inline-SVG charts the
// static site used. Plain divs: no chart library for four bar charts.
export function BarChart({ data, empty = "no data yet" }: { data: Bucket[]; empty?: string }) {
  if (data.length === 0) return <p className="muted">{empty}</p>;
  const max = Math.max(...data.map((d) => d.count));
  return (
    <div className="chart">
      {data.map((d) => (
        <div className="chart-row" key={d.label}>
          <span className="chart-label" title={d.label}>
            {d.label}
          </span>
          <span
            className="chart-bar"
            style={{ width: `${Math.max(2, (d.count / max) * 100)}%` }}
            role="img"
            aria-label={`${d.label}: ${d.count}`}
          />
          <span className="chart-count">{d.count}</span>
        </div>
      ))}
    </div>
  );
}
