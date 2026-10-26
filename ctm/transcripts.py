"""Token usage from Claude Code transcripts (~/.claude/projects/<project>/*.jsonl).

Only `type=assistant` lines with `message.usage` are read; text is never kept.
The same message id can appear several times (streaming): the last occurrence wins.
A disk cache keyed by (mtime, size) avoids re-reading unchanged files.
"""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta

CACHE_VERSION = 1  # bump when FIELDS or the parsing logic change
FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _local_day(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def parse_file(path):
    """Return {day: [input, output, cache_creation, cache_read]} for one transcript."""
    msgs = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
                m = d.get("message")
                if d.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
                    continue
                day = _local_day(d.get("timestamp"))
                if not day:
                    continue
                u = m["usage"]
                key = m.get("id") or "line%d" % n
                msgs[key] = (day, [int(u.get(k) or 0) for k in FIELDS])
            except (ValueError, TypeError, AttributeError):
                continue  # one malformed line must not break the whole scan
    out = {}
    for day, vals in msgs.values():
        acc = out.setdefault(day, [0, 0, 0, 0])
        for i, v in enumerate(vals):
            acc[i] += v
    return out


def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data["files"] if data.get("v") == CACHE_VERSION else {}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


def scan(root, cache_path=None, days=7, now=None):
    """Aggregate usage of the last `days` days, per day and per project."""
    now = time.time() if now is None else now
    first_day = (datetime.fromtimestamp(now) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    cache = _load_cache(cache_path) if cache_path else {}
    new_cache, per_day, per_project = {}, {}, {}
    files_read = 0
    if root and os.path.isdir(root):
        for proj in sorted(os.listdir(root)):
            pdir = os.path.join(root, proj)
            if not os.path.isdir(pdir):
                continue
            files = [os.path.join(dp, fn) for dp, _, fns in os.walk(pdir) for fn in fns if fn.endswith(".jsonl")]
            for path in sorted(files):  # includes <session>/subagents/*.jsonl
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_mtime < now - (days + 1) * 86400:
                    continue
                ent = cache.get(path)
                if not ent or ent.get("mtime") != st.st_mtime or ent.get("size") != st.st_size:
                    try:
                        ent = {"mtime": st.st_mtime, "size": st.st_size, "days": parse_file(path)}
                    except OSError:
                        continue
                    files_read += 1
                new_cache[path] = ent
                for day, vals in ent["days"].items():
                    if day < first_day:
                        continue
                    for tgt, key in ((per_day, day), (per_project, proj)):
                        acc = tgt.setdefault(key, [0, 0, 0, 0])
                        for i, v in enumerate(vals):
                            acc[i] += v
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache_path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "files": new_cache}, f)
            os.replace(tmp, cache_path)
        except OSError:
            pass

    def row(name, v):
        return {"name": name, "input": v[0], "output": v[1], "cache_creation": v[2],
                "cache_read": v[3], "total": sum(v)}
    return {
        "days": [row(k, per_day[k]) for k in sorted(per_day)],
        "projects": sorted((row(k, v) for k, v in per_project.items()), key=lambda r: -r["total"]),
        "files_read": files_read,
    }
