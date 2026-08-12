// Typed client for the two APIs. Mirrors the Pydantic models in
// mr_mouse_stats/api/{public,admin}.py.

export const PUBLIC_API =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";
export const ADMIN_API =
  process.env.NEXT_PUBLIC_ADMIN_API_BASE?.replace(/\/$/, "") ?? "http://127.0.0.1:8001";

export interface Player {
  id: number;
  liquipedia_page: string;
  display_name: string;
  team: string | null;
  role: string | null;
  country: string | null;
  dpi: number | null;
  sensitivity: number | null;
  edpi: number | null;
  /** What the player actually said, verbatim. */
  mouse: string | null;
  /** `mouse` folded onto a canonical device name; null when it isn't a mouse. */
  device: string | null;
  observations: number;
  last_observed_at: string | null;
}

export interface HistoryEntry {
  first_seen_at: string;
  last_seen_at: string;
  times_seen: number;
  source: string;
  dpi: number | null;
  sensitivity: number | null;
  windows_sens: number | null;
  mouse: string | null;
  raw_text: string | null;
}

export interface Bucket {
  label: string;
  count: number;
}

export interface RoleStat {
  role: string;
  players_with_edpi: number;
  median_edpi: number | null;
}

export interface Metric {
  count: number;
  median: number | null;
  mean: number | null;
  low: number | null;
  high: number | null;
}

export interface Stats {
  total_players: number;
  covered_players: number;
  total_teams: number;
  total_observations: number;
  dpi_distribution: Bucket[];
  edpi_distribution: Bucket[];
  mouse_popularity: Bucket[];
  edpi: Metric;
  dpi: Metric;
  roles: RoleStat[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body; the status text will do
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  players: (coveredOnly = false) =>
    request<Player[]>(PUBLIC_API, `/players?covered_only=${coveredOnly}`),
  player: (id: number) => request<Player>(PUBLIC_API, `/players/${id}`),
  history: (id: number) => request<HistoryEntry[]>(PUBLIC_API, `/players/${id}/history`),
  stats: () => request<Stats>(PUBLIC_API, "/stats"),
};

// The header search and the players table both want the whole roster, and it
// is a few dozen rows. Cache the promise for the life of the tab so moving
// between pages does not refetch it; drop it on failure so a retry can work.
let rosterPromise: Promise<Player[]> | null = null;

export function roster(): Promise<Player[]> {
  if (!rosterPromise) {
    rosterPromise = api.players().catch((err) => {
      rosterPromise = null;
      throw err;
    });
  }
  return rosterPromise;
}

// --- admin -----------------------------------------------------------------
// Every call carries the Cognito access token. API Gateway rejects requests
// without a valid one before the Lambda is ever invoked.

function adminRequest<T>(token: string, path: string, init: RequestInit = {}) {
  return request<T>(ADMIN_API, path, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

export interface AdminCounts {
  players: number;
  resolved: number;
  active_twitch: number;
  failing_channels: number;
  players_without_twitch: number;
  unresolved: number;
  candidates: number;
}

export interface FailingChannel {
  channel: string;
  last_checked_at: string;
  account_id: number;
  handle: string;
  player_id: number;
  liquipedia_page: string;
  real_name: string | null;
  other_socials: string | null;
}

export interface MissingTwitch {
  player_id: number;
  liquipedia_page: string;
  handle_name: string | null;
  real_name: string | null;
  country: string | null;
  other_socials: string | null;
}

export interface Candidate {
  id: number;
  channel: string;
  login: string;
  observed_at: string;
  text: string;
  trigger_text: string | null;
  trigger_login: string | null;
}

export interface ManualObservation {
  dpi?: number | null;
  sensitivity?: number | null;
  windows_sens?: number | null;
  polling_rate?: number | null;
  mouse_brand?: string | null;
  mouse_model?: string | null;
}

export const adminApi = {
  overview: (token: string) =>
    adminRequest<{
      counts: AdminCounts;
      observations_by_source: { source: string; n: number }[];
      messages_by_kind: { kind: string; n: number }[];
    }>(token, "/overview"),

  handles: (token: string) =>
    adminRequest<{ failing: FailingChannel[]; missing: MissingTwitch[] }>(
      token,
      "/handles",
    ),

  candidates: (token: string) => adminRequest<Candidate[]>(token, "/candidates"),

  unresolved: (token: string) =>
    adminRequest<Record<string, unknown>[]>(token, "/unresolved"),

  replaceHandle: (
    token: string,
    body: { player_id: number; new_handle: string; old_account_id?: number },
  ) => adminRequest(token, "/handles/replace", { method: "POST", body: JSON.stringify(body) }),

  retireHandle: (token: string, accountId: number) =>
    adminRequest(token, `/handles/${accountId}/retire`, { method: "POST" }),

  recordObservation: (token: string, messageId: number, body: ManualObservation) =>
    adminRequest(token, `/candidates/${messageId}/observation`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  dismissCandidate: (token: string, messageId: number) =>
    adminRequest(token, `/candidates/${messageId}/dismiss`, { method: "POST" }),
};
