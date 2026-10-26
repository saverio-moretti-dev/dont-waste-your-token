import os
import tempfile
import unittest
from datetime import datetime

from ctm import conflicts, jobs, report

HERE = os.path.dirname(__file__)


def job(i, title, targets, status="queued", kind="dev", **kw):
    d = {"id": i, "title": title, "targets": jobs.split_targets(targets), "status": status, "kind": kind,
         "model": "", "priority": 5, "estimate_pct_5h": None, "estimate_pct_7d": None}
    d.update(kw)
    return d


class Targets(unittest.TestCase):
    def test_split(self):
        self.assertEqual(jobs.split_targets("hub (`RADAR.md` + `radar/`) + App nuova, hub"), ["hub", "app nuova"])
        self.assertEqual(jobs.split_targets(["A/", "b"]), ["a", "b"])
        self.assertEqual(jobs.split_targets(None), [])

    def test_markdown_targets_come_from_repo_column(self):
        md = jobs.load_markdown(os.path.join(HERE, "fixtures", "jobs.md"))
        self.assertEqual(md[0]["targets"], ["tool", "new app"])
        self.assertEqual(md[1]["targets"], ["video-demo"])


class Conflicts(unittest.TestCase):
    def test_no_conflict_disjoint_or_inactive(self):
        js = [job("A", "one", "x"), job("B", "two", "y"), job("C", "three", "x", status="done"),
              job("D", "four", "x", status="blocked"), job("E", "five", "")]
        self.assertEqual(conflicts.find(js), [])

    def test_path_prefix_overlap(self):
        js = [job("A", "refactor", "repo/src"), job("B", "docs pass", "repo/src/ui", kind="docs")]
        r = conflicts.find(js)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["shared"], ["repo/src/ui"])
        self.assertEqual(r[0]["proposal"], "integrate")
        self.assertEqual(conflicts.find([job("A", "a", "repo/srcx"), job("B", "b", "repo/src")]), [])

    def test_proposals(self):
        dup = conflicts.find([job("A", "Update the radar survey", "hub"), job("B", "Update the radar survey!", "hub")])
        self.assertEqual(dup[0]["proposal"], "exclude")
        merge = conflicts.find([job("A", "tune compressor", "p1, p2"), job("B", "write benchmark", "p2, p1")])
        self.assertEqual(merge[0]["proposal"], "merge")
        seq = conflicts.find([job("A", "tune compressor", "p1, p2"), job("B", "release notes", "p2, p3", status="approved")])
        self.assertEqual(seq[0]["proposal"], "sequence")
        self.assertIn("prima B", seq[0]["reason"])

    def test_wildcard_never_conflicts_alone(self):
        js = [job("A", "everything", "tutte le repo"), job("B", "x", "tutte le repo")]
        self.assertEqual(conflicts.find(js), [])

    def test_with_markdown_fixture(self):
        md = jobs.load_markdown(os.path.join(HERE, "fixtures", "jobs.md"))
        own = [job("J1", "another thing", "video-demo")]
        r = conflicts.find(own + md)
        self.assertEqual([(c["a"], c["b"]) for c in r], [("J1", "L2")])


NOW = datetime(2026, 1, 1, 12, 0, 0).timestamp()
H = 3600


def rows_for(p7, elapsed_h=100):
    r7 = NOW + (168 - elapsed_h) * H
    return [{"t": NOW, "p5": 10.0, "p7": float(p7), "r5": NOW + 3 * H, "r7": r7}]


class Report(unittest.TestCase):
    def setUp(self):
        self.done = [dict(job("D%d" % i, "old", "z", status="done", kind="research"), measure="clean",
                          estimate_pct_7d=10.0, actual_pct_7d=1.0) for i in range(2)]

    def test_sections_and_calibration(self):
        own = self.done + [
            job("J1", "survey", "a", status="approved", kind="research", estimate_pct_7d=10.0, priority=2),
            job("J2", "big job", "b", status="approved", kind="dev", estimate_pct_7d=30.0, priority=1),
            job("J3", "later", "a", status="queued", kind="research", estimate_pct_7d=5.0),
            job("J4", "waiting", "c", status="blocked")]
        md = [job("L1", "imported", "c", status="approved")]
        text = report.build(rows_for(40), own, md, now=NOW)
        for h in ("## Consumo attuale", "## Lavori aperti per stato", "### approvati", "### in coda", "### bloccati",
                  "## Conflitti", "## Cosa ci sta prima del ripristino settimanale"):
            self.assertIn(h, text)
        self.assertIn("| J1 | survey | a | - / 10 | - / 1 | tipo+modello (n=2) |", text)  # ratio 0.1
        self.assertIn("| J2 | big job | b | - / 30 | - / 3 | globale (n=2) |", text)    # falls back to global
        self.assertIn("J1 + J3 su a: **unire**", text)  # conflict listed
        self.assertIn("senza stima 7g (non contati): L1", text)
        # margin = 100 - projected(40 + 40/100*68 = 67.2) = 32.8 ; J2 (priority 1) first
        self.assertIn("Margine: **32.8 punti**", text)
        self.assertIn("| 1 | J2 | big job | 3 | 3 | sì |", text)
        self.assertIn("| 2 | J1 | survey | 1 | 4 | sì |", text)

    def test_imported_job_with_override_is_counted(self):
        md = jobs.load_markdown(os.path.join(HERE, "fixtures", "jobs.md"), None,
                                {"L8": {"estimate_pct_7d": 5, "priority": 1}})
        text = report.build(rows_for(40), [], md, now=NOW)
        self.assertIn("### in corso", text)
        self.assertIn("| 1 | L8 | Approved job | 5 | 5 | sì |", text)

    def test_does_not_fit_and_exceeds(self):
        own = [job("J1", "huge", "a", status="approved", estimate_pct_7d=50.0)]
        self.assertIn("| 1 | J1 | huge | 50 | 50 | no |", report.build(rows_for(40), own, [], now=NOW))
        text = report.build(rows_for(95), own, [], now=NOW)
        self.assertIn(">100%: supera il limite verso", text)
        self.assertIn("Margine: **0 punti**", text)

    def test_empty_and_insufficient(self):
        text = report.build([], [], [], now=NOW)
        self.assertIn("ancora nessuna lettura dei limiti", text)
        self.assertIn("Sconosciuto (nessuna lettura settimanale)", text)
        text = report.build(rows_for(5, elapsed_h=3), [], [], now=NOW)
        self.assertIn("dati insufficienti", text)
        self.assertIn("previsione insufficiente", text)


class Cli(unittest.TestCase):
    def test_report_command_out_file(self):
        import json, subprocess, sys
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "c.json")
        json.dump({"limits_csv": os.path.join(d, "none.csv"), "data_dir": d, "transcripts_dir": d}, open(cfg, "w"))
        out = os.path.join(d, "r.md")
        root = os.path.dirname(HERE)
        subprocess.run([sys.executable, "-m", "ctm", "report", "--config", cfg, "--out", out], check=True,
                       cwd=root, capture_output=True)
        self.assertIn("# Resoconto token", open(out).read())


if __name__ == "__main__":
    unittest.main()
