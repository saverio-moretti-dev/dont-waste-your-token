# dont-waste-your-token

A small, local, open-source-minded app for people who use **Claude Code on a Pro/Max plan**:
watch your usage limits (5-hour and weekly windows) and keep a **queue of planned jobs** with
token/percent estimates that get **calibrated against what jobs really cost**.

Python 3.9+, standard library only. One local web page (hand-written HTML/CSS/JS in `ctm/static/`, system fonts, light and dark theme, responsive down to 390 px). No network access, no dependencies.


## What it does (v0.1)
- **Limits panel**: latest 5h and 7d percentage, reset time, pace (points per hour over the last hours),
  robust projection at reset (window mean since the last reset; "insufficient data" when the window is too young; ">100%: exceeds the limit around <date>" instead of absurd numbers), 48 h SVG chart. Handles missing/empty CSV and missing windows.
- **Tokens panel**: tokens per day and per project for the last 7 days, read from Claude Code transcripts
  (`~/.claude/projects/*/*.jsonl`, only the `usage` numbers), with an on-disk cache.
- **Job queue** (`queue.json`): title, project, kind, model, estimate (% of 5h and 7d, tokens), status, notes.
  Start/finish a job and the app measures the **real cost** as the difference of the limit readings.
  From finished jobs it computes the actual/estimate ratio per kind and model and suggests a corrected estimate.
- **Conflicts**: jobs declare `targets` (repos/projects/paths); queued/approved jobs with overlapping targets are flagged
  (`/api/conflicts`, page, report) with a proposal: merge, integrate, exclude or run in sequence.
- **Report before the weekly reset**: `python3 -m ctm report [--out FILE]` (also in the page): pending jobs by status,
  calibrated estimates, conflicts, pace/projection, and which approved jobs fit in the remaining margin (by priority, then size).
- **Read-only markdown import**: point `markdown_queue` at a markdown table (columns `ID`, `Repo / progetto`,
  `Lavoro`, `Stima`, `Stato`). The status is recognized from keywords in the free text (Italian/English, configurable via
  `markdown_status_keywords`) and shown next to the original text; `markdown_overrides` forces status, estimates, targets
  and priority per job id so the report counts imported jobs too. The file is never written.
- **Orders to an orchestrator agent**: write an order from the page (text, targets, priority, optional job); it is saved
  as a markdown file in `outbox_dir` with front matter (`id, created, status, targets, priority, job`). Whoever executes it
  edits `status` (`new -> taken -> done | rejected`). The app **never runs Claude or any command**. Conflicts with the queue
  are checked before saving. List from the terminal: `python3 -m ctm orders [--status new]`.

## New in v0.2 (merged from a second deployment, Windows)
- **Tokens per hour and per session** (`ctm/usage.py`): the exact token cost of a job through its session ids, even with
  other jobs running; **weighted tokens worth 1% of the weekly limit**, measured in the current window (page header,
  `/api/usage`, `python -m ctm usage`), optionally overridden by a declared `tokens_per_pct_7d`; active sessions panel.
- **Queue**: `failed` status, `after` dependency, real tokens per job (`actual_tokens`, `token_measure`), `attempts`, `result`.
  `queued` = proposal (never started automatically), `approved` = may run.
- **Optional runner** `python -m ctm run [--until 10:30] [--max-jobs N] [--dry-run]`: runs approved jobs one at a time with
  `claude -p --output-format json`, records each session's real tokens; a job the agent does not close itself
  (`python -m ctm job finish J3 --result "..."`) is never marked done; usage-limit interruptions are retried until the
  deadline, other failures up to `max_attempts`. The **page still never runs anything**: the runner is a separate command
  you start yourself. Configure it in the `runner` section of `config.json` (see `config.example.json`).
- **Terminal commands**: `python -m ctm job list|add|approve|status|start|finish|show`, `usage`,
  `import-limits FILE.jsonl` (append readings from another logger's JSON lines: `ts, s5h, sett, r5h, rsett`).
- **Windows**: `statusline/log-limits.ps1` (no jq; a named mutex prevents two sessions from writing at once).
  In your PowerShell statusline: `& "C:\path\to\statusline\log-limits.ps1" -Json $In`.
- `$CTM_CONFIG` is used when `--config` is not given.

## How it differs from existing tools
ccusage (cost reports), claude-pace and claumon (pace/forecast), Otsukare and unsnooze (pause/resume at the
limit), nightshift (prompt queue). None of them combines a job queue with percent estimates calibrated on
measured consumption. Conflict detection between queued/approved/running jobs is implemented (jobs declare `targets`).

## Install and run
```
git clone https://github.com/saverio-moretti-dev/dont-waste-your-token && cd dont-waste-your-token
cp config.example.json config.json     # optional, edit paths
python3 -m ctm                         # http://127.0.0.1:8765  (--port N, --config PATH)
python3 -m unittest discover -s tests -t .
```

## Feeding the limits: statusline snippet
Claude Code exposes `rate_limits.five_hour` / `seven_day` only to the **statusline** command (Pro/Max, after the
first API response; each window may be missing). Add `statusline/log-limits.sh` to your own statusline script:
```
input=$(cat)
printf '%s' "$input" | /path/to/dont-waste-your-token/statusline/log-limits.sh
```
It appends (ISO local time with UTC offset, e.g. `2026-10-14T19:35:22+0200`; older rows without offset are still read as local time) `ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d` rows to
`$CTM_LIMITS_CSV` (default `~/.config/claude-token-manager/limits.csv`) when a value changes. Requires `jq`.
Nothing is installed automatically.

## Scheduling the report (not done by the app)
The app creates no cron or launchd jobs. If you want the report on a schedule, add your own entry, for example
`0 22 * * 3  cd /path/to/dont-waste-your-token && python3 -m ctm report --out ~/ctm-report.md` in `crontab -e`
(Wednesday 22:00), or a launchd agent that runs the same command.

## Accuracy notes
Percentages are integers (+-1 point), and the CSV only updates when an interactive session gets a response.
Calibration uses sum(actual)/sum(estimate), treats a measured 0 as "<1 point", ignores tiny unmeasurable jobs and shows a min-max range;
runs shorter than 2 minutes are marked `mixed`. If `queue.json` is unreadable the app reports an error and never overwrites it (`queue.json.bak` keeps the last good version).
Jobs running in parallel blur the measured difference: such measures are marked `mixed` and excluded from calibration.
The projection assumes the mean pace of the current window continues; the recent pace is shown for information only.

## Privacy
Everything stays on your machine: server bound to 127.0.0.1, no outgoing requests, no CDN. Transcript lines that contain
`usage` are decoded in memory, but only the token counts, the message id and the timestamp are kept; the text is never stored or shown.
The page is served with `X-Frame-Options: DENY` and a CSP (`frame-ancestors 'none'`) against clickjacking. Scripts and styles are external files served from a fixed whitelist (`script-src 'self'; style-src 'self'`), so there is no inline code. Design notes: `UI-DESIGN.md`.

The alert level shown on the page is configurable with `warn_pct` in `config.json` (default 80).

---

## Italiano (breve)
App locale per chi usa Claude Code col piano Pro/Max: mostra consumo dei limiti (5h e settimanale) con ritmo e
proiezione, token per giorno/progetto dalle trascrizioni, e una coda di lavori con stima in % che si **calibra** sul
consumo reale (differenza dei valori nel CSV fra inizio e fine lavoro; misure in parallelo marcate «mista»).
Fase 3: import markdown con stati dalle parole chiave e `markdown_overrides`; ordini all'orchestratore come file in `outbox_dir` (l'app non esegue nulla). Fase 2: proiezione robusta (media di finestra, «dati insufficienti»), conflitti fra lavori con `targets` sovrapposti, resoconto `python3 -m ctm report`.
Avvio: `python3 -m ctm` poi apri http://127.0.0.1:8765. Solo libreria standard, tutto in locale, nessuna rete.
Per alimentare i limiti aggiungi `statusline/log-limits.sh` alla tua statusline (non viene installato da solo).

## License

MIT, see [LICENSE](LICENSE).
