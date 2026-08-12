"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { Card, Stat, num } from "@/components/Card";
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
    <>
      <section className="hero">
        <h1>{p.display_name}</h1>
        <p className="muted">
          {[p.team, p.role, p.country].filter(Boolean).join(" · ") || "no roster data"}
          {" · "}
          <a href={`https://liquipedia.net/marvelrivals/${p.liquipedia_page}`}>
            Liquipedia
          </a>
          {p.last_observed_at && ` · last seen ${p.last_observed_at.slice(0, 10)}`}
        </p>
        <div className="stat-grid">
          <Stat value={num(p.dpi)} label="DPI" />
          <Stat value={num(p.sensitivity)} label="Sensitivity" />
          <Stat accent value={num(p.edpi)} label="eDPI" />
          <Stat value={p.observations} label="Readings" />
        </div>
      </section>

      <div className="cards wide">
        <Card title="Mouse" note={p.device ? "canonical name" : undefined}>
          {p.device || p.mouse ? (
            <>
              <p style={{ fontSize: "1.15rem", fontWeight: 600, margin: "0 0 0.4rem" }}>
                {p.device ?? p.mouse}
              </p>
              {p.mouse && p.mouse !== p.device && (
                <p className="muted">said as “{p.mouse}”</p>
              )}
              {p.device && (
                <p style={{ marginBottom: 0 }}>
                  <Link href={`/players/?device=${encodeURIComponent(p.device)}`}>
                    Others using this mouse →
                  </Link>
                </p>
              )}
            </>
          ) : (
            <p className="empty">no mouse observed yet</p>
          )}
        </Card>

        <Card title="Where this came from" note={`${history.data.length} stints`}>
          <p className="muted" style={{ marginTop: 0 }}>
            Readings are never overwritten. Consecutive identical ones are
            collapsed below, so a change back to earlier settings stays visible.
          </p>
          <div className="metric-row" style={{ border: 0, paddingBottom: 0 }}>
            {[...new Set(history.data.map((h) => h.source))].map((source) => (
              <span key={source} className="source">
                {source}
              </span>
            ))}
          </div>
        </Card>
      </div>

      <h2>History</h2>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>First seen</th>
              <th>Last seen</th>
              <th className="num">Seen</th>
              <th>Source</th>
              <th className="num">DPI</th>
              <th className="num">Sens</th>
              <th className="num">Win</th>
              <th>Mouse</th>
              <th>Raw</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((h, i) => (
              <tr key={`${h.first_seen_at}-${i}`}>
                <td>{h.first_seen_at.slice(0, 10)}</td>
                <td>{h.last_seen_at.slice(0, 10)}</td>
                <td className="num">{h.times_seen}</td>
                <td>
                  <span className="source">{h.source}</span>
                </td>
                <td className="num">{num(h.dpi)}</td>
                <td className="num">{num(h.sensitivity)}</td>
                <td className="num">{num(h.windows_sens)}</td>
                <td>{h.mouse ?? "—"}</td>
                <td className="raw">{h.raw_text ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {history.data.length === 0 && (
          <p className="empty">Nothing observed for this player yet.</p>
        )}
      </div>
    </>
  );
}

export default function PlayerPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <main>
      <Suspense fallback={<Loading />}>
        <PlayerDetail />
      </Suspense>
    </main>
  );
}
