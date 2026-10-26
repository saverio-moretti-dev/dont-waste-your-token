"""Local HTTP server (127.0.0.1 only): JSON API + single page."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import glob

from . import conflicts, jobs, limits, orders, report, transcripts, usage
from .page import PAGE

MAX_BODY = 1_000_000
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}
# Fixed whitelist of static files (no path is ever built from the request).
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


def make_handler(cfg):
    queue = jobs.Queue(cfg["data_dir"])
    cache_path = os.path.join(cfg["data_dir"], "transcripts-cache.json")
    usage_cache = os.path.join(cfg["data_dir"], "usage-cache.json")

    def usage_data():
        return usage.load(cfg["transcripts_dir"], usage_cache)

    def token_meter(t0, t1, session_ids):
        data = usage_data()
        raw, how = (usage.of_sessions(data, session_ids), "sessions") if session_ids else \
            (usage.between(data, t0, t1), "interval")
        return raw, usage.weigh(raw, cfg.get("token_weights")), how

    def usage_summary(rows):
        m = usage.tokens_per_pct(rows, usage_data(), cfg.get("token_weights"))
        m["declared_tokens_per_pct"] = cfg.get("tokens_per_pct_7d")
        m["used_tokens_per_pct"] = cfg.get("tokens_per_pct_7d") or m.get("tokens_per_pct")
        return m

    def runner_log(n=40):
        d = cfg["runner"].get("log_dir") or cfg["data_dir"]
        files = sorted(glob.glob(os.path.join(d, "runner-*.log")))
        if not files:
            return {"file": None, "lines": []}
        with open(files[-1], encoding="utf-8", errors="replace") as f:
            return {"file": os.path.basename(files[-1]), "lines": f.read().splitlines()[-n:]}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ctm/0.1"

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, bytes):
                data = body
            else:
                try:
                    data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                except ValueError as e:  # NaN/Infinity somewhere in the data
                    code, data = 500, json.dumps({"error": "data not serializable: %s" % e}).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _local_hosts(self):
            port = self.server.server_address[1]
            return {"127.0.0.1:%d" % port, "localhost:%d" % port}

        def _host_ok(self):
            hosts = self._local_hosts()
            if self.headers.get("Host", "") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return not origin or origin.split("//", 1)[-1] in hosts

        def _all_jobs(self, skipped=None):
            own = queue.load()
            for j in own:
                j.setdefault("source", "own")
            return own, jobs.load_markdown_cfg(cfg, skipped)

        def _fail(self, exc):
            if isinstance(exc, jobs.UnknownJob):
                return self._send(404, {"error": "unknown job"})
            self._send(500, {"error": "%s: %s" % (type(exc).__name__, exc)})

        def do_GET(self):
            try:
                self._get()
            except Exception as e:  # never leave the client without an answer
                self._fail(e)

        def _get(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                with open(os.path.join(STATIC_DIR, name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            if u.path == "/api/limits":
                rows = limits.parse_csv(cfg["limits_csv"])
                s = limits.summary(rows, cfg["rate_window_hours"])
                s["warn_pct"] = cfg.get("warn_pct", 80)
                s["series"] = limits.series(rows)
                return self._send(200, s)
            if u.path == "/api/usage":
                return self._send(200, usage_summary(limits.parse_csv(cfg["limits_csv"])))
            if u.path == "/api/sessions":
                return self._send(200, {"sessions": usage.active_sessions(usage_data(), minutes=int(q.get("minutes") or 30))})
            if u.path == "/api/runner":
                return self._send(200, runner_log())
            if u.path == "/api/tokens":
                return self._send(200, transcripts.scan(cfg["transcripts_dir"], cache_path, cfg["token_days"]))
            if u.path == "/api/jobs":
                skipped = []
                own, md = self._all_jobs(skipped)
                return self._send(200, {"jobs": own, "markdown": md, "markdown_skipped": skipped,
                                        "markdown_path_set": bool(cfg.get("markdown_queue"))})
            if u.path == "/api/orders":
                return self._send(200, {"orders": orders.list_orders(cfg["outbox_dir"], q.get("status")),
                                        "outbox_dir_set": True})
            if u.path == "/api/conflicts":
                own, md = self._all_jobs()
                return self._send(200, {"conflicts": conflicts.find(own + md)})
            if u.path == "/api/report":
                own, md = self._all_jobs()
                rows = limits.parse_csv(cfg["limits_csv"])
                return self._send(200, {"markdown": report.build(rows, own, md, cfg["rate_window_hours"])})
            if u.path == "/api/calibration":
                own, _ = self._all_jobs()
                return self._send(200, jobs.calibration(own))
            if u.path == "/api/suggest":
                own, _ = self._all_jobs()
                try:
                    f = lambda k: float(q[k]) if q.get(k) else None
                    est5, est7, estt = f("est_5h"), f("est_7d"), f("est_tokens")
                except ValueError:
                    return self._send(400, {"error": "est_5h / est_7d / est_tokens must be numbers"})
                return self._send(200, jobs.suggest(own, q.get("kind", ""), q.get("model", ""), est5, est7, est_tok=estt))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                self._post()
            except (ValueError, TypeError) as e:
                self._send(400, {"error": str(e)})
            except Exception as e:
                self._fail(e)

        def _post(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "json required"})
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                n = -1
            if n < 0:
                self.close_connection = True
                return self._send(400, {"error": "bad Content-Length"})
            if n > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": "body too large (max %d bytes)" % MAX_BODY})
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("object expected")
            path = urlparse(self.path).path
            if path == "/api/orders":
                own, md = self._all_jobs()
                if data.get("dry_run"):
                    o = orders.validate(data)
                    return self._send(200, {"conflicts": orders.conflicts_for(
                        o, own + md, orders.list_orders(cfg["outbox_dir"]))})
                o, warn = orders.create(cfg["outbox_dir"], data, own + md)
                return self._send(200, {"order": o, "conflicts": warn})
            if path == "/api/jobs":
                return self._send(200, queue.add(data))
            m = re.fullmatch(r"/api/jobs/(J\d+)/(status|start|finish)", path)
            if m:
                jid, action = m.groups()
                if action == "status":
                    return self._send(200, queue.set_status(jid, data.get("status")))
                if action == "start":
                    return self._send(200, queue.start(jid))
                rows = limits.parse_csv(cfg["limits_csv"])
                return self._send(200, queue.finish(jid, rows, bool(data.get("mixed")), tokens=token_meter,
                                                    status="failed" if data.get("failed") else "done",
                                                    result=data.get("result")))
            self._send(404, {"error": "not found"})

    return Handler


def serve(cfg):
    httpd = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), make_handler(cfg))
    print("Claude Token Manager on http://127.0.0.1:%d (Ctrl-C to stop)" % cfg["port"])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
