"""Pre-reset report in markdown: pending jobs, corrected estimates, conflicts, pace, what fits."""
import time
from datetime import datetime

from . import conflicts, jobs, limits

ORDER = ("running", "approved", "queued", "blocked")
PROP = {"exclude": "escludere", "merge": "unire", "integrate": "integrare", "sequence": "in sequenza"}
BASIS = {"kind+model": "tipo+modello", "kind": "tipo", "global": "globale"}
LABEL = {"running": "in corso", "approved": "approvati", "queued": "in coda", "blocked": "bloccati"}


def _corr(j, cal_jobs):
    s = jobs.suggest(cal_jobs, j.get("kind") or "", j.get("model") or "",
                     j.get("estimate_pct_5h"), j.get("estimate_pct_7d"))
    return s


def _n(v):
    return "-" if v is None else ("%g" % round(v, 1))


def margin(summary):
    """Percent points of the weekly limit still usable before the reset, with a note."""
    w = summary.get("seven_day")
    if not w:
        return None, "nessuna lettura settimanale"
    if w["stale"]:
        return 100.0, "lettura della finestra precedente (già ripristinata): margine pieno"
    p = w["projection"]
    if p["status"] == "insufficient":
        return max(0.0, 100 - w["pct"]), "previsione insufficiente (%s): margine = 100 - attuale" % p["reason"]
    return max(0.0, 100 - p["at_reset_pct"]), "100 - previsto %s%% al ripristino (se il ritmo resta questo)" % (
        ">=100" if p["status"] == "exceeds" else p["at_reset_pct"])


def build(rows, own_jobs, md_jobs, window_hours=6, now=None):
    now = time.time() if now is None else now
    s = limits.summary(rows, window_hours, now)
    all_jobs = own_jobs + md_jobs
    lines = ["# Resoconto token - %s" % datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"), ""]
    lines.append("## Consumo attuale")
    for label, key in (("finestra 5 h", "five_hour"), ("finestra 7 giorni", "seven_day")):
        w = s.get(key)
        if not w:
            lines.append("- %s: nessuna lettura" % label)
            continue
        p = w["projection"]
        if p["status"] == "insufficient":
            proj = "dati insufficienti (%s)" % p["reason"]
        elif p["status"] == "exceeds":
            proj = ">100%%: supera il limite verso %s" % p["exceeds_at_text"]
        else:
            proj = "%s%% al ripristino" % p["at_reset_pct"]
        lines.append("- %s: **%s%%**, ripristino %s, ritmo medio %s pt/h, ritmo recente %s pt/h, previsione: %s%s" % (
            label, _n(w["pct"]), w["resets_at_text"] or "?", _n(p.get("rate_per_hour")),
            _n(w["rate_per_hour"]), proj, " (lettura vecchia)" if w["stale"] else ""))
    if not s["readings"]:
        lines.append("- ancora nessuna lettura dei limiti (snippet della statusline non installato?)")
    m, note = margin(s)
    lines += ["", "## Lavori aperti per stato"]
    pending = [j for j in all_jobs if j["status"] in ORDER]
    if not pending:
        lines.append("(nessuno)")
    for st in ORDER:
        group = [j for j in pending if j["status"] == st]
        if not group:
            continue
        lines += ["", "### %s" % LABEL[st], "", "| ID | Titolo | Obiettivi | Stima 5h/7g (grezza) | Stima 5h/7g (corretta) | Base |",
                  "|---|---|---|---|---|---|"]
        for j in group:
            c = _corr(j, own_jobs)
            lines.append("| %s | %s | %s | %s / %s | %s / %s | %s |" % (
                j["id"], j["title"].replace("|", "/"), ", ".join(j.get("targets") or []) or "-",
                _n(c["5h"]["raw"]), _n(c["7d"]["raw"]), _n(c["5h"]["corrected"]), _n(c["7d"]["corrected"]),
                "%s (n=%d)" % (BASIS.get(c["7d"]["basis"], c["7d"]["basis"]), c["7d"]["samples"]) if c["7d"]["basis"] != "none" else "nessuna calibrazione"))
    lines += ["", "## Conflitti"]
    cf = conflicts.find(all_jobs)
    if not cf:
        lines.append("(nessuno)")
    for c in cf:
        lines.append("- %s + %s su %s: **%s** - %s" % (c["a"], c["b"], ", ".join(c["shared"]), PROP.get(c["proposal"], c["proposal"]), c["reason"]))
    lines += ["", "## Cosa ci sta prima del ripristino settimanale"]
    if m is None:
        lines.append("Sconosciuto (%s)." % note)
    else:
        lines.append("Margine: **%s punti** (%s)." % (_n(m), note))
        appr = [j for j in all_jobs if j["status"] == "approved"]
        appr.sort(key=lambda j: (j.get("priority", 5), _corr(j, own_jobs)["7d"]["corrected"] if
                                 _corr(j, own_jobs)["7d"]["corrected"] is not None else 1e9))
        left, unknown = m, []
        lines += ["", "| Ordine | ID | Titolo | 7g corretta | Cumulata | Ci sta |", "|---|---|---|---|---|---|"]
        cum = 0.0
        for i, j in enumerate(appr, 1):
            est = _corr(j, own_jobs)["7d"]["corrected"]
            if est is None:
                unknown.append(j["id"])
                continue
            cum += est
            lines.append("| %d | %s | %s | %s | %s | %s |" % (i, j["id"], j["title"].replace("|", "/"), _n(est), _n(cum),
                                                              "sì" if cum <= m else "no"))
        if not appr:
            lines.append("(nessun lavoro approvato)")
        if unknown:
            lines.append("")
            lines.append("Approvati ma senza stima 7g (non contati): %s." % ", ".join(unknown))
    lines += ["", "_Le stime si correggono con il rapporto storico reale/stima quando ci sono almeno 2 misure pulite "
              "per lo stesso tipo/modello; le percentuali sono intere (+-1 pt)._", ""]
    return "\n".join(lines)
