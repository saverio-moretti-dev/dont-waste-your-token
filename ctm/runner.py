"""Optional runner: executes APPROVED jobs one at a time with `claude -p`, until a deadline.

This is NOT part of the page/server (which never runs anything): it is a separate command the user
starts explicitly (`python -m ctm run`), by hand or from their own scheduler.

Rules (learned the hard way):
- only `approved` jobs whose `after` job is done; priority 1 first;
- the agent closes its own job (`python -m ctm job finish <id> --result "..."` or `--failed`): a job the
  agent did NOT close is never marked done;
- interrupted by a usage limit -> back to approved, wait, retry until the deadline (it is not a failure);
- any other interruption -> back to approved and retry, at most `max_attempts`, then failed;
- timeout -> failed;
- the real token cost of every run is recorded from the JSON output of `claude -p` (exact per session).
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from . import jobs, limits, usage

LIMIT_WORDS = ("usage limit", "limit reached", "rate limit", "hit your limit", "resets ", "overloaded",
               "quota", "limite di utilizzo")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BRIEFING = os.path.join(os.path.dirname(HERE), "runner", "briefing.example.md")


class _Keep(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def is_limit(text):
    t = (text or "").lower()
    return any(w in t for w in LIMIT_WORDS)


def parse_output(stdout):
    """Fields of `claude -p --output-format json`: session_id, usage, is_error, result text."""
    try:
        d = json.loads(stdout)
    except (ValueError, TypeError):
        # noise before the JSON (e.g. a wrapper printed something): take the last line that is a JSON object
        d = {}
        for ln in reversed((stdout or "").splitlines()):
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    d = json.loads(ln)
                    break
                except ValueError:
                    continue
    if isinstance(d, list):  # stream-json style: take the final result object
        d = next((x for x in reversed(d) if isinstance(x, dict) and x.get("type") == "result"), {})
    return d if isinstance(d, dict) else {}


def ctm_command(cfg_path):
    """How an agent calls back into the queue (same interpreter, repo on PYTHONPATH)."""
    exe = '"%s"' % sys.executable
    return "%s -m ctm%s" % (exe, ' --config "%s"' % cfg_path if cfg_path else "")


def briefing(cfg, job, cfg_path=None):
    path = cfg["runner"].get("briefing") or DEFAULT_BRIEFING
    with open(path, encoding="utf-8") as f:
        tpl = f.read()
    cmd = ctm_command(cfg_path)
    return tpl.format_map(_Keep(
        id=job["id"], title=job.get("title", ""), project=job.get("project", ""), kind=job.get("kind", ""),
        model=job.get("model", ""), targets=", ".join(job.get("targets") or []) or "-",
        notes=job.get("notes", "") or "-", result=job.get("result", "") or "-",
        attempt=int(job.get("attempts") or 0) + 1,
        finish_cmd='%s job finish %s --result "<one line: what was done>"' % (cmd, job["id"]),
        fail_cmd='%s job finish %s --failed --result "<one line: why>"' % (cmd, job["id"])))


class Runner:
    def __init__(self, cfg, cfg_path=None, clock=time.time, sleep=time.sleep, run=subprocess.run, out=None):
        self.cfg, self.cfg_path, self.r = cfg, cfg_path, cfg["runner"]
        self.q = jobs.Queue(cfg["data_dir"])
        self.clock, self.sleep, self.run_proc = clock, sleep, run
        log_dir = self.r.get("log_dir") or cfg["data_dir"]
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "runner-%s.log" % datetime.now().strftime("%Y%m%d"))
        self.out = out

    def log(self, msg):
        line = "[%s] %s" % (datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d %H:%M:%S"), msg)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.out:
            print(line, file=self.out)

    def _five_hour(self):
        rows = limits.parse_csv(self.cfg["limits_csv"])
        last = next((x for x in reversed(rows) if x["p5"] is not None), None)
        if not last or (last["r5"] and last["r5"] < self.clock()):
            return None  # no reading, or the window has reset since
        return last["p5"]

    def _rows(self):
        return limits.parse_csv(self.cfg["limits_csv"])

    def _exec(self, job, deadline):
        cmd = list(self.r["command"]) + ["-p", briefing(self.cfg, job, self.cfg_path)] + \
            list(self.r["args"]) + list(self.r["extra_args"])
        env = dict(os.environ, PYTHONUTF8="1", CTM_RUNNER="1", CTM_JOB=job["id"],
                   PYTHONPATH=os.pathsep.join(p for p in (os.path.dirname(HERE), os.environ.get("PYTHONPATH")) if p))
        if self.cfg_path:
            env["CTM_CONFIG"] = self.cfg_path
        timeout = max(60, min(self.r["max_hours_per_job"] * 3600, deadline - self.clock()))
        try:
            p = self.run_proc(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=self.r.get("cwd") or None, timeout=timeout, env=env)
            return p.returncode, p.stdout or "", p.stderr or ""
        except subprocess.TimeoutExpired:
            return None, "", "TIMEOUT"

    def run(self, until=None, max_jobs=None, dry_run=False):
        deadline = until or (self.clock() + 12 * 3600)
        self.log("start, deadline %s" % datetime.fromtimestamp(deadline).strftime("%Y-%m-%d %H:%M"))
        started = 0
        while self.clock() < deadline:
            cand = jobs.runnable(self.q.load())
            if not cand:
                self.log("nothing approved to run")
                break
            if dry_run:
                for j in cand:
                    self.log("would run %s (priority %s): %s" % (j["id"], j.get("priority"), j["title"]))
                break
            if max_jobs is not None and started >= max_jobs:
                self.log("max jobs reached (%d)" % max_jobs)
                break
            p5 = self._five_hour()
            if p5 is not None and p5 >= self.r["stop_at_5h_pct"]:
                self.log("5h window at %s%%: waiting %s min" % (p5, self.r["wait_minutes"]))
                self.sleep(self.r["wait_minutes"] * 60)
                continue
            j = cand[0]
            self.q.start(j["id"])
            started += 1
            self.log("run %s (attempt %d): %s" % (j["id"], int(j.get("attempts") or 0) + 1, j["title"]))
            rc, so, se = self._exec(j, deadline)
            out = parse_output(so)
            u = out.get("usage") or {}
            raw = [int(u.get(k) or 0) for k in usage.FIELDS]
            self.q.add_run(j["id"], out.get("session_id"), raw if any(raw) else None,
                           usage.weigh(raw, self.cfg.get("token_weights")))
            text = (out.get("result") or "") + "\n" + so[-2000:] + "\n" + se[-2000:]
            self.log("end %s rc=%s session=%s tokens=%s %s" % (j["id"], rc, out.get("session_id"), raw,
                                                               text.strip().replace("\n", " | ")[-300:]))
            now_j = next((x for x in self.q.load() if x["id"] == j["id"]), {})
            if now_j.get("status") != "running":
                self.log("%s closed by the agent: %s" % (j["id"], now_j.get("status")))
                continue
            # not closed by the agent: NEVER done
            if rc is None:
                self.q.finish(j["id"], self._rows(), status="failed", result="timeout")
                self.log("%s timeout -> failed" % j["id"])
                continue
            if is_limit(text):
                self.q.update(j["id"], status="approved", started_at=None, result="interrupted: usage limit")
                self.log("%s interrupted by a usage limit: retry after %s min" % (j["id"], self.r["wait_minutes"]))
                self.sleep(self.r["wait_minutes"] * 60)
                continue
            n = int(now_j.get("attempts") or 0) + 1
            if n < self.r["max_attempts"]:
                self.q.update(j["id"], status="approved", started_at=None, attempts=n,
                              result="interrupted (attempt %d, rc=%s)" % (n, rc))
                self.log("%s not closed by the agent (attempt %d): retry after %s min" % (j["id"], n, self.r["wait_minutes"]))
                self.sleep(self.r["wait_minutes"] * 60)
                continue
            self.q.update(j["id"], attempts=n)
            self.q.finish(j["id"], self._rows(), status="failed",
                          result="not closed by the agent after %d attempts: %s" % (n, text.strip()[-200:]))
            self.log("%s failed after %d attempts" % (j["id"], n))
        self.log("end of run")
