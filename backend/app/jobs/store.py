"""In-memory job store for background planning and scenario jobs.

Jobs live only in this process's memory: one web worker process must serve both
the POST that creates a job and the GETs that poll it. Jobs are lost when the
process restarts (Step 5 moves job state to Supabase).
"""

import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, Optional

TERMINAL_STATUSES = {"succeeded", "failed"}


def new_job(kind: str, owner_id: str, now: float) -> Dict[str, Any]:
    return {
        "job_id": uuid.uuid4().hex,
        "kind": kind,
        "owner_id": owner_id,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "progress": {"stage": "queued", "done": 0, "total": 0, "percent": 0, "tasks": {}},
        "timings": {},
        "result": None,
        "error": None,
    }


def progress_percent(done: int, total: int) -> int:
    return int(round(100.0 * done / total)) if total else 0


def public_view(job: Dict[str, Any]) -> Dict[str, Any]:
    """What the API returns: everything except owner and internal state."""
    return {k: deepcopy(v) for k, v in job.items() if k not in {"owner_id", "internal"}}


def is_expired(job: Dict[str, Any], now: float, ttl_seconds: float) -> bool:
    return job["status"] in TERMINAL_STATUSES and job["finished_at"] is not None and now - job["finished_at"] > ttl_seconds


class JobStore:
    def __init__(self, ttl_seconds: float):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def _evict(self, now: float) -> None:
        for job_id in [jid for jid, job in self._jobs.items() if is_expired(job, now, self._ttl)]:
            del self._jobs[job_id]

    def create(self, kind: str, owner_id: str, internal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = time.time()
        job = new_job(kind, owner_id, now)
        job["internal"] = internal or {}
        with self._lock:
            self._evict(now)
            self._jobs[job["job_id"]] = job
        return public_view(job)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(changes)

    def set_progress(self, job_id: str, stage: str, done: int, total: int, task_label: Optional[str] = None, task_state: Any = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            tasks = dict(job["progress"].get("tasks", {}))
            if task_label is not None:
                tasks[task_label] = task_state
            job["progress"] = {
                "stage": stage, "done": done, "total": total,
                "percent": progress_percent(done, total), "tasks": tasks,
            }

    def get(self, job_id: str, owner_id: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The job's public view, only for its owner (and matching kind, if given)."""
        with self._lock:
            self._evict(time.time())
            job = self._jobs.get(job_id)
            if job is None or job["owner_id"] != owner_id or (kind and job["kind"] != kind):
                return None
            return public_view(job)

    def internal(self, job_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["owner_id"] != owner_id:
                return None
            return job["internal"]

    def latest(self, owner_id: str, kind: str, status: str) -> Optional[str]:
        with self._lock:
            matches = [j for j in self._jobs.values() if j["owner_id"] == owner_id and j["kind"] == kind and j["status"] == status]
            return max(matches, key=lambda j: j["created_at"])["job_id"] if matches else None
