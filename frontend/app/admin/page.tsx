"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Loading } from "@/components/Async";
import {
  ApiError,
  adminApi,
  type Candidate,
  type FailingChannel,
  type MissingTwitch,
  type ManualObservation,
} from "@/lib/api";
import { accessToken, authConfigured, login, logout } from "@/lib/auth";

interface Snapshot {
  counts: Record<string, number>;
  failing: FailingChannel[];
  missing: MissingTwitch[];
  candidates: Candidate[];
}

export default function Admin() {
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setToken(accessToken()), []);

  const refresh = useCallback(async (t: string) => {
    try {
      const [overview, handles, candidates] = await Promise.all([
        adminApi.overview(t),
        adminApi.handles(t),
        adminApi.candidates(t),
      ]);
      setData({
        counts: overview.counts as unknown as Record<string, number>,
        failing: handles.failing,
        missing: handles.missing,
        candidates,
      });
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        logout();
        setToken(null);
      }
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (token) void refresh(token);
  }, [token, refresh]);

  async function act(fn: () => Promise<unknown>, message: string) {
    if (!token) return;
    setBusy(true);
    try {
      await fn();
      setNotice(message);
      await refresh(token);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!authConfigured) {
    return (
      <main>
        <h1>Admin</h1>
        <p className="notice error">
          Cognito is not configured. Set <code>NEXT_PUBLIC_COGNITO_DOMAIN</code> and{" "}
          <code>NEXT_PUBLIC_COGNITO_CLIENT_ID</code> at build time.
        </p>
      </main>
    );
  }

  if (!token) {
    return (
      <main>
        <h1>Admin</h1>
        {/* <p className="muted">
          This view owns every write to the database. Sign in with your Cognito
          account.
        </p> */}
        <button className="primary" onClick={() => void login()}>
          Sign in
        </button>
      </main>
    );
  }

  return (
    <main>
      <h1>Admin</h1>
      <p className="muted">
        Signed in.{" "}
        <button
          onClick={() => {
            logout();
            setToken(null);
          }}
        >
          Sign out
        </button>
      </p>

      {notice && <p className="notice">{notice}</p>}
      {error && <ErrorNotice message={error} />}
      {!data && !error && <Loading />}

      {data && (
        <>
          <ul className="muted">
            <li>
              <b>{data.counts.players}</b> players, <b>{data.counts.resolved}</b> resolved
            </li>
            <li>
              <b>{data.counts.active_twitch}</b> active twitch handles,{" "}
              <b className={data.counts.failing_channels ? "warn" : undefined}>
                {data.counts.failing_channels}
              </b>{" "}
              failing to join
            </li>
            <li>
              <b>{data.counts.players_without_twitch}</b> players without twitch,{" "}
              <b>{data.counts.unresolved}</b> unresolved
            </li>
            <li>
              <b>{data.counts.candidates}</b> unparsed candidates
            </li>
          </ul>

          <h2>Channels failing to join</h2>
          <p className="muted">
            Usually a renamed or suspended channel. Replacing the handle retires the old
            one and appends the new; the collector joins it within a few minutes without a
            restart.
          </p>
          <HandleTable
            rows={data.failing}
            busy={busy}
            onReplace={(playerId, handle, accountId) =>
              act(
                () =>
                  adminApi.replaceHandle(token, {
                    player_id: playerId,
                    new_handle: handle,
                    old_account_id: accountId,
                  }),
                `handle '${handle}' recorded`,
              )
            }
            onRetire={(accountId) =>
              act(() => adminApi.retireHandle(token, accountId), "handle retired")
            }
          />

          <h2>Players with no twitch handle</h2>
          <MissingTable
            rows={data.missing}
            busy={busy}
            onAdd={(playerId, handle) =>
              act(
                () =>
                  adminApi.replaceHandle(token, {
                    player_id: playerId,
                    new_handle: handle,
                  }),
                `handle '${handle}' added`,
              )
            }
          />

          <h2>Unparsed candidates ({data.candidates.length})</h2>
          <p className="muted">
            Bot responses the parser could not read. Record the values by hand, or dismiss
            the message — dismissed rows are kept, just removed from this queue.
          </p>
          <CandidateList
            rows={data.candidates}
            busy={busy}
            onRecord={(id, body) =>
              act(
                () => adminApi.recordObservation(token, id, body),
                "manual observation recorded",
              )
            }
            onDismiss={(id) =>
              act(() => adminApi.dismissCandidate(token, id), "candidate dismissed")
            }
          />
        </>
      )}
    </main>
  );
}

function HandleTable({
  rows,
  busy,
  onReplace,
  onRetire,
}: {
  rows: FailingChannel[];
  busy: boolean;
  onReplace: (playerId: number, handle: string, accountId: number) => void;
  onRetire: (accountId: number) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  if (rows.length === 0) return <p className="muted">Nothing failing.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Channel</th>
            <th>Player</th>
            <th>Other socials</th>
            <th>New handle</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.account_id}>
              <td>{r.channel}</td>
              <td>{r.liquipedia_page}</td>
              <td className="muted">{r.other_socials ?? "—"}</td>
              <td>
                <input
                  className="narrow"
                  value={drafts[r.account_id] ?? ""}
                  onChange={(e) =>
                    setDrafts({ ...drafts, [r.account_id]: e.target.value })
                  }
                  placeholder="newhandle"
                />
              </td>
              <td>
                <button
                  disabled={busy || !(drafts[r.account_id] ?? "").trim()}
                  onClick={() =>
                    onReplace(r.player_id, drafts[r.account_id].trim(), r.account_id)
                  }
                >
                  Replace
                </button>{" "}
                <button disabled={busy} onClick={() => onRetire(r.account_id)}>
                  Retire
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MissingTable({
  rows,
  busy,
  onAdd,
}: {
  rows: MissingTwitch[];
  busy: boolean;
  onAdd: (playerId: number, handle: string) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  if (rows.length === 0) return <p className="muted">Every resolved player has one.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Country</th>
            <th>Known socials</th>
            <th>Twitch handle</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.player_id}>
              <td>{r.handle_name ?? r.liquipedia_page}</td>
              <td>{r.country ?? "—"}</td>
              <td className="muted">{r.other_socials ?? "—"}</td>
              <td>
                <input
                  className="narrow"
                  value={drafts[r.player_id] ?? ""}
                  onChange={(e) =>
                    setDrafts({ ...drafts, [r.player_id]: e.target.value })
                  }
                  placeholder="handle"
                />
              </td>
              <td>
                <button
                  disabled={busy || !(drafts[r.player_id] ?? "").trim()}
                  onClick={() => onAdd(r.player_id, drafts[r.player_id].trim())}
                >
                  Add
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const FIELDS = [
  ["dpi", "DPI"],
  ["sensitivity", "Sens"],
  ["windows_sens", "Windows"],
  ["polling_rate", "Hz"],
  ["mouse_brand", "Brand"],
  ["mouse_model", "Model"],
] as const;

function CandidateList({
  rows,
  busy,
  onRecord,
  onDismiss,
}: {
  rows: Candidate[];
  busy: boolean;
  onRecord: (id: number, body: ManualObservation) => void;
  onDismiss: (id: number) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, Record<string, string>>>({});
  if (rows.length === 0) return <p className="muted">Queue is empty.</p>;

  function body(id: number): ManualObservation {
    const draft = drafts[id] ?? {};
    const numeric = (key: string) =>
      draft[key]?.trim() ? Number(draft[key]) : null;
    return {
      dpi: numeric("dpi"),
      sensitivity: numeric("sensitivity"),
      windows_sens: numeric("windows_sens"),
      polling_rate: numeric("polling_rate"),
      mouse_brand: draft.mouse_brand?.trim() || null,
      mouse_model: draft.mouse_model?.trim() || null,
    };
  }

  const filled = (id: number) =>
    Object.values(body(id)).some((v) => v !== null && !Number.isNaN(v));

  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Channel</th>
            <th>Message</th>
            <th>Values</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="muted">{r.observed_at.slice(0, 16).replace("T", " ")}</td>
              <td>{r.channel}</td>
              <td className="raw">
                {r.text}
                {r.trigger_text && (
                  <div className="muted">re: {r.trigger_text}</div>
                )}
              </td>
              <td>
                {FIELDS.map(([key, label]) => (
                  <input
                    key={key}
                    className="narrow"
                    placeholder={label}
                    value={drafts[r.id]?.[key] ?? ""}
                    onChange={(e) =>
                      setDrafts({
                        ...drafts,
                        [r.id]: { ...(drafts[r.id] ?? {}), [key]: e.target.value },
                      })
                    }
                  />
                ))}
              </td>
              <td>
                <button
                  disabled={busy || !filled(r.id)}
                  onClick={() => onRecord(r.id, body(r.id))}
                >
                  Record
                </button>{" "}
                <button disabled={busy} onClick={() => onDismiss(r.id)}>
                  Dismiss
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
