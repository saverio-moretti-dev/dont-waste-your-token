#!/usr/bin/env bash
# Logs Claude Code plan-limit readings to a CSV, one row each time a value changes.
# NOT installed by this project. Add it yourself to your statusline script, e.g.:
#     input=$(cat)
#     printf '%s' "$input" | /path/to/log-limits.sh
# Reads the statusline JSON on stdin, prints nothing. Needs `jq`.
# CSV path: $CTM_LIMITS_CSV (default ~/.config/claude-token-manager/limits.csv)
# Format: ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d
#   ISO local time WITH UTC offset (e.g. 2026-10-14T19:35:22+0200), percentages, reset times as
#   epoch seconds; empty = window missing. Older files with 'YYYY-MM-DD HH:MM:SS' (no offset) still load.
csv="${CTM_LIMITS_CSV:-$HOME/.config/claude-token-manager/limits.csv}"
input=$(cat)
five=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty' 2>/dev/null)
week=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty' 2>/dev/null)
[ -z "$five$week" ] && exit 0
r5=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.resets_at // empty' 2>/dev/null)
r7=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.resets_at // empty' 2>/dev/null)
mkdir -p "$(dirname "$csv")"
[ -f "$csv" ] || echo "ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d" > "$csv"
key="${five}|${week}"
[ "$key" = "$(cat "$csv.key" 2>/dev/null)" ] && exit 0
echo "$(date '+%Y-%m-%dT%H:%M:%S%z'),$five,$week,$r5,$r7" >> "$csv"
printf '%s' "$key" > "$csv.key"
