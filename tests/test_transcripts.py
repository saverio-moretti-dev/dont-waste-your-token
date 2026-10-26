import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from ctm import transcripts


def line(mid, ts, i=1, o=2, cc=3, cr=4, typ="assistant"):
    return json.dumps({"type": typ, "timestamp": ts, "message": {"id": mid, "usage": {
        "input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr}}})


NOW = datetime(2026, 1, 10, 12, 0, 0).timestamp()
TS = datetime(2026, 1, 10, 12, 0, 0).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
OLD = datetime(2025, 12, 1, 12, 0, 0).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Parse(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "proj-a"))
        os.makedirs(os.path.join(self.root, "proj-b"))
        self.fa = os.path.join(self.root, "proj-a", "s1.jsonl")
        with open(self.fa, "w") as f:
            f.write(line("m1", TS) + "\n")
            f.write(line("m1", TS, o=20) + "\n")          # streaming duplicate: last wins
            f.write(line("m2", TS) + "\n")
            f.write(line("m3", TS, typ="user") + "\n")      # not assistant
            f.write("not json {\n")
            f.write(json.dumps({"type": "assistant", "timestamp": TS, "message": {"content": "x"}}) + "\n")
            f.write(line("old", OLD) + "\n")
        with open(os.path.join(self.root, "proj-b", "s2.jsonl"), "w") as f:
            f.write(line("m9", TS, i=100, o=0, cc=0, cr=0) + "\n")
        for p in (self.fa, os.path.join(self.root, "proj-b", "s2.jsonl")):
            os.utime(p, (NOW, NOW))

    def test_parse_file_dedupes(self):
        days = transcripts.parse_file(self.fa)
        day = datetime(2026, 1, 10).strftime("%Y-%m-%d")
        self.assertEqual(days[day], [2, 22, 6, 8])  # m1(last)+m2

    def test_scan_by_day_and_project(self):
        r = transcripts.scan(self.root, None, days=7, now=NOW)
        self.assertEqual(len(r["days"]), 1)  # old day excluded
        self.assertEqual(r["days"][0]["input"], 102)
        self.assertEqual({p["name"] for p in r["projects"]}, {"proj-a", "proj-b"})
        self.assertEqual(r["projects"][0]["name"], "proj-b")  # sorted by total

    def test_cache_skips_unchanged(self):
        cache = os.path.join(self.root, "cache.json")
        r1 = transcripts.scan(self.root, cache, days=7, now=NOW)
        r2 = transcripts.scan(self.root, cache, days=7, now=NOW)
        self.assertEqual(r1["files_read"], 2)
        self.assertEqual(r2["files_read"], 0)
        self.assertEqual(r1["days"], r2["days"])
        with open(self.fa, "a") as f:
            f.write(line("m5", TS) + "\n")
        r3 = transcripts.scan(self.root, cache, days=7, now=NOW)
        self.assertEqual(r3["files_read"], 1)

    def test_missing_dir(self):
        r = transcripts.scan("/nonexistent", None)
        self.assertEqual(r["days"], [])


if __name__ == "__main__":
    unittest.main()
