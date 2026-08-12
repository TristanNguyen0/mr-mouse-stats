"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { roster, type Player } from "@/lib/api";

interface Hit {
  group: "Players" | "Teams" | "Mice";
  label: string;
  meta: string;
  href: string;
}

function hits(players: Player[], query: string): Hit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const matches = (value: string | null) =>
    !!value && value.toLowerCase().includes(needle);

  const found: Hit[] = players
    .filter((p) => matches(p.display_name))
    .map((p) => ({
      group: "Players" as const,
      label: p.display_name,
      meta: [p.team, p.role].filter(Boolean).join(" · ") || "no team",
      // Players with nothing observed still resolve — the page says so.
      href: `/player/?id=${p.id}`,
    }));

  const teams = new Map<string, number>();
  const devices = new Map<string, number>();
  for (const p of players) {
    if (p.team) teams.set(p.team, (teams.get(p.team) ?? 0) + 1);
    if (p.device) devices.set(p.device, (devices.get(p.device) ?? 0) + 1);
  }

  for (const [team, n] of teams) {
    if (matches(team)) {
      found.push({
        group: "Teams",
        label: team,
        meta: `${n} ${n === 1 ? "player" : "players"}`,
        href: `/players/?q=${encodeURIComponent(team)}`,
      });
    }
  }
  for (const [device, n] of devices) {
    if (matches(device)) {
      found.push({
        group: "Mice",
        label: device,
        meta: `${n} ${n === 1 ? "player" : "players"}`,
        href: `/players/?device=${encodeURIComponent(device)}`,
      });
    }
  }
  return found.slice(0, 12);
}

export function Search({
  placeholder = "Search players, teams, mice",
  autoFocus = false,
}: {
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const router = useRouter();
  const box = useRef<HTMLDivElement>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  // Fetched once for the tab and shared with the players table; typing is
  // then filtering in memory, so there is no request per keystroke.
  useEffect(() => {
    let cancelled = false;
    roster()
      .then((rows) => !cancelled && setPlayers(rows))
      .catch(() => undefined); // the page itself reports load failures
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const results = useMemo(() => hits(players, query), [players, query]);
  const groups = ["Players", "Teams", "Mice"] as const;

  function go(href: string) {
    setOpen(false);
    setQuery("");
    router.push(href);
  }

  return (
    <div className="search" ref={box}>
      <span className="glass" aria-hidden="true">
        ⌕
      </span>
      <input
        type="search"
        value={query}
        placeholder={placeholder}
        autoFocus={autoFocus}
        aria-label="Search players, teams and mice"
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && results.length > 0) go(results[0].href);
        }}
      />
      {open && query.trim() && (
        <div className="search-results">
          {results.length === 0 && <p className="empty">No match for “{query}”.</p>}
          {groups.map((group) => {
            const rows = results.filter((r) => r.group === group);
            if (rows.length === 0) return null;
            return (
              <div key={group}>
                <div className="search-group">{group}</div>
                {rows.map((hit) => (
                  <button
                    key={`${hit.group}-${hit.label}`}
                    type="button"
                    className="search-hit"
                    onClick={() => go(hit.href)}
                  >
                    {hit.label}
                    <span className="meta">{hit.meta}</span>
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
