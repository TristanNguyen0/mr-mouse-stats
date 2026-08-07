// Cognito Hosted UI login, authorization-code flow with PKCE.
//
// PKCE rather than the implicit flow: this is a static site with no backend
// to hold a client secret, and implicit puts tokens in the URL fragment
// where they land in history and referrers. The Cognito app client is a
// public client with no secret.
//
// Tokens live in sessionStorage, so they die with the tab. localStorage
// would survive it, which is a worse default for an admin console.

const REGION = process.env.NEXT_PUBLIC_COGNITO_REGION ?? "";
const DOMAIN = process.env.NEXT_PUBLIC_COGNITO_DOMAIN ?? "";
const CLIENT_ID = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID ?? "";

const TOKEN_KEY = "mms.access_token";
const EXPIRY_KEY = "mms.access_token_expiry";
const VERIFIER_KEY = "mms.pkce_verifier";

export const authConfigured = Boolean(DOMAIN && CLIENT_ID);

function redirectUri(): string {
  return `${window.location.origin}/auth/callback/`;
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  const verifier = base64url(random);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

export async function login(): Promise<void> {
  const { verifier, challenge } = await pkcePair();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    scope: "openid email",
    redirect_uri: redirectUri(),
    code_challenge_method: "S256",
    code_challenge: challenge,
  });
  window.location.assign(`https://${DOMAIN}/oauth2/authorize?${params}`);
}

export async function completeLogin(code: string): Promise<void> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!verifier) throw new Error("missing PKCE verifier — restart the login");
  const response = await fetch(`https://${DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new Error(`token exchange failed: ${response.status}`);
  const body = await response.json();
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.setItem(TOKEN_KEY, body.access_token);
  sessionStorage.setItem(
    EXPIRY_KEY,
    String(Date.now() + (body.expires_in ?? 3600) * 1000),
  );
}

export function accessToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = sessionStorage.getItem(TOKEN_KEY);
  const expiry = Number(sessionStorage.getItem(EXPIRY_KEY) ?? 0);
  if (!token) return null;
  if (Date.now() >= expiry) {
    logout();
    return null;
  }
  return token;
}

export function logout(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(EXPIRY_KEY);
}

export { REGION };
