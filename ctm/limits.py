"""Plan-limit readings: CSV parsing, pace (rate) and projection, job deltas.

CSV columns: ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d
(local time 'YYYY-MM-DD HH:MM:SS'; resets are epoch seconds; empty = window missing).
"""
import csv
import json
import math
import os
import time
from datetime import datetime


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_time(text, prev=None):
    """Epoch seconds from ISO with offset (`2026-10-14T19:35:22+0200`, `+02:00`, `Z`) or from the
    legacy local format `YYYY-MM-DD HH:MM:SS`. A legacy local time that happens twice (DST end) is
    resolved with `prev` (the previous row, rows being chronological): the earliest occurrence that
    does not go back in time."""
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if dt.tzinfo is not None:  # Python 3.15+: %z also matches an empty offset
            return dt.timestamp()
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    lo, hi = sorted((dt.replace(fold=0).timestamp(), dt.replace(fold=1).timestamp()))
    if lo == hi or prev is None or lo >= prev:
        return lo
    return hi


def parse_csv(path):
    """Return a list of dicts sorted by time: t (epoch), p5, p7, r5, r7 (None if missing)."""
    rows = []
    if not path or not os.path.exists(path):
        return rows
    prev = None
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.reader(f):
            if len(r) < 3 or r[0] == "ora":
                continue
            try:
                t = parse_time(r[0], prev)
            except ValueError:
                continue
            prev = t
            r += [""] * (5 - len(r))
            rows.append({"t": t, "p5": _num(r[1]), "p7": _num(r[2]),
                         "r5": _num(r[3]), "r7": _num(r[4])})
    rows.sort(key=lambda x: x["t"])
    return rows


def pace(rows, key, window_hours=6, min_minutes=10, rkey=None):
    """Recent pace: percent points per hour over the last `window_hours`, since the last reset.

    A reset is a value drop or (if `rkey` is given) a change of the reset time.
    Returns None when there are not enough points or the span is too short.
    Informational only: the projection does not use it (too volatile on short bursts).
    """
    pts = [(r["t"], r[key], r.get(rkey) if rkey else None) for r in rows if r[key] is not None]
    if len(pts) < 2:
        return None
    start = 0
    for i in range(1, len(pts)):
        moved = rkey and pts[i][2] and pts[i - 1][2] and abs(pts[i][2] - pts[i - 1][2]) > 60
        if pts[i][1] < pts[i - 1][1] or moved:
            start = i
    pts = pts[start:]
    t_end = pts[-1][0]
    pts = [p for p in pts if p[0] >= t_end - window_hours * 3600]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < min_minutes * 60:
        return None
    return (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600.0)


def _fmt_time(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def projection(pct, read_t, resets_at, window_hours, min_elapsed_hours, now=None):
    """Project the % at reset using the mean pace of the current window.

    The window started at `resets_at - window_hours`; the rate is pct / hours elapsed at the
    reading. This uses the whole useful span (since the last reset) and ignores short bursts.
    Returns a dict with `status`: `ok`, `exceeds` or `insufficient` (never absurd numbers:
    the displayed value is capped at 100 and `exceeds_at` tells when 100% would be reached).
    """
    now = time.time() if now is None else now
    if pct is None or not resets_at:
        return {"status": "insufficient", "reason": "manca l'ora di ripristino o la percentuale"}
    if resets_at < now:
        return {"status": "insufficient", "reason": "finestra già ripristinata"}
    elapsed = (read_t - (resets_at - window_hours * 3600)) / 3600.0
    if elapsed < min_elapsed_hours:
        return {"status": "insufficient",
                "reason": "finestra più giovane di %g h (%.1f h)" % (min_elapsed_hours, max(elapsed, 0))}
    rate = pct / elapsed
    left_from_read = max(0.0, (resets_at - read_t) / 3600.0)
    value = pct + rate * left_from_read
    out = {"rate_per_hour": round(rate, 3), "basis": "window_mean",
           "hours_left": round(max(0.0, (resets_at - now) / 3600.0), 1)}
    if value >= 100 and rate > 0:
        t100 = read_t + (100 - pct) / rate * 3600
        out.update(status="exceeds", at_reset_pct=100.0, exceeds_at=t100, exceeds_at_text=_fmt_time(t100),
                   raw_at_reset_pct=round(value, 1))
    else:
        out.update(status="ok", at_reset_pct=round(value, 1))
    return out


def summary(rows, window_hours=6, now=None):
    now = time.time() if now is None else now
    out = {"readings": len(rows), "last": None, "five_hour": None, "seven_day": None}
    if not rows:
        return out
    last = rows[-1]
    out["last"] = {"time": datetime.fromtimestamp(last["t"]).strftime("%Y-%m-%d %H:%M:%S")}
    # (name, pct key, reset key, window hours, min useful elapsed hours)
    for name, p, r, wh, minh in (("five_hour", "p5", "r5", 5, 0.5), ("seven_day", "p7", "r7", 168, 12)):
        latest = next((x for x in reversed(rows) if x[p] is not None), None)
        if latest is None:
            continue
        reset = latest[r]
        stale = bool(reset and reset < now)
        recent = pace(rows, p, window_hours, rkey=r)
        out[name] = {
            "pct": latest[p], "read_at": datetime.fromtimestamp(latest["t"]).strftime("%Y-%m-%d %H:%M:%S"),
            "resets_at": reset, "resets_at_text": _fmt_time(reset) if reset else None,
            "stale": stale,
            "rate_per_hour": None if recent is None else round(recent, 2),  # recent pace, informational
            "projection": projection(latest[p], latest["t"], reset, wh, minh, now),
        }
    return out


def series(rows, hours=48, now=None):
    now = time.time() if now is None else now
    return [{"t": r["t"], "p5": r["p5"], "p7": r["p7"]} for r in rows if r["t"] >= now - hours * 3600]


HEADER = "ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d"


def _fmt_iso(t):
    return datetime.fromtimestamp(t).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def import_jsonl(src, csv_path):
    """Append readings from JSON lines {"ts", "s5h", "sett", "r5h", "rsett"} (another statusline logger's
    format) to the limits CSV, oldest first, keeping only rows where a value changes and only rows newer
    than the last one already in the CSV. Returns the number of rows appended."""
    pts = []
    with open(src, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
                t = float(d["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            p5, p7 = _num(d.get("s5h")), _num(d.get("sett"))
            if p5 is None and p7 is None:
                continue
            pts.append((t, p5, p7, _num(d.get("r5h")), _num(d.get("rsett"))))
    pts.sort()
    have = parse_csv(csv_path)
    last_t = have[-1]["t"] if have else -1
    prev = (have[-1]["p5"], have[-1]["p7"]) if have else None
    out = []
    for t, p5, p7, r5, r7 in pts:
        if t <= last_t or (p5, p7) == prev:
            continue
        prev = (p5, p7)
        cell = lambda v: "" if v is None else ("%d" % v if float(v).is_integer() else "%g" % v)
        out.append("%s,%s,%s,%s,%s" % (_fmt_iso(t), cell(p5), cell(p7), cell(r5), cell(r7)))
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        new = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(HEADER + "\n")
            f.write("\n".join(out) + "\n")
    return len(out)


def _at(rows, key, t, after_ok=True):
    """Last reading with value <= t; else (if after_ok) first reading after t."""
    cand = [r for r in rows if r[key] is not None]
    before = [r for r in cand if r["t"] <= t]
    if before:
        return before[-1]
    return cand[0] if (cand and after_ok) else None


def delta_between(rows, t_start, t_end):
    """Measured percent-point consumption between two instants, per window.

    A window with a reset in the middle (different reset time or value drop) gives None.
    Values are integers on the source side: precision is about +-1 point.
    """
    res = {}
    for name, p, r in (("actual_pct_5h", "p5", "r5"), ("actual_pct_7d", "p7", "r7")):
        a, b = _at(rows, p, t_start), _at(rows, p, t_end)
        if a is None or b is None:
            res[name] = None
            continue
        inside = [x for x in rows if x[p] is not None and a["t"] <= x["t"] <= b["t"]]
        rs = sorted(x[r] for x in inside if x[r])
        dropped = any(inside[i][p] < inside[i - 1][p] for i in range(1, len(inside)))
        moved = bool(rs) and rs[-1] - rs[0] > 60  # same tolerance as pace()
        res[name] = None if (moved or dropped) else b[p] - a[p]
    return res
