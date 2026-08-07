"use client";

import Link from "next/link";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { api, type Player } from "@/lib/api";

export default function Players() {
  const { data, error, loading } = useAsync<Player[]>(() => api.players());

  if (loading) return <Loading />;
  if (error) return <ErrorNotice message={error} />;
  if (!data) return null;

  return (
    <main>
      <h1>Players</h1>
      <p className="muted">
        {data.filter((p) => p.observations).length} of {data.length} have at least one
        observation. Players with no data yet are listed but not linked.
      </p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Player</th>
              <th>Team</th>
              <th>Role</th>
              <th>Country</th>
              <th>DPI</th>
              <th>Sens</th>
              <th>eDPI</th>
              <th>Mouse</th>
              <th>Obs</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.id} className={p.observations ? undefined : "no-data"}>
                <td>
                  {p.observations ? (
                    <Link href={`/player/?id=${p.id}`}>{p.display_name}</Link>
                  ) : (
                    p.display_name
                  )}
                </td>
                <td>{p.team ?? "—"}</td>
                <td>{p.role ?? "—"}</td>
                <td>{p.country ?? "—"}</td>
                <td>{p.dpi ?? "—"}</td>
                <td>{p.sensitivity ?? "—"}</td>
                <td>{p.edpi ?? "—"}</td>
                <td>{p.mouse ?? "—"}</td>
                <td>{p.observations}</td>
                <td className="muted">{p.last_observed_at?.slice(0, 10) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
