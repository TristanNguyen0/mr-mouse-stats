import Link from "next/link";
import type { Bucket } from "@/lib/api";

/** Mice ranked by how many players use them, each row linking to the players
 *  filtered to that device. Counts are canonical devices, not spellings —
 *  see mr_mouse_stats/site/devices.py. */
export function DeviceRanking({
  devices,
  limit = 10,
}: {
  devices: Bucket[];
  limit?: number;
}) {
  if (devices.length === 0) return <p className="empty">no mice observed yet</p>;

  const max = devices[0].count;
  const total = devices.reduce((sum, d) => sum + d.count, 0);
  const shown = devices.slice(0, limit);

  return (
    <>
      <div className="rank">
        {shown.map((d, i) => (
          <Link
            key={d.label}
            href={`/players/?device=${encodeURIComponent(d.label)}`}
            className={i === 0 ? "rank-row top" : "rank-row"}
          >
            <span className="rank-num">{i + 1}</span>
            <span>
              <span className="rank-name">{d.label}</span>
              <span
                className="rank-share"
                style={{ width: `${Math.max(3, (d.count / max) * 100)}%` }}
              />
            </span>
            <span className="rank-count">
              <b>{d.count}</b> {d.count === 1 ? "player" : "players"}
            </span>
          </Link>
        ))}
      </div>
      <p className="card-note" style={{ marginTop: "0.8rem" }}>
        {total} players with a known mouse across {devices.length} devices
        {devices.length > shown.length ? `, top ${shown.length} shown` : ""}.
      </p>
    </>
  );
}
