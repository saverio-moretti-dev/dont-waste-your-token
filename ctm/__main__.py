import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import config, jobs, limits, orders, report, runner, server, usage

COMMANDS = ("serve", "report", "orders", "job", "run", "usage", "import-limits")
ap = argparse.ArgumentParser(prog="python3 -m ctm", description="Claude Token Manager (local only)", epilog="""
job subcommands:
  job list [--all]                                   pending jobs (queued, approved, running, blocked)
  job add --title T [--project P] [--targets a,b] [--kind K] [--model M] [--priority N]
          [--est-tokens N] [--est-5h X] [--est-7d X] [--after J3] [--notes TEXT] [--approved]
  job approve J3 | job status J3 <status>             status: %s
  job start J3 | job finish J3 [--failed] [--mixed] [--result TEXT]
  job show J3                                        the job as JSON
run [--until HH:MM|ISO] [--max-jobs N] [--dry-run]   optional runner of approved jobs (claude -p)
usage                                                tokens worth 1%% of the weekly limit (measured / declared)
import-limits FILE.jsonl                             append limit readings from JSON lines (fields ts, s5h, sett,
                                                     r5h, rsett) to limits_csv, keeping only changes
""" % ", ".join(jobs.STATUSES), formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("command", nargs="?", choices=COMMANDS, default="serve",
                help="serve (default): start the local page; report: pre-reset markdown report; orders: list orders in "
                     "outbox_dir; job/run/usage/import-limits: see below")
ap.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
ap.add_argument("--port", type=int)
ap.add_argument("--config", help="path to config.json (default: $CTM_CONFIG, else ./config.json if present)")
ap.add_argument("--status", choices=orders.STATUSES, help="orders: only this status")
ap.add_argument("--out", help="report: write to FILE instead of stdout")
for flag in ("--title", "--project", "--targets", "--kind", "--model", "--notes", "--after", "--result", "--until"):
    ap.add_argument(flag, help=argparse.SUPPRESS)
for flag in ("--priority", "--max-jobs"):
    ap.add_argument(flag, type=int, help=argparse.SUPPRESS)
for flag in ("--est-tokens", "--est-5h", "--est-7d"):
    ap.add_argument(flag, type=float, help=argparse.SUPPRESS)
for flag in ("--all", "--approved", "--failed", "--mixed", "--dry-run"):
    ap.add_argument(flag, action="store_true", help=argparse.SUPPRESS)
a = ap.parse_args()
cfg_path = a.config or os.environ.get("CTM_CONFIG") or None
cfg = config.load(cfg_path)
if cfg_path:
    cfg_path = os.path.abspath(os.path.expanduser(cfg_path))
if a.port:
    cfg["port"] = a.port


def usage_data():
    return usage.load(cfg["transcripts_dir"], os.path.join(cfg["data_dir"], "usage-cache.json"))


def token_meter(t0, t1, session_ids):
    """(raw, weighted, how) for Queue.finish: exact by session when known, else the whole interval."""
    data = usage_data()
    if session_ids:
        raw, how = usage.of_sessions(data, session_ids), "sessions"
    else:
        raw, how = usage.between(data, t0, t1), "interval"
    return raw, usage.weigh(raw, cfg.get("token_weights")), how


def show(j):
    tok = "" if not j.get("actual_tokens") else " real %s tok (%s)" % (j["actual_tokens"], j.get("token_measure"))
    est = " est %s tok" % int(j["est_tokens"]) if j.get("est_tokens") else ""
    print("%-5s %-9s p%s %-40s%s%s%s" % (j["id"], j["status"], j.get("priority", 5), j["title"][:40], est, tok,
                                        " after %s" % j["after"] if j.get("after") else ""))


def cmd_job():
    q = jobs.Queue(cfg["data_dir"])
    sub = a.rest[0] if a.rest else "list"
    jid = a.rest[1] if len(a.rest) > 1 else None
    if sub == "list":
        for j in q.load():
            if a.all or j["status"] in ("queued", "approved", "running", "blocked"):
                show(j)
    elif sub == "add":
        data = {"title": a.title, "project": a.project, "targets": a.targets, "kind": a.kind, "model": a.model,
                "priority": a.priority, "est_tokens": a.est_tokens, "estimate_pct_5h": a.est_5h,
                "estimate_pct_7d": a.est_7d, "after": a.after, "notes": a.notes,
                "status": "approved" if a.approved else "queued"}
        show(q.add({k: v for k, v in data.items() if v is not None}))
    elif sub in ("approve", "status", "start", "finish", "show"):
        if not jid:
            sys.exit("job %s needs a job id" % sub)
        if sub == "approve":
            show(q.set_status(jid, "approved"))
        elif sub == "status":
            if len(a.rest) < 3:
                sys.exit("job status J3 <status>")
            show(q.set_status(jid, a.rest[2]))
        elif sub == "start":
            show(q.start(jid))
        elif sub == "finish":
            # inside a runner the runner records the exact tokens of the session itself
            meter = None if os.environ.get("CTM_RUNNER") == "1" else token_meter
            show(q.finish(jid, limits.parse_csv(cfg["limits_csv"]), a.mixed, tokens=meter,
                          status="failed" if a.failed else "done", result=a.result))
        else:
            print(json.dumps(next(j for j in q.load() if j["id"] == jid), ensure_ascii=False, indent=2))
    else:
        sys.exit("unknown job subcommand %r (list, add, approve, status, start, finish, show)" % sub)


def parse_until(text):
    if not text:
        return None
    if len(text) <= 5 and ":" in text:
        h, m = (int(x) for x in text.split(":"))
        t = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        return t.timestamp() + (86400 if t.timestamp() <= time.time() else 0)
    return limits.parse_time(text)


def cmd_usage():
    rows = limits.parse_csv(cfg["limits_csv"])
    m = usage.tokens_per_pct(rows, usage_data(), cfg.get("token_weights"))
    m["declared_tokens_per_pct"] = cfg.get("tokens_per_pct_7d")
    print(json.dumps(m, indent=2))


def cmd_import_limits():
    if not a.rest:
        sys.exit("import-limits FILE.jsonl")
    n = limits.import_jsonl(a.rest[0], cfg["limits_csv"])
    print("%d readings appended to %s" % (n, cfg["limits_csv"]))


if a.command == "orders":
    for o in orders.list_orders(cfg["outbox_dir"], a.status):
        print(" | ".join([o["id"], o["status"], "p%s" % o["priority"], ",".join(o["targets"]) or "-",
                          o.get("job") or "-", o["text"].split("\n", 1)[0][:70]]))
elif a.command == "report":
    own = jobs.Queue(cfg["data_dir"]).load()
    text = report.build(limits.parse_csv(cfg["limits_csv"]), own, jobs.load_markdown_cfg(cfg),
                        cfg["rate_window_hours"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("report written to", a.out, file=sys.stderr)
    else:
        print(text)
elif a.command == "job":
    try:
        cmd_job()
    except (ValueError, jobs.UnknownJob, StopIteration) as e:
        sys.exit("error: %s" % (e or "unknown job"))
elif a.command == "run":
    runner.Runner(cfg, cfg_path, out=sys.stdout).run(parse_until(a.until), a.max_jobs, a.dry_run)
elif a.command == "usage":
    cmd_usage()
elif a.command == "import-limits":
    cmd_import_limits()
else:
    server.serve(cfg)
