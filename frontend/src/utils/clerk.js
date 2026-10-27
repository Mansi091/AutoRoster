// Route all relative /api/* calls through the correct backend.
//
// Local dev:   VITE_BACKEND_URL is not set → Vite proxy forwards /api/* to localhost:5000 (or Render)
// Production:  VITE_BACKEND_URL is not set → vercel.json rewrites /api/* to https://autoroster.onrender.com
//
// If VITE_BACKEND_URL is explicitly set (e.g. for staging), it overrides both.
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

export async function authenticatedFetch(url, options = {}, tokenSource) {
  let token = null;

  if (tokenSource) {
    try {
      token = typeof tokenSource === "function" ? await tokenSource() : tokenSource;
      if (!token && typeof tokenSource === "function") {
        token = await tokenSource({ skipCache: true });
      }
    } catch (e) {
      console.warn("Could not retrieve Clerk token:", e);
    }
  }

  // Fallback to active Clerk session on window if available
  if (!token && typeof window !== "undefined" && window.Clerk?.session) {
    try {
      token = await window.Clerk.session.getToken();
    } catch (e) {
      console.warn("Could not retrieve Clerk session token from window:", e);
    }
  }

  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  // Prepend backend URL only if explicitly configured. In production the
  // vercel.json rewrite transparently forwards /api/* to Render.
  const fullUrl = BACKEND_URL && url.startsWith("/") ? `${BACKEND_URL}${url}` : url;

  return fetch(fullUrl, { ...options, headers });
}

export async function fetchRosterHistory(getToken) {
  const response = await authenticatedFetch("/api/history/list", {}, getToken);
  if (!response.ok) throw new Error("Could not load your saved rosters.");
  const payload = await response.json();
  return payload.history || [];
}

export async function recordRosterHistory(record, getToken) {
  try {
    const response = await authenticatedFetch("/api/history/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
    }, getToken);
    if (!response.ok) return null;
    return await response.json();
  } catch (err) {
    console.warn("Could not sync history record with backend:", err);
    return null;
  }
}
