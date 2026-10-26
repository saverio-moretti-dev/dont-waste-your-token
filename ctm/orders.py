"""Orders to an orchestrator agent, as plain markdown files in `outbox_dir`.

The app only WRITES new orders and READS their state. It never runs Claude or any command:
whoever executes an order edits the `status` line of the front matter.

File name: YYYYMMDD-HHMMSS-<slug>.md. Format:
    ---
    id: 20261014-201500-fix-tests
    created: 2026-10-14T20:15:00
    status: new
    targets: [repo-a, repo-b]
    priority: 3
    job: J1
    ---
    Free text of the order.
"""
import os
import re
import tempfile
import threading
from datetime import datetime

from . import conflicts, jobs

STATUSES = ("new", "taken", "done", "rejected")
OPEN = ("new", "taken")
MAX_TEXT = 20000
NAME = re.compile(r"\d{8}-\d{6}-.+\.md")  # only files named like this are orders
_LOCK = threading.Lock()


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().split("\n", 1)[0]).strip("-")[:40].strip("-")
    return s or "order"


def _one_line(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def parse(text):
    """Parse front matter + body. Returns a dict (unknown keys kept) or None if malformed."""
    m = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", text, re.S)
    if not m:
        return None
    meta, dup = {}, False
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            if k in meta:
                dup = True  # first occurrence wins, but the file is flagged
                continue
            meta[k] = v.strip()
    t = meta.get("targets", "").strip()
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1]
    meta["targets"] = jobs.split_targets(t)
    try:
        meta["priority"] = int(meta.get("priority", 5))
    except ValueError:
        meta["priority"] = 5
    meta["status"] = "invalid" if dup else meta.get("status", "new").lower()
    meta["text"] = m.group(2).strip()
    return meta


def list_orders(outbox, status=None):
    out = []
    if not outbox or not os.path.isdir(outbox):
        return out
    for fn in sorted(os.listdir(outbox)):
        path = os.path.join(outbox, fn)
        if not NAME.fullmatch(fn) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                o = parse(f.read())
        except OSError:
            continue
        if o is None:
            o = {"id": fn[:-3], "status": "invalid", "targets": [], "priority": 5, "text": "(unreadable front matter)"}
        o.setdefault("id", fn[:-3])
        o["file"] = fn
        o["unknown_status"] = o["status"] not in STATUSES and o["status"] != "invalid"
        if status is None or o["status"] == status:
            out.append(o)
    return out


def conflicts_for(order, queue_jobs, open_orders):
    """Conflicts of a (not yet saved) order against queue jobs and other open orders."""
    mine = {"id": "NEW", "title": order["text"].split("\n", 1)[0][:80], "targets": order["targets"],
            "status": "queued", "kind": ""}
    others = [j for j in queue_jobs if j["id"] != order.get("job")]
    others += [{"id": o["id"], "title": o["text"].split("\n", 1)[0][:80], "targets": o["targets"],
                "status": "queued", "kind": ""} for o in open_orders if o["status"] in OPEN]
    return [c for c in conflicts.find([mine] + others) if "NEW" in (c["a"], c["b"])]


def validate(data):
    text = str(data.get("text", "")).strip()
    if not text:
        raise ValueError("text required")
    if len(text) > MAX_TEXT:
        raise ValueError("text too long")
    try:
        prio = int(data.get("priority") or 5)
    except (TypeError, ValueError):
        raise ValueError("priority must be an integer")
    return {"text": text, "targets": jobs.split_targets(data.get("targets") or []),
            "priority": prio, "job": _one_line(data.get("job"))}


def create(outbox, data, queue_jobs=(), now=None):
    """Validate, check conflicts, write the file. Returns (order, conflicts)."""
    o = validate(data)
    warnings = conflicts_for(o, list(queue_jobs), list_orders(outbox))
    now = now or datetime.now()
    with _LOCK:
        os.makedirs(outbox, exist_ok=True)
        base = "%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), _slug(o["text"]))
        created = now.strftime("%Y-%m-%dT%H:%M:%S")
        n = 1
        while True:
            oid = base if n == 1 else "%s-%d" % (base, n)
            head = "---\nid: %s\ncreated: %s\nstatus: new\ntargets: [%s]\npriority: %d\njob: %s\n---\n" % (
                oid, created, ", ".join(o["targets"]), o["priority"], o["job"])
            fd, tmp = tempfile.mkstemp(dir=outbox, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(head + o["text"] + "\n")
            try:
                os.link(tmp, os.path.join(outbox, oid + ".md"))  # atomic, fails if the name exists
                break
            except FileExistsError:
                n += 1
            finally:
                os.unlink(tmp)
    o.update(id=oid, created=created, status="new", file=oid + ".md")
    return o, warnings
