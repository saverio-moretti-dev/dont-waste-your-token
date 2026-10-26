# Logs Claude Code plan-limit readings to a CSV, one row each time a value changes (Windows / PowerShell 5.1+).
# Same CSV as log-limits.sh: ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d
# NOT installed by this project. Call it from your own statusline script, passing the JSON it received:
#     $In = [Console]::In.ReadToEnd()
#     & "C:\path\to\claude-token-manager\statusline\log-limits.ps1" -Json $In
# (or pipe it: ... | powershell -NoProfile -File log-limits.ps1). Prints nothing, never throws.
# CSV path: $env:CTM_LIMITS_CSV (default %USERPROFILE%\.config\claude-token-manager\limits.csv)
# Several sessions can call it at the same moment: a named mutex serializes the check-and-append
# (without it two sessions once wrote the same file together and a bad file stopped all logging).
param([string]$Json)
try {
    if (-not $Json) { $Json = [Console]::In.ReadToEnd() }
    $d = $Json | ConvertFrom-Json
    $f = $d.rate_limits.five_hour; $w = $d.rate_limits.seven_day
    if ($null -eq $f -and $null -eq $w) { return }
    $five = if ($null -ne $f.used_percentage) { "$($f.used_percentage)" } else { "" }
    $week = if ($null -ne $w.used_percentage) { "$($w.used_percentage)" } else { "" }
    if (-not ($five + $week)) { return }
    $r5 = if ($null -ne $f.resets_at) { "$($f.resets_at)" } else { "" }
    $r7 = if ($null -ne $w.resets_at) { "$($w.resets_at)" } else { "" }
    $csv = $env:CTM_LIMITS_CSV
    if (-not $csv) { $csv = Join-Path $env:USERPROFILE '.config\claude-token-manager\limits.csv' }
    $m = New-Object System.Threading.Mutex($false, 'Global\ctm-log-limits')
    if (-not $m.WaitOne(3000)) { return }
    try {
        $dir = Split-Path -Parent $csv
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        if (-not (Test-Path $csv)) { [IO.File]::WriteAllText($csv, "ora,sessione_5h_pct,settimana_7d_pct,ripristino_5h,ripristino_7d`n", $utf8) }
        $key = "$five|$week"; $kf = "$csv.key"
        $old = if (Test-Path $kf) { ([IO.File]::ReadAllText($kf)).Trim() } else { "" }
        if ($key -eq $old) { return }
        $now = Get-Date
        $off = $now.ToString('zzz').Replace(':', '')
        $row = "{0}{1},{2},{3},{4},{5}`n" -f $now.ToString('yyyy-MM-ddTHH:mm:ss'), $off, $five, $week, $r5, $r7
        [IO.File]::AppendAllText($csv, $row, $utf8)
        [IO.File]::WriteAllText($kf, $key, $utf8)
    } finally { $m.ReleaseMutex(); $m.Dispose() }
} catch {}
