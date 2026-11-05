// Background job helpers: start a job, then poll it until it succeeds or fails.
import { authenticatedFetch } from "./clerk";

const POLL_INTERVAL_MS = 1500;
const WAKE_RETRY_DELAY_MS = 3000;
const MAX_TRANSIENT_POLL_ERRORS = 5;
const TRANSIENT_STATUSES = new Set([502, 503, 504]);

function abortError() {
  return new DOMException("Aborted", "AbortError");
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(abortError());
    }, { once: true });
  });
}

async function readJson(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(
      res.ok
        ? "Invalid server response."
        : `Backend service error (HTTP ${res.status}). If the server was sleeping, please retry in a few seconds.`
    );
  }
}

// POST that creates a job. Retries once if the backend is still waking up.
export async function startJob(url, options, getToken, onWaking) {
  let res;
  for (let attempt = 1; attempt <= 2; attempt++) {
    res = await authenticatedFetch(url, options, getToken);
    if (TRANSIENT_STATUSES.has(res.status) && attempt === 1) {
      onWaking?.();
      await wait(WAKE_RETRY_DELAY_MS, options?.signal);
      continue;
    }
    break;
  }
  const data = await readJson(res);
  if (!res.ok || !data.job_id) {
    throw new Error(data.error || data.detail || `Server returned HTTP ${res.status}`);
  }
  return data;
}

// Poll a job until it finishes. Resolves with job.result; rejects with the job error.
export async function pollJob(pollUrl, getToken, { signal, onProgress } = {}) {
  let transientErrors = 0;
  for (;;) {
    const res = await authenticatedFetch(pollUrl, { signal }, getToken);
    if (TRANSIENT_STATUSES.has(res.status) && transientErrors < MAX_TRANSIENT_POLL_ERRORS) {
      transientErrors += 1;
      await wait(POLL_INTERVAL_MS, signal);
      continue;
    }
    const job = await readJson(res);
    if (!res.ok) throw new Error(job.error || job.detail || `Server returned HTTP ${res.status}`);
    transientErrors = 0;
    onProgress?.(job);
    if (job.status === "succeeded") return job.result;
    if (job.status === "failed") throw new Error(job.error || "The job failed.");
    await wait(POLL_INTERVAL_MS, signal);
  }
}
