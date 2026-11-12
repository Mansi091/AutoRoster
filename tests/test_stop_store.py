"""Pickup stop memory: read order, import grouping, saves with history (offline fake Supabase)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import config  # noqa: E402
from backend.app.db import stop_store  # noqa: E402


class FakeSupabase:
    """Just enough of PostgREST for stop_store: three tables in memory."""

    def __init__(self):
        self.tables = {stop_store.STOPS_TABLE: [], stop_store.ALIASES_TABLE: [], stop_store.CHANGES_TABLE: []}
        self.next_id = 1

    def __call__(self, method, table, *, params=None, body=None, prefer=None):
        params = params or {}
        rows = self.tables[table]
        if method == "GET" and table == stop_store.ALIASES_TABLE and "alias_key" in params:
            key = params["alias_key"].removeprefix("eq.")
            return [{"stop_id": a["stop_id"], stop_store.STOPS_TABLE: self._stop(a["stop_id"])}
                    for a in rows if a["alias_key"] == key]
        if method == "GET" and table == stop_store.STOPS_TABLE:
            return [dict(s, **{stop_store.ALIASES_TABLE: [a for a in self.tables[stop_store.ALIASES_TABLE] if a["stop_id"] == s["id"]]})
                    for s in rows]
        if method == "POST":
            new = [dict(r) for r in (body if isinstance(body, list) else [body])]
            for r in new:
                if table == stop_store.STOPS_TABLE and "id" not in r:
                    r["id"] = f"stop-{self.next_id}"
                    self.next_id += 1
            rows.extend(new)
            return new
        if method == "PATCH":
            (field, value), = params.items()
            for row in rows:
                if row.get(field) == value.removeprefix("eq."):
                    row.update(body)
            return None
        if method == "DELETE":
            (field, value), = params.items()
            self.tables[table] = [r for r in rows if r.get(field) != value.removeprefix("eq.")]
            return None
        raise AssertionError(f"unexpected {method} {table} {params}")

    def _stop(self, stop_id):
        return next(s for s in self.tables[stop_store.STOPS_TABLE] if s["id"] == stop_id)


class StopStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cache = tmp / "cache.json"
        self.seed = tmp / "seed.json"
        self.seed.write_text(json.dumps({"Shastri Chowk": [18.62, 73.85], "Moshi": [18.67, 73.84]}), encoding="utf-8")
        self.fake = FakeSupabase()
        self.patches = [
            mock.patch.object(config, "STOP_STORE_CACHE_FILE", self.cache),
            mock.patch.object(config, "MASTER_STOPS_FILE", self.seed),
            mock.patch.object(stop_store, "_rest", self.fake),
        ]
        for p in self.patches:
            p.start()
        stop_store.invalidate()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        stop_store.invalidate()
        self.tmp.cleanup()

    def configured(self, yes=True):
        return mock.patch.object(stop_store, "supabase_settings", return_value=("https://x", "k") if yes else None)

    # ---- grouping for import
    def test_import_groups_spellings_and_flags_conflicts(self):
        seed = {"Shastri Chowk": [1.0, 2.0], "shastri chawk": [1.0, 2.0], "SHASTRI CHOWK,": [3.0, 4.0], "Moshi": [5.0, 6.0]}
        groups = {g["name"]: g for g in stop_store.group_seed_for_import(seed)}
        self.assertEqual(len(groups), 2)
        shastri = groups["SHASTRI CHOWK,"]
        self.assertEqual((shastri["lat"], shastri["lon"]), (3.0, 4.0), "last spelling wins, as the planner did")
        self.assertEqual(shastri["quality"], "conflict")
        self.assertEqual(len(shastri["candidates"]), 2)
        self.assertEqual(groups["Moshi"]["quality"], "approximate")

    # ---- read order
    def test_reads_seed_when_supabase_not_configured(self):
        with self.configured(False):
            records, source = stop_store.load_stop_records(force=True)
        self.assertEqual(source, "seed file")
        self.assertEqual({r["alias_text"] for r in records}, {"Shastri Chowk", "Moshi"})

    def test_reads_supabase_and_saves_local_copy(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Bharat Mata Chowk", 18.66, 73.85, quality="approximate", source="t")
            records, source = stop_store.load_stop_records(force=True)
        self.assertEqual(source, "supabase")
        self.assertEqual([r["alias_text"] for r in records], ["Bharat Mata Chowk"])
        self.assertTrue(self.cache.exists())

    def test_falls_back_to_local_copy_when_supabase_down(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Bharat Mata Chowk", 18.66, 73.85, quality="approximate", source="t")
            stop_store.load_stop_records(force=True)
            with mock.patch.object(stop_store, "fetch_supabase_rows", side_effect=RuntimeError("paused")):
                records, source = stop_store.load_stop_records(force=True)
        self.assertEqual(source, "local copy of supabase")
        self.assertEqual(records[0]["alias_text"], "Bharat Mata Chowk")

    def test_falls_back_to_seed_when_supabase_down_and_no_copy(self):
        with self.configured(), mock.patch.object(stop_store, "fetch_supabase_rows", side_effect=RuntimeError("down")):
            _, source = stop_store.load_stop_records(force=True)
        self.assertEqual(source, "seed file")

    # ---- saves
    def test_new_spelling_creates_stop_alias_and_history(self):
        with self.configured():
            out = stop_store.save_stop_to_supabase("Ankul Chowk", 18.7, 73.8, quality="confirmed", source="pin", changed_by="sup@x")
        self.assertEqual(out["action"], "created")
        stop = self.fake.tables[stop_store.STOPS_TABLE][0]
        self.assertEqual((stop["quality"], stop["confirmed_by"]), ("confirmed", "sup@x"))
        self.assertEqual(self.fake.tables[stop_store.ALIASES_TABLE][0]["alias_key"], stop_store.alias_key("Ankul Chowk"))
        self.assertEqual(self.fake.tables[stop_store.CHANGES_TABLE][0]["action"], "created")

    def test_moving_a_stop_logs_old_and_new_point(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Ankul Chowk", 18.7, 73.8, quality="approximate", source="search")
            out = stop_store.save_stop_to_supabase("ankul chawk", 18.71, 73.81, quality="confirmed", source="pin", changed_by="sup@x")
        self.assertEqual(out["action"], "moved")
        change = self.fake.tables[stop_store.CHANGES_TABLE][-1]
        self.assertEqual((change["old_value"]["lat"], change["new_value"]["lat"]), (18.7, 18.71))
        self.assertEqual(self.fake.tables[stop_store.STOPS_TABLE][0]["quality"], "confirmed")

    def test_automatic_save_never_overwrites_confirmed_stop(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Ankul Chowk", 18.7, 73.8, quality="confirmed", source="pin", changed_by="sup@x")
            out = stop_store.save_stop_to_supabase("Ankul Chowk", 18.9, 73.9, quality="approximate", source="search")
        self.assertEqual(out["action"], "kept_confirmed")
        stop = self.fake.tables[stop_store.STOPS_TABLE][0]
        self.assertEqual((stop["lat"], stop["lon"], stop["quality"]), (18.7, 73.8, "confirmed"))

    # ---- gap 2: new spelling near an existing stop is linked, not duplicated
    def test_new_spelling_within_same_place_distance_is_linked(self):
        with self.configured():
            first = stop_store.save_stop_to_supabase("Bharat Mata Chowk Moshi", 18.6677, 73.8504, quality="approximate", source="s")
            out = stop_store.save_stop_to_supabase("Bharat Mata Chk., Moshi", 18.6679, 73.8505, quality="approximate", source="s")
        self.assertEqual(out, {"stop_id": first["stop_id"], "action": "linked"})
        self.assertEqual(len(self.fake.tables[stop_store.STOPS_TABLE]), 1)
        self.assertEqual(len(self.fake.tables[stop_store.ALIASES_TABLE]), 2)

    def test_far_new_spelling_creates_new_stop(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Moshi", 18.6677, 73.8504, quality="approximate", source="s")
            out = stop_store.save_stop_to_supabase("Chakan", 18.76, 73.86, quality="approximate", source="s")
        self.assertEqual(out["action"], "created")
        self.assertEqual(len(self.fake.tables[stop_store.STOPS_TABLE]), 2)

    def test_pin_near_unchecked_stop_links_and_confirms_it(self):
        with self.configured():
            first = stop_store.save_stop_to_supabase("Moshi Chowk", 18.6677, 73.8504, quality="approximate", source="s")
            stop_store.save_stop_to_supabase("moshi chouk naka", 18.6678, 73.8503, quality="confirmed", source="pin", changed_by="sup@x")
        stop = self.fake.tables[stop_store.STOPS_TABLE][0]
        self.assertEqual((stop["id"], stop["quality"], stop["lat"]), (first["stop_id"], "confirmed", 18.6678))

    # ---- gap 1: stops that could not be placed are remembered
    def test_unplaced_stop_is_remembered_once_and_not_routed(self):
        with self.configured():
            a = stop_store.mark_needs_location("Rahul Marbal Dehugaon", "search disagreed")
            b = stop_store.mark_needs_location("rahul marbal, dehugaon", "search disagreed again")
            records, _ = stop_store.load_stop_records(force=True)
        self.assertEqual((a["action"], b["action"]), ("needs_location", "still_needed"))
        self.assertEqual(len(self.fake.tables[stop_store.STOPS_TABLE]), 1)
        self.assertEqual(self.fake.tables[stop_store.STOPS_TABLE][0]["quality"], stop_store.NEEDS_LOCATION)
        self.assertEqual(records, [], "a stop without a location is never routed")

    def test_needs_location_entry_gets_located_later(self):
        with self.configured():
            waiting = stop_store.mark_needs_location("Rahul Marbal Dehugaon", "search disagreed")
            out = stop_store.save_stop_to_supabase("Rahul Marbal Dehugaon", 18.72, 73.766, quality="confirmed", source="pin", changed_by="sup@x")
        self.assertEqual(out, {"stop_id": waiting["stop_id"], "action": "located"})
        stop = self.fake.tables[stop_store.STOPS_TABLE][0]
        self.assertEqual((stop["quality"], stop["lat"]), ("confirmed", 18.72))

    def test_needs_location_entry_near_existing_stop_is_merged(self):
        with self.configured():
            existing = stop_store.save_stop_to_supabase("Dehugaon HDFC ATM", 18.7208, 73.766, quality="approximate", source="s")
            stop_store.mark_needs_location("HDFC Bank Dehu", "search disagreed")
            out = stop_store.save_stop_to_supabase("HDFC Bank Dehu", 18.7209, 73.7661, quality="approximate", source="s")
        self.assertEqual(out, {"stop_id": existing["stop_id"], "action": "linked"})
        self.assertEqual([s["id"] for s in self.fake.tables[stop_store.STOPS_TABLE]], [existing["stop_id"]])
        self.assertTrue(all(a["stop_id"] == existing["stop_id"] for a in self.fake.tables[stop_store.ALIASES_TABLE]))

    def test_marking_a_placed_stop_changes_nothing(self):
        with self.configured():
            stop_store.save_stop_to_supabase("Moshi", 18.6677, 73.8504, quality="approximate", source="s")
            out = stop_store.mark_needs_location("Moshi", "search failed")
        self.assertEqual(out["action"], "has_location")
        self.assertEqual(self.fake.tables[stop_store.STOPS_TABLE][0]["quality"], "approximate")

    def test_alias_key_matches_planner_rule(self):
        self.assertEqual(stop_store.alias_key("Shastri Chawk, Bhosari"), stop_store.alias_key("shastri chowk bhosari"))
        self.assertEqual(stop_store.alias_key("Vadgaon Fata"), stop_store.alias_key("VADGAON PHATA"))


if __name__ == "__main__":
    unittest.main()
