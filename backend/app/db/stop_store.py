"""Pickup stop memory backed by Supabase.

Read order: Supabase -> last copy downloaded from Supabase (local cache file)
-> the stops_seed.json file shipped with the code. Writes go to Supabase with a
change-history row; without Supabase configured they fall back to the seed file
(handled by the stops repository).

A "record" is one roster spelling of a stop:
    {"alias_text", "stop_id", "name", "lat", "lon", "quality", "source"}
"""

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..core.config import config
from ..core.logging import logger

STOPS_TABLE = "pickup_stops"
ALIASES_TABLE = "pickup_stop_aliases"
CHANGES_TABLE = "pickup_stop_changes"
QUALITIES = {"confirmed", "approximate", "conflict"}
NEEDS_LOCATION = "needs_location"  # roster stop that could not be placed on the map yet

_memo: Dict[str, Any] = {"records": None, "source": None, "loaded_at": 0.0}
_memo_lock = threading.Lock()


# ------------------------------------------------------------------ pure helpers

def alias_key(name: Any) -> str:
    """Normalized spelling used to match roster stop names (same rule as the planner)."""
    text = str(name or "").lower()
    text = re.sub(r"\b(chawk|chouk|chowck)\b", "chowk", text)
    text = re.sub(r"\b(phatta|fata)\b", "phata", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _point(coords: Any) -> Optional[Tuple[float, float]]:
    try:
        return float(coords[0]), float(coords[1])
    except (TypeError, ValueError, IndexError):
        return None


def _metres(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def distinct_points(points: List[Tuple[float, float]], same_place_m: float) -> List[Tuple[float, float]]:
    """Points that are more than `same_place_m` apart (first one seen represents each place)."""
    kept: List[Tuple[float, float]] = []
    for point in points:
        if all(_metres(point, other) > same_place_m for other in kept):
            kept.append(point)
    return kept


def nearest_stop(
    records: List[Dict[str, Any]], lat: float, lon: float, within_m: float, exclude_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The closest stored stop within `within_m` metres of a point, if any."""
    best, best_m = None, within_m
    for record in records:
        if not record.get("stop_id") or record["stop_id"] == exclude_id:
            continue
        metres = _metres((lat, lon), (float(record["lat"]), float(record["lon"])))
        if metres <= best_m:
            best, best_m = record, metres
    return best


def records_from_seed(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One record per seed-file spelling (all approximate)."""
    records = []
    for name, coords in seed.items():
        point = _point(coords)
        if point and alias_key(name):
            records.append({
                "alias_text": str(name).strip(), "stop_id": None, "name": str(name).strip(),
                "lat": point[0], "lon": point[1], "quality": "approximate", "source": "seed file",
            })
    return records


def group_seed_for_import(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group seed spellings into one stop per normalized name.

    The chosen point is the one the planner used before the import (the last
    spelling in file order wins), so routes do not change. Groups whose
    spellings were saved at different points are marked 'conflict' and keep
    every point in `candidates` for a person to choose from.
    """
    groups: Dict[str, List[Tuple[str, Tuple[float, float]]]] = {}
    for name, coords in seed.items():
        point, key = _point(coords), alias_key(name)
        if point and key:
            groups.setdefault(key, []).append((str(name).strip(), point))
    stops = []
    for spellings in groups.values():
        chosen_name, chosen_point = spellings[-1]
        distinct = distinct_points([point for _, point in spellings], config.STOP_SAME_PLACE_METERS)
        stops.append({
            "name": chosen_name,
            "lat": chosen_point[0],
            "lon": chosen_point[1],
            "quality": "conflict" if len(distinct) > 1 else "approximate",
            "source": "imported from stops_seed.json",
            "candidates": [
                {"lat": p[0], "lon": p[1],
                 "spellings": [n for n, q in spellings if _metres(q, p) <= config.STOP_SAME_PLACE_METERS]}
                for p in distinct
            ] if len(distinct) > 1 else [],
            "aliases": list(dict.fromkeys(n for n, _ in spellings)),
        })
    return stops


def records_from_supabase_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten stops (each with its aliases) into one record per spelling.

    Stops still waiting for a location are left out: they cannot be routed.
    """
    records = []
    for row in rows:
        if row.get("lat") is None or row.get("lon") is None:
            continue
        base = {
            "stop_id": row.get("id"), "name": row.get("name") or "",
            "lat": float(row["lat"]), "lon": float(row["lon"]),
            "quality": row.get("quality") or "approximate", "source": row.get("source") or "",
        }
        spellings = [a.get("alias_text") for a in row.get(ALIASES_TABLE) or [] if a.get("alias_text")]
        for spelling in spellings or [base["name"]]:
            records.append({**base, "alias_text": spelling})
    return records


# --------------------------------------------------------------- supabase access

def supabase_settings() -> Optional[Tuple[str, str]]:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_KEY", "").strip()
    return (url, key) if url and key else None


def _headers(key: str, prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _rest(method: str, table: str, *, params=None, body=None, prefer=None) -> Any:
    settings = supabase_settings()
    if not settings:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).")
    url, key = settings
    response = requests.request(
        method, f"{url}/rest/v1/{table}", params=params, json=body,
        headers=_headers(key, prefer), timeout=config.STOP_STORE_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase {method} {table} failed: HTTP {response.status_code} {response.text[:300]}")
    return response.json() if response.content else None


def fetch_supabase_rows() -> List[Dict[str, Any]]:
    rows, start, page = [], 0, 1000
    while True:
        chunk = _rest("GET", STOPS_TABLE, params={
            "select": f"id,name,lat,lon,quality,source,{ALIASES_TABLE}(alias_key,alias_text)",
            "order": "created_at.asc", "offset": start, "limit": page,
        }) or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


# ------------------------------------------------------------------- read path

def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(records: List[Dict[str, Any]]) -> None:
    path = config.STOP_STORE_CACHE_FILE
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "records": records}, f)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(f"Could not write stop cache: {exc}")
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_uncached() -> Tuple[List[Dict[str, Any]], str]:
    if supabase_settings():
        try:
            rows = fetch_supabase_rows()
            if rows:
                records = records_from_supabase_rows(rows)
                _write_cache(records)
                return records, "supabase"
            logger.warning("Supabase pickup_stops is empty; using local stop list. Run scripts/import_stops_to_supabase.py.")
        except Exception as exc:
            logger.warning(f"Supabase stop store unavailable ({exc}); using last saved copy.")
        if config.STOP_STORE_CACHE_FILE.exists():
            try:
                return list(_read_json(config.STOP_STORE_CACHE_FILE)["records"]), "local copy of supabase"
            except Exception as exc:
                logger.warning(f"Stop cache unreadable ({exc}); using seed file.")
    if config.MASTER_STOPS_FILE.exists():
        try:
            return records_from_seed(_read_json(config.MASTER_STOPS_FILE)), "seed file"
        except Exception as exc:
            logger.error(f"Could not read stop seed file: {exc}")
    return [], "none"


def load_stop_records(force: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """All stop spellings and where they were read from (memoized for a short time)."""
    with _memo_lock:
        fresh = time.time() - _memo["loaded_at"] < config.STOP_STORE_TTL_SECONDS
        if not force and _memo["records"] is not None and fresh:
            return list(_memo["records"]), _memo["source"]
        records, source = _load_uncached()
        _memo.update(records=records, source=source, loaded_at=time.time())
        return list(records), source


def invalidate() -> None:
    with _memo_lock:
        _memo.update(records=None, loaded_at=0.0)


# ------------------------------------------------------------------ write path

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_change(stop_id: Optional[str], action: str, old: Any, new: Any, changed_by: str) -> None:
    _rest("POST", CHANGES_TABLE, body={
        "stop_id": stop_id, "action": action, "old_value": old, "new_value": new, "changed_by": changed_by or "",
    }, prefer="return=minimal")


def _find_stop_by_spelling(key: str) -> Optional[Dict[str, Any]]:
    rows = _rest("GET", ALIASES_TABLE, params={
        "alias_key": f"eq.{key}", "select": f"stop_id,{STOPS_TABLE}(id,name,lat,lon,quality,source)",
    }) or []
    return rows[0][STOPS_TABLE] if rows and rows[0].get(STOPS_TABLE) else None


def _has_location(stop: Dict[str, Any]) -> bool:
    return stop.get("lat") is not None and stop.get("lon") is not None


def _confirm_fields(quality: str, changed_by: str) -> Dict[str, Any]:
    return {"confirmed_by": changed_by or None, "confirmed_at": _now(), "candidates": []} if quality == "confirmed" else {}


def _update_stop(stop: Dict[str, Any], lat: float, lon: float, quality: str, source: str, changed_by: str, action: str) -> None:
    new_value = {"lat": lat, "lon": lon, "quality": quality, "source": source}
    old_value = {k: stop.get(k) for k in ("lat", "lon", "quality", "source")}
    _rest("PATCH", STOPS_TABLE, params={"id": f"eq.{stop['id']}"},
          body={**new_value, "updated_at": _now(), **_confirm_fields(quality, changed_by)}, prefer="return=minimal")
    _log_change(stop["id"], action, old_value, new_value, changed_by)


def _link_spelling(near: Dict[str, Any], spelling: str, lat: float, lon: float, quality: str, source: str,
                   changed_by: str, placeholder_id: Optional[str] = None) -> Dict[str, Any]:
    """Attach a spelling to an existing nearby stop (moving it off a placeholder if needed).

    A person's pin upgrades a nearby unchecked stop to confirmed at the pinned point.
    """
    key = alias_key(spelling)
    if placeholder_id:
        _rest("PATCH", ALIASES_TABLE, params={"stop_id": f"eq.{placeholder_id}"},
              body={"stop_id": near["stop_id"]}, prefer="return=minimal")
        _rest("DELETE", STOPS_TABLE, params={"id": f"eq.{placeholder_id}"}, prefer="return=minimal")
    else:
        _rest("POST", ALIASES_TABLE, body={"alias_key": key, "alias_text": spelling.strip(), "stop_id": near["stop_id"]},
              prefer="resolution=ignore-duplicates,return=minimal")
    _log_change(near["stop_id"], "alias_added", None,
                {"spelling": spelling.strip(), "searched_point": {"lat": lat, "lon": lon}, "source": source}, changed_by)
    if quality == "confirmed" and near.get("quality") != "confirmed":
        _update_stop({"id": near["stop_id"], **{k: near.get(k) for k in ("lat", "lon", "quality", "source")}},
                     lat, lon, "confirmed", source, changed_by, "confirmed")
    invalidate()
    return {"stop_id": near["stop_id"], "action": "linked"}


def save_stop_to_supabase(
    spelling: str, lat: float, lon: float, *, quality: str, source: str, changed_by: str = ""
) -> Dict[str, Any]:
    """Save where a roster spelling is; every change is logged.

    - spelling already belongs to a placed stop: move/confirm it (a confirmed stop is
      never downgraded by an automatic save);
    - otherwise, if a stored stop is within STOP_SAME_PLACE_METERS, the spelling is
      linked to it instead of creating a duplicate;
    - a 'needs location' entry for the spelling gets this point;
    - else a new stop is created.
    Returns {"stop_id", "action"}.
    """
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {sorted(QUALITIES)}")
    key = alias_key(spelling)
    if not key:
        raise ValueError("Stop name is empty.")
    stop = _find_stop_by_spelling(key)

    if stop and _has_location(stop):
        if stop.get("quality") == "confirmed" and quality != "confirmed":
            return {"stop_id": stop["id"], "action": "kept_confirmed"}
        moved = (float(stop["lat"]), float(stop["lon"])) != (lat, lon)
        action = "moved" if moved else ("confirmed" if quality == "confirmed" else "updated")
        _update_stop(stop, lat, lon, quality, source, changed_by, action)
        invalidate()
        return {"stop_id": stop["id"], "action": action}

    records, _ = load_stop_records()
    near = nearest_stop(records, lat, lon, config.STOP_SAME_PLACE_METERS, exclude_id=stop["id"] if stop else None)
    if near:
        return _link_spelling(near, spelling, lat, lon, quality, source, changed_by,
                              placeholder_id=stop["id"] if stop else None)

    if stop:  # was waiting for a location
        _update_stop(stop, lat, lon, quality, source, changed_by, "located")
        invalidate()
        return {"stop_id": stop["id"], "action": "located"}

    body = {"name": spelling.strip(), "lat": lat, "lon": lon, "quality": quality, "source": source,
            **_confirm_fields(quality, changed_by)}
    body.pop("candidates", None)
    created = _rest("POST", STOPS_TABLE, body=body, prefer="return=representation")
    stop_id = created[0]["id"]
    _rest("POST", ALIASES_TABLE, body={"alias_key": key, "alias_text": spelling.strip(), "stop_id": stop_id},
          prefer="resolution=ignore-duplicates,return=minimal")
    _log_change(stop_id, "created", None, {"lat": lat, "lon": lon, "quality": quality, "source": source}, changed_by)
    invalidate()
    return {"stop_id": stop_id, "action": "created"}


def mark_needs_location(spelling: str, reason: str, changed_by: str = "automatic") -> Dict[str, Any]:
    """Remember a roster stop that could not be placed, so it stays on the to-do list.

    Does nothing to a stop that already has a location. Returns {"stop_id", "action"}.
    """
    key = alias_key(spelling)
    if not key:
        raise ValueError("Stop name is empty.")
    stop = _find_stop_by_spelling(key)
    if stop and _has_location(stop):
        return {"stop_id": stop["id"], "action": "has_location"}
    if stop:
        _rest("PATCH", STOPS_TABLE, params={"id": f"eq.{stop['id']}"},
              body={"source": reason, "updated_at": _now()}, prefer="return=minimal")
        return {"stop_id": stop["id"], "action": "still_needed"}
    created = _rest("POST", STOPS_TABLE, body={
        "name": spelling.strip(), "lat": None, "lon": None, "quality": NEEDS_LOCATION, "source": reason,
    }, prefer="return=representation")
    stop_id = created[0]["id"]
    _rest("POST", ALIASES_TABLE, body={"alias_key": key, "alias_text": spelling.strip(), "stop_id": stop_id},
          prefer="resolution=ignore-duplicates,return=minimal")
    _log_change(stop_id, "needs_location", None, {"reason": reason}, changed_by)
    return {"stop_id": stop_id, "action": "needs_location"}
