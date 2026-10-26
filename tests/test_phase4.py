"""Regression tests for the findings of AUDIT-2026-10-14.md (each failed before the fix)."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone

from ctm import config, jobs, limits, orders, report, transcripts

HERE = os.path.dirname(__file__)


def done(kind, model, est, act, w="5h"):
    return {"status": "done", "measure": "clean", "kind": kind, "model": model,
            "estimate_pct_" + w: est, "actual_pct_" + w: act}


class Tz:
    """Context manager: run with a given TZ. time.tzset() exists only on Unix: on Windows the test is skipped."""
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        if not hasattr(time, "tzset"):
            raise unittest.SkipTest("time.tzset() not available on this platform (Windows)")
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = self.name
        time.tzset()

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        time.tzset()


class QueueIntegrity(unittest.TestCase):  # audit 1, 2
    def setUp(self):
        self.q = jobs.Queue(tempfile.mkdtemp())

    def test_corrupt_file_is_not_overwritten(self):
        self.q.add({"title": "A"})
        raw = open(self.q.path).read()
        open(self.q.path, "w").write(raw + "\x00garbage")
        with self.assertRaises(jobs.QueueCorrupt):
            self.q.load()
        with self.assertRaises(jobs.QueueCorrupt):
            self.q.add({"title": "B"})
        self.assertEqual(open(self.q.path).read(), raw + "\x00garbage")

    def test_missing_file_is_empty(self):
        self.assertEqual(self.q.load(), [])

    def test_backup_of_last_good_version(self):
        self.q.add({"title": "A"})
        self.q.add({"title": "B"})
        bak = json.load(open(self.q.path + ".bak"))
        self.assertEqual([j["title"] for j in bak["jobs"]], ["A"])

    def test_parallel_adds_are_not_lost(self):
        errs = []

        def work():
            try:
                self.q.add({"title": "x"})
            except Exception as e:  # noqa
                errs.append(e)
        ts = [threading.Thread(target=work) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        ids = [j["id"] for j in self.q.load()]
        self.assertEqual((len(ids), len(set(ids))), (20, 20))

    def test_parallel_orders_unique_files(self):
        box = os.path.join(tempfile.mkdtemp(), "outbox")
        now = datetime(2026, 1, 2, 3, 4, 5)
        res, errs = [], []

        def work():
            try:
                res.append(orders.create(box, {"text": "same text"}, [], now=now)[0]["id"])
            except Exception as e:  # noqa
                errs.append(e)
        ts = [threading.Thread(target=work) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(set(res)), 10)
        self.assertEqual(len(os.listdir(box)), 10)


class CalibrationRobust(unittest.TestCase):  # audit 3
    def test_zero_actuals_never_give_zero_estimate(self):
        js = [done("research", "s", 3, 0), done("research", "s", 2, 0)]
        s = jobs.suggest(js, "research", "s", est_5h=40)
        self.assertGreater(s["5h"]["corrected"], 0)

    def test_weighted_by_size_not_median_of_ratios(self):
        js = [done("a", "m", 1, 3), done("a", "m", 40, 20)]
        s = jobs.suggest(js, "a", "m", est_5h=40)
        self.assertLess(s["5h"]["corrected"], 40)     # median of ratios would give 70
        self.assertAlmostEqual(s["5h"]["ratio"], 23 / 41, places=2)

    def test_range_is_reported(self):
        js = [done("a", "m", 10, 2), done("a", "m", 10, 6)]
        s = jobs.suggest(js, "a", "m", est_5h=10)["5h"]
        self.assertEqual(s["range"], [2.0, 6.0])

    def test_tiny_unmeasurable_samples_are_excluded(self):
        js = [done("a", "m", 1, 0), done("a", "m", 2, 0)]   # below the resolution of integer percentages
        self.assertEqual(jobs.suggest(js, "a", "m", est_5h=10)["5h"]["basis"], "none")

    def test_very_short_run_is_marked_mixed(self):
        q = jobs.Queue(tempfile.mkdtemp())
        j = q.add({"title": "t", "estimate_pct_5h": 10})
        q.start(j["id"], at="2026-01-01 10:00:00")
        self.assertEqual(q.finish(j["id"], [], at="2026-01-01 10:00:30")["measure"], "mixed")


class StartAndStale(unittest.TestCase):  # audit 8, 9
    def test_start_sets_running_and_cancel_clears_it(self):
        q = jobs.Queue(tempfile.mkdtemp())
        a, b = q.add({"title": "A"}), q.add({"title": "B"})
        self.assertEqual(q.start(a["id"], at="2026-01-01 10:00:00")["status"], "running")
        q.set_status(a["id"], "cancelled")
        self.assertIsNone(q.load()[0]["started_at"])
        q.start(b["id"], at="2026-01-01 11:00:00")
        self.assertEqual(q.finish(b["id"], [], at="2026-01-01 12:00:00")["measure"], "clean")

    def test_margin_after_weekly_reset_is_full(self):
        now = datetime(2026, 1, 8, 12, 0).timestamp()
        rows = [{"t": now - 7200, "p5": 10.0, "p7": 85.0, "r5": now - 100, "r7": now - 3600}]
        m, note = report.margin(limits.summary(rows, now=now))
        self.assertEqual(m, 100.0)
        self.assertIn("finestra precedente", note)


class Validation(unittest.TestCase):  # audit 10, 21
    def test_non_finite_and_negative_rejected(self):
        q = jobs.Queue(tempfile.mkdtemp())
        for bad in ("nan", "inf", "-5", "1e999"):
            with self.assertRaises(ValueError):
                q.add({"title": "n", "estimate_pct_5h": bad})
        self.assertEqual(q.load(), [])

    def test_nan_in_csv_is_missing(self):
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        open(p, "w").write("ora,a,b,c,d\n2026-01-01 10:00:00,nan,inf,,\n")
        r = limits.parse_csv(p)[0]
        self.assertIsNone(r["p5"])
        self.assertIsNone(r["p7"])

    def test_invalid_utf8_csv(self):
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        open(p, "wb").write(b"ora,a,b,c,d\n2026-01-01 10:00:00,5,6,,\n\xff\xfe\n")
        self.assertEqual(len(limits.parse_csv(p)), 1)

    def test_config_types(self):
        d = tempfile.mkdtemp()

        def load(obj):
            p = os.path.join(d, "c.json")
            json.dump(obj, open(p, "w"))
            return config.load(p)
        self.assertEqual(load({"port": 9000})["port"], 9000)
        for bad in ({"port": "8765"}, {"markdown_status_keywords": {"done": "shipped"}},
                    {"markdown_overrides": {"L1": 5}}, {"markdown_overrides": {"L1": {"estimate_pct_7d": "nan"}}}):
            with self.assertRaises(ValueError):
                load(bad)


class Injection(unittest.TestCase):  # audit 6
    def test_targets_cannot_inject_front_matter(self):
        box = os.path.join(tempfile.mkdtemp(), "outbox")
        o, _ = orders.create(box, {"text": "x", "targets": ["a]\nstatus: done\nz: [b"]}, [])
        self.assertEqual(orders.list_orders(box)[0]["status"], "new")
        self.assertEqual(open(os.path.join(box, o["file"])).read().count("status:"), 1)
        o2, _ = orders.create(box, {"text": "y", "targets": "p\n---\nbody"}, [])
        got = [x for x in orders.list_orders(box) if x["id"] == o2["id"]][0]
        self.assertEqual(got["text"], "y")

    def test_duplicate_keys_are_invalid(self):
        self.assertEqual(orders.parse("---\nid: x\nstatus: new\nstatus: done\n---\nt")["status"], "invalid")

    def test_split_targets_strips_control_chars(self):
        self.assertNotIn("\n", "".join(jobs.split_targets(["a\nb", "c]d", "e:f"])))


class OutboxFilter(unittest.TestCase):
    def test_only_dated_md_files_are_orders(self):
        box = tempfile.mkdtemp()
        for fn in ("LEGGIMI.md", "LEGGIMI.txt", "notes.md", "2026010-000000-x.md"):
            open(os.path.join(box, fn), "w").write("---\nid: x\nstatus: new\n---\nt")
        open(os.path.join(box, "20260101-000000-ok.md"), "w").write("---\nid: ok\nstatus: new\n---\nt")
        self.assertEqual([o["id"] for o in orders.list_orders(box)], ["ok"])


class TimeParsing(unittest.TestCase):  # audit 7, 25
    def test_iso_with_offset(self):
        want = datetime(2026, 10, 14, 17, 35, 22, tzinfo=timezone.utc).timestamp()
        for s in ("2026-10-14T19:35:22+0200", "2026-10-14T19:35:22+02:00", "2026-10-14T17:35:22Z"):
            self.assertEqual(limits.parse_time(s), want)

    def test_legacy_format_is_local_time(self):
        with Tz("Europe/Rome"):
            self.assertEqual(limits.parse_time("2026-07-01 12:00:00"),
                             datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())

    def test_dst_fall_back_rows_stay_chronological(self):
        text = ("ora,a,b,c,d\n2026-10-25 01:50:00,10,5,,\n2026-10-25 02:10:00,20,5,,\n2026-10-25 02:50:00,30,5,,\n"
                "2026-10-25 02:10:00,35,5,,\n2026-10-25 02:50:00,50,5,,\n2026-10-25 03:10:00,60,5,,\n")
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        open(p, "w").write(text)
        with Tz("Europe/Rome"):
            rows = limits.parse_csv(p)
            self.assertEqual([r["p5"] for r in rows], [10, 20, 30, 35, 50, 60])
            self.assertEqual(sorted(r["t"] for r in rows), [r["t"] for r in rows])
            self.assertAlmostEqual(limits.pace(rows, "p5", window_hours=6), 50 / (7 / 3), places=1)  # 2 h 20 min elapsed

    def test_mixed_old_and_new_rows(self):
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        open(p, "w").write("ora,a,b,c,d\n2026-10-14 19:00:00,1,2,,\n2026-10-14T19:30:00+0200,3,4,,\n")
        with Tz("Europe/Rome"):
            self.assertEqual([r["p5"] for r in limits.parse_csv(p)], [1, 3])

    def test_new_job_times_carry_offset(self):
        q = jobs.Queue(tempfile.mkdtemp())
        j = q.add({"title": "t"})
        self.assertRegex(q.start(j["id"])["started_at"], r"T\d\d:\d\d:\d\d[+-]\d{4}$")

    def test_reset_time_jitter_does_not_void_delta(self):
        T0 = datetime(2026, 1, 1, 10).timestamp()
        rows = [{"t": T0, "p5": 10.0, "p7": 1.0, "r5": T0 + 18000, "r7": T0 + 99999},
                {"t": T0 + 3600, "p5": 20.0, "p7": 2.0, "r5": T0 + 18005, "r7": T0 + 99999}]
        self.assertEqual(limits.delta_between(rows, T0, T0 + 3600)["actual_pct_5h"], 10)

    def test_now_zero_is_a_valid_instant(self):
        rows = [{"t": 1e9, "p5": None, "p7": 5.0, "r5": None, "r7": 1e9 + 5000}]
        self.assertFalse(limits.summary(rows, now=0)["seven_day"]["stale"])


class TranscriptsRobust(unittest.TestCase):  # audit 4, 11, 27
    def test_subagent_files_are_counted_for_the_top_project(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "proj", "sess", "subagents"))
        now = datetime(2026, 1, 10, 12).timestamp()
        ts = datetime(2026, 1, 10, 12).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for rel in ("main.jsonl", "sess/subagents/agent-1.jsonl"):
            p = os.path.join(root, "proj", rel)
            open(p, "w").write(json.dumps({"type": "assistant", "timestamp": ts, "message": {
                "id": rel, "usage": {"input_tokens": 7}}}) + "\n")
            os.utime(p, (now, now))
        r = transcripts.scan(root, None, now=now)
        self.assertEqual([(p["name"], p["input"]) for p in r["projects"]], [("proj", 14)])

    def test_malformed_lines_do_not_break_the_scan(self):
        p = os.path.join(tempfile.mkdtemp(), "s.jsonl")
        ts = datetime(2026, 1, 10, 12).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with open(p, "w") as f:
            f.write('["usage"]\n')
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": "a", "usage": {"input_tokens": "n/a"}}}) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": "b", "usage": {"input_tokens": 5}}}) + "\n")
        self.assertEqual(sum(v[0] for v in transcripts.parse_file(p).values()), 5)

    def test_old_cache_version_is_discarded(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "p"))
        f = os.path.join(root, "p", "s.jsonl")
        open(f, "w").write("")
        st = os.stat(f)
        cache = os.path.join(root, "cache.json")
        json.dump({f: {"mtime": st.st_mtime, "size": st.st_size, "days": {"2099-01-01": [9, 9, 9, 9]}}}, open(cache, "w"))
        self.assertEqual(transcripts.scan(root, cache, now=st.st_mtime)["files_read"], 1)


class MarkdownRobust(unittest.TestCase):  # audit 12, 13
    HEAD = "| ID | Repo / progetto | Lavoro | Stima | Stato |\n|---|---|---|---|---|\n"

    def test_escaped_and_backticked_pipes(self):
        text = self.HEAD + "| L1 | repo | do `a|b` and c \\| d | - | in coda |\n"
        skipped = []
        got = jobs.parse_markdown(text, skipped=skipped)
        self.assertEqual((len(got), skipped), (1, []))
        self.assertIn("a|b", got[0]["title"])

    def test_bad_rows_are_reported_not_lost_silently(self):
        skipped = []
        got = jobs.parse_markdown(self.HEAD + "| L1 | only | three |\n| L2 | r | w | - | fatto |\n", skipped=skipped)
        self.assertEqual([j["id"] for j in got], ["L2"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("L1", skipped[0])

    def test_table_without_separator_keeps_first_row(self):
        text = "| ID | Repo / progetto | Lavoro | Stima | Stato |\n| L1 | r | w | - | fatto |\n| L2 | r | w | - | fatto |\n"
        self.assertEqual([j["id"] for j in jobs.parse_markdown(text)], ["L1", "L2"])

    def test_bold_id_matches_override(self):
        text = self.HEAD + "| **L1** | repo | w | - | in coda |\n"
        got = jobs.parse_markdown(text, overrides={"L1": {"estimate_pct_7d": 4}})
        self.assertEqual((got[0]["id"], got[0]["estimate_pct_7d"]), ("L1", 4.0))

    def test_negations(self):
        st = lambda t: jobs.recognize_status(t)[0]
        self.assertEqual(st("non ancora fatto"), "queued")
        self.assertEqual(st("not done yet"), "queued")
        self.assertEqual(st("da fare, non approvato"), "queued")
        self.assertEqual(st("no conflicts, done"), "done")
        self.assertEqual(st("fatto"), "done")


class SnippetFormat(unittest.TestCase):  # audit 7
    def test_snippet_writes_iso_with_offset(self):
        import shutil
        import subprocess
        if not shutil.which("jq"):
            self.skipTest("jq not installed")
        csv = os.path.join(tempfile.mkdtemp(), "l.csv")
        payload = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 1792000200},
                                              "seven_day": {"used_percentage": 34, "resets_at": 1792098000}}})
        script = os.path.join(os.path.dirname(HERE), "statusline", "log-limits.sh")
        subprocess.run(["bash", script], input=payload, text=True, check=True, env=dict(os.environ, CTM_LIMITS_CSV=csv))
        row = open(csv).read().splitlines()[1]
        self.assertRegex(row, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4},12,34,1792000200,1792098000$")
        self.assertEqual(limits.parse_csv(csv)[0]["p7"], 34.0)


class PageChecks(unittest.TestCase):  # audit 13, 15, 22 (script now lives in ctm/static/app.js)
    @staticmethod
    def js():
        import os
        from ctm import server
        with open(os.path.join(server.STATIC_DIR, "app.js"), encoding="utf-8") as f:
            return f.read()

    def test_page_shows_recognized_status_and_original_text(self):
        js = self.js()
        self.assertIn("status_text", js)
        self.assertIn("status_how", js)

    def test_finish_has_explicit_clean_and_mixed_buttons(self):
        js = self.js()
        self.assertNotIn("confirm(\"Were other jobs", js)
        self.assertIn('data-act="finish" data-mixed="0"', js)
        self.assertIn('data-act="finish" data-mixed="1"', js)

    def test_numeric_fields_are_escaped(self):
        js = self.js()
        self.assertIn("esc(dash(j.estimate_pct_5h))", js)
        self.assertRegex(js, r"esc\(j\.measure\)")

    def test_no_inline_handlers_or_style_attributes(self):  # CSP script-src/style-src 'self'
        import re
        from ctm.page import PAGE
        for text in (PAGE, self.js()):
            self.assertNotRegex(text, r"\son(click|change|submit)=")
            self.assertNotRegex(text, r"""\sstyle=["']""")


if __name__ == "__main__":
    unittest.main()
