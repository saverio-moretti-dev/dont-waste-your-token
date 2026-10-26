"""Job queue: own JSON file (read/write), markdown table adapter (read only), calibration."""
import json
import math
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime

from . import limits

_LOCK = threading.RLock()  # one process serves the page: a lock around load-modify-save is enough
MIN_CLEAN_SECONDS = 120    # shorter runs cannot be measured with integer percentages: marked mixed


class QueueCorrupt(Exception):
    """queue.json exists but cannot be read: never overwritten, reported to the user instead."""


class UnknownJob(KeyError):
    pass

STATUSES = ("queued", "approved", "running", "blocked", "done", "failed", "cancelled")
# queued = proposed, waiting for a decision (a runner never starts it); approved = may be run.
# Keywords for the free-text status cell of an imported markdown table (case-insensitive, whole
# words/phrases). Checked in PRIORITY order; `done` only counts if no `running` keyword matched.
# "restano" (plural) is deliberately NOT a running keyword: "what remains for Thursday" on a job that
# was partly anticipated does not mean the job is running.
DEFAULT_KEYWORDS = {
    "cancelled": ["annullato", "annullata", "annullati", "annullate", "cancelled", "canceled"],
    "blocked": ["bloccato", "bloccata", "bloccati", "bloccate", "blocked"],
    "running": ["in corso", "running", "in progress", "resta"],
    "done": ["fatto", "fatta", "fatte", "fatti", "done"],
    "approved": ["approvato", "approvata", "approvati", "approvate", "approved"],
    "queued": ["in coda", "queued"],
}
PRIORITY = ("cancelled", "blocked", "running", "done", "approved", "queued")
OVERRIDE_KEYS = ("status", "estimate_pct_7d", "estimate_pct_5h", "targets", "priority")
FIELDS = ("title", "project", "kind", "model", "est_tokens", "estimate_pct_5h",
          "estimate_pct_7d", "notes", "targets", "priority", "after")
# est_tokens and actual_tokens are WEIGHTED tokens (usage.weigh: input 1, output 5, cache write 1.25,
# cache read 0.1), the unit used to calibrate token estimates; actual_tokens_raw keeps the 4 counters.


def _now_text():
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _epoch(text):
    return limits.parse_time(text)


def _number(name, v, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % name)
    if not math.isfinite(x) or x < 0 or x > hi:
        raise ValueError("%s out of range (0..%g)" % (name, hi))
    return x


class Queue:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "queue.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            jobs = data["jobs"]
            if not isinstance(jobs, list) or not all(isinstance(j, dict) and isinstance(j.get("id"), str) for j in jobs):
                raise TypeError("jobs must be a list of objects with an id")
            return jobs
        except FileNotFoundError:
            return []
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise QueueCorrupt("queue.json is unreadable (%s): %s - fix or restore queue.json.bak; "
                               "the file was NOT modified" % (self.path, e))

    def save(self, jobs):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix="queue.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"jobs": jobs}, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(self.path):
            shutil.copyfile(self.path, self.path + ".bak")  # last good version
        os.replace(tmp, self.path)

    def add(self, data):
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("title required")
        with _LOCK:
            return self._add(title, data)

    def _add(self, title, data):
        jobs = self.load()
        n = 1 + max([int(j["id"][1:]) for j in jobs if re.fullmatch(r"J\d+", j["id"])] or [0])
        job = {"id": "J%d" % n, "title": title, "project": "", "kind": "", "model": "",
               "est_tokens": None, "estimate_pct_5h": None, "estimate_pct_7d": None,
               "targets": [], "priority": 5, "actual_pct_5h": None, "actual_pct_7d": None, "measure": None,
               "actual_tokens": None, "actual_tokens_raw": None, "token_measure": None, "session_ids": [],
               "attempts": 0, "result": "", "after": None,
               "started_at": None, "finished_at": None, "status": "queued", "notes": "",
               "created": datetime.now().strftime("%Y-%m-%d")}
        for k in FIELDS:
            if k in data and data[k] not in (None, ""):
                job[k] = data[k]
        if data.get("status") in ("queued", "approved"):
            job["status"] = data["status"]
        if job["after"] is not None:
            job["after"] = str(job["after"]).strip() or None
            if job["after"] and job["after"] not in {j["id"] for j in jobs}:
                raise ValueError("after: unknown job %s" % job["after"])
        for k, hi in (("est_tokens", 1e12), ("estimate_pct_5h", 1000), ("estimate_pct_7d", 1000)):
            if job[k] is not None:
                job[k] = _number(k, job[k], hi)
        job["targets"] = split_targets(job["targets"])
        job["priority"] = int(job["priority"])
        jobs.append(job)
        self.save(jobs)
        return job

    def _get(self, jobs, jid):
        for j in jobs:
            if j["id"] == jid:
                return j
        raise UnknownJob(jid)

    def set_status(self, jid, status):
        if status not in STATUSES:
            raise ValueError("bad status")
        with _LOCK:
            jobs = self.load()
            j = self._get(jobs, jid)
            j["status"] = status
            if status in ("cancelled", "blocked", "queued") and not j.get("finished_at"):
                j["started_at"] = None  # an abandoned run must not blur later measurements
            self.save(jobs)
            return j

    def start(self, jid, at=None):
        with _LOCK:
            jobs = self.load()
            j = self._get(jobs, jid)
            j["started_at"] = at or _now_text()
            j["finished_at"] = None
            j["status"] = "running"
            self.save(jobs)
            return j

    def finish(self, jid, rows, mixed=False, at=None, tokens=None, status="done", result=None):
        """Close a job and measure the real consumption from the limit readings.

        `tokens(t0, t1, session_ids)` (optional) returns (raw [in, out, cw, cr], weighted, how): with the
        job's session ids the token cost is exact ("sessions") even if other jobs ran in parallel;
        without them it is every session in the interval ("interval", mixed if anything overlapped)."""
        if status not in ("done", "failed"):
            raise ValueError("a job finishes as done or failed")
        with _LOCK:
            jobs = self.load()
            j = self._get(jobs, jid)
            if not j.get("started_at"):
                raise ValueError("job not started")
            j["finished_at"] = at or _now_text()
            t0, t1 = _epoch(j["started_at"]), _epoch(j["finished_at"])
            j.update(limits.delta_between(rows, t0, t1))
            overlap = any(o is not j and o.get("status") == "running" and o.get("started_at") and not o.get("finished_at")
                          for o in jobs) or \
                any(o is not j and o.get("started_at") and o.get("finished_at")
                    and _epoch(o["started_at"]) < t1 and _epoch(o["finished_at"]) > t0 for o in jobs)
            too_short = (t1 - t0) < MIN_CLEAN_SECONDS
            j["measure"] = "mixed" if (mixed or overlap or too_short) else "clean"
            if tokens:
                raw, weighted, how = tokens(t0, t1, j.get("session_ids") or [])
                if any(raw):
                    j["actual_tokens_raw"], j["actual_tokens"] = raw, round(weighted)
                    j["token_measure"] = how if how == "sessions" or not (mixed or overlap) else "mixed"
            j["status"] = status
            if result is not None:
                j["result"] = str(result)[:500]
            self.save(jobs)
            return j

    def add_run(self, jid, session_id=None, raw=None, weighted=None):
        """A runner reports one execution of a job: its session id and the tokens it used (summed over
        attempts, so an interrupted run still counts)."""
        with _LOCK:
            jobs = self.load()
            j = self._get(jobs, jid)
            if session_id and session_id not in (j.get("session_ids") or []):
                j["session_ids"] = (j.get("session_ids") or []) + [session_id]
            if raw:
                old = j.get("actual_tokens_raw") or [0, 0, 0, 0]
                j["actual_tokens_raw"] = [a + b for a, b in zip(old, raw)]
                j["actual_tokens"] = round((j.get("actual_tokens") or 0) + (weighted or 0))
                j["token_measure"] = "sessions"
            self.save(jobs)
            return j

    def update(self, jid, **fields):
        """Runner bookkeeping: attempts, result, status back to approved after an interruption."""
        with _LOCK:
            jobs = self.load()
            j = self._get(jobs, jid)
            for k, v in fields.items():
                if k == "status" and v not in STATUSES:
                    raise ValueError("bad status")
                j[k] = v
            self.save(jobs)
            return j


def runnable(jobs_list):
    """Approved jobs whose `after` job is done, by priority (1 first) then by number."""
    done = {j["id"] for j in jobs_list if j.get("status") == "done"}

    def num(j):
        m = re.fullmatch(r"J(\d+)", j["id"])
        return int(m.group(1)) if m else 10 ** 9
    out = [j for j in jobs_list if j.get("status") == "approved" and (not j.get("after") or j["after"] in done)]
    return sorted(out, key=lambda j: (int(j.get("priority") or 5), num(j)))


def _usable(est, act):
    """Sample usable for calibration. Percentages are integers: a tiny job whose real cost is 0 or 1
    point tells nothing (and a 0 would otherwise zero every future estimate)."""
    return bool(est) and est > 0 and act is not None and not (est < 3 and act < 2)


def calibration(jobs):
    """Actual/estimate ratio per window as sum(actual)/sum(estimate) (weights by size), grouped by
    (kind, model), kind, global. A measured 0 is treated as 1 point (censored: the true value is < 1).
    `min`/`max` are the extreme per-sample ratios."""
    groups = {}
    for j in jobs:
        if j.get("status") != "done":
            continue
        samples = []
        if j.get("measure") == "clean":
            for w in ("5h", "7d"):
                est, act = j.get("estimate_pct_" + w), j.get("actual_pct_" + w)
                if _usable(est, act):
                    samples.append((w, est, max(act, 1.0)))
        # tokens: exact per session ("sessions") or a clean interval; no integer rounding problem here
        if j.get("token_measure") in ("sessions", "interval") and j.get("est_tokens") and j.get("actual_tokens"):
            samples.append(("tok", float(j["est_tokens"]), float(j["actual_tokens"])))
        for w, est, act in samples:
            for key in (("km", j.get("kind") or "", j.get("model") or ""),
                        ("k", j.get("kind") or "", ""), ("g", "", "")):
                groups.setdefault((w,) + key, []).append((est, act))
    out = {}
    for k, v in groups.items():
        ratios = [a / e for e, a in v]
        out["|".join(k)] = {"ratio": round(sum(a for _, a in v) / sum(e for e, _ in v), 3), "n": len(v),
                            "min": round(min(ratios), 3), "max": round(max(ratios), 3)}
    return out


def suggest(jobs, kind="", model="", est_5h=None, est_7d=None, min_samples=2, est_tok=None):
    """Corrected estimate for a new job, with the basis it rests on and a min-max range."""
    cal = calibration(jobs)
    out = {}
    for w, est in (("5h", est_5h), ("7d", est_7d), ("tok", est_tok)):
        found = None
        for level, key in (("kind+model", "|".join((w, "km", kind, model))),
                           ("kind", "|".join((w, "k", kind, ""))),
                           ("global", "|".join((w, "g", "", "")))):
            c = cal.get(key)
            if c and c["n"] >= min_samples:
                found = (level, c)
                break
        if found and est is not None:
            c = found[1]
            out[w] = {"raw": est, "corrected": round(est * c["ratio"], 2), "ratio": c["ratio"],
                      "samples": c["n"], "basis": found[0],
                      "range": [round(est * c["min"], 2), round(est * c["max"], 2)]}
        else:
            out[w] = {"raw": est, "corrected": est, "ratio": 1.0, "samples": 0, "basis": "none", "range": None}
    return out


def split_targets(value):
    """Targets from a list or a free text cell: drop (...) notes, split on , ; +, and remove
    characters that could break the order front matter (brackets, colons, control chars)."""
    text = ",".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value or "")
    text = re.sub(r"\([^)]*\)", "", text)
    out = []
    for p in re.split(r"[,;+]", text):
        p = re.sub(r"[`*\[\]:]", "", p)
        p = re.sub(r"\s+", " ", p).strip().strip("/").lower()
        if p and p not in out:
            out.append(p)
    return out


# ---- read-only markdown adapter -------------------------------------------------

def _cells(line):
    """Split a table row on `|`, ignoring `\\|` and pipes inside backticks."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, tick, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            tick = not tick
        if ch == "|" and not tick:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _clean_id(v):
    return re.sub(r"[*`]", "", v or "").strip()


def parse_markdown(text, keywords=None, overrides=None, skipped=None):
    """Parse the first markdown table that has an `ID` column into job dicts.

    Rows that cannot be read (wrong number of cells) are not lost silently: their text is appended
    to the optional `skipped` list."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and _clean_id(_cells(line)[0]).lower() == "id":
            head = [h.lower() for h in _cells(line)]
            start = i + 1
            if start < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[start]) and "-" in lines[start]:
                start += 1  # separator row (optional for the parser)
            jobs = []
            for row in lines[start:]:
                if not row.lstrip().startswith("|"):
                    break
                c = _cells(row)
                if len(c) != len(head):
                    if skipped is not None:
                        skipped.append(row.strip()[:160])
                    continue
                d = dict(zip(head, c))
                d["id"] = _clean_id(d.get("id"))
                jobs.append(apply_override(_md_job(d, keywords), (overrides or {}).get(d["id"], {})))
            return jobs
    return []


def _col(d, *names):
    for k, v in d.items():
        if any(k.startswith(n) for n in names):
            return v
    return ""


NEGATION = re.compile(r"(?<!\w)(?:non|not|no|mai|never)(?:\s+\w+){0,2}\s+$")


def _has(text, words):
    """A keyword counts unless a negation (non/not/no/mai/never) precedes it within two words."""
    for w in words:
        for m in re.finditer(r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)", text):
            if not NEGATION.search(text[max(0, m.start() - 40):m.start()]):
                return True
    return False


def recognize_status(text, keywords=None):
    """Map the free text of a status cell to one status. Returns (status, how)."""
    kw = dict(DEFAULT_KEYWORDS)
    kw.update(keywords or {})
    t = re.sub(r"[*`_]", "", text or "").lower()
    hits = {st: _has(t, kw.get(st, [])) for st in PRIORITY}
    for st in PRIORITY:
        if not hits[st]:
            continue
        if st == "done" and hits["running"]:
            continue
        return st, "keyword"
    return "queued", "default"


def check_keywords(kw):
    if not isinstance(kw, dict):
        raise ValueError("markdown_status_keywords must be an object")
    for st, words in kw.items():
        if st not in PRIORITY:
            raise ValueError("markdown_status_keywords: unknown status %r" % st)
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise ValueError("markdown_status_keywords[%s] must be a list of strings" % st)


def check_overrides(overrides):
    if not isinstance(overrides, dict):
        raise ValueError("markdown_overrides must be an object")
    for jid, o in overrides.items():
        if not isinstance(o, dict):
            raise ValueError("markdown_overrides[%s] must be an object" % jid)
        bad = set(o) - set(OVERRIDE_KEYS)
        if bad:
            raise ValueError("markdown_overrides[%s]: unknown keys %s" % (jid, sorted(bad)))
        if "status" in o and o["status"] not in STATUSES:
            raise ValueError("markdown_overrides[%s]: bad status %r" % (jid, o["status"]))
        for k in ("estimate_pct_5h", "estimate_pct_7d"):
            if o.get(k) is not None:
                _number("markdown_overrides[%s].%s" % (jid, k), o[k], 1000)
        if "priority" in o:
            int(o["priority"])


def apply_override(job, o):
    for k in OVERRIDE_KEYS:
        if k not in o:
            continue
        if k == "targets":
            job[k] = split_targets(o[k])
        elif k == "priority":
            job[k] = int(o[k])
        elif k == "status":
            job[k] = o[k]
            job["status_how"] = "override"
        else:
            job[k] = None if o[k] is None else float(o[k])
    return job


def _md_job(d, keywords=None):
    state_text = _col(d, "stato")
    status, how = recognize_status(state_text, keywords)
    work = _col(d, "lavoro")
    title = re.sub(r"\*\*|`", "", work).split(":")[0].strip()[:120] or work[:120]
    return {"id": d.get("id", ""), "title": title, "project": _col(d, "repo", "progetto"),
            "kind": "", "model": "", "est_tokens": None, "estimate_pct_5h": None,
            "estimate_pct_7d": None, "actual_pct_5h": None, "actual_pct_7d": None,
            "targets": split_targets(_col(d, "repo", "progetto")), "priority": 5,
            "measure": None, "started_at": None, "finished_at": None,
            "status": status, "status_how": how, "status_text": state_text[:300],
            "notes": _col(d, "stima")[:300], "source": "markdown"}


def load_markdown(path, keywords=None, overrides=None, skipped=None):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_markdown(f.read(), keywords, overrides, skipped)


def load_markdown_cfg(cfg, skipped=None):
    return load_markdown(cfg.get("markdown_queue"), cfg.get("markdown_status_keywords"),
                         cfg.get("markdown_overrides"), skipped)
