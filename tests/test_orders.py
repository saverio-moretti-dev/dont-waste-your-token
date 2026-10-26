import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

from ctm import orders

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 1, 2, 3, 4, 5)


def qjob(i, targets, status="queued"):
    return {"id": i, "title": "queue job " + i, "targets": targets, "status": status, "kind": "x"}


class Orders(unittest.TestCase):
    def setUp(self):
        self.box = os.path.join(tempfile.mkdtemp(), "outbox")

    def test_create_file_format(self):
        o, warn = orders.create(self.box, {"text": "Fix the tests\nand more", "targets": "Repo-A, repo-b",
                                           "priority": "3", "job": "J1"}, [], now=NOW)
        self.assertEqual(o["id"], "20260102-030405-fix-the-tests")
        self.assertEqual(warn, [])
        with open(os.path.join(self.box, o["id"] + ".md")) as f:
            self.assertEqual(f.read(), "---\nid: 20260102-030405-fix-the-tests\ncreated: 2026-01-02T03:04:05\n"
                             "status: new\ntargets: [repo-a, repo-b]\npriority: 3\njob: J1\n---\nFix the tests\nand more\n")

    def test_defaults_and_collision(self):
        a, _ = orders.create(self.box, {"text": "Same"}, [], now=NOW)
        b, _ = orders.create(self.box, {"text": "Same"}, [], now=NOW)
        self.assertEqual((a["id"], b["id"]), ("20260102-030405-same", "20260102-030405-same-2"))
        got = orders.list_orders(self.box)
        self.assertEqual([(x["priority"], x["targets"], x["job"]) for x in got], [(5, [], "")] * 2)

    def test_validation(self):
        with self.assertRaises(ValueError):
            orders.create(self.box, {"text": "  "}, [])
        with self.assertRaises(ValueError):
            orders.create(self.box, {"text": "x", "priority": "high"}, [])
        self.assertFalse(os.path.exists(self.box))

    def test_executor_edits_status_and_app_sees_it(self):
        o, _ = orders.create(self.box, {"text": "Do it", "targets": "a"}, [], now=NOW)
        path = os.path.join(self.box, o["id"] + ".md")
        text = open(path).read().replace("status: new", "status: taken")
        open(path, "w").write(text)
        self.assertEqual(orders.list_orders(self.box)[0]["status"], "taken")
        self.assertEqual(orders.list_orders(self.box, "new"), [])
        self.assertEqual(len(orders.list_orders(self.box, "taken")), 1)
        open(path, "w").write(text.replace("taken", "weird"))
        self.assertTrue(orders.list_orders(self.box)[0]["unknown_status"])

    def test_invalid_files_and_missing_dir(self):
        self.assertEqual(orders.list_orders("/nonexistent/dir"), [])
        os.makedirs(self.box)
        open(os.path.join(self.box, "20260101-000000-bad.md"), "w").write("no front matter")
        open(os.path.join(self.box, "notes.txt"), "w").write("ignored")
        got = orders.list_orders(self.box)
        self.assertEqual([(g["id"], g["status"]) for g in got], [("20260101-000000-bad", "invalid")])

    def test_conflicts_with_queue_and_open_orders(self):
        queue = [qjob("J1", ["repo-a"]), qjob("J2", ["other"]), qjob("J3", ["repo-a"], status="done")]
        o, warn = orders.create(self.box, {"text": "Touch repo", "targets": "repo-a"}, queue, now=NOW)
        self.assertEqual([(w["a"], w["b"]) for w in warn], [("NEW", "J1")])
        # a second order on the same target conflicts with the first (open) order
        _, warn2 = orders.create(self.box, {"text": "Another thing", "targets": "repo-a/src"}, [], now=NOW)
        self.assertEqual(len(warn2), 1)
        self.assertEqual(warn2[0]["b"], o["id"])
        # linked job is the same work: no warning against it
        _, warn3 = orders.create(self.box, {"text": "Do J1", "targets": "repo-a", "job": "J1"}, queue[:1], now=NOW)
        self.assertTrue(all(w["b"] != "J1" for w in warn3))

    def test_cli_lists_with_status_filter(self):
        orders.create(self.box, {"text": "First order", "targets": "t"}, [], now=NOW)
        o2, _ = orders.create(self.box, {"text": "Second order", "priority": 2}, [], now=datetime(2026, 1, 2, 3, 4, 6))
        p = os.path.join(self.box, o2["id"] + ".md")
        open(p, "w").write(open(p).read().replace("status: new", "status: done"))
        cfg = os.path.join(os.path.dirname(self.box), "c.json")
        json.dump({"outbox_dir": self.box}, open(cfg, "w"))
        run = lambda *a: subprocess.run([sys.executable, "-m", "ctm", "orders", "--config", cfg] + list(a),
                                        cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(run().strip().splitlines()), 2)
        out = run("--status", "new").strip().splitlines()
        self.assertEqual(len(out), 1)
        self.assertIn("first-order | new | p5 | t | - | First order", out[0])


if __name__ == "__main__":
    unittest.main()
