"""HTTP-level tests: security headers, Host/Origin/content-type checks, error handling."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from ctm import server


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = {"port": 0, "limits_csv": os.path.join(self.d, "l.csv"), "transcripts_dir": os.path.join(self.d, "t"),
                    "data_dir": os.path.join(self.d, "data"), "markdown_queue": None, "rate_window_hours": 6,
                    "token_days": 7, "outbox_dir": os.path.join(self.d, "outbox"),
                    "markdown_status_keywords": {}, "markdown_overrides": {}}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.cfg))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def req(self, method, path, body=None, headers=None, skip_host=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        try:
            js = json.loads(data)
        except ValueError:
            js = None
        return r.status, dict(r.getheaders()), js, data

    def raw(self, method, path, extra):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path)
        for k, v in extra.items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        return r.status, r.read()


class Headers(ServerCase):  # audit 16
    def test_anti_framing_everywhere(self):
        for path in ("/", "/api/limits", "/nonexistent"):
            st, h, _, _ = self.req("GET", path)
            self.assertEqual(h.get("X-Frame-Options"), "DENY", path)
            self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy", ""), path)
            self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)

    def test_page_works_under_csp(self):
        st, h, _, body = self.req("GET", "/")
        csp = h["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("http", csp)
        self.assertIn(b'/static/app.js', body)
        self.assertNotIn(b"<script>", body)
        self.assertNotIn(b"<style", body)

    def test_static_files_served_from_fixed_whitelist(self):
        for path, ctype in (("/static/app.css", "text/css"), ("/static/app.js", "text/javascript"),
                            ("/static/favicon.svg", "image/svg+xml")):
            st, h, _, body = self.req("GET", path)
            self.assertEqual(st, 200, path)
            self.assertTrue(h["Content-Type"].startswith(ctype), path)
            self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
            self.assertTrue(body)

    def test_static_no_traversal_or_unknown_files(self):
        for path in ("/static/../server.py", "/static/%2e%2e/config.py", "/static/", "/static/app.js/x",
                     "/static/nope.js", "/static/..%2fserver.py"):
            self.assertEqual(self.req("GET", path)[0], 404, path)

    def test_static_checks_host_header(self):
        st = self.req("GET", "/static/app.js", headers={"Host": "evil.example:80"})[0]
        self.assertEqual(st, 403)

    def test_limits_exposes_warn_pct(self):
        self.assertEqual(self.req("GET", "/api/limits")[2]["warn_pct"], 80)


class WarnConfig(unittest.TestCase):
    def test_warn_pct_validated(self):
        from ctm import config
        base = dict(config.DEFAULTS)
        config.validate(base)
        for bad in (0, 101, "80", True, None):
            with self.assertRaises(ValueError):
                config.validate(dict(base, warn_pct=bad))


class Security(ServerCase):
    def test_foreign_host_origin_and_content_type(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("GET", "/api/limits", skip_host=True)
        c.putheader("Host", "evil.com")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 403)
        st, _, _, _ = self.req("POST", "/api/jobs", {"title": "x"}, {"Origin": "http://evil.com"})
        self.assertEqual(st, 403)
        st, _, _, _ = self.req("POST", "/api/jobs", '{"title":"x"}', {"Content-Type": "text/plain"})
        self.assertEqual(st, 415)

    def test_body_size_limits(self):
        st, _ = self.raw("POST", "/api/jobs", {"Host": "127.0.0.1:%d" % self.port, "Content-Type": "application/json",
                                                "Content-Length": "-1"})
        self.assertEqual(st, 400)
        st, _ = self.raw("POST", "/api/jobs", {"Host": "127.0.0.1:%d" % self.port, "Content-Type": "application/json",
                                                "Content-Length": "5000000"})
        self.assertEqual(st, 413)


class Errors(ServerCase):  # audit 10, 11, 17, 18
    def test_bad_query_is_400_json(self):
        st, _, js, _ = self.req("GET", "/api/suggest?est_5h=abc")
        self.assertEqual(st, 400)
        self.assertIn("error", js)

    def test_nan_is_rejected_by_api(self):
        st, _, js, _ = self.req("POST", "/api/jobs", {"title": "n", "estimate_pct_5h": "nan"})
        self.assertEqual(st, 400)
        self.assertEqual(self.req("GET", "/api/jobs")[2]["jobs"], [])

    def test_corrupt_queue_gives_500_with_message_and_keeps_file(self):
        self.req("POST", "/api/jobs", {"title": "a"})
        path = os.path.join(self.cfg["data_dir"], "queue.json")
        open(path, "w").write("{broken")
        st, _, js, _ = self.req("GET", "/api/jobs")
        self.assertEqual(st, 500)
        self.assertIn("queue.json", js["error"])
        self.assertEqual(self.req("POST", "/api/jobs", {"title": "b"})[0], 500)
        self.assertEqual(open(path).read(), "{broken")

    def test_unwritable_outbox_is_500_json(self):
        blocker = os.path.join(self.d, "file")
        open(blocker, "w").write("x")
        self.cfg["outbox_dir"] = os.path.join(blocker, "sub")
        st, _, js, _ = self.req("POST", "/api/orders", {"text": "hello"})
        self.assertEqual(st, 500)
        self.assertIn("error", js)

    def test_unknown_job_is_404_but_other_keyerrors_are_not(self):
        self.assertEqual(self.req("POST", "/api/jobs/J9/start", {})[0], 404)
        self.req("POST", "/api/jobs", {"title": "a"})
        path = os.path.join(self.cfg["data_dir"], "queue.json")
        json.dump({"jobs": [{"title": "no id here"}]}, open(path, "w"))
        self.assertEqual(self.req("POST", "/api/jobs/J1/start", {})[0], 500)

    def test_bad_utf8_csv_is_served(self):
        open(self.cfg["limits_csv"], "wb").write(b"ora,a,b,c,d\n2026-01-01 10:00:00,5,6,,\n\xff\n")
        self.assertEqual(self.req("GET", "/api/limits")[0], 200)


class OrdersHttp(ServerCase):
    def test_injection_via_api_does_not_change_status(self):
        st, _, js, _ = self.req("POST", "/api/orders", {"text": "x", "targets": ["a]\nstatus: done\nz: [b"]})
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", "/api/orders")[2]["orders"][0]["status"], "new")


if __name__ == "__main__":
    unittest.main()
