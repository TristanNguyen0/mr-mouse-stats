"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { api, type HistoryEntry, type Player } from "@/lib/api";

// A query parameter rather than /players/[id]/: a static export would need
// every id known at build time, and the whole point of this rewrite is that
// the data comes from the API at runtime.
function PlayerDetail() {
  const id = Number(useSearchParams().get("id"));
  const player = useAsync<Player>(() => api.player(id), [id]);
  const history = useAsync<HistoryEntry[]>(() => api.history(id), [id]);

  if (!id) return <ErrorNotice message="no player id given" />;
  if (player.loading || history.loading) return <Loading />;
  if (player.error) return <ErrorNotice message={player.error} />;
  if (history.error) return <ErrorNotice message={history.error} />;
  if (!player.data || !history.data) return null;

  const p = player.data;
  return (
    <main>
      <h1>{p.display_name}</h1>
      <p className="muted">
        {[p.team, p.role, p.country].filter(Boolean).join(" · ") || "—"}
        {" · "}
        <a href={`https://liquipedia.net/marvelrivals/${p.liquipedia_page}`}>
          Liquipedia
        </a>
      </p>

      <h2>Current</h2>
      <div className="tablewrap">
        <table>
          <tbody>
            <tr>
              <th>DPI</th>
              <td>{p.dpi ?? "—"}</td>
            </tr>
            <tr>
              <th>Sensitivity</th>
              <td>{p.sensitivity ?? "—"}</td>
            </tr>
            <tr>
              <th>eDPI</th>
              <td>{p.edpi ?? "—"}</td>
            </tr>
            <tr>
              <th>Mouse</th>
              <td>{p.mouse ?? "—"}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>History</h2>
      <p className="muted">
        Consecutive identical readings are collapsed, so a change back to earlier
        settings stays visible.
      </p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>First seen</th>
              <th>Last seen</th>
              <th>Seen</th>
              <th>Source</th>
              <th>DPI</th>
              <th>Sens</th>
              <th>Win</th>
              <th>Mouse</th>
              <th>Raw</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((h, i) => (
              <tr key={`${h.first_seen_at}-${i}`}>
                <td>{h.first_seen_at.slice(0, 10)}</td>
                <td>{h.last_seen_at.slice(0, 10)}</td>
                <td>{h.times_seen}</td>
                <td>
                  <span className="source">{h.source}</span>
                </td>
                <td>{h.dpi ?? "—"}</td>
                <td>{h.sensitivity ?? "—"}</td>
                <td>{h.windows_sens ?? "—"}</td>
                <td>{h.mouse ?? "—"}</td>
                <td className="raw muted">{h.raw_text ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

export default function PlayerPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <Suspense fallback={<Loading />}>
      <PlayerDetail />
    </Suspense>
  );
}
