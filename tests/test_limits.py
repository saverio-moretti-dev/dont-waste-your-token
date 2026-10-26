import os
import tempfile
import unittest
from datetime import datetime

from ctm import limits

T0 = datetime(2026, 1, 1, 10, 0, 0).timestamp()


def row(h, p5, p7, r5=T0 + 5 * 3600, r7=T0 + 100 * 3600):
    return {"t": T0 + h * 3600, "p5": p5, "p7": p7, "r5": r5, "r7": r7}


class ParseCsv(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "l.csv")
        open(p, "w").write(text)
        return p

    def test_missing_file(self):
        self.assertEqual(limits.parse_csv("/nonexistent/x.csv"), [])

    def test_header_only(self):
        p = self.write("ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d\n")
        self.assertEqual(limits.parse_csv(p), [])

    def test_rows_and_missing_windows(self):
        p = self.write("ora,a,b,c,d\n2026-01-01 10:00:00,37,72,1792000200,1792098000\n"
                       "2026-01-01 11:00:00,,73,,1792098000\nbad line\n2026-01-01 12:00:00,40\n")
        rows = limits.parse_csv(p)
        self.assertEqual(len(rows), 2)  # short and garbage lines skipped
        self.assertEqual(rows[0]["p5"], 37.0)
        self.assertIsNone(rows[1]["p5"])
        self.assertEqual(rows[1]["p7"], 73.0)
        self.assertIsNone(rows[1]["r5"])


class Pace(unittest.TestCase):
    def test_rate(self):
        rows = [row(0, 10, 50), row(2, 20, 52)]
        self.assertAlmostEqual(limits.pace(rows, "p5"), 5.0)
        self.assertAlmostEqual(limits.pace(rows, "p7"), 1.0)

    def test_not_enough_points(self):
        self.assertIsNone(limits.pace([row(0, 10, 10)], "p5"))
        self.assertIsNone(limits.pace([], "p5"))

    def test_too_short_span(self):
        rows = [row(0, 10, 10), row(0.05, 11, 10)]
        self.assertIsNone(limits.pace(rows, "p5"))

    def test_reset_ignored(self):
        rows = [row(0, 90, 10), row(1, 95, 10), row(2, 5, 10), row(4, 15, 10)]
        self.assertAlmostEqual(limits.pace(rows, "p5"), 5.0)

    def test_window_limit(self):
        rows = [row(0, 0, 0), row(10, 50, 0), row(12, 60, 0)]
        self.assertAlmostEqual(limits.pace(rows, "p5", window_hours=6), 5.0)

    def test_missing_window(self):
        rows = [row(0, None, 5), row(2, None, 7)]
        self.assertIsNone(limits.pace(rows, "p5"))


H = 3600
R7 = T0 + 100 * H   # weekly reset used below: window started at R7 - 168 h


def wrow(t, p5, p7, r5=None, r7=R7):
    return {"t": t, "p5": p5, "p7": p7, "r5": r5, "r7": r7}


class Projection(unittest.TestCase):
    def test_window_mean_ok(self):
        # 50% after 84 h of a 168 h window -> mean 0.595/h, 84 h left -> 100 (edge)
        start = R7 - 168 * H
        p = limits.projection(40, start + 84 * H, R7, 168, 12, now=start + 84 * H)
        self.assertEqual(p["status"], "ok")
        self.assertAlmostEqual(p["at_reset_pct"], 80.0, places=1)

    def test_exceeds_is_capped_with_date(self):
        start = R7 - 168 * H
        p = limits.projection(90, start + 100 * H, R7, 168, 12, now=start + 100 * H)
        self.assertEqual(p["status"], "exceeds")
        self.assertEqual(p["at_reset_pct"], 100.0)
        self.assertGreater(p["raw_at_reset_pct"], 100)
        self.assertLess(p["exceeds_at"], R7)
        self.assertTrue(p["exceeds_at_text"].startswith("2026-"))

    def test_insufficient(self):
        start = R7 - 168 * H
        self.assertEqual(limits.projection(None, 0, R7, 168, 12, now=0)["status"], "insufficient")
        self.assertEqual(limits.projection(5, 0, None, 168, 12, now=0)["status"], "insufficient")
        young = limits.projection(30, start + 3 * H, R7, 168, 12, now=start + 3 * H)
        self.assertEqual(young["status"], "insufficient")  # 3 h into the window
        past = limits.projection(30, start + 3 * H, R7, 168, 12, now=R7 + 1)
        self.assertEqual(past["status"], "insufficient")

    def test_zero_usage_is_ok(self):
        start = R7 - 168 * H
        p = limits.projection(0, start + 50 * H, R7, 168, 12, now=start + 50 * H)
        self.assertEqual((p["status"], p["at_reset_pct"]), ("ok", 0.0))

    def test_real_case_short_burst_not_absurd(self):
        """Real trial: 72->74% in ~15 min gave a recent pace of 8 pt/h and a 293% projection."""
        start = R7 - 168 * H
        t_last = start + 140.6 * H
        rows = [wrow(t_last - 900, 72, 72), wrow(t_last - 400, 73, 72), wrow(t_last, 74, 72)]
        rows = [dict(r, p7=v) for r, v in zip(rows, (72, 73, 74))]
        s = limits.summary(rows, now=t_last + 60)
        proj = s["seven_day"]["projection"]
        self.assertGreater(s["seven_day"]["rate_per_hour"], 5)   # recent pace is still shown...
        self.assertEqual(proj["status"], "ok")                    # ...but not used
        self.assertLess(proj["at_reset_pct"], 100)
        self.assertAlmostEqual(proj["at_reset_pct"], 74 + 74 / 140.6 * 27.4, places=0)

    def test_5h_reset_handled(self):
        # 5h window renewed: values drop and reset time changes; pace only uses the new window
        r5a, r5b = T0 + 5 * H, T0 + 10 * H
        rows = [wrow(T0, 80, 10, r5a), wrow(T0 + H, 95, 10, r5a),
                wrow(T0 + 6 * H, 2, 10, r5b), wrow(T0 + 7 * H, 12, 10, r5b)]
        self.assertAlmostEqual(limits.pace(rows, "p5", rkey="r5"), 10.0)
        s = limits.summary(rows, now=T0 + 7.1 * H)
        self.assertEqual(s["five_hour"]["pct"], 12)
        self.assertEqual(s["five_hour"]["projection"]["status"], "ok")  # 12% after 2 h
        # new window just opened: too young
        rows.append(wrow(T0 + 10.1 * H, 1, 10, T0 + 15 * H))
        s = limits.summary(rows, now=T0 + 10.15 * H)
        self.assertEqual(s["five_hour"]["projection"]["status"], "insufficient")

    def test_pace_reset_time_change_without_drop(self):
        rows = [wrow(T0, 10, 0, T0 + 5 * H), wrow(T0 + H, 10, 0, T0 + 5 * H),
                wrow(T0 + 6 * H, 10, 0, T0 + 11 * H), wrow(T0 + 7 * H, 14, 0, T0 + 11 * H)]
        self.assertAlmostEqual(limits.pace(rows, "p5", rkey="r5"), 4.0)

    def test_summary_empty_and_stale(self):
        self.assertIsNone(limits.summary([])["five_hour"])
        s = limits.summary([row(0, 10, 10), row(2, 20, 12)], now=T0 + 150 * H)
        self.assertTrue(s["five_hour"]["stale"])
        self.assertEqual(s["five_hour"]["projection"]["status"], "insufficient")


class Delta(unittest.TestCase):
    def test_simple(self):
        rows = [row(0, 10, 50), row(1, 15, 50), row(2, 25, 51)]
        d = limits.delta_between(rows, T0 + 0.5 * 3600, T0 + 2.5 * 3600)
        self.assertEqual(d, {"actual_pct_5h": 15, "actual_pct_7d": 1})

    def test_reset_inside_gives_none(self):
        rows = [row(0, 90, 50), row(1, 5, 51, r5=T0 + 9 * 3600)]
        d = limits.delta_between(rows, T0, T0 + 3600)
        self.assertIsNone(d["actual_pct_5h"])
        self.assertEqual(d["actual_pct_7d"], 1)

    def test_no_data(self):
        self.assertEqual(limits.delta_between([], 0, 1), {"actual_pct_5h": None, "actual_pct_7d": None})


if __name__ == "__main__":
    unittest.main()
