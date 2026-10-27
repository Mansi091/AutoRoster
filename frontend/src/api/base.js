const configuredApiBaseUrl = String(import.meta.env.VITE_API_BASE_URL || "")
  .trim()
  .replace(/\/+$/, "");

export function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;

  if (import.meta.env.PROD && !configuredApiBaseUrl) {
    throw new Error(
      "The backend URL is missing from this deployment. Set VITE_API_BASE_URL in Vercel and redeploy.",
    );
  }

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return configuredApiBaseUrl ? `${configuredApiBaseUrl}${normalizedPath}` : normalizedPath;
}

export async function readApiJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json") && !contentType.includes("+json")) {
    throw new Error(
      `The API returned a non-JSON response (HTTP ${response.status}). Check the backend URL and CORS settings.`,
    );
  }

  try {
    return await response.json();
  } catch {
    throw new Error(`The API returned invalid JSON (HTTP ${response.status}).`);
  }
}
