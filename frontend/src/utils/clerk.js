import { apiUrl, readApiJson } from "../api/base";

export async function authenticatedFetch(url, options = {}, tokenSource) {
  const token = typeof tokenSource === "function" ? await tokenSource() : tokenSource;
  if (!token) throw new Error("Your Clerk session has expired. Please sign in again.");

  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(apiUrl(url), { ...options, headers });
}

export async function fetchRosterHistory(getToken) {
  const response = await authenticatedFetch("/api/history/list", {}, getToken);
  if (!response.ok) throw new Error("Could not load your saved rosters.");
  const payload = await readApiJson(response);
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
