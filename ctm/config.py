"""Configuration: defaults, optional config.json override, ~ expansion."""
import json
import os

DEFAULTS = {
    "port": 8765,
    "limits_csv": "~/.config/claude-token-manager/limits.csv",
    "transcripts_dir": "~/.claude/projects",
    "data_dir": "~/.config/claude-token-manager",
    "markdown_queue": None,
    "markdown_status_keywords": {},
    "markdown_overrides": {},
    "outbox_dir": "~/.config/claude-token-manager/outbox",
    "rate_window_hours": 6,
    "token_days": 7,
    "warn_pct": 80,
    # token accounting (usage.py): weights of the 4 counters and, optionally, the DECLARED weighted tokens
    # worth 1% of the weekly limit (e.g. from the plan); null = use only the measured value
    "token_weights": {},
    "tokens_per_pct_7d": None,
    # runner (python -m ctm run): OPTIONAL, separate from the page; the page never runs anything
    "runner": {
        "command": ["claude"],               # executable (+ fixed args), e.g. ["claude"] or a full path
        "args": ["--output-format", "json"],  # appended after "-p <prompt>"
        "extra_args": [],                     # e.g. ["--permission-mode", "bypassPermissions"] (your choice)
        "cwd": None,                          # working directory of the agent (default: current)
        "briefing": None,                     # template file; default: runner/briefing.example.md
        "max_hours_per_job": 4,
        "max_attempts": 3,                    # failures that are NOT a usage limit
        "wait_minutes": 30,                   # pause after an interruption / a usage limit
        "stop_at_5h_pct": 98,                 # do not start a job above this 5h reading
        "log_dir": None,                      # default: data_dir
    },
}
PATH_KEYS = ("limits_csv", "transcripts_dir", "data_dir", "markdown_queue", "outbox_dir")
RUNNER_KEYS = set(DEFAULTS["runner"])


def load(path=None):
    """Return a config dict. `path` None -> ./config.json if present, else defaults."""
    cfg = dict(DEFAULTS)
    if path is None and os.path.exists("config.json"):
        path = "config.json"
    if path:
        with open(os.path.expanduser(path), encoding="utf-8-sig") as f:
            user = json.load(f)
        runner = dict(DEFAULTS["runner"])
        runner.update(user.pop("runner", None) or {})
        cfg.update(user)
        cfg["runner"] = runner
    else:
        cfg["runner"] = dict(DEFAULTS["runner"])
    for k in PATH_KEYS:
        if cfg.get(k):
            cfg[k] = os.path.expanduser(cfg[k])
    for k in ("cwd", "briefing", "log_dir"):
        if cfg["runner"].get(k):
            cfg["runner"][k] = os.path.expanduser(cfg["runner"][k])
    validate(cfg)
    return cfg


def validate(cfg):
    from .jobs import check_keywords, check_overrides
    if not isinstance(cfg["port"], int) or isinstance(cfg["port"], bool) or not 0 <= cfg["port"] <= 65535:
        raise ValueError("port must be an integer 0..65535")
    for k in ("rate_window_hours", "token_days"):
        if not isinstance(cfg[k], (int, float)) or isinstance(cfg[k], bool) or cfg[k] <= 0:
            raise ValueError("%s must be a positive number" % k)
    w = cfg["warn_pct"]
    if not isinstance(w, (int, float)) or isinstance(w, bool) or not 1 <= w <= 100:
        raise ValueError("warn_pct must be a number between 1 and 100")
    for k in PATH_KEYS:
        if cfg.get(k) is not None and not isinstance(cfg[k], str):
            raise ValueError("%s must be a string path" % k)
    check_keywords(cfg.get("markdown_status_keywords"))
    check_overrides(cfg.get("markdown_overrides"))
    tw = cfg.get("token_weights") or {}
    if not isinstance(tw, dict) or any(k not in ("input", "output", "cache_creation", "cache_read") or
                                       not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0
                                       for k, v in tw.items()):
        raise ValueError("token_weights: keys input/output/cache_creation/cache_read, values >= 0")
    tp = cfg.get("tokens_per_pct_7d")
    if tp is not None and (not isinstance(tp, (int, float)) or isinstance(tp, bool) or tp <= 0):
        raise ValueError("tokens_per_pct_7d must be a positive number or null")
    r = cfg["runner"]
    bad = set(r) - RUNNER_KEYS
    if bad:
        raise ValueError("runner: unknown keys %s" % sorted(bad))
    for k in ("command", "args", "extra_args"):
        if not isinstance(r[k], list) or not all(isinstance(x, str) for x in r[k]) or (k == "command" and not r[k]):
            raise ValueError("runner.%s must be a list of strings" % k)
    for k in ("max_hours_per_job", "max_attempts", "wait_minutes", "stop_at_5h_pct"):
        if not isinstance(r[k], (int, float)) or isinstance(r[k], bool) or r[k] <= 0:
            raise ValueError("runner.%s must be a positive number" % k)
