import type { ReactNode } from "react";

export function Card({
  title,
  note,
  action,
  children,
}: {
  title: string;
  note?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {action ?? (note ? <span className="card-note">{note}</span> : null)}
      </div>
      {children}
    </section>
  );
}

export function Stat({
  value,
  label,
  accent = false,
}: {
  value: ReactNode;
  label: string;
  accent?: boolean;
}) {
  return (
    <div className={accent ? "stat accent" : "stat"}>
      <span className="value">{value}</span>
      <span className="label">{label}</span>
    </div>
  );
}

/** One figure inside a card header strip — median, mean, range. */
export function Metric({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="metric">
      <span className="value">{value}</span>
      <span className="label">{label}</span>
    </div>
  );
}

/** Grouped digits, at most one decimal, em dash for nothing. */
export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
