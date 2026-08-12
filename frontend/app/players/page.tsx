"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ErrorNotice, Loading, useAsync } from "@/components/Async";
import { num } from "@/components/Card";
import { roster, type Player } from "@/lib/api";

type Key = "display_name" | "team" | "role" | "country" | "dpi" | "sensitivity" |
  "edpi" | "device" | "observations" | "last_observed_at";

const COLUMNS: { key: Key; label: string; numeric?: boolean }[] = [
  { key: "display_name", label: "Player" },
  { key: "team", label: "Team" },
  { key: "role", label: "Role" },
  { key: "country", label: "Country" },
  { key: "dpi", label: "DPI", numeric: true },
  { key: "sensitivity", label: "Sens", numeric: true },
  { key: "edpi", label: "eDPI", numeric: true },
  { key: "device", label: "Mouse" },
  { key: "observations", label: "Obs", numeric: true },
  { key: "last_observed_at", label: "Last seen" },
];

/** Nulls sort last whichever way the column is pointing — a player with no
 *  eDPI is not "the lowest eDPI". */
function compare(a: Player, b: Player, key: Key, ascending: boolean): number {
  const left = a[key];
  const right = b[key];
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  const order =
    typeof left === "number" && typeof right === "number"
      ? left - right
      : String(left).localeCompare(String(right), undefined, { sensitivity: "base" });
  return ascending ? order : -order;
}

function PlayersTable() {
  const params = useSearchParams();
  const device = params.get("device");
  const { data, error, loading } = useAsync<Player[]>(() => roster());
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [sort, setSort] = useState<{ key: Key; ascending: boolean }>({
    key: "display_name",
    ascending: true,
  });
  const [coveredOnly, setCoveredOnly] = useState(false);

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    const matches = (value: string | null) =>
      !!value && value.toLowerCase().includes(needle);
    return data
      .filter((p) => !device || p.device === device)
      .filter((p) => !coveredOnly || p.observations > 0)
      .filter(
        (p) =>
          !needle ||
          matches(p.display_name) ||
          matches(p.team) ||
          matches(p.role) ||
          matches(p.country) ||
          matches(p.device) ||
          matches(p.mouse),
      )
      .sort((a, b) => compare(a, b, sort.key, sort.ascending));
  }, [data, device, query, coveredOnly, sort]);

  if (loading) return <Loading />;
  if (error) return <ErrorNotice message={error} />;
  if (!data) return null;

  const covered = data.filter((p) => p.observations).length;

  return (
    <>
      <h1>Players</h1>
      <p className="lead">
        {covered} of {data.length} players in the circuit have at least one reading.
        Those with none are listed too — the gap is part of the picture.
      </p>

      <div className="toolbar">
        <div className="search">
          <span className="glass" aria-hidden="true">
            ⌕
          </span>
          <input
            type="search"
            value={query}
            placeholder="Filter by player, team, role, country, mouse"
            aria-label="Filter players"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {device && (
          <span className="filter-chip">
            Mouse: {device}
            <Link href="/players/" aria-label="Clear the mouse filter">
              ×
            </Link>
          </span>
        )}
        <button
          type="button"
          onClick={() => setCoveredOnly((on) => !on)}
          aria-pressed={coveredOnly}
        >
          {coveredOnly ? "Show all players" : "Hide players with no data"}
        </button>
        <span className="muted spacer">
          {rows.length} {rows.length === 1 ? "player" : "players"}
        </span>
      </div>

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((column) => {
                const active = sort.key === column.key;
                return (
                  <th
                    key={column.key}
                    className={[
                      column.numeric ? "num" : "",
                      "sortable",
                      active ? "active" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    aria-sort={
                      active ? (sort.ascending ? "ascending" : "descending") : "none"
                    }
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSort((current) =>
                          current.key === column.key
                            ? { key: column.key, ascending: !current.ascending }
                            : // Numbers are most interesting from the top.
                              { key: column.key, ascending: !column.numeric },
                        )
                      }
                    >
                      {column.label}
                      {active ? (sort.ascending ? " ↑" : " ↓") : ""}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
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
                <td className="num">{num(p.dpi)}</td>
                <td className="num">{num(p.sensitivity)}</td>
                <td className="num">{num(p.edpi)}</td>
                <td title={p.mouse ?? undefined}>{p.device ?? p.mouse ?? "—"}</td>
                <td className="num">{p.observations}</td>
                <td className="muted">{p.last_observed_at?.slice(0, 10) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="empty">No player matches that filter.</p>}
      </div>
    </>
  );
}

export default function Players() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <main>
      <Suspense fallback={<Loading />}>
        <PlayersTable />
      </Suspense>
    </main>
  );
}
