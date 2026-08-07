"use client";

import { BarChart } from "@/components/BarChart";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { api, type Stats } from "@/lib/api";

export default function Overview() {
  const { data, error, loading } = useAsync<Stats>(() => api.stats());

  if (loading) return <Loading />;
  if (error) return <ErrorNotice message={error} />;
  if (!data) return null;

  return (
    <main>
      <h1>Marvel Rivals pro mouse settings</h1>
      <p>
        Settings for <b>{data.covered_players}</b> of <b>{data.total_players}</b> players
        in the Ignite circuit, observed passively from public Twitch chat and from
        Liquipedia.
      </p>

      <h2>DPI</h2>
      <BarChart data={data.dpi_distribution} />

      <h2>eDPI</h2>
      <BarChart data={data.edpi_distribution} />

      <h2>Mice</h2>
      <BarChart data={data.mouse_popularity} />

      <h2>By role</h2>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Role</th>
              <th>Players with eDPI</th>
              <th>Median eDPI</th>
            </tr>
          </thead>
          <tbody>
            {data.roles.map((r) => (
              <tr key={r.role}>
                <td>{r.role}</td>
                <td>{r.players_with_edpi}</td>
                <td>{r.median_edpi ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
