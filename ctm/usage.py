"""Token usage per hour and per session, from Claude Code transcripts.

Complements transcripts.py (per day / per project, for the page) with two finer views:
- per hour: tokens spent inside any time interval (e.g. the current weekly window) -> tokens per 1%;
- per session: the exact cost of one job run (a `claude -p` session), even when other jobs run in parallel.

Only `type=assistant` lines with `message.usage` are read; the text is never kept. The same message id
can appear several times (streaming): the last occurrence wins. The session id is the `sessionId` field
of the line, else the file name. Disk cache keyed by (mtime, size), like transcripts.py.
"""
import json
import os
import tempfile
import time
from datetime import datetime

CACHE_VERSION = 1
FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
# Default weights = ratios of API prices (input 1, output 5, cache write 1.25, cache read 0.1).
# A convention to compare jobs, NOT Anthropic's limit formula (which is not public): calibrate with
# tokens_per_pct() against the real percentages.
DEFAULT_WEIGHTS = {"input": 1.0, "output": 5.0, "cache_creation": 1.25, "cache_read": 0.1}
KEYS = ("input", "output", "cache_creation", "cache_read")


def weigh(vals, weights=None):
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    return sum(v * w[k] for k, v in zip(KEYS, vals))


def _hour(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return int(dt.timestamp() // 3600 * 3600)  # epoch of the hour start (timezone independent)


def parse_file(path):
    """Return {"hours": {epoch_hour: [in, out, cw, cr]}, "sessions": {session_id: [in, out, cw, cr]},
    "first": epoch, "last": epoch} for one transcript."""
    msgs = {}
    default_sid = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
                m = d.get("message")
                if d.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
                    continue
                h = _hour(d.get("timestamp"))
                if h is None:
                    continue
                t = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                u = m["usage"]
                msgs[m.get("id") or "line%d" % n] = (h, t, d.get("sessionId") or default_sid,
                                                     [int(u.get(k) or 0) for k in FIELDS])
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
    hours, sessions, first, last = {}, {}, None, None
    for h, t, sid, vals in msgs.values():
        for tgt, key in ((hours, str(h)), (sessions, sid)):
            acc = tgt.setdefault(key, [0, 0, 0, 0])
            for i, v in enumerate(vals):
                acc[i] += v
        first = t if first is None else min(first, t)
        last = t if last is None else max(last, t)
    return {"hours": hours, "sessions": sessions, "first": first, "last": last}


def _files(root, since):
    if not root or not os.path.isdir(root):
        return
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith(".jsonl"):
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime >= since:
                    yield p, st


def load(root, cache_path=None, since=None, now=None):
    """Parsed data of every transcript modified after `since` (default: 8 days ago), using the cache."""
    now = time.time() if now is None else now
    since = now - 8 * 86400 if since is None else since
    cache = {}
    if cache_path:
        try:
            with open(cache_path, encoding="utf-8") as f:
                c = json.load(f)
            cache = c["files"] if c.get("v") == CACHE_VERSION else {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            cache = {}
    out, changed = {}, False
    for p, st in _files(root, since):
        ent = cache.get(p)
        if not ent or ent.get("mtime") != st.st_mtime or ent.get("size") != st.st_size:
            try:
                ent = dict(parse_file(p), mtime=st.st_mtime, size=st.st_size)
            except OSError:
                continue
            changed = True
        out[p] = ent
    if cache_path and (changed or set(out) != set(cache)):
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache_path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "files": out}, f)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return out


def between(data, t0, t1):
    """Raw tokens [in, out, cw, cr] of all sessions in the hours that overlap [t0, t1] (hour precision)."""
    acc = [0, 0, 0, 0]
    lo = int(t0 // 3600 * 3600)
    for ent in data.values():
        for h, vals in ent["hours"].items():
            if lo <= int(h) <= t1:
                for i, v in enumerate(vals):
                    acc[i] += v
    return acc


def of_sessions(data, session_ids):
    """Exact raw tokens of the given sessions (subagent files of a session count too)."""
    want = set(session_ids or [])
    acc = [0, 0, 0, 0]
    for ent in data.values():
        for sid, vals in ent["sessions"].items():
            if sid in want:
                for i, v in enumerate(vals):
                    acc[i] += v
    return acc


def active_sessions(data, now=None, minutes=30):
    """Sessions with activity in the last `minutes`: [{session, project, last, tokens}]."""
    now = time.time() if now is None else now
    out = []
    for path, ent in data.items():
        if not ent.get("last") or ent["last"] < now - minutes * 60:
            continue
        project = os.path.basename(os.path.dirname(path))
        if project == "subagents":
            project = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
        for sid, vals in ent["sessions"].items():
            out.append({"session": sid, "project": project, "last": ent["last"],
                        "input": vals[0], "output": vals[1], "cache_creation": vals[2], "cache_read": vals[3]})
    out.sort(key=lambda r: -r["last"])
    return out


def tokens_per_pct(rows, data, weights=None, min_pct=3, now=None):
    """Weighted tokens worth 1% of the weekly limit, measured in the current 7-day window.

    = weighted tokens of this machine's transcripts since the window started / latest weekly %.
    Needs a reading with a reset time and at least `min_pct` points (integer percentages).
    Sessions on other machines or in the cloud are not in the transcripts: then the value is too LOW.
    """
    now = time.time() if now is None else now
    last = next((r for r in reversed(rows) if r.get("p7") is not None and r.get("r7")), None)
    if not last:
        return {"status": "insufficient", "reason": "nessuna lettura settimanale con ora di ripristino"}
    if last["r7"] < now:
        return {"status": "insufficient", "reason": "finestra settimanale già ripristinata"}
    if last["p7"] < min_pct:
        return {"status": "insufficient", "reason": "consumo settimanale sotto il %d%%" % min_pct}
    start = last["r7"] - 168 * 3600
    raw = between(data, start, last["t"])
    w = weigh(raw, weights)
    return {"status": "ok", "tokens_per_pct": round(w / last["p7"]), "weighted_tokens": round(w),
            "pct": last["p7"], "since": start, "raw": raw}
