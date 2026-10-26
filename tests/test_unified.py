"""Tests for the features merged from the second deployment (usage per session/hour, runner, job CLI,
JSONL import, Windows statusline snippet, failed/after/tokens in the queue)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from ctm import config, jobs, limits, runner, usage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1792000000  # a fixed instant (2026-10-17)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def line(mid, t, sid, inp=10, out=1, cw=100, cr=1000):
    return json.dumps({"type": "assistant", "timestamp": iso(t), "sessionId": sid,
                       "message": {"id": mid, "usage": {"input_tokens": inp, "output_tokens": out,
                                                        "cache_creation_input_tokens": cw,
                                                        "cache_read_input_tokens": cr}}}) + "\n"


class Usage(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        p = os.path.join(self.root, "proj-a")
        os.makedirs(os.path.join(p, "s1", "subagents"))
        with open(os.path.join(p, "s1.jsonl"), "w") as f:
            f.write(line("m1", T0, "s1") + line("m1", T0, "s1", inp=20) + line("m2", T0 + 3600, "s1"))
        with open(os.path.join(p, "s1", "subagents", "agent-x.jsonl"), "w") as f:
            f.write(line("m3", T0 + 60, "s1", inp=5))
        with open(os.path.join(p, "s2.jsonl"), "w") as f:
            f.write(line("m4", T0 + 7200, "s2", inp=7))
        for fn in ("s1.jsonl", "s2.jsonl"):
            os.utime(os.path.join(p, fn), (T0 + 7200, T0 + 7200))
        os.utime(os.path.join(p, "s1", "subagents", "agent-x.jsonl"), (T0 + 7200, T0 + 7200))
        self.data = usage.load(self.root, None, since=0, now=T0 + 7300)

    def test_streaming_duplicates_count_once_and_subagents_belong_to_the_session(self):
        self.assertEqual(usage.of_sessions(self.data, ["s1"])[0], 20 + 10 + 5)
        self.assertEqual(usage.of_sessions(self.data, ["s2"])[0], 7)

    def test_between_is_hour_precise(self):
        self.assertEqual(usage.between(self.data, T0, T0 + 100)[0], 20 + 5)
        self.assertEqual(usage.between(self.data, T0, T0 + 7200)[0], 20 + 5 + 10 + 7)

    def test_weights(self):
        self.assertEqual(usage.weigh([1, 1, 1, 10]), 1 + 5 + 1.25 + 1)
        self.assertEqual(usage.weigh([1, 1, 1, 10], {"cache_read": 0}), 7.25)

    def test_tokens_per_pct(self):
        rows = [{"t": T0 + 7200, "p5": 10, "p7": 4, "r5": None, "r7": T0 + 86400}]
        m = usage.tokens_per_pct(rows, self.data, now=T0 + 7300)
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["tokens_per_pct"], round(m["weighted_tokens"] / 4))
        rows[0]["p7"] = 2
        self.assertEqual(usage.tokens_per_pct(rows, self.data, now=T0 + 7300)["status"], "insufficient")

    def test_active_sessions(self):
        act = usage.active_sessions(self.data, now=T0 + 7300, minutes=30)
        self.assertEqual({a["session"] for a in act}, {"s2"})

    def test_cache_roundtrip(self):
        c = os.path.join(tempfile.mkdtemp(), "u.json")
        a = usage.load(self.root, c, since=0)
        b = usage.load(self.root, c, since=0)
        self.assertEqual(usage.of_sessions(a, ["s1"]), usage.of_sessions(b, ["s1"]))


class QueueExtensions(unittest.TestCase):
    def setUp(self):
        self.q = jobs.Queue(tempfile.mkdtemp())

    def test_after_must_exist_and_gates_runnable(self):
        a = self.q.add({"title": "A", "status": "approved", "priority": 3})
        b = self.q.add({"title": "B", "status": "approved", "after": a["id"], "priority": 1})
        with self.assertRaises(ValueError):
            self.q.add({"title": "C", "after": "J99"})
        self.assertEqual([j["id"] for j in jobs.runnable(self.q.load())], [a["id"]])
        self.q.start(a["id"])
        self.q.finish(a["id"], [], at=None)
        self.assertEqual([j["id"] for j in jobs.runnable(self.q.load())], [b["id"]])

    def test_queued_is_never_runnable(self):
        self.q.add({"title": "proposal"})
        self.assertEqual(jobs.runnable(self.q.load()), [])

    def test_failed_status_and_result(self):
        j = self.q.add({"title": "A"})
        self.q.start(j["id"])
        k = self.q.finish(j["id"], [], status="failed", result="why")
        self.assertEqual((k["status"], k["result"]), ("failed", "why"))
        with self.assertRaises(ValueError):
            self.q.finish(j["id"], [], status="cancelled")

    def test_finish_measures_tokens_by_session_when_known(self):
        j = self.q.add({"title": "A"})
        self.q.start(j["id"])
        self.q.add_run(j["id"], "sess-1")
        seen = {}

        def meter(t0, t1, sids):
            seen["sids"] = sids
            return [10, 1, 0, 0], 15.0, "sessions"
        k = self.q.finish(j["id"], [], tokens=meter)
        self.assertEqual(seen["sids"], ["sess-1"])
        self.assertEqual((k["actual_tokens"], k["token_measure"]), (15, "sessions"))

    def test_add_run_sums_attempts(self):
        j = self.q.add({"title": "A"})
        self.q.add_run(j["id"], "s1", [1, 1, 0, 0], 6)
        k = self.q.add_run(j["id"], "s2", [2, 0, 0, 0], 2)
        self.assertEqual((k["actual_tokens_raw"], k["actual_tokens"], k["session_ids"]), ([3, 1, 0, 0], 8, ["s1", "s2"]))

    def test_token_calibration(self):
        js = [{"status": "done", "kind": "dev", "model": "", "est_tokens": 100, "actual_tokens": 50,
               "token_measure": "sessions"},
              {"status": "done", "kind": "dev", "model": "", "est_tokens": 300, "actual_tokens": 150,
               "token_measure": "sessions"}]
        s = jobs.suggest(js, "dev", "", est_tok=1000)
        self.assertEqual((s["tok"]["ratio"], s["tok"]["corrected"]), (0.5, 500.0))


class ImportJsonl(unittest.TestCase):
    def test_only_changes_and_only_newer(self):
        d = tempfile.mkdtemp()
        src, csv_path = os.path.join(d, "s.jsonl"), os.path.join(d, "l.csv")
        with open(src, "w", encoding="utf-8") as f:
            for t, a, b in ((T0, 10, 3), (T0 + 60, 10, 3), (T0 + 120, 12, 3), (T0 + 180, None, None)):
                f.write(json.dumps({"ts": t, "s5h": a, "sett": b, "r5h": T0 + 9000, "rsett": T0 + 99000}) + "\n")
            f.write("not json\n")
        self.assertEqual(limits.import_jsonl(src, csv_path), 2)
        rows = limits.parse_csv(csv_path)
        self.assertEqual([(r["t"], r["p5"], r["p7"], r["r7"]) for r in rows],
                         [(T0, 10, 3, T0 + 99000), (T0 + 120, 12, 3, T0 + 99000)])
        self.assertEqual(limits.import_jsonl(src, csv_path), 0)  # nothing newer


FAKE = r'''
import json, os, sys, subprocess
mode = open(os.environ["FAKE_MODE"]).read().strip()
sid = "sess-" + os.environ["CTM_JOB"]
usage = {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
if mode == "close":
    subprocess.run([sys.executable, "-m", "ctm", "job", "finish", os.environ["CTM_JOB"], "--result", "ok"], check=True)
    print(json.dumps({"type": "result", "session_id": sid, "usage": usage, "result": "done"}))
elif mode == "limit":
    print(json.dumps({"type": "result", "session_id": sid, "usage": usage, "is_error": True,
                      "result": "You've hit your limit - resets 11am"}))
    sys.exit(1)
else:
    print(json.dumps({"type": "result", "session_id": sid, "usage": usage, "result": "crashed"}))
    sys.exit(2)
'''


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.fake = os.path.join(self.d, "fake_claude.py")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        self.mode = os.path.join(self.d, "mode")
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w") as f:
            json.dump({"data_dir": self.d, "limits_csv": os.path.join(self.d, "l.csv"),
                       "transcripts_dir": os.path.join(self.d, "none"),
                       "runner": {"command": [sys.executable, self.fake], "max_attempts": 3, "wait_minutes": 1}}, f)
        self.cfg = config.load(self.cfgp)
        self.q = jobs.Queue(self.d)
        os.environ["FAKE_MODE"] = self.mode
        self.t = [T0]
        self.sleeps = []

    def tearDown(self):
        os.environ.pop("FAKE_MODE", None)

    def go(self, mode, until=None):
        with open(self.mode, "w") as f:
            f.write(mode)

        def sleep(s):
            self.sleeps.append(s)
            self.t[0] += s
        r = runner.Runner(self.cfg, self.cfgp, clock=lambda: self.t[0], sleep=sleep, out=io.StringIO())
        r.run(until=until or self.t[0] + 3600)
        return r

    def test_agent_closes_its_job_and_tokens_are_recorded(self):
        j = self.q.add({"title": "A", "status": "approved"})
        self.go("close")
        k = self.q.load()[0]
        self.assertEqual((k["status"], k["result"], k["session_ids"]), ("done", "ok", ["sess-" + j["id"]]))
        self.assertEqual((k["actual_tokens"], k["token_measure"]), (150, "sessions"))

    def test_generic_failure_retries_then_fails_never_done(self):
        self.q.add({"title": "A", "status": "approved"})
        self.go("crash")
        k = self.q.load()[0]
        self.assertEqual((k["status"], k["attempts"]), ("failed", 3))
        self.assertEqual(len(self.sleeps), 2)

    def test_usage_limit_is_retried_until_the_deadline_and_stays_approved(self):
        self.q.add({"title": "A", "status": "approved"})
        self.go("limit", until=self.t[0] + 200)  # wait 60 s each time
        k = self.q.load()[0]
        self.assertEqual((k["status"], k["attempts"]), ("approved", 0))
        self.assertGreaterEqual(len(self.sleeps), 3)
        self.assertEqual(k["actual_tokens"], 150 * (len(self.sleeps)))  # every interrupted run is counted

    def test_queued_jobs_are_not_run(self):
        self.q.add({"title": "proposal"})
        r = self.go("close")
        with open(r.log_path, encoding="utf-8") as f:
            self.assertIn("nothing approved", f.read())
        self.assertEqual(self.q.load()[0]["status"], "queued")

    def test_briefing_placeholders(self):
        j = self.q.add({"title": "Fix {x}", "targets": "repo-a", "status": "approved"})
        text = runner.briefing(self.cfg, j, self.cfgp)
        self.assertIn("JOB J1", text)
        self.assertIn("Fix {x}", text)
        self.assertIn("job finish J1", text)
        self.assertIn("repo-a", text)


class JobCli(unittest.TestCase):
    def test_add_approve_start_finish(self):
        d = tempfile.mkdtemp()
        cfgp = os.path.join(d, "c.json")
        with open(cfgp, "w") as f:
            json.dump({"data_dir": d, "limits_csv": os.path.join(d, "l.csv"), "transcripts_dir": os.path.join(d, "t")}, f)
        env = dict(os.environ, CTM_CONFIG=cfgp, PYTHONPATH=ROOT)
        run = lambda *a: subprocess.run([sys.executable, "-m", "ctm"] + list(a), env=env, cwd=d,
                                        capture_output=True, text=True)
        self.assertEqual(run("job", "add", "--title", "Hello", "--est-tokens", "500").returncode, 0)
        self.assertEqual(run("job", "approve", "J1").returncode, 0)
        self.assertEqual(run("job", "start", "J1").returncode, 0)
        r = run("job", "finish", "J1", "--failed", "--result", "nope")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = jobs.Queue(d).load()[0]
        self.assertEqual((j["status"], j["result"], j["est_tokens"]), ("failed", "nope", 500))
        self.assertNotEqual(run("job", "status", "J9", "done").returncode, 0)


class ConfigRunner(unittest.TestCase):
    def test_runner_section_merges_and_validates(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c.json")
        with open(p, "w") as f:
            json.dump({"runner": {"extra_args": ["--x"]}}, f)
        c = config.load(p)
        self.assertEqual((c["runner"]["extra_args"], c["runner"]["max_attempts"]), (["--x"], 3))
        for bad in ({"runner": {"nope": 1}}, {"runner": {"command": []}}, {"tokens_per_pct_7d": -1},
                    {"token_weights": {"output": "x"}}):
            with open(p, "w") as f:
                json.dump(bad, f)
            with self.assertRaises(ValueError):
                config.load(p)


@unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell snippet")
class PowerShellSnippet(unittest.TestCase):
    def test_writes_iso_rows_only_on_change(self):
        d = tempfile.mkdtemp()
        csv_path = os.path.join(d, "l.csv")
        ps = os.path.join(ROOT, "statusline", "log-limits.ps1")
        env = dict(os.environ, CTM_LIMITS_CSV=csv_path)
        for p5 in (10, 10, 11):
            j = json.dumps({"rate_limits": {"five_hour": {"used_percentage": p5, "resets_at": T0},
                                            "seven_day": {"used_percentage": 3, "resets_at": T0 + 9}}})
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps], input=j,
                           text=True, env=env, check=True)
        rows = limits.parse_csv(csv_path)
        self.assertEqual([(r["p5"], r["p7"], r["r7"]) for r in rows], [(10, 3, T0 + 9), (11, 3, T0 + 9)])


if __name__ == "__main__":
    unittest.main()
