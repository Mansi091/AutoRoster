export async function authenticatedFetch(url, options = {}, tokenSource) {
  let token = null;
  if (tokenSource) {
    try {
      token = typeof tokenSource === "function" ? await tokenSource() : tokenSource;
    } catch (e) {
      console.warn("Could not retrieve Clerk token:", e);
    }
  }

  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(url, { ...options, headers });
}

export async function fetchRosterHistory(getToken) {
  const response = await authenticatedFetch("/api/history/list", {}, getToken);
  if (!response.ok) throw new Error("Could not load your saved rosters.");
  const payload = await response.json();
  return payload.history || [];
}

export async function recordRosterHistory(record, getToken) {
  const response = await authenticatedFetch("/api/history/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(record),
  }, getToken);
  if (!response.ok) throw new Error("The plan was created, but its private history could not be saved.");
}
