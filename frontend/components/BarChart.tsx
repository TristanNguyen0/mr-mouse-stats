import type { Bucket } from "@/lib/api";

// Bars scaled to the largest count, with each bucket's share of the total
// alongside it. Plain divs: no chart library for two bar charts.
export function BarChart({
  data,
  empty = "no data yet",
  longLabels = false,
}: {
  data: Bucket[];
  empty?: string;
  longLabels?: boolean;
}) {
  if (data.length === 0) return <p className="empty">{empty}</p>;
  const max = Math.max(...data.map((d) => d.count));
  const total = data.reduce((sum, d) => sum + d.count, 0);

  return (
    <div className="chart">
      {data.map((d) => (
        <div className={longLabels ? "chart-row long" : "chart-row"} key={d.label}>
          <span className="chart-label" title={d.label}>
            {d.label}
          </span>
          <span className="chart-track">
            <span
              className={d.count === max ? "chart-fill peak" : "chart-fill"}
              style={{ width: `${Math.max(1.5, (d.count / max) * 100)}%` }}
              role="img"
              aria-label={`${d.label}: ${d.count} of ${total}`}
            />
          </span>
          <span className="chart-count">
            <b>{d.count}</b> · {Math.round((d.count / total) * 100)}%
          </span>
        </div>
      ))}
    </div>
  );
}
