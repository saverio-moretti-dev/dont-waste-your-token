import os
import tempfile
import unittest
from datetime import datetime

from ctm import jobs

HERE = os.path.dirname(__file__)
T0 = datetime(2026, 1, 1, 10, 0, 0).timestamp()


def row(h, p5, p7):
    return {"t": T0 + h * 3600, "p5": p5, "p7": p7, "r5": T0 + 5 * 3600, "r7": T0 + 99 * 3600}


class Markdown(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs.load_markdown(os.path.join(HERE, "fixtures", "jobs.md"))

    def test_first_table_only(self):
        self.assertEqual([j["id"] for j in self.jobs], ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"])

    def test_status_mapping(self):
        self.assertEqual([j["status"] for j in self.jobs], ["running", "queued", "done", "blocked", "running", "queued", "cancelled", "approved"])

    def test_fields(self):
        self.assertEqual(self.jobs[0]["title"], "Token rationing system")
        self.assertEqual(self.jobs[2]["project"], "docs")
        self.assertTrue(all(j["source"] == "markdown" for j in self.jobs))

    def test_never_writes(self):
        p = os.path.join(HERE, "fixtures", "jobs.md")
        before = (os.path.getmtime(p), open(p).read())
        jobs.load_markdown(p)
        self.assertEqual(before, (os.path.getmtime(p), open(p).read()))

    def test_missing_or_no_table(self):
        self.assertEqual(jobs.load_markdown("/nonexistent.md"), [])
        self.assertEqual(jobs.parse_markdown("just text"), [])


class StatusKeywords(unittest.TestCase):
    def st(self, text, kw=None):
        return jobs.recognize_status(text, kw)[0]

    def test_real_lines(self):
        self.assertEqual(self.st("**fase 1 e 2 fatte** 14/10 (fase 2 @ `e8a15b2`). Resta: ordini, nome, licenza."), "running")
        self.assertEqual(self.st("**fatto** (censimento) 14/10 19:25: ~205 voci. Partito (utente: «L3 ora»)"), "done")
        # choice: a partly anticipated job whose rest waits for the slot stays queued
        self.assertEqual(self.st("**in parte anticipato** (14/10): partono subito; giovedì restano i 40 di `x.tsv`"), "queued")
        self.assertEqual(self.st("**in corso** dal 14/10 sera: fase 1"), "running")
        self.assertEqual(jobs.recognize_status("**in coda**, da progettare")[0], "queued")

    def test_priority_order(self):
        self.assertEqual(self.st("fatto ma ANNULLATO"), "cancelled")
        self.assertEqual(self.st("Cancelled, was done"), "cancelled")
        self.assertEqual(self.st("bloccato, in corso"), "blocked")
        self.assertEqual(self.st("DONE (tests green)"), "done")
        self.assertEqual(self.st("Done, but still running"), "running")
        self.assertEqual(self.st("approved by user"), "approved")
        self.assertEqual(self.st("Queued"), "queued")
        self.assertEqual(self.st("whatever"), "queued")
        self.assertEqual(jobs.recognize_status("whatever")[1], "default")

    def test_whole_words_only(self):
        self.assertEqual(self.st("undone work, fattore"), "queued")

    def test_custom_keywords(self):
        self.assertEqual(self.st("shipped!", {"done": ["shipped"]}), "done")
        self.assertEqual(self.st("fatto", {"done": ["shipped"]}), "queued")  # replaces that status list

    def test_overrides(self):
        md = jobs.load_markdown(os.path.join(HERE, "fixtures", "jobs.md"), None,
                                {"L6": {"status": "approved", "estimate_pct_7d": 4, "estimate_pct_5h": 9.5,
                                        "targets": ["beta", "Beta/ui"], "priority": 1}})
        l6 = md[5]
        self.assertEqual((l6["status"], l6["status_how"]), ("approved", "override"))
        self.assertEqual((l6["estimate_pct_7d"], l6["estimate_pct_5h"], l6["priority"]), (4.0, 9.5, 1))
        self.assertEqual(l6["targets"], ["beta", "beta/ui"])
        self.assertIn("restano i 40", l6["status_text"])  # original text kept next to the recognized state
        self.assertEqual(md[0]["status_how"], "keyword")

    def test_check_overrides(self):
        jobs.check_overrides({"L1": {"status": "done"}})
        with self.assertRaises(ValueError):
            jobs.check_overrides({"L1": {"status": "bogus"}})
        with self.assertRaises(ValueError):
            jobs.check_overrides({"L1": {"colour": "red"}})


class QueueFile(unittest.TestCase):
    def setUp(self):
        self.q = jobs.Queue(tempfile.mkdtemp())

    def test_add_and_status(self):
        a = self.q.add({"title": "A", "estimate_pct_5h": "10", "kind": "research"})
        b = self.q.add({"title": "B"})
        self.assertEqual((a["id"], b["id"]), ("J1", "J2"))
        self.assertEqual(a["estimate_pct_5h"], 10.0)
        self.q.set_status("J1", "approved")
        self.assertEqual(self.q.load()[0]["status"], "approved")
        with self.assertRaises(ValueError):
            self.q.set_status("J1", "weird")
        with self.assertRaises(ValueError):
            self.q.add({"title": " "})

    def test_start_finish_measures_actual(self):
        rows = [row(0, 10, 50), row(1, 20, 50), row(2, 30, 51)]
        j = self.q.add({"title": "A", "estimate_pct_5h": 100, "estimate_pct_7d": 10})
        self.q.start(j["id"], at="2026-01-01 10:30:00")
        done = self.q.finish(j["id"], rows, at="2026-01-01 12:00:00")
        self.assertEqual(done["actual_pct_5h"], 20)
        self.assertEqual(done["actual_pct_7d"], 1)
        self.assertEqual(done["measure"], "clean")
        self.assertEqual(done["status"], "done")

    def test_overlap_marks_mixed(self):
        rows = [row(0, 10, 50), row(2, 30, 51)]
        a = self.q.add({"title": "A"})
        b = self.q.add({"title": "B"})
        self.q.start(a["id"], at="2026-01-01 10:00:00")
        self.q.start(b["id"], at="2026-01-01 10:30:00")
        done = self.q.finish(a["id"], rows, at="2026-01-01 12:00:00")
        self.assertEqual(done["measure"], "mixed")

    def test_explicit_mixed_and_unstarted(self):
        j = self.q.add({"title": "A"})
        with self.assertRaises(ValueError):
            self.q.finish(j["id"], [])
        self.q.start(j["id"], at="2026-01-01 10:00:00")
        self.assertEqual(self.q.finish(j["id"], [], mixed=True, at="2026-01-01 11:00:00")["measure"], "mixed")


def done(kind, model, est, act, measure="clean", w="5h"):
    return {"status": "done", "measure": measure, "kind": kind, "model": model,
            "estimate_pct_" + w: est, "actual_pct_" + w: act}


class Calibration(unittest.TestCase):
    def test_ratio_and_suggest_by_kind_model(self):
        js = [done("research", "sonnet", 50, 5), done("research", "sonnet", 100, 12),
              done("dev", "opus", 10, 20)]
        s = jobs.suggest(js, "research", "sonnet", est_5h=40)
        self.assertEqual(s["5h"]["basis"], "kind+model")
        self.assertAlmostEqual(s["5h"]["ratio"], 17 / 150, places=3)  # sum(actual) / sum(estimate)
        self.assertAlmostEqual(s["5h"]["corrected"], 40 * 17 / 150, places=1)

    def test_fallback_to_kind_then_global_then_none(self):
        js = [done("research", "sonnet", 50, 5), done("research", "opus", 50, 5)]
        self.assertEqual(jobs.suggest(js, "research", "haiku", 10)["5h"]["basis"], "kind")
        self.assertEqual(jobs.suggest(js, "audit", "haiku", 10)["5h"]["basis"], "global")
        s = jobs.suggest([], "x", "y", 10, 5)
        self.assertEqual((s["5h"]["basis"], s["5h"]["corrected"], s["7d"]["corrected"]), ("none", 10, 5))

    def test_mixed_and_zero_estimates_excluded(self):
        js = [done("a", "m", 10, 1, measure="mixed"), done("a", "m", 0, 1), done("a", "m", 10, 1)]
        one = {"ratio": 0.1, "n": 1, "min": 0.1, "max": 0.1}
        self.assertEqual(jobs.calibration(js), {"5h|km|a|m": one, "5h|k|a|": one, "5h|g||": one})

    def test_7d_independent(self):
        js = [done("a", "m", 10, 1, w="7d"), done("a", "m", 20, 4, w="7d")]
        s = jobs.suggest(js, "a", "m", est_5h=10, est_7d=10)
        self.assertEqual(s["5h"]["basis"], "none")
        self.assertEqual(s["7d"]["basis"], "kind+model")


if __name__ == "__main__":
    unittest.main()
